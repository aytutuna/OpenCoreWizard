import contextlib
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import repo_path  # noqa: F401
from repo_path import REPO_ROOT, FIXTURES
from Scripts import log_analyzer
from Scripts import utils
from Scripts.datasets import boot_log_patterns

CI_SAMPLE = os.path.join(FIXTURES, "boot_logs", "ci-serial-sample.txt")

START = [
    "BS: Starting OpenCore application...",
    "OC: Starting OpenCore...",
    "OCST: Missing vault data, ignoring...",
]
CONFIG = START + [
    "OC: Loaded configuration of 21958 bytes",
    "00:120 00:005 OC: OpenCore DBG-109-2026-10-06 is loading in Optional mode (0/0)...",
]
DRIVERS = CONFIG + [
    "00:130 00:010 OC: Got 4 drivers",
    "00:140 00:010 OC: Driver HfsPlus.efi at 0 (HfsPlus.efi) is being loaded...",
    "00:150 00:010 OC: Driver HfsPlus.efi at 0 is successfully loaded!",
]
PICKER = DRIVERS + [
    "01:154 00:007 OCB: Found 1 potentially bootable filesystems",
    "01:299 00:008 OCB: Showing menu... ",
    "01:307 00:008 OCHK: InitHotKeys",
]
CHOSEN = PICKER + [
    "05:100 00:010 OCB: Should boot from 1. Install macOS Tahoe (T:2|F:0|G:0|E:0|DEF:0)",
    "05:110 00:010 OCB: Perform boot Install macOS Tahoe to dp PciRoot(0x0)/Pci(0x14,0x0)",
]
HANDOFF = CHOSEN + [
    "06:000 00:100 OCAK: Read kernel version 25.0.0 (250000)",
    "06:500 00:500 AAPL: #[EB|#LOG:EXITBS:START] 2026-10-21T10:00:00",
]
KERNEL = HANDOFF + [
    "Darwin Kernel Version 25.0.0: Mon Sep 15 00:00:00 PDT 2026",
]


def analyze_lines(lines):
    analyzer = log_analyzer.LogAnalyzer()
    analyzer.load_text("\r\n".join(lines) + "\r\n", "synthetic")
    return analyzer.analyze()


def finding_ids(report):
    return [finding["id"] for finding in report["findings"]]


class StageTest(unittest.TestCase):
    def test_no_opencore_output(self):
        report = analyze_lines(["BdsDxe: loading Boot0001", "nothing else"])
        self.assertEqual(report["stage"], "none")
        self.assertTrue(report["verdict"].startswith("No OpenCore lines found"))

    def test_opencore_started_only(self):
        report = analyze_lines(START)
        self.assertEqual(report["stage"], "opencore_started")
        self.assertEqual(report["verdict"], "Stalled after OpenCore started: read the last lines below")

    def test_config_loaded(self):
        report = analyze_lines(CONFIG)
        self.assertEqual(report["stage"], "config_loaded")
        self.assertEqual(report["version"], "1.0.9")
        self.assertEqual(report["build"], "DEBUG")
        self.assertEqual(report["mode"], "Optional")

    def test_drivers(self):
        report = analyze_lines(DRIVERS)
        self.assertEqual(report["stage"], "drivers")
        self.assertEqual(report["verdict"], "Stalled after drivers loaded: read the last lines below")

    def test_picker(self):
        report = analyze_lines(PICKER)
        self.assertEqual(report["stage"], "picker_shown")
        self.assertTrue(report["picker_reached"])
        self.assertEqual(report["verdict"], "Reached the boot picker")

    def test_entry_chosen(self):
        report = analyze_lines(CHOSEN)
        self.assertEqual(report["stage"], "entry_chosen")
        self.assertTrue(report["verdict"].startswith("Stalled after the boot entry was chosen"))

    def test_exitbs_stall(self):
        report = analyze_lines(HANDOFF)
        self.assertEqual(report["stage"], "booter_handoff")
        self.assertEqual(report["verdict"], "Stalled after booter handoff: see Booter quirks")
        self.assertIn("exitbs_start", finding_ids(report))
        self.assertIn("EXITBS:START", report["tail"][-1])

    def test_kernel_started(self):
        report = analyze_lines(KERNEL)
        self.assertEqual(report["stage"], "kernel_started")
        self.assertTrue(report["verdict"].startswith("Reached the macOS kernel"))

    def test_stage_is_furthest_marker_not_last_line(self):
        report = analyze_lines(PICKER + ["OC: Starting OpenCore..."])
        self.assertEqual(report["stage"], "picker_shown")

    def test_release_build_from_banner(self):
        report = analyze_lines(START + ["OC: OpenCore REL-108-2026-01-05 is loading in Optional mode (0/0)..."])
        self.assertEqual((report["version"], report["build"]), ("1.0.8", "RELEASE"))


class CISampleTest(unittest.TestCase):
    def test_ci_serial_log_reaches_picker(self):
        analyzer = log_analyzer.LogAnalyzer()
        self.assertEqual(analyzer.load(CI_SAMPLE), CI_SAMPLE)
        report = analyzer.analyze()
        self.assertEqual(report["stage"], "picker_shown")
        self.assertEqual(report["verdict"], "Reached the boot picker")
        self.assertEqual(report["findings"], [])
        self.assertGreater(report["benign_lines"], 5)
        self.assertEqual(len(report["tail"]), 15)
        self.assertEqual(report["tail"][-1], "OCTY: Registered handler")
        self.assertFalse(any("\x1b" in line for line in report["tail"]))
        self.assertFalse(any(line.startswith("01:") for line in report["tail"]))

    def test_ci_sample_as_serial_log_in_a_folder(self):
        folder = tempfile.mkdtemp()
        try:
            shutil.copy(CI_SAMPLE, os.path.join(folder, "serial.log"))
            analyzer = log_analyzer.LogAnalyzer()
            self.assertEqual(analyzer.load(folder), os.path.join(folder, "serial.log"))
            self.assertTrue(analyzer.analyze()["picker_reached"])
        finally:
            shutil.rmtree(folder, ignore_errors=True)


class PatternTest(unittest.TestCase):
    cases = [
        ("x64_exception", "!!!! X64 Exception Type - 06(#UD - Invalid Opcode)  CPU Apic ID - 00000000 !!!!", "fatal"),
        ("assert", "ASSERT [OpenCore] OcMainLib/OpenCoreMisc.c(123): Status == 0", "fatal"),
        ("driver_missing", "OC: Driver OpenCanopy.efi at 2 cannot be found!", "fatal"),
        ("driver_load_failed", "OC: Driver HfsPlus.efi at 0 cannot be loaded - Unsupported!", "fatal"),
        ("openruntime_mismatch", "OCABC: Incompatible OpenRuntime r4, require r10", "fatal"),
        ("vault_invalid", "OC: Invalid Vault mode: optional", "fatal"),
        ("vault_missing", "OCS: no vault provided!", "fatal"),
        ("no_schema", "OCS: No schema for DSDT at 0 index, context <ACPI>!", "warning"),
        ("real_field", "OCS: Failed to parse real field of type 1", "fatal"),
        ("plist_unreadable", "OCS: Couldn't parse serialized file!", "fatal"),
        ("memory_pool", "OCABC: Memory pool allocation failure - Not Found", "fatal"),
        ("image_access_denied", "BS: Failed to open OpenCore image - Access Denied", "fatal"),
        ("no_boot_entries", "OCB: System has no boot entries", "fatal"),
        ("loadimage_security_violation", "OCB: LoadImage failed - Security Violation", "fatal"),
        ("secure_boot_prohibits", "OCB: Apple Secure Boot prohibits this boot entry, enforcing!", "fatal"),
        ("loadimage_failed", "OCB: LoadImage failed - Not Found", "fatal"),
        ("startimage_already_started", "OCB: StartImage failed - Already started", "fatal"),
        ("exitbs_failed", "OCABC: ExitBootServices failed twice - Invalid Parameter", "fatal"),
        ("prelinked_ofs", "[EB|LD:OFS] Err(0xE) @ OPEN (PrelinkedKernel)", "fatal"),
        ("runtime_area", "Couldn't allocate runtime area", "fatal"),
        ("cfg_lock", "OCCPU: EIST CFG Lock 1", "warning"),
        ("kernel_patch_failed", "OCAK: [FAIL] Failed to apply patch AppleCpuPmCfgLock - Not Found", "warning"),
        ("root_device", "Still waiting for root device", "fatal"),
        ("usb_create_device", "AppleUSBHostPort::createDevice: failed to create device", "fatal"),
        ("gio_screen_lock", "IOConsoleUsers: gIOScreenLock retain count", "fatal"),
        ("kextd_stall", "kextd stall[0]: AppleACPICPU", "fatal"),
        ("cckprng", "cckprng_int_gen: generator has already been sealed", "fatal"),
    ]

    def test_dataset_has_enough_documented_patterns(self):
        self.assertGreaterEqual(len(boot_log_patterns.patterns), 20)
        ids = [pattern["id"] for pattern in boot_log_patterns.patterns]
        self.assertEqual(len(ids), len(set(ids)))
        for pattern in boot_log_patterns.patterns:
            self.assertIn(pattern["severity"], ("fatal", "warning", "info"), pattern["id"])
            for field in ("regex", "stage", "category", "cause", "fix", "doc"):
                self.assertTrue(pattern.get(field), "{} lacks {}".format(pattern["id"], field))

    def test_each_case_matches_its_pattern(self):
        self.assertGreaterEqual(len(self.cases), 12)
        for pattern_id, line, severity in self.cases:
            with self.subTest(pattern_id):
                report = analyze_lines(DRIVERS + ["02:000 00:010 " + line])
                matched = [finding for finding in report["findings"] if finding["id"] == pattern_id]
                self.assertEqual(len(matched), 1, finding_ids(report))
                self.assertEqual(matched[0]["severity"], severity)
                self.assertTrue(matched[0]["cause"] and matched[0]["fix"])

    def test_benign_ci_lines_are_not_findings(self):
        with open(CI_SAMPLE, "r", encoding="utf-8") as sample:
            lines = sample.read().splitlines()
        report = analyze_lines(lines)
        self.assertNotIn("fatal", [finding["severity"] for finding in report["findings"]])

    def test_x64_exception_names_the_driver_being_loaded(self):
        report = analyze_lines(DRIVERS + [
            "00:160 00:010 OC: Driver OpenRuntime.efi at 1 (OpenRuntime.efi) is being loaded...",
            "!!!! X64 Exception Type - 06(#UD - Invalid Opcode)  CPU Apic ID - 00000000 !!!!",
            "RIP  - 000000007E5A1234, CS  - 0000000000000038, RFLAGS - 0000000000010246",
        ])
        self.assertEqual(report["verdict"], "Crashed while loading driver OpenRuntime.efi")

    def test_x64_exception_after_drivers_loaded(self):
        report = analyze_lines(PICKER + ["!!!! X64 Exception Type - 0D(#GP - General Protection)  CPU Apic ID - 00000000 !!!!"])
        self.assertTrue(report["verdict"].startswith("Crashed after picker shown:"), report["verdict"])

    def test_no_schema_stall_is_a_config_error(self):
        report = analyze_lines(CONFIG + [
            "00:121 00:001 OCS: No schema for DSDT at 0 index, context <ACPI>!",
            "00:122 00:001 OCS: No schema for KernelAndKextPatches at 1 index, context <root>!",
        ])
        self.assertTrue(report["verdict"].startswith("Config error:"), report["verdict"])
        self.assertIn("DSDT", report["verdict"])
        self.assertEqual(report["findings"][0]["count"], 2)

    def test_vault_error_is_a_config_error(self):
        report = analyze_lines(START + ["OC: Invalid Vault mode: optional"])
        self.assertEqual(report["verdict"], "Config error: Misc -> Security -> Vault has a value OpenCore does not accept.")

    def test_missing_driver_is_a_config_error(self):
        report = analyze_lines(DRIVERS + ["OC: Driver OpenCanopy.efi at 2 cannot be found!"])
        self.assertEqual(report["verdict"], "Config error: config.plist lists the driver OpenCanopy.efi, but it is not in EFI/OC/Drivers.")

    def test_loadimage_failure_after_entry_chosen(self):
        report = analyze_lines(CHOSEN + ["05:120 00:010 OCB: LoadImage failed - Security Violation"])
        self.assertEqual(report["stage"], "entry_chosen")
        self.assertTrue(report["verdict"].startswith("Stopped after entry chosen: Apple Secure Boot manifests"), report["verdict"])
        self.assertIn("SecureBootModel", report["findings"][0]["fix"])


class VerdictAndBootTest(unittest.TestCase):
    def test_fatal_before_later_stage_is_not_the_verdict(self):
        report = analyze_lines(DRIVERS[:-1] + ["OC: Driver Foo.efi at 3 cannot be loaded - Unsupported!"] + DRIVERS[-1:] + PICKER[len(DRIVERS):])
        self.assertEqual(report["verdict"], "Reached the boot picker")
        self.assertIn("driver_load_failed", finding_ids(report))

    def test_only_last_boot_counts(self):
        report = analyze_lines(PICKER + ["00:000 BS: Starting OpenCore application"])
        self.assertEqual(report["stage"], "opencore_started")

    def test_file_log_at_handoff_asks_for_screen(self):
        analyzer = log_analyzer.LogAnalyzer()
        analyzer.load_text("\n".join(HANDOFF), os.path.join("x", "opencore-2026-01-01-000000.txt"))
        report = analyzer.analyze()
        self.assertEqual(report["stage"], "booter_handoff")
        self.assertIn("screen", report["verdict"])
        self.assertNotIn("Stalled after booter handoff", report["verdict"])

    def test_cpu_power_management_needs_panic(self):
        report = analyze_lines(PICKER + ["AppleIntelCPUPowerManagement: loaded"])
        self.assertNotIn("cpu_power_management", finding_ids(report))

    def test_large_file_reads_only_tail(self):
        folder = tempfile.mkdtemp()
        try:
            path = os.path.join(folder, "serial.log")
            with open(path, "wb") as handle:
                handle.write(b"x" * (log_analyzer.MAX_READ + 100) + b"\nBS: Starting OpenCore application\n")
            analyzer = log_analyzer.LogAnalyzer()
            analyzer.load(path)
            self.assertLess(sum(len(line) for line in analyzer.raw_lines), 1000)
        finally:
            shutil.rmtree(folder, ignore_errors=True)


class MaskingTest(unittest.TestCase):
    secrets = ["C02ABCDEFGH1", "C02123456789ABCDE", "12345678-1234-1234-1234-123456789ABC", "11:22:33:44:55:66", "F0ABCDEF1234567"]

    def test_masks_serials_mlb_rom_and_system_id(self):
        lines = PICKER + [
            "01:039 00:006 OC: Setting SSN C02ABCDEFGH1 - Success",
            "01:040 00:006 OC: Setting HW_SSN C02ABCDEFGH1 - Success",
            "01:041 00:006 OC: Setting MLB C02123456789ABCDE - Success",
            "01:042 00:006 OC: Setting HW_MLB C02123456789ABCDE - Success",
            "01:043 00:006 OC: Setting system-id 12345678-1234-1234-1234-123456789ABC - Success",
            "01:044 00:006 OC: Setting ROM 11:22:33:44:55:66 - Success",
            "01:044 00:006 OC: Setting HW_ROM 11:22:33:44:55:66 - Success",
            "01:044 00:006 OCSMB: SSN from NVRAM took precedence: C02ABCDEFGH1",
            "01:044 00:006 OCSMB: MLB from NVRAM took precedence: C02123456789ABCDE",
            "01:045 00:006 OCSMB: SystemSerialNumber=F0ABCDEF1234567",
            "01:046 00:006 OC: Grabbed SB uuid 12345678-1234-1234-1234-123456789ABC direct from NVRAM - Success",
        ]
        analyzer = log_analyzer.LogAnalyzer()
        analyzer.load_text("\n".join(lines))
        text = analyzer.format_report(analyzer.analyze())
        for secret in self.secrets:
            self.assertNotIn(secret, text)
        self.assertIn("OC: Setting SSN <masked> - Success", text)
        self.assertIn("OC: Setting system-id <masked> - Success", text)

    def test_masks_values_inside_findings(self):
        report = analyze_lines(DRIVERS + ["OCS: No schema for SystemSerialNumber=F0ABCDEF1234567 at 0 index"])
        rendered = log_analyzer.LogAnalyzer().format_report(report)
        self.assertNotIn("F0ABCDEF1234567", rendered)

    def test_leaves_ordinary_words_alone(self):
        analyzer = log_analyzer.LogAnalyzer()
        self.assertEqual(analyzer.mask("OC: Grabbed zero system-id for SB, this is not allowed"), "OC: Grabbed zero system-id for SB, this is not allowed")


class LoadTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def write(self, name, text, encoding="utf-8", mtime=None):
        path = os.path.join(self.folder, name)
        with open(path, "wb") as log_file:
            log_file.write(text.encode(encoding))
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def test_newest_log_by_name_timestamp(self):
        self.write("opencore-2026-10-20-101010.txt", "\n".join(START))
        newest = self.write("opencore-2026-10-21-090000.txt", "\n".join(PICKER), mtime=1000000)
        self.write("opencore-2026-10-19-235959.txt", "\n".join(CONFIG), mtime=2000000000)
        self.write("serial.log", "\n".join(START))
        self.write("notes.txt", "x")
        analyzer = log_analyzer.LogAnalyzer()
        self.assertEqual(analyzer.load(self.folder), newest)
        self.assertEqual(analyzer.analyze()["stage"], "picker_shown")

    def test_direct_file_path_and_quotes(self):
        path = self.write("anything.txt", "\n".join(CONFIG))
        analyzer = log_analyzer.LogAnalyzer()
        self.assertEqual(analyzer.load('"{}"'.format(path)), path)

    def test_missing_log(self):
        self.write("notes.txt", "x")
        analyzer = log_analyzer.LogAnalyzer()
        self.assertIsNone(analyzer.load(self.folder))
        self.assertIsNone(analyzer.load(os.path.join(self.folder, "missing")))
        self.assertIsNone(analyzer.load(""))

    def test_utf16_with_bom_and_crlf(self):
        self.write("opencore-2026-10-21-090000.txt", "\ufeff" + "\r\n".join(HANDOFF), encoding="utf-16-le")
        analyzer = log_analyzer.LogAnalyzer()
        analyzer.load(self.folder)
        report = analyzer.analyze()
        self.assertEqual(report["stage"], "booter_handoff")
        self.assertEqual(report["version"], "1.0.9")

    def test_utf16_without_bom(self):
        path = self.write("serial.log", "\r\n".join(PICKER), encoding="utf-16-le")
        analyzer = log_analyzer.LogAnalyzer()
        analyzer.load(path)
        self.assertEqual(analyzer.analyze()["verdict"], "Reached the boot picker")

    def test_utf8_bom_and_bare_cr(self):
        path = self.write("serial.log", "\ufeff" + "\r".join(PICKER))
        analyzer = log_analyzer.LogAnalyzer()
        analyzer.load(path)
        self.assertEqual(analyzer.analyze()["stage"], "picker_shown")

    @unittest.skipUnless(os.name == "nt", "drive letters are Windows only")
    def test_drive_letter_becomes_root(self):
        analyzer = log_analyzer.LogAnalyzer()
        self.assertEqual(analyzer._drive_root("e"), "E:\\")
        self.assertEqual(analyzer._drive_root("E:"), "E:\\")
        self.assertEqual(analyzer._drive_root("E:\\"), "E:\\")


def load_main_module():
    spec = importlib.util.spec_from_file_location("opcore_simplify_main", os.path.join(REPO_ROOT, "OpenCoreWizard.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FrontEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_main_module()

    def tearDown(self):
        utils.Utils.set_headless(None)

    def test_render_returns_plain_text_in_headless(self):
        utils.Utils.set_headless({})
        analyzer = log_analyzer.LogAnalyzer()
        analyzer.load(CI_SAMPLE)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            text = analyzer.render()
        self.assertIn("Verdict:       Reached the boot picker", text)
        self.assertNotIn("\033[", text)
        self.assertIn(text, output.getvalue())

    def test_menu_lists_analyzer_last(self):
        with open(os.path.join(REPO_ROOT, "OpenCoreWizard.py"), "r", encoding="utf-8") as source:
            text = source.read()
        self.assertLess(text.index('print("10. Create USB Installer")'), text.index('print("11. Analyze Boot Log")'))

    def test_menu_option_needs_no_hardware_report(self):
        ocpe = self.module.OCPE.__new__(self.module.OCPE)
        ocpe.u = utils.Utils()
        utils.Utils.set_headless({"prompts": {"Drive, folder or log file": CI_SAMPLE}})
        with contextlib.redirect_stdout(io.StringIO()) as output:
            report = ocpe.analyze_boot_log()
        self.assertTrue(report["picker_reached"])
        self.assertIn("Reached the boot picker", output.getvalue())

    def test_menu_option_reports_missing_log(self):
        ocpe = self.module.OCPE.__new__(self.module.OCPE)
        ocpe.u = utils.Utils()
        utils.Utils.set_headless({})
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(ocpe.analyze_boot_log(os.path.join(REPO_ROOT, "no-such-folder")))

    def test_cli_exit_codes(self):
        script = os.path.join(REPO_ROOT, "OpenCoreWizard.py")
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        found = subprocess.run([sys.executable, script, "--analyze-log", CI_SAMPLE], capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)
        self.assertEqual(found.returncode, 0, found.stdout + found.stderr)
        self.assertIn("Verdict:       Reached the boot picker", found.stdout)
        missing = subprocess.run([sys.executable, script, "--analyze-log", os.path.join(REPO_ROOT, "no-such-folder")], capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)
        self.assertEqual(missing.returncode, 1)
        self.assertIn("No OpenCore log", missing.stdout)


if __name__ == "__main__":
    unittest.main()
