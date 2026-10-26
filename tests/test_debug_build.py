import contextlib
import copy
import importlib.util
import io
import os
import shutil
import tempfile
import types
import unittest
from unittest import mock

import repo_path  # noqa: F401
from repo_path import REPO_ROOT
from Scripts import config_prodigy
from Scripts import gathering_files
from Scripts import postinstall_kit
from Scripts import utils

DEBUG_PROMPT = "Build a DEBUG EFI with logging? Use this only to diagnose a boot that fails; it is slower and writes log files to the EFI partition. (yes/No): "


def release_config():
    return {"Misc": {"Debug": {
        "AppleDebug": False,
        "ApplePanic": False,
        "DisableWatchDog": True,
        "DisplayDelay": 0,
        "DisplayLevel": 2147483650,
        "LogModules": "*",
        "SysReport": False,
        "Target": 3
    }}}


def dortania_builds():
    return {"OpenCorePkg": {"versions": [{
        "release": {"id": 417365787, "url": "https://example.invalid/tag"},
        "links": {"release": "https://example.invalid/OpenCore-1.0.9-RELEASE.zip", "debug": "https://example.invalid/OpenCore-1.0.9-DEBUG.zip"},
        "hashes": {"release": {"sha256": "aa" * 32}, "debug": {"sha256": "bb" * 32}}
    }]}}


def load_main_module():
    spec = importlib.util.spec_from_file_location("opcore_simplify_main", os.path.join(REPO_ROOT, "MacBootsmith.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DebugConfigTest(unittest.TestCase):
    def setUp(self):
        self.co = config_prodigy.ConfigProdigy.__new__(config_prodigy.ConfigProdigy)

    def test_release_config_is_untouched(self):
        config = release_config()
        self.co.apply_debug_settings(config, False)
        self.assertEqual(config, release_config())

    def test_debug_keys(self):
        config = self.co.apply_debug_settings(release_config(), True)
        debug = config["Misc"]["Debug"]
        self.assertIs(debug["AppleDebug"], True)
        self.assertIs(debug["ApplePanic"], True)
        self.assertIs(debug["DisableWatchDog"], True)
        self.assertEqual(debug["Target"], 67)
        self.assertEqual(debug["DisplayLevel"], 2147483714)
        self.assertIs(debug["SysReport"], False)
        self.assertEqual(debug["LogModules"], "*")

    def test_generate_still_forces_release_values(self):
        with open(os.path.join(REPO_ROOT, "Scripts", "config_prodigy.py"), "r", encoding="utf-8") as source:
            text = source.read()
        self.assertIn('config["Misc"]["Debug"]["AppleDebug"] = config["Misc"]["Debug"]["ApplePanic"] = False', text)


class DebugPackageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp()
        self.gather = gathering_files.gatheringFiles.__new__(gathering_files.gatheringFiles)
        self.gather.utils = utils.Utils()
        self.gather.fetcher = types.SimpleNamespace(fetch_and_parse_content=lambda url, kind=None: dortania_builds())
        self.gather.temporary_dir = os.path.join(self.temp, "tmp")
        self.gather.ock_files_dir = os.path.join(self.temp, "OCK_Files")
        self.gather.dortania_builds_url = "https://example.invalid/latest.json"

    def tearDown(self):
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_debug_cache_product(self):
        self.assertEqual(gathering_files.OPENCORE_DEBUG_PRODUCT, "OpenCorePkg-DEBUG")
        products = self.gather.fetch_latest_products_info([], {})
        release = products["OpenCorePkg"]
        debug = products["OpenCorePkg-DEBUG"]
        self.assertEqual(debug["id"], release["id"])
        self.assertEqual(debug["url"], "https://example.invalid/OpenCore-1.0.9-DEBUG.zip")
        self.assertEqual(debug["sha256"], "bb" * 32)
        self.assertEqual(release["sha256"], "aa" * 32)

    def test_debug_package_only_gathered_when_asked(self):
        self.assertEqual([p["Name"] for p in self.gather.opencore_products()], ["OpenCorePkg"])
        self.assertEqual([p["Name"] for p in self.gather.opencore_products(True)], ["OpenCorePkg", "OpenCorePkg-DEBUG"])

    def test_debug_package_keeps_release_tools(self):
        product = gathering_files.OPENCORE_DEBUG_PRODUCT
        root = os.path.join(self.gather.temporary_dir, product)
        for relative in ("X64/EFI/OC/OpenCore.efi", "X64/EFI/BOOT/BOOTx64.efi", "Docs/Sample.plist",
                         "Utilities/macserial/macserial.exe", "Utilities/ocvalidate/ocvalidate.exe"):
            path = os.path.join(root, *relative.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as handle:
                handle.write(b"debug")
        os.makedirs(os.path.join(self.gather.ock_files_dir, product))

        scripts_dir = os.path.dirname(os.path.realpath(gathering_files.__file__))
        real_move = shutil.move

        def guarded_move(source, destination):
            if os.path.dirname(os.path.abspath(destination)) == scripts_dir:
                raise AssertionError("the DEBUG package must not replace {}".format(destination))
            return real_move(source, destination)

        with mock.patch.object(gathering_files.shutil, "move", side_effect=guarded_move):
            self.assertTrue(self.gather.move_bootloader_kexts_to_product_directory(product))
        efi = os.path.join(self.gather.ock_files_dir, product, "EFI")
        self.assertTrue(os.path.isfile(os.path.join(efi, "OC", "OpenCore.efi")))
        self.assertTrue(os.path.isfile(os.path.join(efi, "OC", "config.plist")))


class DebugBuildFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_main_module()

    def setUp(self):
        self.temp = tempfile.mkdtemp()
        self.ocpe = self.module.OCPE.__new__(self.module.OCPE)
        self.ocpe.u = utils.Utils()
        self.ocpe.debug_build = False

    def tearDown(self):
        utils.Utils.set_headless(None)
        shutil.rmtree(self.temp, ignore_errors=True)

    def ask(self, answers):
        utils.Utils.set_headless(answers)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            result = self.ocpe.select_debug_build()
        return result, output.getvalue()

    def test_prompt_defaults_to_release(self):
        result, output = self.ask({})
        self.assertFalse(result)
        self.assertIn(DEBUG_PROMPT, output)

    def test_prompt_yes(self):
        self.assertTrue(self.ask({"prompts": {"Build a DEBUG EFI": "yes"}})[0])
        self.assertTrue(self.ask({"prompts": {"Build a DEBUG EFI": "Y"}})[0])
        self.assertFalse(self.ask({"prompts": {"Build a DEBUG EFI": "no"}})[0])

    def headless_prompts(self, answers):
        self.ocpe.ac = types.SimpleNamespace(acpi=types.SimpleNamespace())
        self.ocpe.usb_map_override = None
        answers = dict({"macos_version": "25.0.0"}, **answers)
        with self.assertRaises(Exception) as context:
            self.ocpe.run_headless(answers)
        self.assertIn("hardware report", str(context.exception))
        return utils.Utils.headless_answers["prompts"]

    def test_headless_debug_key(self):
        self.assertEqual(self.headless_prompts({"debug": True})["Build a DEBUG EFI"], "yes")
        self.assertEqual(self.headless_prompts({"debug": False})["Build a DEBUG EFI"], "no")
        self.assertNotIn("Build a DEBUG EFI", self.headless_prompts({}))

    def test_headless_debug_key_overrides_prompt_entry(self):
        prompts = self.headless_prompts({"debug": False, "prompts": {"Build a DEBUG EFI": "yes"}})
        self.assertEqual(prompts["Build a DEBUG EFI"], "no")

    def test_headless_rejects_non_boolean_debug(self):
        with self.assertRaises(Exception) as context:
            self.ocpe.run_headless({"debug": "yes", "macos_version": "25.0.0"})
        self.assertIn("debug must be true or false", str(context.exception))

    def test_debug_build_needs_the_debug_package(self):
        ock_files = os.path.join(self.temp, "OCK_Files")
        os.makedirs(os.path.join(ock_files, "OpenCorePkg", "EFI", "OC"))
        self.ocpe.k = types.SimpleNamespace(ock_files_dir=ock_files)
        self.ocpe.pk = postinstall_kit.PostInstallKit()
        self.ocpe.result_dir = os.path.join(self.temp, "Results")
        utils.Utils.set_headless({})
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(Exception) as context:
                self.ocpe.build_opencore_efi({}, [], "iMac20,1", "25.0.0", False, debug=True)
        self.assertIn("DEBUG OpenCore package is missing", str(context.exception))
        self.assertIn("OpenCorePkg-DEBUG", str(context.exception))

    def test_summary_records_debug(self):
        self.assertIs(postinstall_kit.make_build_summary({}, {}, "iMac20,1", "25.0.0", False)["debug"], False)
        self.assertIs(postinstall_kit.make_build_summary({}, {}, "iMac20,1", "25.0.0", False, debug=True)["debug"], True)

    def test_notes_point_at_the_log_and_the_analyzer(self):
        notes = " ".join(self.ocpe.debug_build_notes())
        self.assertIn("opencore-YYYY-MM-DD-hhmmss.txt", notes)
        self.assertIn("root of the EFI partition", notes)
        self.assertIn("The boot log analyzer (menu option 11, or the Finish page in the window)", notes)

    def test_debug_answers_fixture(self):
        import json
        with open(os.path.join(REPO_ROOT, "tests", "fixtures", "answers-desktop-debug.json"), "r", encoding="utf-8") as fixture:
            debug_answers = json.load(fixture)
        with open(os.path.join(REPO_ROOT, "tests", "fixtures", "answers-desktop.json"), "r", encoding="utf-8") as fixture:
            release_answers = json.load(fixture)
        self.assertIs(debug_answers.pop("debug"), True)
        self.assertEqual(debug_answers, release_answers)
        self.assertNotIn("debug", release_answers)


if __name__ == "__main__":
    unittest.main()
