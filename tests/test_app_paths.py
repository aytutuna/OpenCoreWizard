import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from repo_path import REPO_ROOT

from Scripts import app_paths
from Scripts import gathering_files
from Scripts import kext_maestro
from Scripts import postinstall_kit
from Scripts import smbios
from Scripts import usb_mapper
from Scripts import validator
from test_headless import load_main_module

REAL_ROOT = os.path.realpath(REPO_ROOT)


@contextlib.contextmanager
def frozen(app_folder, bundle_folder):
    with mock.patch.object(sys, "frozen", True, create=True), \
            mock.patch.object(sys, "executable", os.path.join(app_folder, "OpenCoreWizard.exe")), \
            mock.patch.object(sys, "_MEIPASS", bundle_folder, create=True):
        yield


class SourceTreeTest(unittest.TestCase):
    def test_not_frozen_uses_the_repository_root(self):
        self.assertFalse(app_paths.is_frozen())
        self.assertEqual(app_paths.app_dir(), REAL_ROOT)
        self.assertEqual(app_paths.bundle_dir(), REAL_ROOT)
        self.assertEqual(app_paths.tools_dir(), os.path.join(REAL_ROOT, "Scripts"))
        self.assertEqual(app_paths.data_dir(), os.path.join(REAL_ROOT, "Scripts"))

    def test_module_paths_are_unchanged_from_the_file_based_ones(self):
        scripts = os.path.dirname(os.path.realpath(gathering_files.__file__))
        root = os.path.dirname(scripts)
        self.assertEqual(gathering_files.gatheringFiles().ock_files_dir, os.path.join(root, "OCK_Files"))
        self.assertEqual(kext_maestro.KextMaestro().ock_files_dir, os.path.join(root, "OCK_Files"))
        self.assertEqual(validator.ConfigValidator().script_dir, scripts)
        self.assertEqual(smbios.SMBIOS().script_dir, scripts)
        mapper = usb_mapper.USBMapper()
        self.assertEqual(mapper.report_dir, os.path.join(root, "SysReport"))
        self.assertEqual(mapper.enum_source, os.path.join(scripts, "usb_enum.cs"))
        self.assertTrue(os.path.isfile(mapper.enum_source))

    def test_main_script_paths_are_unchanged(self):
        module = load_main_module()
        ocpe = module.OCPE.__new__(module.OCPE)
        self.assertEqual(ocpe.resolve_headless_path("tests/fixtures/desktop/Report.json"),
                         os.path.normpath(os.path.join(REAL_ROOT, "tests", "fixtures", "desktop", "Report.json")))


class FrozenTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp()
        self.app_folder = os.path.join(self.temp, "OpenCoreWizard")
        self.bundle_folder = os.path.join(self.app_folder, "_internal")
        os.makedirs(self.bundle_folder)

    def tearDown(self):
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_frozen_uses_the_executable_folder_and_bundle(self):
        with frozen(self.app_folder, self.bundle_folder):
            self.assertTrue(app_paths.is_frozen())
            self.assertEqual(app_paths.app_dir(), os.path.realpath(self.app_folder))
            self.assertEqual(app_paths.bundle_dir(), self.bundle_folder)
            self.assertEqual(app_paths.data_dir(), os.path.join(self.bundle_folder, "Scripts"))
            tools = app_paths.tools_dir()
        self.assertEqual(tools, os.path.join(os.path.realpath(self.app_folder), "Scripts"))
        self.assertTrue(os.path.isdir(tools))

    def test_writable_folders_move_next_to_the_executable(self):
        app = os.path.realpath(self.app_folder)
        with frozen(self.app_folder, self.bundle_folder):
            self.assertEqual(gathering_files.gatheringFiles().ock_files_dir, os.path.join(app, "OCK_Files"))
            self.assertEqual(kext_maestro.KextMaestro().ock_files_dir, os.path.join(app, "OCK_Files"))
            self.assertEqual(validator.ConfigValidator().script_dir, os.path.join(app, "Scripts"))
            self.assertEqual(smbios.SMBIOS().script_dir, os.path.join(app, "Scripts"))
            mapper = usb_mapper.USBMapper()
        self.assertEqual(mapper.report_dir, os.path.join(app, "SysReport"))
        self.assertEqual(mapper.enum_source, os.path.join(self.bundle_folder, "Scripts", "usb_enum.cs"))

    def test_read_only_docs_come_from_the_bundle(self):
        docs = os.path.join(self.bundle_folder, "docs", "reference")
        os.makedirs(docs)
        for name in postinstall_kit.DOCS_FILES:
            with open(os.path.join(docs, name), "w", encoding="utf-8") as handle:
                handle.write("doc " + name)
        dest = os.path.join(self.temp, "kit")
        with frozen(self.app_folder, self.bundle_folder), contextlib.redirect_stdout(io.StringIO()):
            postinstall_kit.PostInstallKit().copy_docs(dest)
        for name in postinstall_kit.DOCS_FILES:
            with open(os.path.join(dest, postinstall_kit.DOCS_FOLDER, name), encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "doc " + name)

    def test_main_script_results_and_relative_paths(self):
        module = load_main_module()
        ocpe = module.OCPE.__new__(module.OCPE)
        app = os.path.realpath(self.app_folder)
        relative = os.path.join("tests", "fixtures", "desktop", "Report.json")
        with frozen(self.app_folder, self.bundle_folder):
            self.assertEqual(ocpe.resolve_headless_path(relative), os.path.normpath(os.path.join(os.getcwd(), relative)))
            os.makedirs(os.path.join(app, "answers"))
            self.assertEqual(ocpe.resolve_headless_path("answers"), os.path.join(app, "answers"))
            with mock.patch.object(module.acpi_guru, "ACPIGuru"), mock.patch.object(module.utils.Utils, "clean_temporary_dir"):
                instance = module.OCPE()
        self.assertEqual(instance.result_dir, os.path.join(app, "Results"))


if __name__ == "__main__":
    unittest.main()
