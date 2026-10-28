import contextlib
import glob
import importlib.util
import io
import os
import re
import threading
import types
import unittest
from unittest import mock

import repo_path  # noqa: F401
from repo_path import REPO_ROOT
from Scripts import explain
from Scripts import hardware_customizer
from Scripts import utils
from Scripts.datasets import explanations
from Scripts.gui import broker
from Scripts.gui import session as session_module
from test_gui_app import TkTestCase
from test_tahoe_extras import BROADCOM_NAME, checked, load_desktop_report, reset_kexts, select_kexts, with_amd_gpu, with_broadcom

PROMPT_CALL = re.compile(r"request_input\(\s*f?([\"'])((?:\\.|(?!\1).)*)\1")


def code_prompts():
    prompts = []
    for path in glob.glob(os.path.join(REPO_ROOT, "Scripts", "*.py")) + [os.path.join(REPO_ROOT, "OpenCoreWizard.py")]:
        with open(path, "r", encoding="utf-8") as handle:
            prompts += [match.group(2) for match in PROMPT_CALL.finditer(handle.read())]
    return prompts


def plain(text):
    return " ".join(utils.ANSI_ESCAPE.sub("", text).split())


def load_choices_script():
    spec = importlib.util.spec_from_file_location("render_choices", os.path.join(REPO_ROOT, "tests", "ci", "render_choices.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DatasetTest(unittest.TestCase):
    def test_every_prompt_decision_matches_one_prompt_in_the_code(self):
        prompts = code_prompts()
        self.assertGreater(len(prompts), 40)
        for key, decision in explanations.decisions.items():
            if decision["prompt"] is None:
                continue
            matches = [prompt for prompt in prompts if explain.decision_for_prompt(prompt) == key]
            self.assertEqual(len(matches), 1, "{}: {}".format(key, matches))

    def test_longest_match_wins(self):
        self.assertEqual(explain.decision_for_prompt("SELECT A GPU COMBINATION (1-3): "), "device_combination")
        self.assertEqual(explain.decision_for_prompt("Select a WiFi device (1-2): "), "device_choice")
        self.assertIsNone(explain.decision_for_prompt("Select an option: "))
        with mock.patch.dict(explanations.decisions, {"short": dict(explanations.decisions["audio"], prompt="audio kext")}):
            self.assertEqual(explain.decision_for_prompt("Select audio kext for your system (default: 2): "), "audio")

    def test_every_entry_is_complete(self):
        for key, decision in explanations.decisions.items():
            for field in ("title", "what", "why", "default"):
                self.assertTrue(decision[field].strip(), "{} has no {}".format(key, field))
            self.assertTrue(decision["options"], key)
            for option in decision["options"]:
                self.assertEqual(len(option), 3, key)
        for step, keys in explain.STEP_STAGES.items():
            for key in keys:
                self.assertIn(key, explanations.stages, step)

    def test_render_decision_has_every_part(self):
        for key, decision in explanations.decisions.items():
            text = plain(explain.render_decision(key))
            self.assertTrue(text.startswith(decision["title"]))
            for part in ("What: " + decision["what"], "Why: " + decision["why"], "Default: " + decision["default"]):
                self.assertIn(" ".join(part.split()), text)
            for value, label, when in decision["options"]:
                self.assertIn(" ".join("{}: {}. {}".format(value, label, when).split()), text)
            for line in utils.ANSI_ESCAPE.sub("", explain.render_decision(key)).splitlines():
                self.assertLessEqual(len(line), explain.WIDTH)

    def test_decision_line_resolves_defaults(self):
        self.assertEqual(explain.decision_line("audio", "", "Select audio kext for your system (default: 2): "),
                         "Sound on macOS 26: AppleALC + SimpleHDA. " + explanations.decisions["audio"]["options"][1][2])
        self.assertTrue(explain.decision_line("macos_version", "25", "Please enter the macOS version you want to use (default: macOS Tahoe 26): ")
                        .startswith("Which macOS to install: the suggested version (macOS Tahoe 26). Almost always."))
        self.assertTrue(explain.decision_line("amd_navi", "", "Select kext for your AMD Navi 21 GPU (default: WhateverGreen): ")
                        .startswith("Driver helper for your AMD graphics card: WhateverGreen. First install."))
        self.assertTrue(explain.decision_line("uefi", "", "Build EFI for UEFI? (Yes/no): ").startswith("UEFI or legacy BIOS boot: build for UEFI. "))
        self.assertTrue(explain.decision_line("uefi", "n", "Build EFI for UEFI? (Yes/no): ").startswith("UEFI or legacy BIOS boot: build for legacy BIOS. "))
        self.assertTrue(explain.decision_line("force_load", "", "Do you want to force load this kext on the unsupported macOS version? (yes/No): ")
                        .startswith("Loading a kext on a macOS it does not declare support for: leave it out. "))
        self.assertTrue(explain.decision_line("intel_wifi", "", "Select Intel Wi-Fi kext (default: AirportItlwm-laobamac): ")
                        .startswith("Intel Wi-Fi driver: AirportItlwm-laobamac, native menu, no patches. First choice"))
        self.assertEqual(explain.decision_line("device_choice", "2"), "Which device to keep: 2.")


class InstallStepsTest(unittest.TestCase):
    def test_steps_are_plain_sentences(self):
        self.assertGreaterEqual(len(explanations.install_steps), 6)
        for step in explanations.install_steps:
            self.assertTrue(step.endswith("."), step)
            self.assertNotIn("\n", step)


class ChoicesDocTest(unittest.TestCase):
    def test_committed_doc_equals_the_generator_output(self):
        with open(os.path.join(REPO_ROOT, "docs", "reference", "choices.md"), "r", encoding="utf-8") as handle:
            committed = handle.read()
        self.assertEqual(committed, load_choices_script().render(), "run: python tests/ci/render_choices.py")


class AcceptedAnswersTest(unittest.TestCase):
    def tearDown(self):
        utils.Utils.set_headless(None)
        reset_kexts()

    def values(self, key):
        return [value for value, _, _ in explanations.decisions[key]["options"]]

    def test_audio(self):
        self.assertEqual(self.values("audio"), ["1", "2"])
        for value, simplehda in (("1", False), ("2", True)):
            _, needs_oclp, output = select_kexts(load_desktop_report(), "25.0.0", False, {"Select audio kext": value})
            self.assertNotIn("Invalid selection", output)
            self.assertTrue(checked("AppleALC"))
            self.assertEqual(checked("SimpleHDA"), simplehda)
            self.assertEqual(needs_oclp, not simplehda)
            reset_kexts()

    def test_amd_navi(self):
        self.assertEqual(self.values("amd_navi"), ["1", "2"])
        for value, kext in (("1", "NootRX"), ("2", "WhateverGreen")):
            report = with_amd_gpu(load_desktop_report(), codename="Navi 21")
            report["GPU"] = {name: props for name, props in report["GPU"].items() if props["Manufacturer"] == "AMD"}
            _, _, output = select_kexts(report, "24.0.0", False, {"Select kext for your AMD": value})
            self.assertIn("Select kext for your AMD Navi 21 GPU", output)
            self.assertNotIn("Invalid selection", output)
            self.assertTrue(checked(kext))
            reset_kexts()

    def test_broadcom(self):
        self.assertEqual(self.values("broadcom"), ["1", "2"])
        for value, kext in (("1", "IOSkywalkFamily"), ("2", "AppleBCMWLANCompanion")):
            _, _, output = select_kexts(with_broadcom(load_desktop_report()), "25.0.0", True, {"Select Broadcom Wi-Fi option": value})
            self.assertIn(BROADCOM_NAME, output)
            self.assertNotIn("Invalid selection", output)
            self.assertTrue(checked(kext))
            reset_kexts()

    def test_whatevergreen(self):
        self.assertEqual(self.values("whatevergreen"), ["yes", "no"])
        for value in ("yes", "no"):
            maestro, _, output = select_kexts(with_amd_gpu(load_desktop_report()), "25.0.0", False, {"Disable WhateverGreen for the first boot": value})
            self.assertNotIn("Invalid selection", output)
            self.assertEqual(maestro.whatevergreen_first_boot_off, value == "yes")
            reset_kexts()

    def test_force_load(self):
        from Scripts import kext_maestro
        self.assertEqual(self.values("force_load"), ["no", "yes"])
        maestro = kext_maestro.KextMaestro()
        for value, expected in (("no", set()), ("n", set()), ("", set()), ("yes", {"Lilu"}), ("y", {"Lilu"})):
            utils.Utils.set_headless({"prompts": {"Do you want to force load": value}, "default": ""})
            with mock.patch.object(maestro, "is_kext_compatible", return_value=False), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(maestro.verify_kext_compatibility(["Lilu"], "25.0.0"), expected)

    def test_debug(self):
        self.assertEqual(self.values("debug"), ["no", "yes"])
        main = session_module.load_main_module()
        for value in ("no", "yes"):
            utils.Utils.set_headless({"prompts": {"Build a DEBUG EFI": value}, "default": ""})
            fake = types.SimpleNamespace(u=utils.Utils())
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main.OCPE.select_debug_build(fake), value == "yes")

    def test_uefi(self):
        self.assertEqual(self.values("uefi"), ["yes", "no"])
        for value, firmware in (("yes", "UEFI"), ("y", "UEFI"), ("", "UEFI"), ("no", "Legacy"), ("n", "Legacy")):
            utils.Utils.set_headless({"prompts": {"Build EFI for UEFI": value}, "default": ""})
            with contextlib.redirect_stdout(io.StringIO()):
                customized, _, _ = hardware_customizer.HardwareCustomizer().hardware_customization({"BIOS": {"Firmware Type": "Legacy"}}, "24.0.0")
            self.assertEqual(customized["BIOS"]["Firmware Type"], firmware)

    def test_oclp_warning(self):
        self.assertEqual(self.values("oclp_warning"), ["yes", "no"])
        main = session_module.load_main_module()
        for value, expected in (("yes", True), ("y", True), ("", False), ("n", False), ("no", False)):
            utils.Utils.set_headless({"prompts": {"Continue with OpenCore Legacy Patcher": value}, "default": ""})
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertIs(main.OCPE.show_oclp_warning(types.SimpleNamespace(u=utils.Utils()), "25.0.0"), expected)


class TerminalExplanationTest(unittest.TestCase):
    def setUp(self):
        utils.Utils.explained_prompt = None
        utils.Utils.decisions = []

    def tearDown(self):
        utils.Utils.set_headless(None)
        utils.Utils.set_accept_defaults(False)
        utils.Utils.set_presets({})
        utils.Utils.decisions = []

    def ask(self, *prompts):
        with mock.patch("builtins.input", return_value="2"), contextlib.redirect_stdout(io.StringIO()) as output:
            for prompt in prompts:
                utils.Utils().request_input(prompt)
        return output.getvalue()

    def test_block_is_printed_once_for_a_repeated_prompt(self):
        prompt = "Select audio kext for your system (default: 2): "
        output = self.ask(prompt, prompt, prompt)
        self.assertEqual(output.count("Sound on macOS 26"), 1)
        self.assertIn("What: Apple removed AppleHDA", output)
        output = self.ask("Select Broadcom Wi-Fi option (default: 1): ", prompt)
        self.assertEqual(output.count("Sound on macOS 26"), 1)
        self.assertEqual(output.count("Broadcom Wi-Fi on macOS 14 and newer"), 1)

    def test_block_returns_after_another_prompt(self):
        prompt = "Please enter the macOS version you want to use (default: macOS Tahoe 26): "
        output = self.ask(prompt, "Select an option: ", prompt)
        self.assertEqual(output.count("Which macOS to install"), 2)

    def test_cleared_screen_explains_again(self):
        prompt = "Select audio kext for your system (default: 2): "
        with mock.patch("os.system"), mock.patch.object(utils.Utils, "adjust_window_size"):
            output = self.ask(prompt)
            with contextlib.redirect_stdout(io.StringIO()):
                utils.Utils().head("Select Required Kernel Extensions")
            output += self.ask(prompt)
        self.assertEqual(output.count("Sound on macOS 26"), 2)

    def test_prompts_without_a_decision_print_nothing_extra(self):
        self.assertEqual(self.ask("Select an option: "), "")

    def test_headless_answers_are_explained_without_counting_twice(self):
        prompt = "Disable WhateverGreen for the first boot? (Yes/no): "
        utils.Utils.set_headless({"prompts": {"Disable WhateverGreen": "yes"}, "default": ""})
        output = self.ask(prompt)
        self.assertIn("Turn the graphics helper off for the first boot", output)
        self.assertEqual(utils.Utils._headless_prompt_counts[prompt], 1)
        self.assertEqual(utils.Utils._headless_total_count, 1)
        self.assertEqual(utils.Utils.decisions, [])

    def test_express_records_the_automatic_answers(self):
        utils.Utils.set_accept_defaults(True)
        utils.Utils.set_presets({"Select Broadcom Wi-Fi option": "1"})
        output = self.ask("Select audio kext for your system (default: 2): ", "Select audio kext for your system (default: 2): ",
                          "Select Broadcom Wi-Fi option (default: 1): ")
        self.assertIn("Sound on macOS 26", output)
        self.assertEqual([key for key, _, _ in utils.Utils.decisions], ["audio", "broadcom"])
        lines = utils.Utils.decision_lines()
        self.assertTrue(lines[0].startswith("Sound on macOS 26: AppleALC + SimpleHDA. When nothing else needs root patches."))
        self.assertTrue(lines[1].startswith("Broadcom Wi-Fi on macOS 14 and newer: OCLP root patches. The proven way."))

    def test_stage_notes_are_logged_once(self):
        progress = []
        utils.Utils._stage_keys = ()
        utils.Utils.set_progress_handler(lambda *args: progress.append(args))
        try:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                utils.Utils().progress_bar("Build everything", ["Hardware scan", "Configuration"], 0)
                utils.Utils().progress_bar("Scan", ["Gathering PnP devices"], 0)
                utils.Utils().progress_bar("Build everything", ["Hardware scan", "Configuration"], 1)
        finally:
            utils.Utils.set_progress_handler(None)
        text = plain(output.getvalue())
        self.assertEqual(text.count(explanations.stages["scan"]), 1)
        self.assertIn(explanations.stages["verdict"], text)
        self.assertIn(explanations.stages["macos"], text)
        self.assertEqual(len(progress), 3)


class BrokerExplanationTest(unittest.TestCase):
    def tearDown(self):
        utils.Utils.decisions = []
        utils.Utils.explained_prompt = None

    def test_dialog_request_carries_the_decision(self):
        posted = []
        prompts = broker.PromptBroker(posted.append)
        answers = []
        worker = threading.Thread(target=lambda: answers.append(prompts("Select audio kext for your system (default: 2): ", "1. AppleALC + OCLP\n2. AppleALC + SimpleHDA\n")))
        worker.start()
        for _ in range(200):
            if posted:
                break
            worker.join(0.02)
        kind, request = posted[0]
        self.assertEqual(kind, "prompt")
        self.assertEqual(request.decision, "audio")
        self.assertEqual(request.default, "2")
        request.reply("1")
        worker.join(5)
        self.assertEqual(answers, ["1"])
        self.assertIsNone(broker.PromptRequest("Select an option: ", "", []).decision)

    def test_automatic_answers_are_logged_and_recorded_in_express(self):
        prompts = broker.PromptBroker(lambda message: None)
        prompts.set_presets({"Select Broadcom Wi-Fi option": "2"})
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(prompts("Select Broadcom Wi-Fi option (default: 1): ", ""), "2")
        self.assertIn("Broadcom Wi-Fi on macOS 14 and newer", output.getvalue())
        self.assertEqual(utils.Utils.decisions, [])
        prompts.set_accept_defaults(True)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(prompts("Select audio kext for your system (default: 2): ", ""), "")
        self.assertEqual([key for key, _, _ in utils.Utils.decisions], ["audio"])


class ExpressSummaryTest(unittest.TestCase):
    def test_summary_lists_the_decisions_taken(self):
        from test_gui_session import ExpressTest
        case = ExpressTest("test_full_chain_order_and_summary")
        case.setUp()
        self.addCleanup(case.doCleanups)
        session = case.session
        session.defaults_hook = utils.Utils.set_accept_defaults
        case.configure_hook = lambda: utils.Utils().request_input("Select audio kext for your system (default: 2): ")
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                summary = session.express("report.json")
        finally:
            utils.Utils.set_accept_defaults(False)
        self.assertEqual(len(summary["decisions"]), 1)
        self.assertTrue(summary["decisions"][0].startswith("Sound on macOS 26: AppleALC + SimpleHDA."))
        lines = session_module.summary_lines(summary)
        self.assertIn("Decided for you:", lines)
        self.assertIn("  - " + summary["decisions"][0], lines)


class WindowExplanationTest(TkTestCase):
    def label_texts(self, widget):
        texts = []
        for child in widget.winfo_children():
            if child.winfo_class() in ("TLabel", "Label"):
                texts.append(str(child.cget("text")))
            texts += self.label_texts(child)
        return texts

    def test_options_page_shows_what_why_and_when(self):
        from Scripts.gui import pages
        app = self.make_app(install_hooks=False)
        page = app.page_by_class(pages.OptionsPage)
        texts = self.label_texts(page)
        for key in ("audio", "broadcom", "whatevergreen", "debug"):
            decision = explanations.decisions[key]
            self.assertIn(decision["what"], texts)
            self.assertIn(decision["why"], texts)
            for _, _, when in decision["options"]:
                self.assertIn(when, texts)
        app.show_page(app.pages.index(page))
        app.update_idletasks()

    def test_blank_option_names_the_recommended_choice(self):
        from Scripts.gui import pages
        app = self.make_app(install_hooks=False)
        page = app.page_by_class(pages.OptionsPage)
        page.session.option_relevance = lambda darwin: {"audio": True, "broadcom": False, "whatevergreen": True}
        page.on_show()
        self.assertEqual(page.frames["audio"][0].cget("text"), "Use the recommended choice: AppleALC + SimpleHDA (no root patch)")
        self.assertEqual(page.frames["broadcom"][0].cget("text"), "Use the recommended choice (not needed for this hardware)")
        self.assertEqual(page.frames["whatevergreen"][0].cget("text"), "Use the recommended choice: Disable it for the first boot")

    def test_finish_page_lists_the_install_steps(self):
        from Scripts.gui import pages
        app = self.make_app(install_hooks=False)
        page = app.page_by_class(pages.FinishPage)
        text = page.steps.get("1.0", "end")
        self.assertTrue(text.startswith("1. Write the stick (USB page in the window"))
        self.assertIn("{}. {}".format(len(explanations.install_steps), explanations.install_steps[-1]), text)

    def test_pages_carry_their_notes(self):
        from Scripts.gui import pages
        app = self.make_app(install_hooks=False)
        for page_class, key in ((pages.MacOSPage, "macos_version"), (pages.UsbMapPage, "usb_map"), (pages.RecoveryPage, "recovery"),
                                (pages.UsbWriterPage, "postinstall_kit"), (pages.UsbWriterPage, "usb_erase")):
            texts = "\n".join(self.label_texts(app.page_by_class(page_class)))
            self.assertIn(explanations.decisions[key]["why"], texts)
        legend = self.label_texts(app.page_by_class(pages.HardwarePage))
        for name in ("Supported", "Needs root patches (OCLP)", "Unsupported", "Unchecked"):
            self.assertIn(name, legend)

    def test_prompt_dialog_explains_the_decision(self):
        app = self.make_app(install_hooks=False)
        request = broker.PromptRequest("Select audio kext for your system (default: 2): ", "1. AppleALC + OCLP\n2. AppleALC + SimpleHDA\n",
                                       [("1. AppleALC + OCLP", "1"), ("2. AppleALC + SimpleHDA", "2")], "2", "audio")
        dialog = self.app_module.PromptDialog(app, request)
        app.dialogs.append(dialog)
        text = dialog.explanation.get("1.0", "end")
        self.assertTrue(text.startswith("Sound on macOS 26\n"))
        self.assertIn("What: " + explanations.decisions["audio"]["what"], text)
        self.assertIn("\n2: AppleALC + SimpleHDA. When nothing else", text)
        self.assertIn("Default: " + explanations.decisions["audio"]["default"], text)
        self.assertEqual(int(dialog.explanation.cget("height")), 8)
        dialog.submit("2")
        self.assertEqual(request.answer, "2")

    def test_glossary_window_opens_and_closes(self):
        app = self.make_app(install_hooks=False)
        window = app.show_glossary()
        app.update_idletasks()
        content = window.text.get("1.0", "end")
        for term, meaning in explanations.glossary.items():
            self.assertIn(term + "\n" + meaning, content)
        self.assertIs(app.show_glossary(), window)
        window.destroy()
        app.update_idletasks()
        self.assertIsNot(app.show_glossary(), window)


class TerminalGlossaryTest(unittest.TestCase):
    def test_menu_g_pages_through_the_glossary(self):
        main = session_module.load_main_module()
        asked = []
        fake = types.SimpleNamespace(u=types.SimpleNamespace(head=lambda *args, **kwargs: None, request_input=lambda prompt="": asked.append(prompt) or ""))
        with contextlib.redirect_stdout(io.StringIO()) as output:
            main.OCPE.show_glossary(fake)
        text = plain(output.getvalue())
        for term, meaning in explanations.glossary.items():
            self.assertIn(plain("{}: {}".format(term, meaning)), text)
        self.assertEqual(asked[-1], "Press Enter to main menu...")
        self.assertTrue(all(prompt == "Press Enter for more..." for prompt in asked[:-1]))
        self.assertGreater(len(asked), 1)


if __name__ == "__main__":
    unittest.main()
