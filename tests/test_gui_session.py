import contextlib
import io
import json
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from repo_path import FIXTURES

from Scripts import usb_mapper
from Scripts import utils
from Scripts.gui import broker
from Scripts.gui import session as session_module


class ScriptedPrompts:
    def __init__(self, answers):
        self.answers = answers
        self.prompts = []
        self.log = []

    def post(self, message):
        if message[0] == "log":
            self.log.append(message[1])
        elif message[0] == "prompt":
            request = message[1]
            self.prompts.append(request.prompt)
            lowered = request.prompt.lower()
            answer = next((value for key, value in self.answers.items() if key.lower() in lowered), "")
            request.reply(answer)


class SessionPipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(FIXTURES, "answers-desktop.json"), encoding="utf-8") as handle:
            cls.answers = json.load(handle)["prompts"]

    def setUp(self):
        self.script = ScriptedPrompts(self.answers)
        self.broker = broker.PromptBroker(self.script.post)
        self.session = session_module.Session(preset_hook=self.broker.set_presets)
        self.restore = utils.Utils.install_output_proxy()
        utils.Utils.set_output_sink(self.broker.sink)
        utils.Utils.set_prompt_handler(self.broker)

    def tearDown(self):
        utils.Utils.set_prompt_handler(None)
        utils.Utils.set_output_sink(None)
        self.restore()

    def test_recommended_options_on_the_desktop_fixture(self):
        self.session.load_report(os.path.join(FIXTURES, "desktop", "Report.json"))
        self.assertEqual(self.session.recommended_options(25)["audio"], "2")

    def test_desktop_report_verdict_and_configuration(self):
        verdict = self.session.load_report(os.path.join(FIXTURES, "desktop", "Report.json"))
        self.assertIsNone(verdict["acpi"])
        self.assertTrue(self.session.ocpe.ac.skip_acpi_tables)
        sections = {row["section"] for row in verdict["rows"]}
        self.assertTrue({"CPU", "GPU", "Network"} <= sections)
        self.assertTrue(all(row["status"] in ("supported", "unsupported", "oclp", "unchecked") for row in verdict["rows"]))
        self.assertTrue(all("\033[" not in row["support"] for row in verdict["rows"]))
        self.assertIsNotNone(verdict["native"])

        choices = self.session.macos_choices()
        darwins = [choice["darwin"] for choice in choices]
        self.assertIn(25, darwins)
        self.assertEqual(darwins, sorted(darwins))
        default = self.session.default_macos()
        self.assertIn(default, darwins)
        self.assertTrue(next(choice for choice in choices if choice["darwin"] == default)["suggested"])
        self.assertEqual(self.broker.presets, [])

        options = {"audio": "2", "broadcom": "1", "whatevergreen": "yes", "debug": True}
        config = self.session.configure(25, options)
        self.assertTrue(config["macos_version"].startswith("25."))
        self.assertEqual(config["smbios_model"], "iMac20,1")
        self.assertTrue(config["debug"])
        self.assertTrue(self.session.ocpe.debug_build)
        self.assertIn("Lilu", config["kexts"])
        self.assertEqual(self.broker.presets, [])
        self.assertFalse(any(prompt.lower().startswith("please enter the macos version") for prompt in self.script.prompts))
        self.assertTrue(self.session.option_relevance(25)["audio"])

        bios = self.session.bios_requirements()
        self.assertEqual(set(bios), {"disable", "enable", "notes"})

    def test_incompatible_report_raises_stage_error(self):
        with self.assertRaises(session_module.StageError) as raised:
            self.session.load_report(os.path.join(FIXTURES, "laptop", "Report.json"))
        self.assertIn("not compatible", str(raised.exception))
        self.assertIsInstance(raised.exception, session_module.UnsupportedHardware)
        self.assertIsNone(self.session.hardware_report)

    def test_invalid_report_lists_the_errors(self):
        with self.assertRaises(session_module.StageError) as raised:
            self.session.load_report(os.path.join(FIXTURES, "no-such-report.json"))
        self.assertTrue(raised.exception.details)

    def test_configure_needs_a_report(self):
        with self.assertRaises(session_module.StageError):
            self.session.configure(25)

    def test_declined_root_patches_name_the_cause(self):
        self.session.load_report(os.path.join(FIXTURES, "desktop", "Report.json"))
        self.script.answers = dict(self.answers, **{"Continue with OpenCore Legacy Patcher": "no"})
        with self.assertRaises(session_module.StageError) as raised:
            self.session.configure(25, {"audio": "1", "broadcom": "1", "whatevergreen": "yes", "debug": False})
        self.assertIn("The AppleALC + OCLP audio choice needs root patches on macOS Tahoe 26", str(raised.exception))
        self.assertFalse(self.session.configured)


class SessionHelpersTest(unittest.TestCase):
    def test_option_presets_take_the_default_when_nothing_was_chosen(self):
        session = session_module.Session()
        presets = session.option_presets(24, {"audio": None, "broadcom": "2", "whatevergreen": "no", "debug": False})
        self.assertEqual(presets, {
            "Please enter the macOS version": "24",
            "Select audio kext for your system": "",
            "Select Broadcom Wi-Fi option": "2",
            "Disable WhateverGreen for the first boot": "no"
        })

    def test_answering_clears_presets_even_on_error(self):
        seen = []
        session = session_module.Session(preset_hook=seen.append)
        with self.assertRaises(RuntimeError):
            with session.answering({"Type ERASE": "ERASE"}):
                raise RuntimeError("boom")
        self.assertEqual(seen, [{"Type ERASE": "ERASE"}, {}])

    def test_macos_choices_mark_oclp_and_suggested(self):
        session = session_module.Session()
        session.native_macos_version = ("17.0.0", "23.99.99")
        session.ocl_patched_macos_version = ("25.99.99", "24.0.0")
        session.suggested_macos_version = "23.99.99"
        choices = session.macos_choices()
        self.assertEqual([choice["darwin"] for choice in choices], list(range(17, 26)))
        self.assertEqual([choice["darwin"] for choice in choices if choice["oclp"]], [24, 25])
        self.assertEqual(session.default_macos(), 23)

    def test_recommended_options_follow_the_root_patch_range(self):
        session = session_module.Session()
        self.assertEqual(session.recommended_options(25), {"audio": "2", "broadcom": "1", "whatevergreen": "yes"})
        session.native_macos_version = ("17.0.0", "23.99.99")
        session.ocl_patched_macos_version = ("25.99.99", "24.0.0")
        session.suggested_macos_version = "23.99.99"
        self.assertEqual(session.recommended_options(25)["audio"], "1")
        self.assertEqual(session.recommended_options(23)["audio"], "2")

    def test_usb_map_disable_flag_reaches_the_build(self):
        session = session_module.Session()
        session.set_usb_map_disabled(True)
        self.assertTrue(session.usb_map_disabled)
        session.set_usb_map_disabled(False)
        self.assertFalse(session.usb_map_disabled)

    def test_write_installer_passes_the_selected_disk(self):
        session = session_module.Session()
        seen = {}

        class Usb:
            def create_installer(self, *args, **kwargs):
                seen.update(kwargs)

        class Ocpe:
            usb = Usb()
            result_dir = "Results"

        session.ensure_ocpe = lambda: Ocpe()
        disk = {"number": 3, "size_bytes": 16, "identity": "A"}
        session.write_installer(disk, "ERASE", dry_run=True)
        self.assertEqual(seen["expected_disk"], disk)

    def test_paths_are_under_the_app_folder(self):
        paths = session_module.Session().paths()
        self.assertTrue(paths["recovery"].endswith(os.path.join("Results", "com.apple.recovery.boot")))
        self.assertTrue(paths["usbmap"].endswith(os.path.join("SysReport", "USBMap.json")))


class FakeUtils:
    def __init__(self):
        self.steps = []

    def progress_bar(self, title, steps, index, done=False):
        self.steps.append("done" if done else steps[index])


class FakeKexts:
    def has_discrete_amd_gpu(self, report):
        return False

    def monitor_on_intel_gpu(self, report):
        return False


class FakeChecker:
    hardware_report = None


class FakeConfig:
    layout_reason = None


class FakeOcpe:
    def __init__(self):
        self.u = FakeUtils()
        self.k = FakeKexts()
        self.c = FakeChecker()
        self.co = FakeConfig()
        self.result_dir = "Results"


GPU_REPORT = {"CPU": {"Processor Name": "Core i5-10400", "Manufacturer": "GenuineIntel", "Compatibility": ("25.99.99", "17.0.0")},
              "GPU": {"UHD 630": {"Compatibility": ("25.99.99", "17.0.0")}}}


class ExpressTest(unittest.TestCase):
    def setUp(self):
        # automatic answers print their explanation; keep it out of the test output
        quiet = contextlib.redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)
        self.calls = []
        self.defaults = []
        self.ocpe = FakeOcpe()
        self.session = session_module.Session(ocpe_factory=lambda: self.ocpe, preset_hook=lambda mapping: None, defaults_hook=self.defaults.append)
        self.native = ("17.0.0", "25.99.99")
        self.usb_map = None
        self.usb_report = None
        self.ocvalidate = "passed"
        self.configure_hook = lambda: None
        session = self.session

        def preflight():
            self.calls.append("preflight")
            return {"warnings": ["bitlocker"]}

        def load_report(path, acpi_dir=None):
            self.calls.append("load_report")
            session.hardware_report = GPU_REPORT
            session.native_macos_version = self.native
            session.suggested_macos_version = "25.99.99" if self.native else None
            return {"warnings": ["report warning"]}

        def configure(darwin, options):
            self.calls.append(("configure", darwin, options["audio"]))
            self.configure_hook()
            session.macos_version = "25.99.99"
            session.smbios_model = "iMac20,1"
            session.configured = True

        def build():
            self.calls.append("build")
            return {"ocvalidate": self.ocvalidate, "result_dir": "Results", "usb_map": self.usb_map, "bios": {"disable": [], "enable": [], "notes": []}, "preflight": ["bitlocker"]}

        def express_usb_map():
            self.calls.append("usb_map")
            return self.usb_report

        def recovery(version):
            self.calls.append(("recovery", version))
            return "Results/com.apple.recovery.boot"

        def post_install_kit(mode):
            self.calls.append(("kit", mode))
            return {"dest_dir": "Results/PostInstall"}

        session.preflight = preflight
        session.load_report = load_report
        session.scan_report = lambda: self.calls.append("scan_report") or load_report(None)
        session.configure = configure
        session.build = build
        session.express_usb_map = express_usb_map
        session.recovery = recovery
        session.post_install_kit = post_install_kit

    def test_full_chain_order_and_summary(self):
        summary = self.session.express("report.json")
        self.assertEqual(self.calls, ["preflight", "load_report", ("configure", 25, None), "usb_map", "build", ("recovery", "25.99.99"), ("kit", "full")])
        self.assertEqual(self.ocpe.u.steps, list(session_module.EXPRESS_STEPS) + ["done"])
        self.assertEqual(self.defaults, [True, False])
        self.assertEqual(summary["macos_name"], "macOS Tahoe 26")
        self.assertEqual(summary["smbios"], "iMac20,1")
        self.assertEqual(summary["recovery"], "Results/com.apple.recovery.boot")
        self.assertEqual(summary["kit"], "Results/PostInstall")
        self.assertEqual(summary["warnings"], ["bitlocker", "report warning", session_module.USB_MAP_NOTE])
        self.assertIn("SMBIOS: iMac20,1", session_module.summary_lines(summary))

    def test_scans_when_no_report_is_given(self):
        self.session.express()
        self.assertEqual(self.calls[1], "scan_report")

    def test_stops_when_nothing_is_installable(self):
        self.native = None
        report = {"GPU": {"RTX 5070 Ti": {"Compatibility": (None, None)}}}

        def load_report(path, acpi_dir=None):
            self.session.hardware_report = report
            self.session.native_macos_version = None
            return {"warnings": []}

        self.session.load_report = load_report
        with self.assertRaises(session_module.UnsupportedHardware) as raised:
            self.session.express("report.json")
        self.assertIn("RTX 5070 Ti", str(raised.exception))
        self.assertEqual(self.calls, ["preflight"])
        self.assertEqual(self.defaults, [True, False])

    def test_failed_compatibility_check_names_the_device(self):
        self.ocpe.c.hardware_report = {"CPU": {"Processor Name": "Core Ultra 9 275HX", "Compatibility": ("25.99.99", "17.0.0")},
                                       "GPU": {"Arc": {"Compatibility": (None, None)}, "RTX 5070 Ti": {"Compatibility": (None, None)}}}

        def load_report(path, acpi_dir=None):
            raise session_module.UnsupportedHardware("This hardware is not compatible with macOS.")

        self.session.load_report = load_report
        with self.assertRaises(session_module.UnsupportedHardware) as raised:
            self.session.express("report.json")
        self.assertIn("Arc, RTX 5070 Ti", str(raised.exception))
        self.assertNotIn("Core Ultra", str(raised.exception))

    def test_other_load_failures_keep_their_message(self):
        self.ocpe.c.hardware_report = {"GPU": {"Arc": {"Compatibility": (None, None)}}}

        def load_report(path, acpi_dir=None):
            raise session_module.StageError("The hardware report report.json failed validation.", ["missing CPU"])

        self.session.load_report = load_report
        with self.assertRaises(session_module.StageError) as raised:
            self.session.express("report.json")
        self.assertNotIsInstance(raised.exception, session_module.UnsupportedHardware)
        self.assertEqual(str(raised.exception), "The hardware report report.json failed validation.")

    def test_unsupported_devices_lists_gpus_only_when_none_work(self):
        report = {"CPU": {"Processor Name": "Old CPU", "Compatibility": (None, None)},
                  "GPU": {"Arc": {"Compatibility": (None, None)}, "UHD 630": {"Compatibility": ("25.99.99", "17.0.0")}, "Unchecked": {}}}
        self.assertEqual(session_module.unsupported_devices(report), ["Old CPU"])
        report["GPU"]["UHD 630"]["Compatibility"] = (None, None)
        self.assertEqual(session_module.unsupported_devices(report), ["Old CPU", "Arc", "UHD 630"])
        self.assertEqual(session_module.unsupported_devices({"CPU": {"Processor Name": "Unchecked"}}), [])

    def test_ocvalidate_errors_skip_the_recovery_download(self):
        self.ocvalidate = "errors"
        summary = self.session.express("report.json")
        self.assertNotIn(("recovery", "25.99.99"), self.calls)
        self.assertIsNone(summary["recovery"])
        self.assertTrue(any("ocvalidate reported errors" in warning for warning in summary["warnings"]))
        self.assertEqual(self.calls[-1], ("kit", "full"))

    def test_kit_can_be_left_out(self):
        summary = self.session.express("report.json", kit_mode=None, recovery=False)
        self.assertEqual(self.calls[-1], "build")
        self.assertIsNone(summary["kit"])
        self.assertIn("Post-install kit: not prepared", session_module.summary_lines(summary))
        self.assertEqual(self.ocpe.u.steps, ["Windows preflight", "Hardware scan", "Configuration", "USB map", "EFI build", "done"])

    def test_usb_map_note_only_without_a_map(self):
        self.usb_map = "SysReport/USBMap.json"
        summary = self.session.express("report.json")
        self.assertNotIn(session_module.USB_MAP_NOTE, summary["warnings"])

    def test_automatic_usb_map_goes_into_the_summary(self):
        self.usb_map = "SysReport/USBMap.json"
        self.usb_report = {"possible": True, "confidence": "good", "selected": 11, "total": 14, "mapped": 1}
        summary = self.session.express("report.json")
        self.assertIn("USB: automatic map, 11 of 14 ports on 1 controller, confidence good", session_module.summary_lines(summary))
        self.usb_report = dict(self.usb_report, confidence="guess", mapped=2)
        summary = self.session.express("report.json")
        self.assertEqual(summary["usb"], "automatic map, 11 of 14 ports on 2 controllers, confidence guess: "
                                         "run USB mapping (menu 6 / USB page) if a port is dead or sleep fails")

    def test_saved_map_and_no_map_in_the_summary(self):
        self.usb_map = "SysReport/USBMap.json"
        self.assertEqual(self.session.express("report.json")["usb"], "saved map SysReport/USBMap.json")
        self.usb_map = None
        self.usb_report = {"possible": False, "reason": "no USB ports were found."}
        summary = self.session.express("report.json")
        self.assertEqual(summary["usb"], "none, UTBDefault.kext enables every port")
        self.assertEqual(summary["warnings"][-2:], ["Automatic USB map not possible: no USB ports were found.", session_module.USB_MAP_NOTE])

    def test_unsaved_usb_page_warning(self):
        self.usb_report = {"possible": False, "reason": session_module.USB_DIRTY_NOTE, "warning": session_module.USB_DIRTY_NOTE}
        summary = self.session.express("report.json")
        self.assertEqual(summary["warnings"][-2:], [session_module.USB_DIRTY_NOTE, session_module.USB_MAP_NOTE])

    def test_usb_stage_left_out_when_mapping_is_off(self):
        self.session.usb_map_disabled = True
        self.session.express("report.json", kit_mode=None, recovery=False)
        self.assertNotIn("usb_map", self.calls)
        self.assertEqual(self.ocpe.u.steps, ["Windows preflight", "Hardware scan", "Configuration", "EFI build", "done"])

    def test_recovery_can_be_skipped(self):
        summary = self.session.express("report.json", kit_mode="docs-only", recovery=False)
        self.assertNotIn(("recovery", "25.99.99"), self.calls)
        self.assertEqual(self.calls[-1], ("kit", "docs-only"))
        self.assertIsNone(summary["recovery"])
        self.assertNotIn("macOS recovery", self.ocpe.u.steps)

    def test_express_options_take_the_audio_default(self):
        self.assertEqual(session_module.EXPRESS_OPTIONS, {"audio": None, "broadcom": "1", "whatevergreen": "yes", "debug": False})
        presets = session_module.Session().option_presets(25, session_module.EXPRESS_OPTIONS)
        self.assertEqual(presets["Select audio kext for your system"], "")
        self.assertEqual(presets["Select Broadcom Wi-Fi option"], "1")

    def test_defaults_are_answered_without_a_dialog(self):
        script = ScriptedPrompts({"Select a GPU device": "1"})
        prompts = broker.PromptBroker(script.post)
        self.session.preset_hook = prompts.set_presets
        self.session.defaults_hook = prompts.set_accept_defaults
        answers = []
        self.configure_hook = lambda: answers.extend([
            utils.Utils().request_input("Select audio kext for your system (default: 2): "),
            utils.Utils().request_input("Select a GPU device: ")
        ])
        utils.Utils.set_prompt_handler(prompts)
        restore = utils.Utils.install_output_proxy()
        utils.Utils.set_output_sink(prompts.sink)
        try:
            self.session.express("report.json")
        finally:
            utils.Utils.set_prompt_handler(None)
            utils.Utils.set_output_sink(None)
            restore()
        self.assertEqual(answers, ["", "1"])
        self.assertEqual(script.prompts, ["Select a GPU device: "])
        self.assertFalse(prompts.accept_defaults)

    def test_broker_posts_defaults_when_not_accepting(self):
        script = ScriptedPrompts({})
        prompts = broker.PromptBroker(script.post)
        prompts("Select audio kext for your system (default: 2): ", "")
        self.assertEqual(len(script.prompts), 1)

    def test_broker_hands_a_rejected_default_to_the_dialog(self):
        script = ScriptedPrompts({})
        prompts = broker.PromptBroker(script.post)
        prompts.set_accept_defaults(True)
        prompt = "Select audio kext for your system (default: 2): "
        for _ in range(broker.PRESET_REPEAT_LIMIT):
            self.assertEqual(prompts(prompt, ""), "")
        self.assertEqual(script.prompts, [])
        prompts(prompt, "")
        self.assertEqual(script.prompts, [prompt])

    def test_broker_still_asks_yes_no_questions(self):
        script = ScriptedPrompts({"Legacy Patcher": "yes"})
        prompts = broker.PromptBroker(script.post)
        prompts.set_accept_defaults(True)
        self.assertEqual(prompts("Continue with OpenCore Legacy Patcher? (yes/No): ", ""), "yes")
        self.assertEqual(len(script.prompts), 1)


class ExpressUsbMapTest(unittest.TestCase):
    def setUp(self):
        from test_usb_mapper import CANNED_DUMP
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.ocpe = FakeOcpe()
        self.ocpe.usb_map_override = None
        self.ocpe.usb_mapper = usb_mapper.USBMapper(report_dir=temp.name)
        self.ocpe.usb_mapper.enumerate = lambda: usb_mapper.parse_controllers(json.dumps(CANNED_DUMP))
        self.ocpe.select_usb_map_source = types.MethodType(session_module.load_main_module().OCPE.select_usb_map_source, self.ocpe)
        self.session = session_module.Session(ocpe_factory=lambda: self.ocpe)
        self.session.hardware_report = {"USB Controllers": {"Intel XHCI": {"Device ID": "8086-7F6E", "Subsystem ID": "3D6C17AA"}}}

    def test_saves_the_automatic_map(self):
        report = self.session.express_usb_map()
        self.assertTrue(report["possible"])
        self.assertFalse(report["replaced"])
        self.assertEqual(report["selected"], 8)
        saved = self.ocpe.usb_mapper.load_saved()
        self.assertEqual(usb_mapper.validate_selection(saved), [])
        self.assertEqual(sum(usb_mapper.selected_count(controller) for controller in saved["controllers"]), 8)

    def test_a_usable_saved_map_wins(self):
        self.session.express_usb_map()
        self.ocpe.usb_mapper.enumerate = lambda: self.fail("should not enumerate")
        self.assertIsNone(self.session.express_usb_map())

    def test_an_unusable_saved_map_is_replaced(self):
        self.ocpe.usb_mapper.save({"controllers": []})
        report = self.session.express_usb_map()
        self.assertTrue(report["replaced"])
        self.assertIn("replaced by the automatic map", session_module.usb_summary(self.ocpe.usb_mapper.map_path, report))
        self.assertEqual(usb_mapper.validate_selection(self.ocpe.usb_mapper.load_saved()), [])

    def test_saving_clears_the_unsaved_flag(self):
        self.session.load_usb_history()
        self.session.auto_usb_map(self.ocpe.usb_mapper.enumerate())
        self.session.usb_dirty = True
        path, errors = self.session.save_usb_map()
        self.assertEqual(errors, [])
        self.assertFalse(self.session.usb_dirty)

    def test_unsaved_ticks_on_the_usb_page_are_kept(self):
        self.session.usb_dirty = True
        self.ocpe.usb_mapper.enumerate = lambda: self.fail("should not enumerate")
        report = self.session.express_usb_map()
        self.assertEqual(report["warning"], session_module.USB_DIRTY_NOTE)
        self.assertFalse(os.path.exists(self.ocpe.usb_mapper.map_path))

    def test_other_machine_report_is_not_mapped(self):
        self.session.hardware_report = {"USB Controllers": {"Other": {"Device ID": "1022-149C", "Subsystem ID": "14581234"}}}
        report = self.session.express_usb_map()
        self.assertFalse(report["possible"])
        self.assertIn("not the ones in the hardware report", report["reason"])
        self.assertFalse(os.path.exists(self.ocpe.usb_mapper.map_path))

    def test_ports_that_cannot_be_listed(self):
        self.ocpe.usb_mapper.enumerate = lambda: None
        self.assertEqual(self.session.express_usb_map(), {"possible": False, "reason": "the USB ports could not be listed."})


if __name__ == "__main__":
    unittest.main()
