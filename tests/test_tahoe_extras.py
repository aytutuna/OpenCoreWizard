import contextlib
import copy
import importlib.util
import io
import json
import os
import unittest

import repo_path  # noqa: F401
from repo_path import FIXTURES, REPO_ROOT
from Scripts import bios_checklist
from Scripts import config_prodigy
from Scripts import kext_maestro
from Scripts import utils
from Scripts.datasets import kext_data
from Scripts.datasets import os_data

BROADCOM_NAME = "Broadcom BCM43602 802.11ac Wireless LAN SoC"
COMPANION_PROMPT = "Select Broadcom Wi-Fi option"
WEG_PROMPT = "Disable WhateverGreen for the first boot?"


def fresh_kexts(*checked):
    kexts = copy.deepcopy(kext_data.kexts)
    for kext in kexts:
        kext.checked = kext.required or kext.name in checked
    return kexts


def reset_kexts():
    for kext in kext_data.kexts:
        kext.checked = kext.required


def load_desktop_report():
    with open(os.path.join(FIXTURES, "desktop", "Report.json"), "r", encoding="utf-8") as report_file:
        return json.load(report_file)


def with_broadcom(report, device_id="14E4-43BA"):
    report["Network"][BROADCOM_NAME] = {
        "Bus Type": "PCI",
        "Device ID": device_id,
        "PCI Path": "PciRoot(0x0)/Pci(0x1c,0x0)/Pci(0x0,0x0)",
        "OCLP Compatibility": (os_data.get_latest_darwin_version(include_beta=False), "23.0.0")
    }
    return report


def with_amd_gpu(report, codename="Polaris 20", monitor_on_amd=True):
    gpus = {"AMD Radeon RX 580": {
        "Manufacturer": "AMD",
        "Codename": codename,
        "Device ID": "1002-67DF",
        "Device Type": "Discrete GPU",
        "PCI Path": "PciRoot(0x0)/Pci(0x1,0x0)/Pci(0x0,0x0)",
        "Resizable BAR": "Disabled"
    }}
    gpus.update(report["GPU"])
    report["GPU"] = gpus
    if monitor_on_amd:
        for monitor in report.get("Monitor", {}).values():
            monitor["Connected GPU"] = "AMD Radeon RX 580"
    return report


def select_kexts(report, macos_version, needs_oclp, prompts):
    maestro = kext_maestro.KextMaestro()
    base = {"Select audio kext": "2", "force load": "yes", "Press Enter": ""}
    base.update(prompts)
    utils.Utils.set_headless({"prompts": base, "default": ""})
    with contextlib.redirect_stdout(io.StringIO()) as output:
        result = maestro.select_required_kexts(report, macos_version, needs_oclp, [])
    return maestro, result, output.getvalue()


def checked(name):
    return kext_data.kexts[kext_data.kext_index_by_name[name]].checked


def boot_args_report(manufacturer="Intel"):
    return {
        "CPU": {"Manufacturer": manufacturer, "Processor Name": "Intel(R) Core(TM) i7-10700K CPU", "Codename": "Comet Lake-S"},
        "Motherboard": {"Name": "Test Board", "Platform": "Desktop"},
        "GPU": {},
        "Monitor": {}
    }


def boot_args_config():
    return {"Booter": {"Quirks": {"ResizeAppleGpuBars": -1}}}


class IntelBluetoothBootArgTest(unittest.TestCase):
    def setUp(self):
        self.prodigy = config_prodigy.ConfigProdigy()

    def args(self, macos_version, *names):
        return self.prodigy.boot_args(boot_args_report(), macos_version, False, fresh_kexts(*names), boot_args_config()).split()

    def test_tahoe_with_intel_bluetooth(self):
        self.assertIn("-ibtcompatbeta", self.args("25.0.0", "IntelBluetoothFirmware"))

    def test_tahoe_with_patcher_only(self):
        self.assertIn("-ibtcompatbeta", self.args("25.2.0", "IntelBTPatcher"))

    def test_sequoia_with_intel_bluetooth(self):
        self.assertNotIn("-ibtcompatbeta", self.args("24.6.0", "IntelBluetoothFirmware", "IntelBTPatcher"))

    def test_tahoe_without_intel_bluetooth(self):
        self.assertNotIn("-ibtcompatbeta", self.args("25.0.0"))

    def test_sequoia_without_intel_bluetooth(self):
        self.assertNotIn("-ibtcompatbeta", self.args("24.0.0"))

    def test_added_once(self):
        self.assertEqual(self.args("25.0.0", "IntelBluetoothFirmware", "IntelBTPatcher").count("-ibtcompatbeta"), 1)


class BroadcomCompanionSelectionTest(unittest.TestCase):
    def tearDown(self):
        utils.Utils.set_headless(None)
        reset_kexts()

    def test_intel_offers_companion_and_selects_it(self):
        _, needs_oclp, output = select_kexts(with_broadcom(load_desktop_report()), "25.0.0", True, {COMPANION_PROMPT: "2"})
        self.assertIn(COMPANION_PROMPT, output)
        self.assertIn("AppleBCMWLANCompanion\033[0m (no root patches, requires VT-d enabled in BIOS", output)
        self.assertTrue(checked("AppleBCMWLANCompanion"))
        self.assertFalse(checked("IOSkywalkFamily"))
        self.assertFalse(checked("AirPortBrcmNIC-Tahoe"))
        self.assertFalse(checked("AMFIPass"))
        self.assertFalse(needs_oclp)

    def test_intel_default_keeps_root_patch_path(self):
        _, needs_oclp, output = select_kexts(with_broadcom(load_desktop_report()), "25.0.0", True, {})
        self.assertIn(COMPANION_PROMPT, output)
        self.assertFalse(checked("AppleBCMWLANCompanion"))
        self.assertTrue(checked("IOSkywalkFamily"))
        self.assertTrue(checked("AirPortBrcmNIC-Tahoe"))
        self.assertTrue(needs_oclp)

    def test_amd_hides_companion(self):
        report = with_broadcom(load_desktop_report())
        report["CPU"]["Manufacturer"] = "AMD"
        _, needs_oclp, output = select_kexts(report, "25.0.0", True, {COMPANION_PROMPT: "2"})
        self.assertNotIn(COMPANION_PROMPT, output)
        self.assertNotIn("AppleBCMWLANCompanion", output)
        self.assertFalse(checked("AppleBCMWLANCompanion"))
        self.assertTrue(checked("IOSkywalkFamily"))
        self.assertTrue(needs_oclp)

    def test_unsupported_broadcom_chip_not_offered(self):
        _, needs_oclp, output = select_kexts(with_broadcom(load_desktop_report(), "14E4-43A0"), "25.0.0", True, {COMPANION_PROMPT: "2"})
        self.assertNotIn(COMPANION_PROMPT, output)
        self.assertFalse(checked("AppleBCMWLANCompanion"))
        self.assertTrue(needs_oclp)

    def test_sonoma_not_offered(self):
        _, needs_oclp, output = select_kexts(with_broadcom(load_desktop_report()), "23.0.0", True, {COMPANION_PROMPT: "2"})
        self.assertNotIn(COMPANION_PROMPT, output)
        self.assertTrue(checked("IOSkywalkFamily"))

    def test_other_oclp_device_keeps_needs_oclp(self):
        report = with_broadcom(load_desktop_report())
        report["GPU"]["Intel UHD Graphics 630"]["OCLP Compatibility"] = (os_data.get_latest_darwin_version(include_beta=False), "23.0.0")
        _, needs_oclp, _ = select_kexts(report, "25.0.0", True, {COMPANION_PROMPT: "2"})
        self.assertTrue(checked("AppleBCMWLANCompanion"))
        self.assertTrue(needs_oclp)

    def test_audio_root_patch_keeps_needs_oclp(self):
        _, needs_oclp, _ = select_kexts(with_broadcom(load_desktop_report()), "25.0.0", True, {COMPANION_PROMPT: "2", "Select audio kext": "1"})
        self.assertTrue(checked("AppleBCMWLANCompanion"))
        self.assertTrue(needs_oclp)

    def test_bios_override_only_with_companion(self):
        maestro, _, _ = select_kexts(with_broadcom(load_desktop_report()), "25.0.0", True, {COMPANION_PROMPT: "2"})
        self.assertEqual(maestro.bios_overrides()["VT-d"][0], "enable")
        maestro, _, _ = select_kexts(with_broadcom(load_desktop_report()), "25.0.0", True, {COMPANION_PROMPT: "1"})
        self.assertEqual(maestro.bios_overrides(), {})


class CompanionConfigTest(unittest.TestCase):
    def setUp(self):
        self.prodigy = config_prodigy.ConfigProdigy()

    def test_disable_io_mapper_off_with_companion(self):
        self.assertFalse(self.prodigy.disable_io_mapper(boot_args_report(), fresh_kexts("AppleBCMWLANCompanion")))

    def test_disable_io_mapper_on_without_companion(self):
        self.assertTrue(self.prodigy.disable_io_mapper(boot_args_report(), fresh_kexts()))

    def test_disable_io_mapper_off_with_aquantia(self):
        report = boot_args_report()
        report["Network"] = {"AQC107": {"Device ID": "1D6A-07B1"}}
        self.assertFalse(self.prodigy.disable_io_mapper(report, fresh_kexts()))

    def test_disable_io_mapper_off_on_amd(self):
        self.assertFalse(self.prodigy.disable_io_mapper(boot_args_report("AMD"), fresh_kexts()))

    def test_detectsabotage_boot_arg(self):
        args = self.prodigy.boot_args(boot_args_report(), "25.0.0", False, fresh_kexts("AppleBCMWLANCompanion"), boot_args_config()).split()
        self.assertIn("wlan.pcie.detectsabotage=0", args)
        args = self.prodigy.boot_args(boot_args_report(), "25.0.0", False, fresh_kexts(), boot_args_config()).split()
        self.assertNotIn("wlan.pcie.detectsabotage=0", args)

    def test_firmware_properties(self):
        report = with_broadcom(load_desktop_report())
        properties = self.prodigy.deviceproperties(report, {}, "25.0.0", fresh_kexts("AppleBCMWLANCompanion"))
        wifi = properties["PciRoot(0x0)/Pci(0x1c,0x0)/Pci(0x0,0x0)"]
        self.assertEqual(wifi["bcmc-firmware-path"], "/usr/local/share/firmware/wifi/brcmfmac43602-pcie_7.35.177.61.bin")
        self.assertEqual(len(wifi["bcmc-firmware-hash"]), 32)
        self.assertEqual(wifi["bcmc-srom-slide"], bytes(4))
        properties = self.prodigy.deviceproperties(report, {}, "25.0.0", fresh_kexts())
        self.assertNotIn("bcmc-firmware-path", properties.get("PciRoot(0x0)/Pci(0x1c,0x0)/Pci(0x0,0x0)", {}))


class BIOSOverrideTest(unittest.TestCase):
    def setUp(self):
        self.checklist = bios_checklist.BIOSChecklist()
        self.report = {
            "Motherboard": {"Name": "Test Board", "Platform": "Desktop"},
            "CPU": {"Codename": "Comet Lake-S", "Manufacturer": "Intel", "Processor Name": "Intel(R) Core(TM) i7-10700K CPU"},
            "GPU": {}
        }

    def test_default_disables_vtd(self):
        result = self.checklist.build(self.report)
        self.assertIn("VT-d", result["disable"])
        self.assertNotIn("VT-d", result["enable"])

    def test_override_enables_vtd(self):
        result = self.checklist.build(self.report, {"VT-d": ("enable", "VT-d note")})
        self.assertNotIn("VT-d", result["disable"])
        self.assertIn("VT-d", result["enable"])
        self.assertEqual([note for note in result["notes"] if "VT-d" in note], ["VT-d note"])

    def test_check_bios_requirements_passes_overrides(self):
        spec = importlib.util.spec_from_file_location("opcore_simplify_main", os.path.join(REPO_ROOT, "MacBootsmith.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        ocpe = module.OCPE.__new__(module.OCPE)
        ocpe.bios_checklist = self.checklist
        result = ocpe.check_bios_requirements(self.report, self.report, {"VT-d": ("enable", "VT-d note")})
        self.assertIn("VT-d", result["enable"])
        self.assertNotIn("VT-d", result["disable"])


class BroadcomWiFiConflictTest(unittest.TestCase):
    def test_companion_and_tahoe_kext_share_group(self):
        companion = kext_data.kexts[kext_data.kext_index_by_name["AppleBCMWLANCompanion"]]
        tahoe = kext_data.kexts[kext_data.kext_index_by_name["AirPortBrcmNIC-Tahoe"]]
        self.assertEqual(companion.conflict_group_id, "BroadcomWiFi")
        self.assertEqual(tahoe.conflict_group_id, "BroadcomWiFi")

    def test_no_skywalk_block_with_companion(self):
        prodigy = config_prodigy.ConfigProdigy()
        with_companion = fresh_kexts("AppleBCMWLANCompanion", "IOSkywalkFamily")
        self.assertEqual(prodigy.block_kext_bundle(with_companion), [])
        without = fresh_kexts("IOSkywalkFamily")
        self.assertEqual(len(prodigy.block_kext_bundle(without)), 1)


class WhateverGreenFirstBootPromptTest(unittest.TestCase):
    def tearDown(self):
        utils.Utils.set_headless(None)
        reset_kexts()

    def test_tahoe_amd_gpu_prompts_default_yes(self):
        maestro, _, output = select_kexts(with_amd_gpu(load_desktop_report()), "25.0.0", False, {})
        self.assertIn(WEG_PROMPT, output)
        self.assertTrue(checked("WhateverGreen"))
        self.assertTrue(maestro.whatevergreen_first_boot_off)

    def test_tahoe_amd_gpu_answer_no(self):
        maestro, _, output = select_kexts(with_amd_gpu(load_desktop_report()), "25.0.0", False, {WEG_PROMPT: "no"})
        self.assertIn(WEG_PROMPT, output)
        self.assertFalse(maestro.whatevergreen_first_boot_off)

    def test_sequoia_amd_gpu_no_prompt(self):
        maestro, _, output = select_kexts(with_amd_gpu(load_desktop_report()), "24.0.0", False, {})
        self.assertNotIn(WEG_PROMPT, output)
        self.assertFalse(maestro.whatevergreen_first_boot_off)

    def test_tahoe_amd_gpu_monitor_on_igpu_no_prompt(self):
        maestro, _, output = select_kexts(with_amd_gpu(load_desktop_report(), monitor_on_amd=False), "25.0.0", False, {})
        self.assertNotIn(WEG_PROMPT, output)
        self.assertFalse(maestro.whatevergreen_first_boot_off)

    def test_tahoe_intel_only_no_prompt(self):
        maestro, _, output = select_kexts(load_desktop_report(), "25.0.0", False, {})
        self.assertNotIn(WEG_PROMPT, output)
        self.assertFalse(maestro.whatevergreen_first_boot_off)


class WhateverGreenFirstBootConfigTest(unittest.TestCase):
    def setUp(self):
        self.prodigy = config_prodigy.ConfigProdigy()

    def config(self, boot_args):
        return {
            "Kernel": {
                "Add": [
                    {"BundlePath": "Lilu.kext", "Enabled": True},
                    {"BundlePath": "WhateverGreen.kext", "Enabled": True}
                ],
                "Patch": []
            },
            "NVRAM": {"Add": {"7C436110-AB2A-4BBB-A880-FE41995C9F82": {"boot-args": boot_args}}}
        }

    def weg_entry(self, config):
        return next(entry for entry in config["Kernel"]["Add"] if entry["BundlePath"] == "WhateverGreen.kext")

    def test_disables_whatevergreen_and_adds_agdp_patch(self):
        config = self.config("-v keepsyms=1 agdpmod=pikera alcid=7")
        result = self.prodigy.disable_whatevergreen_for_first_boot(config)
        self.assertEqual(result, {"agdp_patch": True})
        self.assertFalse(self.weg_entry(config)["Enabled"])
        self.assertTrue(config["Kernel"]["Add"][0]["Enabled"])
        boot_args = config["NVRAM"]["Add"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"]["boot-args"].split()
        self.assertNotIn("agdpmod=pikera", boot_args)
        self.assertEqual(boot_args, ["-v", "keepsyms=1", "alcid=7"])
        self.assertEqual(len(config["Kernel"]["Patch"]), 1)
        patch = config["Kernel"]["Patch"][0]
        self.assertEqual(patch["Identifier"], "com.apple.driver.AppleGraphicsDevicePolicy")
        self.assertEqual(patch["Comment"], "AGDP board-id patch (pikera) for first boot without WhateverGreen")
        self.assertEqual(patch["Find"], b"board-id\x00")
        self.assertEqual(patch["Replace"], b"board-ix\x00")
        self.assertEqual((patch["Count"], patch["Enabled"], patch["MinKernel"], patch["MaxKernel"], patch["Base"]), (1, True, "25.0.0", "", ""))
        self.assertEqual((patch["Mask"], patch["ReplaceMask"], patch["Skip"], patch["Limit"], patch["Arch"]), (b"", b"", 0, 0, "Any"))
        self.assertEqual(set(patch), {"Arch", "Base", "Comment", "Count", "Enabled", "Find", "Identifier", "Limit", "Mask", "MaxKernel", "MinKernel", "Replace", "ReplaceMask", "Skip"})

    def test_without_agdpmod_no_patch(self):
        config = self.config("-v keepsyms=1")
        result = self.prodigy.disable_whatevergreen_for_first_boot(config)
        self.assertEqual(result, {"agdp_patch": False})
        self.assertFalse(self.weg_entry(config)["Enabled"])
        self.assertEqual(config["Kernel"]["Patch"], [])

    def test_mode_on_through_first_boot_options(self):
        config = self.config("-v agdpmod=pikera")
        self.assertEqual(self.prodigy.apply_first_boot_options(config, True), {"agdp_patch": True})
        self.assertFalse(self.weg_entry(config)["Enabled"])
        self.assertEqual(len(config["Kernel"]["Patch"]), 1)

    def test_mode_off_leaves_config_alone(self):
        config = self.config("-v agdpmod=pikera")
        self.assertIsNone(self.prodigy.apply_first_boot_options(config, False))
        self.assertTrue(self.weg_entry(config)["Enabled"])
        self.assertIn("agdpmod=pikera", config["NVRAM"]["Add"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"]["boot-args"])
        self.assertEqual(config["Kernel"]["Patch"], [])

    def test_no_whatevergreen_entry(self):
        config = self.config("-v agdpmod=pikera")
        config["Kernel"]["Add"] = config["Kernel"]["Add"][:1]
        self.assertIsNone(self.prodigy.disable_whatevergreen_for_first_boot(config))
        self.assertEqual(config["Kernel"]["Patch"], [])
        self.assertIn("agdpmod=pikera", config["NVRAM"]["Add"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"]["boot-args"])


class OclpDeclinedMessageTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("opcore_simplify_main", os.path.join(REPO_ROOT, "MacBootsmith.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.ocpe = module.OCPE.__new__(module.OCPE)
        self.ocpe.u = utils.Utils()

    def tearDown(self):
        reset_kexts()

    def message(self, report, macos_version, prompts):
        self.ocpe.k, _, _ = select_kexts(report, macos_version, True, prompts)
        return self.ocpe.oclp_declined_message(report, macos_version)

    def test_names_the_device_and_the_newest_native_version(self):
        report = load_desktop_report()
        gpu = report["GPU"]["Intel UHD Graphics 630"]
        gpu["Compatibility"] = ("22.99.99", "17.0.0")
        gpu["OCLP Compatibility"] = ("25.99.99", "23.0.0")
        text = self.message(report, "25.0.0", {})
        self.assertIn("macOS Tahoe 26 needs OpenCore Legacy Patcher", text)
        self.assertIn("Intel UHD Graphics 630 has no driver past macOS Ventura 13.", text)
        self.assertIn("Pick macOS Ventura 13 or older, or answer yes to the root patches.", text)

    def test_companion_card_is_not_blamed(self):
        report = with_broadcom(load_desktop_report())
        report["GPU"]["Intel UHD Graphics 630"]["OCLP Compatibility"] = ("25.99.99", "23.0.0")
        text = self.message(report, "25.0.0", {COMPANION_PROMPT: "2"})
        self.assertNotIn(BROADCOM_NAME, text)
        self.assertIn("Intel UHD Graphics 630", text)

    def test_broadcom_card_lists_its_native_limit(self):
        report = with_broadcom(load_desktop_report())
        report["Network"][BROADCOM_NAME]["Compatibility"] = ("22.99.99", "17.0.0")
        text = self.message(report, "25.0.0", {COMPANION_PROMPT: "1"})
        self.assertIn("{} has no driver past macOS Ventura 13.".format(BROADCOM_NAME), text)

    def test_option_choice_points_at_the_other_option(self):
        text = self.message(load_desktop_report(), "25.0.0", {"Select audio kext": "1"})
        self.assertNotIn("no driver past", text)
        self.assertIn("The AppleALC + OCLP audio choice needs root patches on macOS Tahoe 26; pick AppleALC + SimpleHDA instead.", text)


if __name__ == "__main__":
    unittest.main()
