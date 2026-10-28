import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from Scripts import utils
from Scripts.gui import session as session_module


def load_main_module():
    spec = importlib.util.spec_from_file_location("opcore_simplify_main", os.path.join(REPO_ROOT, "OpenCoreWizard.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HeadlessUtilsTest(unittest.TestCase):
    def tearDown(self):
        utils.Utils.set_headless(None)

    def ask(self, prompt):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            answer = utils.Utils().request_input(prompt)
        return answer, output.getvalue()

    def test_first_matching_key_wins_case_insensitive(self):
        utils.Utils.set_headless({"prompts": {"AUDIO KEXT": "2", "kext": "9"}, "default": "x"})
        answer, output = self.ask("Select audio kext for your system: ")
        self.assertEqual(answer, "2")
        self.assertIn("Select audio kext for your system: 2", output)

    def test_unmatched_prompt_uses_default_and_logs_it(self):
        utils.Utils.set_headless({"prompts": {"audio": "2"}, "default": "fallback"})
        answer, output = self.ask("Something else: ")
        self.assertEqual(answer, "fallback")
        self.assertIn("Something else: fallback", output)

    def test_missing_default_is_empty_string(self):
        utils.Utils.set_headless({})
        answer, _ = self.ask("Press Enter to continue...")
        self.assertEqual(answer, "")

    def test_repeated_prompt_raises(self):
        utils.Utils.set_headless({"prompts": {}, "default": "bad"})
        with self.assertRaises(utils.HeadlessPromptLoop):
            for _ in range(utils.Utils.headless_repeat_limit + 1):
                self.ask("Select an option: ")

    def test_exit_program_raises_instead_of_exiting(self):
        utils.Utils.set_headless({})
        with self.assertRaises(utils.HeadlessExit):
            utils.Utils().exit_program()

    def test_head_does_not_clear_screen(self):
        utils.Utils.set_headless({})
        with contextlib.redirect_stdout(io.StringIO()) as output:
            utils.Utils().head("Title")
        self.assertIn("== Title ==", output.getvalue())
        self.assertNotIn("\033[8;", output.getvalue())

    def test_interactive_mode_is_default(self):
        self.assertFalse(utils.Utils.is_headless())


class HeadlessRunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = load_main_module()

    def tearDown(self):
        utils.Utils.set_headless(None)

    def run_answers(self, answers):
        with tempfile.TemporaryDirectory() as temp_dir:
            answers_path = os.path.join(temp_dir, "answers.json")
            with open(answers_path, "w", encoding="utf-8") as answers_file:
                json.dump(answers, answers_file)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                exit_code = self.main.run_headless(answers_path)
        return exit_code, output.getvalue()

    def test_marketing_name_resolution(self):
        ocpe = self.main.OCPE.__new__(self.main.OCPE)
        self.assertEqual(ocpe.resolve_headless_macos_version("25.0.0"), "25.0.0")
        self.assertEqual(ocpe.resolve_headless_macos_version("Tahoe"), "25")
        self.assertEqual(ocpe.resolve_headless_macos_version("macOS Sequoia"), "24")
        self.assertEqual(ocpe.resolve_headless_macos_version("Sonoma 14"), "23")
        with self.assertRaises(Exception):
            ocpe.resolve_headless_macos_version("Cheetah")

    def test_missing_answers_file_returns_1(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            exit_code = self.main.run_headless(os.path.join(REPO_ROOT, "tests", "fixtures", "does-not-exist.json"))
        self.assertEqual(exit_code, 1)
        self.assertIn("Traceback", output.getvalue())

    def test_unsupported_version_returns_3(self):
        exit_code, output = self.run_answers({
            "report": "tests/fixtures/desktop/Report.json",
            "macos_version": "16.0.0",
            "prompts": {},
            "default": ""
        })
        self.assertEqual(exit_code, 3, output)
        self.assertIn("outside the supported range", output)

    def test_unsupported_gpu_returns_3(self):
        exit_code, output = self.run_answers({
            "report": "tests/fixtures/laptop/Report.json",
            "macos_version": "25.0.0",
            "prompts": {},
            "default": ""
        })
        self.assertEqual(exit_code, 3, output)
        self.assertIn("not compatible with macOS", output)

    def test_auto_usb_map_needs_express(self):
        exit_code, output = self.run_answers({
            "report": "tests/fixtures/desktop/Report.json",
            "macos_version": "25.0.0",
            "usb_map": "auto",
            "prompts": {},
            "default": ""
        })
        self.assertEqual(exit_code, 1, output)
        self.assertIn('usb_map "auto" needs "express": true.', output)

    def test_express_stops_at_the_verdict_on_unsupported_hardware(self):
        exit_code, output = self.run_answers({
            "express": True,
            "report": "tests/fixtures/laptop/Report.json",
            "postinstall_kit": "docs-only",
            "prompts": {},
            "default": ""
        })
        self.assertEqual(exit_code, 3, output)
        self.assertIn("Nothing can be installed: macOS does not support NVIDIA GeForce RTX 5070 Ti Laptop GPU, Intel(R) Graphics", output)
        self.assertFalse(utils.Utils.accept_defaults)

    def test_express_fixture_builds_and_prints_the_summary(self):
        with open(os.path.join(REPO_ROOT, "tests", "fixtures", "answers-express.json"), encoding="utf-8") as handle:
            answers = json.load(handle)
        result = {"ocvalidate": "passed", "result_dir": "Results", "usb_map": None, "preflight": [], "bios": {"disable": ["CSM"], "enable": [], "notes": []}}
        # the build and the kit download files; the CI build job runs them for real
        with mock.patch.object(session_module.Session, "build", return_value=result) as build:
            with mock.patch.object(session_module.Session, "post_install_kit", return_value={"dest_dir": "Results/PostInstall"}) as kit:
                with mock.patch.object(session_module.Session, "recovery") as recovery:
                    exit_code, output = self.run_answers(answers)
        self.assertEqual(exit_code, 0, output)
        build.assert_called_once_with()
        kit.assert_called_once_with("docs-only")
        recovery.assert_not_called()
        for line in ("macOS: macOS Tahoe 26", "SMBIOS: iMac20,1", "Recovery: skipped", "Post-install kit: Results/PostInstall", "Disable: CSM",
                     "Headless build result: exit=0"):
            self.assertIn(line, output)

    def test_plain_headless_run_does_not_use_express(self):
        with mock.patch.object(session_module.Session, "express", side_effect=AssertionError("express was used")):
            exit_code, output = self.run_answers({"report": "tests/fixtures/laptop/Report.json", "macos_version": "25.0.0", "prompts": {}, "default": ""})
        self.assertEqual(exit_code, 3, output)
        self.assertIn("not compatible with macOS", output)
        self.assertNotIn("Build everything", output)


class AcceptDefaultsTest(unittest.TestCase):
    def tearDown(self):
        utils.Utils.set_accept_defaults(False)
        utils.Utils.set_presets({})

    def ask(self, prompt, typed="typed"):
        with mock.patch("builtins.input", return_value=typed) as fake_input:
            with contextlib.redirect_stdout(io.StringIO()):
                answer = utils.Utils().request_input(prompt)
        return answer, fake_input.called

    def test_defaults_and_pauses_skip_the_keyboard(self):
        utils.Utils.set_accept_defaults(True)
        self.assertEqual(self.ask("Select audio kext for your system (default: 2): "), ("", False))
        self.assertEqual(self.ask("Press Enter to continue..."), ("", False))
        self.assertEqual(self.ask("Select a GPU device: "), ("typed", True))

    def test_defaults_go_to_the_user_when_not_accepting(self):
        self.assertEqual(self.ask("Select audio kext for your system (default: 2): "), ("typed", True))

    def test_rejected_preset_falls_through_to_the_user(self):
        utils.Utils.set_presets({"Please enter the macOS version": "99"})
        prompt = "Please enter the macOS version you want to use (default: macOS Tahoe 26): "
        self.assertEqual([self.ask(prompt) for _ in range(4)], [("99", False)] * 3 + [("typed", True)])

    def test_rejected_default_hands_over_instead_of_spinning(self):
        utils.Utils.set_accept_defaults(True)
        prompt = "Enter the ID of the codec layout you want to use (default: 3): "
        self.assertEqual([self.ask(prompt) for _ in range(4)], [("", False)] * 3 + [("typed", True)])

    def test_rejected_default_reaches_the_headless_guard(self):
        utils.Utils.set_accept_defaults(True)
        utils.Utils.set_headless({"prompts": {}, "default": ""})
        try:
            with self.assertRaises(utils.HeadlessPromptLoop):
                for _ in range(3 + utils.Utils.headless_repeat_limit + 1):
                    self.ask("Enter the ID of the codec layout you want to use (default: 3): ")
        finally:
            utils.Utils.set_headless(None)

    def test_yes_no_questions_are_still_asked(self):
        utils.Utils.set_accept_defaults(True)
        self.assertEqual(self.ask("Continue with OpenCore Legacy Patcher? (yes/No): "), ("typed", True))
        self.assertEqual(self.ask("Do you want to force load this kext on the unsupported macOS version? (yes/No): "), ("typed", True))

    def test_exit_request_ends_the_express_run_only(self):
        utils.Utils.set_accept_defaults(True)
        with self.assertRaises(utils.GuiExit):
            utils.Utils().exit_program()


class ExpressMenuTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = load_main_module()

    def setUp(self):
        self.ocpe = self.main.OCPE.__new__(self.main.OCPE)
        self.ocpe.u = utils.Utils()
        self.ocpe.result_dir = "Results"
        self.ocpe.usb_map_override = None
        self.ocpe.usb_map_auto = False
        utils.Utils.set_headless({"prompts": {"Select an option": "a"}, "default": ""})

    def tearDown(self):
        utils.Utils.set_headless(None)

    def test_menu_lists_and_runs_the_express_entry(self):
        calls = []

        def express_menu():
            calls.append(True)
            if len(calls) > 1:
                raise utils.HeadlessExit("stop")
            return mock.Mock(configured=False, kit_result=None)

        self.ocpe.express_menu = express_menu
        with contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(utils.HeadlessExit):
                self.ocpe.main()
        self.assertEqual(len(calls), 2)
        text = output.getvalue()
        self.assertLess(text.index("A. Build everything with recommended choices"), text.index("1. Select Hardware Report"))

    def run_menu(self, results):
        results = list(results)

        def express_menu():
            if not results:
                raise utils.HeadlessExit("stop")
            return results.pop(0)

        self.ocpe.express_menu = express_menu
        with contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(utils.HeadlessExit):
                self.ocpe.main()
        return output.getvalue()

    def test_menu_takes_over_a_configured_run_and_resets_after_a_failed_one(self):
        done = mock.Mock(configured=True, report_path="SysReport/Report.json", macos_version="25.99.99", smbios_model="iMac20,1",
                         needs_oclp=False, disabled_devices=None, kit_result={"dest_dir": "Results/PostInstall"})
        failed = mock.Mock(configured=False, kit_result=None)
        text = self.run_menu([done, failed])
        self.assertEqual(text.count("Hardware Report: SysReport/Report.json"), 1)
        self.assertIn("SMBIOS:          iMac20,1", text)
        self.assertEqual(text.count("Next: 10. Create USB Installer"), 1)
        self.assertEqual(text.count("Hardware Report: Not selected"), 2)

    def test_headless_usb_map_false_reaches_the_build(self):
        seen = []
        self.ocpe.usb_map_override = False
        with mock.patch.object(session_module.Session, "express", autospec=True, side_effect=lambda express, *args: seen.append(express.usb_map_disabled)):
            self.ocpe.express()
        self.ocpe.usb_map_override = None
        with mock.patch.object(session_module.Session, "express", autospec=True, side_effect=lambda express, *args: seen.append(express.usb_map_disabled)):
            self.ocpe.express()
        self.assertEqual(seen, [True, False])

    def test_headless_auto_usb_map_reaches_express(self):
        seen = []
        record = lambda express, *args: seen.append((express.usb_auto, express.usb_map_disabled))
        for answer in ("auto", None, False):
            self.ocpe.apply_headless_usb_map(answer)
            with mock.patch.object(session_module.Session, "express", autospec=True, side_effect=record):
                self.ocpe.express()
        self.assertEqual(seen, [(True, False), (False, False), (False, True)])

    def test_unexpected_errors_return_to_the_menu(self):
        with mock.patch.object(session_module.Session, "express", side_effect=OSError("disk full")):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                express, summary, error = self.ocpe.express()
        self.assertIsNone(summary)
        self.assertIsInstance(error, OSError)
        self.assertIn("Error: disk full", output.getvalue())

    def test_express_menu_prints_summary_checklist_and_next_step(self):
        summary = {"macos_name": "macOS Tahoe 26", "smbios": "iMac20,1", "ocvalidate": "passed", "result_dir": "Results",
                   "recovery": None, "kit": "Results/PostInstall", "warnings": ["USB ports not mapped"],
                   "bios": {"disable": ["CSM"], "enable": ["Above 4G Decoding"], "notes": []}}
        self.ocpe.express = lambda: (mock.Mock(), summary, None)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.ocpe.express_menu()
        text = output.getvalue()
        for line in ("SMBIOS: iMac20,1", "Recovery: skipped", "  - USB ports not mapped", "Disable: CSM", "Enable: Above 4G Decoding", "Next: 10. Create USB Installer"):
            self.assertIn(line, text)

    def test_express_failure_is_reported_and_returns_to_the_menu(self):
        with mock.patch.object(session_module.Session, "express", side_effect=session_module.StageError("Nothing can be installed", ["detail"])):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                express, summary, error = self.ocpe.express()
        self.assertIsNone(summary)
        self.assertIn("Nothing can be installed", output.getvalue())
        self.assertIn("  - detail", output.getvalue())


if __name__ == "__main__":
    unittest.main()
