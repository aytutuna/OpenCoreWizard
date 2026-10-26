import contextlib
import copy
import importlib.util
import io
import json
import os
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import repo_path  # noqa: F401
from repo_path import FIXTURES, REPO_ROOT
from Scripts import config_prodigy
from Scripts import preflight
from Scripts import report_validator
from Scripts.datasets import kext_data
from Scripts.datasets import vendor_quirks

SAMPLE_CONFIG = os.path.join(REPO_ROOT, "OCK_Files", "OpenCorePkg", "EFI", "OC", "config.plist")
POST_INSTALL_NOTES = [
    "The macOS installer only copies macOS to the internal disk. The part that starts macOS (OpenCore, the EFI folder this app built) is still only on the USB stick. So for now leave the stick plugged in and pick it in the BIOS boot menu every time you start the PC.",
    "Once macOS runs from the stick, copy the EFI folder to the internal disk's EFI partition (a small hidden start-up area every disk has). Back up the stick's EFI folder to the Desktop first (the stick shows up as OPENCORE), then mount the internal one with MountEFI from the kit's PostInstall folder (made on the Post-install page or with menu option 9 in the terminal). POST-INSTALL.md lists the steps.",
    "Then, in the copy you just made on the internal disk, open the settings file EFI/OC/config.plist with the ProperTree tool from the kit. Find the setting Misc -> Boot -> LauncherOption. It is Disabled so testing from the stick never changes the boot order; change it to Full and save. On the next start OpenCore adds itself to the BIOS boot menu, so the PC starts macOS without the stick. If the BIOS screen shows the name Insyde (common on laptops), use Short instead of Full."
]


def load_main_module():
    spec = importlib.util.spec_from_file_location("opcore_simplify_main", os.path.join(REPO_ROOT, "MacBootsmith.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_desktop_report():
    with open(os.path.join(FIXTURES, "desktop", "Report.json"), "r", encoding="utf-8") as report_file:
        return json.load(report_file)


def quirk_report(name="Test Board", chipset="Z490", vendor=None, cpu="Intel(R) Core(TM) i5-10400 CPU"):
    report = {
        "Motherboard": {"Name": name, "Chipset": chipset, "Platform": "Desktop"},
        "BIOS": {"Firmware Type": "UEFI", "Secure Boot": "Disabled"},
        "CPU": {"Processor Name": cpu, "Manufacturer": "AMD" if "AMD" in cpu else "Intel"}
    }
    if vendor:
        report["BIOS"]["Vendor"] = vendor
    return report


def quirk_config():
    return {
        "ACPI": {"Add": [{"Comment": "SSDT-PLUG.aml", "Enabled": True, "Path": "SSDT-PLUG.aml"}]},
        "Misc": {"Boot": {"LauncherOption": "Full", "LauncherPath": "Default"}, "Security": {}},
        "UEFI": {
            "Drivers": [{"Path": "HfsPlus.efi"}, {"Path": "OpenRuntime.efi"}, {"Path": "ResetNvramEntry.efi"}],
            "Quirks": {"DisableSecurityPolicy": False, "RequestBootVarRouting": True}
        }
    }


def rule_names(report):
    return [rule["name"] for rule in config_prodigy.ConfigProdigy().matching_vendor_rules(report)]


class VendorRuleMatcherTest(unittest.TestCase):
    def setUp(self):
        self.prodigy = config_prodigy.ConfigProdigy()

    def test_generic_board_matches_nothing(self):
        self.assertEqual(rule_names(quirk_report()), [])

    def test_desktop_fixture_matches_nothing(self):
        self.assertEqual(rule_names(load_desktop_report()), [])

    def test_surface(self):
        self.assertEqual(rule_names(quirk_report(name="SURFACE PRO 7")), ["Microsoft Surface"])

    def test_case_insensitive(self):
        self.assertEqual(rule_names(quirk_report(name="Microsoft Surface Laptop 3")), ["Microsoft Surface"])

    def test_b550_and_a520(self):
        self.assertEqual(rule_names(quirk_report(chipset="B550")), ["AMD B550/A520"])
        self.assertEqual(rule_names(quirk_report(chipset="A520")), ["AMD B550/A520"])

    def test_x570_and_b450_not_matched(self):
        self.assertEqual(rule_names(quirk_report(chipset="X570")), [])
        self.assertEqual(rule_names(quirk_report(chipset="B450")), [])

    def test_insyde_vendor(self):
        self.assertEqual(rule_names(quirk_report(vendor="LENOVO (INSYDE Corp.)")), ["Insyde firmware"])

    def test_missing_vendor_field_not_matched(self):
        self.assertEqual(rule_names(quirk_report(vendor=None)), [])
        self.assertEqual(rule_names(quirk_report(vendor="American Megatrends Inc.")), [])

    def test_thinkpad_machine_type(self):
        self.assertEqual(rule_names(quirk_report(name="LENOVO 20HRCTO1WW")), ["Lenovo ThinkPad"])
        self.assertEqual(rule_names(quirk_report(name="LENOVO 21F3002AUS")), ["Lenovo ThinkPad"])
        self.assertEqual(rule_names(quirk_report(name="ThinkPad T480")), ["Lenovo ThinkPad"])

    def test_consumer_lenovo_not_thinkpad(self):
        self.assertEqual(rule_names(quirk_report(name="LENOVO LNVNB161216")), [])

    def test_threadripper_3990x(self):
        self.assertEqual(rule_names(quirk_report(chipset="TRX40", cpu="AMD Ryzen Threadripper 3990X 64-Core Processor")), ["AMD Threadripper over 64 threads"])
        for cpu in ("AMD Ryzen Threadripper PRO 3995WX 64-Cores", "AMD Ryzen Threadripper PRO 5995WX 64-Cores", "AMD Ryzen Threadripper PRO 7995WX 96-Cores", "AMD Ryzen Threadripper 7980X 64-Cores"):
            self.assertEqual(rule_names(quirk_report(chipset="WRX80", cpu=cpu)), ["AMD Threadripper over 64 threads"])
        self.assertEqual(rule_names(quirk_report(chipset="WRX80", cpu="AMD Ryzen Threadripper PRO 3975WX 32-Cores")), [])
        self.assertEqual(rule_names(quirk_report(chipset="TRX40", cpu="AMD Ryzen Threadripper 3970X 32-Core Processor")), [])

    def test_all_fields_must_match(self):
        rule = {"match": {"motherboard": ["SURFACE"], "chipset": ["^B550$"]}}
        self.assertFalse(self.prodigy.match_vendor_rule(rule, quirk_report(name="SURFACE PRO 7")))
        self.assertTrue(self.prodigy.match_vendor_rule(rule, quirk_report(name="SURFACE PRO 7", chipset="B550")))

    def test_rule_without_match_never_applies(self):
        self.assertFalse(self.prodigy.match_vendor_rule({"match": {}}, quirk_report()))

    def test_rules_use_known_fields(self):
        for rule in vendor_quirks.rules:
            self.assertTrue(rule["match"])
            for field in rule["match"]:
                self.assertIn(field, config_prodigy.vendor_quirk_fields)


class ApplyVendorQuirksTest(unittest.TestCase):
    def setUp(self):
        self.prodigy = config_prodigy.ConfigProdigy()
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_no_match_leaves_config_alone(self):
        config = quirk_config()
        notes = self.prodigy.apply_vendor_quirks(quirk_report(), config, self.temp_dir)
        self.assertEqual(notes, [])
        self.assertEqual(config, quirk_config())

    def test_surface_disables_security_policy(self):
        config = quirk_config()
        notes = self.prodigy.apply_vendor_quirks(quirk_report(name="SURFACE PRO 7"), config)
        self.assertIs(config["UEFI"]["Quirks"]["DisableSecurityPolicy"], True)
        self.assertTrue(any("DisableSecurityPolicy" in note for note in notes))

    def test_insyde_leaves_launcher_option_and_notes_short(self):
        config = quirk_config()
        notes = self.prodigy.apply_vendor_quirks(quirk_report(vendor="Insyde Corp."), config)
        self.assertEqual(config["Misc"]["Boot"], quirk_config()["Misc"]["Boot"])
        self.assertTrue(any("LauncherOption to Short" in note for note in notes))

    def test_b550_with_plug_alt_skips_cpur(self):
        config = quirk_config()
        config["ACPI"]["Add"].append({"Comment": "SSDT-PLUG-ALT.aml", "Enabled": True, "Path": "SSDT-PLUG-ALT.aml"})
        before = copy.deepcopy(config["ACPI"]["Add"])
        notes = self.prodigy.apply_vendor_quirks(quirk_report(chipset="B550"), config, self.temp_dir)
        self.assertEqual(config["ACPI"]["Add"], before)
        self.assertEqual(len(notes), 1)
        self.assertIn("do not add SSDT-CPUR.aml", notes[0])

    def test_thinkpad_drops_nvram_reset_driver(self):
        config = quirk_config()
        notes = self.prodigy.apply_vendor_quirks(quirk_report(name="LENOVO 20HRCTO1WW"), config)
        self.assertEqual([driver["Path"] for driver in config["UEFI"]["Drivers"]], ["HfsPlus.efi", "OpenRuntime.efi"])
        self.assertIn("Do not use Reset NVRAM from the picker on ThinkPads; it can brick the firmware. Update the BIOS instead. The match also covers ThinkBook machine types, which get the same precaution.", notes)

    def test_threadripper_note_only(self):
        config = quirk_config()
        notes = self.prodigy.apply_vendor_quirks(quirk_report(chipset="TRX40", cpu="AMD Ryzen Threadripper 3990X 64-Core Processor"), config)
        self.assertEqual(notes, ["macOS supports at most 64 threads: disable SMT in BIOS"])
        self.assertEqual(config, quirk_config())

    def test_b550_without_table_only_notes(self):
        config = quirk_config()
        notes = self.prodigy.apply_vendor_quirks(quirk_report(chipset="B550"), config, self.temp_dir)
        self.assertEqual(config["ACPI"]["Add"], quirk_config()["ACPI"]["Add"])
        self.assertIn("Needs SSDT-CPUR.aml (not on X570 or older) or boot panics in cckprng_int_gen", notes)
        self.assertTrue(any(vendor_quirks.ssdt_cpur_url in note for note in notes))

    def test_b550_without_directory_only_notes(self):
        config = quirk_config()
        notes = self.prodigy.apply_vendor_quirks(quirk_report(chipset="A520"), config)
        self.assertEqual(len(config["ACPI"]["Add"]), 1)
        self.assertEqual(len(notes), 2)

    def test_b550_with_table_adds_acpi_entry_once(self):
        with open(os.path.join(self.temp_dir, "SSDT-CPUR.aml"), "wb") as table:
            table.write(b"SSDT")
        config = quirk_config()
        notes = self.prodigy.apply_vendor_quirks(quirk_report(chipset="B550"), config, self.temp_dir)
        self.prodigy.apply_vendor_quirks(quirk_report(chipset="B550"), config, self.temp_dir)
        entries = [entry for entry in config["ACPI"]["Add"] if entry["Path"] == "SSDT-CPUR.aml"]
        self.assertEqual(entries, [{"Comment": "SSDT-CPUR.aml", "Enabled": True, "Path": "SSDT-CPUR.aml"}])
        self.assertFalse(any(vendor_quirks.ssdt_cpur_url in note for note in notes))

    def test_set_config_value_creates_missing_sections(self):
        config = {}
        self.prodigy.set_config_value(config, "UEFI.Quirks.DisableSecurityPolicy", True)
        self.assertEqual(config, {"UEFI": {"Quirks": {"DisableSecurityPolicy": True}}})


class CfgLockToolTest(unittest.TestCase):
    def setUp(self):
        self.prodigy = config_prodigy.ConfigProdigy()

    def config(self, cpu_pm, xcpm):
        return {"Kernel": {"Quirks": {"AppleCpuPmCfgLock": cpu_pm, "AppleXcpmCfgLock": xcpm}}}

    def test_entry_with_xcpm_quirk(self):
        self.assertEqual(self.prodigy.cfg_lock_tool(self.config(False, True)), {
            "Arguments": "",
            "Auxiliary": True,
            "Comment": "Check CFG Lock",
            "Enabled": True,
            "Flavour": "Auto",
            "FullNvramAccess": False,
            "Name": "ControlMsrE2",
            "Path": "ControlMsrE2.efi",
            "RealPath": False,
            "TextMode": True
        })

    def test_entry_with_cpupm_quirk(self):
        self.assertEqual(self.prodigy.cfg_lock_tool(self.config(True, False))["Path"], "ControlMsrE2.efi")

    def test_no_entry_without_quirks(self):
        self.assertIsNone(self.prodigy.cfg_lock_tool(self.config(False, False)))


@unittest.skipUnless(os.path.exists(SAMPLE_CONFIG), "OpenCorePkg sample config not downloaded")
class GenarateBootPersistenceTest(unittest.TestCase):
    def build(self, report):
        prodigy = config_prodigy.ConfigProdigy()
        prodigy.smbios.generate_smbios = lambda model: {"SystemProductName": model, "SystemSerialNumber": "TESTSERIAL0", "MLB": "TESTMLB000000000", "SystemUUID": "00000000-0000-0000-0000-000000000000", "ROM": "112233445566"}
        with open(SAMPLE_CONFIG, "rb") as sample:
            config = plistlib.load(sample)
        kexts = copy.deepcopy(kext_data.kexts)
        for kext in kexts:
            kext.checked = kext.required
        with contextlib.redirect_stdout(io.StringIO()):
            prodigy.genarate(report, {}, "iMac20,1", "25.0.0", False, kexts, config)
        return prodigy, config

    def test_desktop_defaults(self):
        prodigy, config = self.build(load_desktop_report())
        self.assertEqual(config["Misc"]["Boot"]["LauncherOption"], "Disabled")
        self.assertEqual(config["Misc"]["Boot"]["LauncherPath"], "Default")
        self.assertIs(config["UEFI"]["Quirks"]["RequestBootVarRouting"], True)
        self.assertEqual(config["Misc"]["BlessOverride"], [])
        self.assertNotIn("AllowNvramReset", config["Misc"]["Security"])
        self.assertIn("ResetNvramEntry.efi", [driver["Path"] for driver in config["UEFI"]["Drivers"]])
        self.assertIs(config["UEFI"]["Quirks"]["DisableSecurityPolicy"], False)
        self.assertEqual([tool["Path"] for tool in config["Misc"]["Tools"]], ["ControlMsrE2.efi"])
        self.assertIn(config_prodigy.cfg_lock_note, prodigy.bios_notes)

    def test_insyde_report_keeps_launcher_disabled(self):
        report = load_desktop_report()
        report["BIOS"]["Vendor"] = "Insyde Corp."
        prodigy, config = self.build(report)
        self.assertEqual(config["Misc"]["Boot"]["LauncherOption"], "Disabled")
        self.assertTrue(any("Insyde" in note for note in prodigy.bios_notes))

    def test_bios_notes_reset_between_builds(self):
        report = load_desktop_report()
        report["Motherboard"]["Name"] = "SURFACE PRO 7"
        prodigy, config = self.build(report)
        self.assertTrue(any("DisableSecurityPolicy" in note for note in prodigy.bios_notes))
        with open(SAMPLE_CONFIG, "rb") as sample:
            config = plistlib.load(sample)
        kexts = copy.deepcopy(kext_data.kexts)
        for kext in kexts:
            kext.checked = kext.required
        with contextlib.redirect_stdout(io.StringIO()):
            prodigy.genarate(load_desktop_report(), {}, "iMac20,1", "25.0.0", False, kexts, config)
        self.assertFalse(any("DisableSecurityPolicy" in note for note in prodigy.bios_notes))


class SipTest(unittest.TestCase):
    def test_sip_stays_on_without_root_patches(self):
        prodigy = config_prodigy.ConfigProdigy()
        for version in ("17.0.0", "19.0.0", "22.0.0", "25.0.0"):
            self.assertEqual(prodigy.csr_active_config(version, False), "00000000")
        self.assertEqual(prodigy.csr_active_config("25.0.0", True), "030A0000")
        self.assertEqual(prodigy.csr_active_config("20.0.0", True), "030A0000")
        self.assertEqual(prodigy.csr_active_config("19.0.0", True), "FF070000")
        self.assertEqual(prodigy.csr_active_config("17.0.0", True), "FF030000")


@unittest.skipUnless(os.path.exists(SAMPLE_CONFIG), "OpenCorePkg sample config not downloaded")
class GenarateSipTest(unittest.TestCase):
    def build(self, needs_oclp):
        prodigy = config_prodigy.ConfigProdigy()
        prodigy.smbios.generate_smbios = lambda model: {"SystemProductName": model, "SystemSerialNumber": "TESTSERIAL0", "MLB": "TESTMLB000000000", "SystemUUID": "00000000-0000-0000-0000-000000000000", "ROM": "112233445566"}
        with open(SAMPLE_CONFIG, "rb") as sample:
            config = plistlib.load(sample)
        kexts = copy.deepcopy(kext_data.kexts)
        for kext in kexts:
            kext.checked = kext.required
        with contextlib.redirect_stdout(io.StringIO()):
            prodigy.genarate(load_desktop_report(), {}, "iMac20,1", "25.0.0", needs_oclp, kexts, config)
        nvram = config["NVRAM"]["Add"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"]
        self.assertIn("csr-active-config", config["NVRAM"]["Delete"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"])
        return nvram["csr-active-config"].hex().upper()

    def test_desktop_build_keeps_sip_on(self):
        self.assertEqual(self.build(False), "00000000")

    def test_root_patch_build_lowers_sip(self):
        self.assertEqual(self.build(True), "030A0000")

    def test_patch_manifest_gets_the_lowered_value(self):
        from Scripts import postinstall_kit
        prodigy = config_prodigy.ConfigProdigy()
        config = {"NVRAM": {"Add": {"7C436110-AB2A-4BBB-A880-FE41995C9F82": {"csr-active-config": bytes.fromhex(prodigy.csr_active_config("25.0.0", True))}}}}
        summary = postinstall_kit.make_build_summary(load_desktop_report(), config, "iMac20,1", "25.0.0", True, applealc=True)
        self.assertTrue(postinstall_kit.macpatch_entries(summary))
        self.assertEqual(postinstall_kit.make_patch_manifest(summary)["sip"], "030A0000")


class BIOSNotesTest(unittest.TestCase):
    def setUp(self):
        module = load_main_module()
        self.ocpe = module.OCPE.__new__(module.OCPE)
        self.ocpe.bios_checklist = module.bios_checklist.BIOSChecklist()
        self.report = {
            "Motherboard": {"Name": "Test Board", "Platform": "Desktop"},
            "CPU": {"Codename": "Comet Lake-S", "Manufacturer": "Intel", "Processor Name": "Intel(R) Core(TM) i7-10700K CPU"},
            "GPU": {}
        }

    def test_build_notes_are_appended(self):
        self.ocpe.co = config_prodigy.ConfigProdigy()
        self.ocpe.co.bios_notes = [config_prodigy.cfg_lock_note, "macOS supports at most 64 threads: disable SMT in BIOS"]
        notes = self.ocpe.check_bios_requirements(self.report, self.report)["notes"]
        self.assertEqual(notes[-2:], self.ocpe.co.bios_notes)

    def test_without_build_notes(self):
        notes = self.ocpe.check_bios_requirements(self.report, self.report)["notes"]
        self.assertNotIn(config_prodigy.cfg_lock_note, notes)

    def test_cfg_lock_note_wording(self):
        self.assertEqual(config_prodigy.cfg_lock_note, "CFG Lock: in the boot picker press Space to show hidden entries and run ControlMsrE2. If it reports UNLOCKED, the AppleXcpmCfgLock/AppleCpuPmCfgLock quirks can be turned off later; if LOCKED, leave them on.")

    def test_post_install_line(self):
        self.assertEqual(self.ocpe.post_install_notes(), POST_INSTALL_NOTES)


class BIOSStatePrintTest(unittest.TestCase):
    def setUp(self):
        module = load_main_module()
        self.ocpe = module.OCPE.__new__(module.OCPE)
        self.ocpe.bios_checklist = module.bios_checklist.BIOSChecklist()
        self.ocpe.pf = preflight.Preflight()
        self.bios = {"disable": ["Secure Boot", "Fast Boot"], "enable": ["VT-x"], "notes": ["Update the BIOS first."]}
        self.report = {"Motherboard": {"Name": "TEST BOARD"}, "BIOS": {"Version": "F1"}, "CPU": {"Manufacturer": "Intel", "Codename": "Comet Lake-S"}}
        self.ocpe.pf.info = {"secure_boot": "on", "firmware": {
            "machine": {"Model": "Board", "BiosVersion": "F1"}, "pf_virt": False,
            "cpu": {"vendor": "GenuineIntel", "hypervisor": False, "vmx": True, "svm": False, "hv_root": None, "smt_width": 2}
        }}

    def printed(self, state):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.ocpe.print_bios_requirements(self.bios, state)
        return output.getvalue()

    def test_groups(self):
        text = self.printed(self.ocpe.bios_state(self.bios, self.report))
        self.assertIn("    Change these in the BIOS before you boot from the stick:\n    - Secure Boot: should be Off. Windows reports it is on.", text)
        self.assertIn("    - VT-x: should be On. Windows reports it is off in the BIOS.", text)
        self.assertNotIn("Nothing to do", text)
        self.assertIn("    Check these yourself in the BIOS (the app could not tell from Windows):\n    - Fast Boot: should be Off. Windows cannot see this setting.", text)
        self.assertIn("    Notes:\n    - Update the BIOS first.", text)
        self.assertLess(text.index("Change these in the BIOS"), text.index("Check these yourself"))

    def test_other_pc(self):
        self.report["BIOS"]["Version"] = "F2"
        text = self.printed(self.ocpe.bios_state(self.bios, self.report))
        self.assertIn("    This hardware report is from another PC, so the app cannot check that PC's BIOS from here. Run the app on that PC to check it, or go through the list below in its BIOS yourself.\n    Disable:\n    - Secure Boot", text)
        self.assertNotIn("should be", text)

    def test_without_readings(self):
        self.ocpe.pf.info = None
        self.assertIsNone(self.ocpe.bios_state(self.bios, self.report))
        self.assertIn("    Disable: Secure Boot\n", self.printed(None))


def fake_output(payload, code=0, error=""):
    return (json.dumps(payload) if not isinstance(payload, str) else payload, error, code)


class PreflightParseTest(unittest.TestCase):
    def setUp(self):
        self.pf = preflight.Preflight()

    def test_bitlocker_cmdlet_on(self):
        info = self.pf.parse({"BitLocker": {"Source": "Get-BitLockerVolume", "ProtectionStatus": "On", "VolumeStatus": "FullyEncrypted"}})
        self.assertEqual(info["bitlocker"], "on")

    def test_bitlocker_cmdlet_off(self):
        info = self.pf.parse({"BitLocker": {"Source": "Get-BitLockerVolume", "ProtectionStatus": "Off", "VolumeStatus": "FullyDecrypted"}})
        self.assertEqual(info["bitlocker"], "off")

    def test_bitlocker_suspended_but_encrypted(self):
        info = self.pf.parse({"BitLocker": {"Source": "Get-BitLockerVolume", "ProtectionStatus": "Off", "VolumeStatus": "FullyEncrypted"}})
        self.assertEqual(info["bitlocker"], "off (volume encrypted, protection suspended)")

    def test_cim_numeric_status(self):
        self.assertEqual(self.pf.parse({"BitLocker": {"Source": "Win32_EncryptableVolume", "ProtectionStatus": "1", "VolumeStatus": "1"}})["bitlocker"], "on")
        self.assertEqual(self.pf.parse({"BitLocker": {"Source": "Win32_EncryptableVolume", "ProtectionStatus": "0", "VolumeStatus": "0"}})["bitlocker"], "off")
        self.assertEqual(self.pf.parse({"BitLocker": {"Source": "Win32_EncryptableVolume", "ProtectionStatus": "2", "VolumeStatus": "0"}})["bitlocker"], preflight.Preflight.unknown_bitlocker)

    def test_shell_property_values(self):
        for value, state in (("1", "on"), ("6", "on"), ("2", "off"), ("5", "suspended"), ("9", preflight.Preflight.unknown_bitlocker)):
            self.assertEqual(self.pf.parse({"BitLocker": {"Source": "Shell", "ProtectionStatus": value}})["bitlocker"], state)

    def test_missing_bitlocker_is_unknown(self):
        info = self.pf.parse({"BitLockerError": "Access denied"})
        self.assertEqual(info["bitlocker"], "unknown (run as administrator to check)")

    def test_esp_on_system_disk_preferred(self):
        info = self.pf.parse({
            "Disk": {"Number": 1, "PartitionStyle": "GPT"},
            "Esp": [{"DiskNumber": 0, "Size": 524288000}, {"DiskNumber": 1, "Size": 104857600}]
        })
        self.assertEqual(info["partition_style"], "GPT")
        self.assertEqual(info["esp_mb"], 100)

    def test_single_esp_object(self):
        self.assertEqual(self.pf.parse({"Esp": {"DiskNumber": 0, "Size": 209715200}})["esp_mb"], 200)

    def test_no_esp(self):
        info = self.pf.parse({"Disk": {"Number": 0, "PartitionStyle": "MBR"}, "Esp": []})
        self.assertEqual(info["partition_style"], "MBR")
        self.assertIsNone(info["esp_mb"])

    def test_firmware_readings(self):
        firmware = self.pf.parse({
            "Machine": {"Manufacturer": "LENOVO", "Model": "83F5", "Hypervisor": False, "BoardProduct": "LNVNB161216", "BiosVersion": "Q7CN78WW"},
            "Virtualization": {"Capable": True, "Enabled": False},
            "DeviceGuard": [2, 3, 5],
            "Devices": [
                {"Id": "PCI\\VEN_1C5C&DEV_1959\\4", "Name": "Standard NVM Express Controller", "Hardware": ["PCI\\VEN_1C5C&DEV_1959"], "Compatible": ["PCI\\VEN_1C5C&CC_010802", "PCI\\CC_010802"], "Error": 0},
                {"Id": "acpi\\intc7001\\0", "Name": "Trusted Platform Module 2.0", "Hardware": "ACPI\\INTC7001", "Compatible": None, "Error": None}
            ],
            "DiskBus": 17
        })["firmware"]
        self.assertEqual(firmware["machine"]["BiosVersion"], "Q7CN78WW")
        self.assertFalse(firmware["machine"]["Hypervisor"])
        self.assertEqual((firmware["vmx_capable"], firmware["virt_enabled"]), (True, False))
        self.assertEqual(firmware["device_guard"], [2, 3, 5])
        self.assertEqual(firmware["disk_bus"], [17])
        self.assertEqual(firmware["devices"][0]["compatible"], ["PCI\\VEN_1C5C&CC_010802", "PCI\\CC_010802"])
        self.assertEqual(firmware["devices"][1], {"id": "ACPI\\INTC7001\\0", "name": "Trusted Platform Module 2.0", "hardware": ["ACPI\\INTC7001"], "compatible": [], "error": None})

    def test_firmware_readings_missing(self):
        firmware = self.pf.parse({"MachineError": "denied"})["firmware"]
        self.assertEqual(set(firmware.values()), {None})

    def test_single_device_and_wrapped_arrays(self):
        firmware = self.pf.parse({"Devices": {"Id": "PCI\\VEN_8086&DEV_A352\\3", "Hardware": [], "Compatible": [], "Error": 0},
                                  "DeviceGuard": {"value": [3], "Count": 1}})["firmware"]
        self.assertEqual(len(firmware["devices"]), 1)
        self.assertEqual(firmware["device_guard"], [3])


class PreflightWarningTest(unittest.TestCase):
    def setUp(self):
        self.pf = preflight.Preflight()
        self.uefi = {"BIOS": {"Firmware Type": "UEFI"}}

    def info(self, **values):
        base = {"bitlocker": "off", "partition_style": "GPT", "esp_mb": 260, "secure_boot": "off"}
        base.update(values)
        return base

    def test_clean_system_has_no_warnings(self):
        self.assertEqual(self.pf.warnings(self.info(), self.uefi, self.uefi), [])

    def test_bitlocker_on(self):
        self.assertEqual(self.pf.warnings(self.info(bitlocker="on"), self.uefi, self.uefi), [
            "Suspend BitLocker or save your recovery key before changing TPM or Secure Boot settings; otherwise Windows will demand the key on the next boot."
        ])

    def test_bitlocker_unknown(self):
        warnings = self.pf.warnings(self.info(bitlocker=preflight.Preflight.unknown_bitlocker), self.uefi, self.uefi)
        self.assertEqual(warnings, [preflight.Preflight.bitlocker_unknown_warning])

    def test_secure_boot_on(self):
        self.assertEqual(self.pf.warnings(self.info(secure_boot="on"), self.uefi, self.uefi), [preflight.Preflight.secure_boot_warning])

    def test_secure_boot_unknown_is_quiet(self):
        self.assertEqual(self.pf.warnings(self.info(secure_boot=None), self.uefi, self.uefi), [])

    def test_secure_boot_parsed(self):
        self.assertEqual(self.pf.parse({"SecureBoot": 1})["secure_boot"], "on")
        self.assertEqual(self.pf.parse({"SecureBoot": 0})["secure_boot"], "off")
        self.assertIsNone(self.pf.parse({"SecureBootError": "denied"})["secure_boot"])

    def test_bitlocker_suspended_no_warning(self):
        self.assertEqual(self.pf.warnings(self.info(bitlocker="suspended"), self.uefi, self.uefi), [])

    def test_mbr(self):
        self.assertEqual(self.pf.warnings(self.info(partition_style="MBR", esp_mb=None), self.uefi, self.uefi), [
            "Windows is installed in legacy MBR mode; OpenCore cannot boot it and the machine must switch to UEFI."
        ])

    def test_small_esp(self):
        self.assertEqual(self.pf.warnings(self.info(esp_mb=100), self.uefi, self.uefi), [
            "The Windows EFI partition is small (100 MB); macOS installs expect about 200 MB if you plan to share it. Installing macOS on its own disk avoids this."
        ])

    def test_esp_threshold(self):
        self.assertEqual(self.pf.warnings(self.info(esp_mb=150), self.uefi, self.uefi), [])
        self.assertEqual(len(self.pf.warnings(self.info(esp_mb=149), self.uefi, self.uefi)), 1)

    def test_legacy_firmware_switched_to_uefi(self):
        warnings = self.pf.warnings(None, {"BIOS": {"Firmware Type": "BIOS"}}, self.uefi)
        self.assertEqual(warnings, [preflight.Preflight.legacy_warning])
        self.assertIn("UEFI mode (disable Legacy/CSM (Compatibility Support Module))", warnings[0])

    def test_legacy_build_kept_no_firmware_warning(self):
        self.assertEqual(self.pf.warnings(None, {"BIOS": {"Firmware Type": "BIOS"}}, {"BIOS": {"Firmware Type": "Legacy"}}), [])


class PreflightRunTest(unittest.TestCase):
    def setUp(self):
        self.pf = preflight.Preflight()
        self.pf.windows = True
        self.commands = []
        probe = mock.patch.object(preflight.firmware_probe, "collect", return_value={"dmar": {"present": False}, "cpu": None})
        probe.start()
        self.addCleanup(probe.stop)

    def runner(self, result):
        def run(command):
            self.commands.append(command)
            return result
        return run

    def test_admin_script_tries_cmdlet_then_cim(self):
        script = self.pf.script(True)
        self.assertLess(script.index("Get-BitLockerVolume -MountPoint $env:SystemDrive"), script.index("Win32_EncryptableVolume"))
        self.assertIn("root/cimv2/Security/MicrosoftVolumeEncryption", script)
        self.assertIn("Where-Object IsSystem", script)
        self.assertIn("GptType -eq '{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}'", script)

    def test_non_admin_script_skips_admin_queries(self):
        script = self.pf.script(False)
        self.assertNotIn("Get-BitLockerVolume", script)
        self.assertNotIn("Win32_EncryptableVolume", script)
        self.assertIn("System.Volume.BitLockerProtection", script)

    def test_script_reads_firmware_facts_without_admin(self):
        script = self.pf.script(False)
        for part in ("Win32_ComputerSystem", "HypervisorPresent", "SMBIOSBIOSVersion", "Win32_BaseBoard", "VirtualizationFirmwareEnabled",
                     "root/Microsoft/Windows/DeviceGuard", "Win32_PnPEntity", "-like 'PCI\\*'", "PNP050[01]", "MSFT0101",
                     "MSFT_PhysicalDisk", "CimInstanceProperties['BusType'].Value", "-Depth 5"):
            self.assertIn(part, script)

    def test_collect_adds_the_probe_readings(self):
        self.pf.is_admin = lambda: False
        self.pf._powershell = self.runner(fake_output({"Virtualization": {"Capable": True, "Enabled": False}}))
        info, _ = self.pf.run()
        self.assertIs(self.pf.info, info)
        self.assertEqual(info["firmware"]["dmar"], {"present": False})
        self.assertFalse(info["firmware"]["virt_enabled"])

    def test_failed_run_clears_the_last_reading(self):
        self.pf.info = {"secure_boot": "on"}
        self.pf._powershell = self.runner(("", "boom", 1))
        self.pf.is_admin = lambda: False
        with self.assertRaises(Exception):
            self.pf.run()
        self.assertIsNone(self.pf.info)

    def test_powershell_conventions(self):
        captured = {}
        with mock.patch.object(preflight.subprocess, "run", side_effect=lambda args, **kwargs: captured.update({"args": args, "kwargs": kwargs}) or subprocess.CompletedProcess(args, 0, "", "")):
            self.assertEqual(self.pf._powershell("Get-Disk"), ("", "", 0))
        self.assertEqual(captured["kwargs"]["timeout"], 30)
        args = captured["args"]
        self.assertTrue(args[0].lower().endswith(os.path.join("system32", "windowspowershell", "v1.0", "powershell.exe")))
        self.assertEqual(args[1:4], ["-NoProfile", "-NonInteractive", "-Command"])
        self.assertTrue(args[4].startswith("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "))

    def test_powershell_timeout_becomes_failure(self):
        with mock.patch.object(preflight.subprocess, "run", side_effect=subprocess.TimeoutExpired("powershell", 30)):
            output = self.pf._powershell("Get-Disk")
        self.assertEqual(output[2], 1)
        self.assertIn("did not answer", output[1])

    def test_collect_uses_admin_state(self):
        self.pf.is_admin = lambda: False
        self.pf._powershell = self.runner(fake_output({"Disk": {"Number": 0, "PartitionStyle": "GPT"}, "Esp": [{"DiskNumber": 0, "Size": 104857600}]}))
        info = self.pf.collect()
        self.assertNotIn("Get-BitLockerVolume", self.commands[0])
        self.assertEqual(info["esp_mb"], 100)
        self.assertEqual(info["bitlocker"], preflight.Preflight.unknown_bitlocker)

    def test_collect_raises_on_failure(self):
        self.pf.is_admin = lambda: True
        self.pf._powershell = self.runner(("", "boom", 1))
        with self.assertRaises(Exception):
            self.pf.collect()

    def test_collect_skipped_off_windows(self):
        self.pf.windows = False
        self.pf._powershell = self.runner(fake_output({}))
        self.assertIsNone(self.pf.collect())
        self.assertEqual(self.commands, [])

    def test_show_prints_warnings_in_red(self):
        self.pf.is_admin = lambda: True
        self.pf._powershell = self.runner(fake_output({
            "BitLocker": {"Source": "Get-BitLockerVolume", "ProtectionStatus": "On", "VolumeStatus": "FullyEncrypted"},
            "Disk": {"Number": 0, "PartitionStyle": "GPT"},
            "Esp": [{"DiskNumber": 0, "Size": 104857600}]
        }))
        with contextlib.redirect_stdout(io.StringIO()) as output:
            warnings = self.pf.show({"BIOS": {"Firmware Type": "UEFI"}}, {"BIOS": {"Firmware Type": "UEFI"}})
        text = output.getvalue()
        self.assertEqual(len(warnings), 2)
        self.assertIn("BitLocker on the system drive: on", text)
        self.assertIn("Windows EFI partition: 100 MB", text)
        self.assertIn("\033[91m- " + preflight.Preflight.bitlocker_warning, text)

    def test_show_never_raises(self):
        def broken(command):
            raise OSError("powershell missing")
        self.pf.is_admin = lambda: False
        self.pf._powershell = broken
        with contextlib.redirect_stdout(io.StringIO()) as output:
            result = self.pf.show({}, {})
        self.assertIsNone(result)
        self.assertIn("preflight could not run: powershell missing", output.getvalue())

    def test_show_bad_json(self):
        self.pf.is_admin = lambda: False
        self.pf._powershell = self.runner(("not json", "", 0))
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.pf.show({}, {})
        self.assertIn("preflight could not run:", output.getvalue())


class BIOSVendorTest(unittest.TestCase):
    def setUp(self):
        self.pf = preflight.Preflight()
        self.pf.windows = True
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_combine_oem_and_core_vendor(self):
        self.assertEqual(self.pf.combine_bios_vendor("LENOVO", ["LENOVO - 1", "Q7CN78WW", "INSYDE Corp. - 73315078"]), "LENOVO (INSYDE Corp.)")

    def test_core_vendor_already_in_manufacturer(self):
        self.assertEqual(self.pf.combine_bios_vendor("American Megatrends Inc.", ["ALASKA - 1072009", "American Megatrends - 5000E"]), "American Megatrends Inc.")

    def test_manufacturer_only(self):
        self.assertEqual(self.pf.combine_bios_vendor("Dell Inc.", "DELL   - 1072009"), "Dell Inc.")
        self.assertIsNone(self.pf.combine_bios_vendor("", []))

    def test_add_bios_vendor_writes_report(self):
        path = os.path.join(self.temp_dir, "Report.json")
        with open(path, "w") as report_file:
            json.dump({"BIOS": {"Firmware Type": "UEFI"}}, report_file)
        self.pf._powershell = lambda command: fake_output({"Manufacturer": "Acer", "BIOSVersion": ["ACRSYS - 1", "INSYDE Corp. - 1"]})
        self.assertTrue(self.pf.add_bios_vendor(path))
        with open(path) as report_file:
            self.assertEqual(json.load(report_file)["BIOS"]["Vendor"], "Acer (INSYDE Corp.)")

    def test_add_bios_vendor_keeps_existing_value(self):
        path = os.path.join(self.temp_dir, "Report.json")
        with open(path, "w") as report_file:
            json.dump({"BIOS": {"Vendor": "Phoenix"}}, report_file)
        self.pf._powershell = lambda command: fake_output({"Manufacturer": "Acer", "BIOSVersion": []})
        self.assertFalse(self.pf.add_bios_vendor(path))

    def test_add_bios_vendor_swallows_errors(self):
        self.assertFalse(self.pf.add_bios_vendor(os.path.join(self.temp_dir, "missing.json")))

    def test_validator_keeps_vendor(self):
        report = load_desktop_report()
        report["BIOS"]["Vendor"] = "Insyde Corp."
        path = os.path.join(self.temp_dir, "Report.json")
        with open(path, "w") as report_file:
            json.dump(report, report_file)
        validator = report_validator.ReportValidator()
        with contextlib.redirect_stdout(io.StringIO()):
            is_valid, errors, warnings, data = validator.validate_report(path)
        self.assertTrue(is_valid)
        self.assertEqual(data["BIOS"]["Vendor"], "Insyde Corp.")
        self.assertFalse(any("Vendor" in warning for warning in warnings))


if __name__ == "__main__":
    unittest.main()
