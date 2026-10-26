import unittest

import repo_path  # noqa: F401
from Scripts import bios_checklist


def report(codename, platform="Desktop", name="Intel(R) Core(TM) CPU", **extra):
    data = {
        "Motherboard": {"Name": "Test Board", "Platform": platform},
        "CPU": {"Codename": codename, "Manufacturer": "Intel", "Processor Name": name},
        "GPU": {},
    }
    data.update(extra)
    return data


class ResolveKeyTest(unittest.TestCase):
    def setUp(self):
        self.checklist = bios_checklist.BIOSChecklist()

    def test_comet_lake_desktop(self):
        self.assertEqual(self.checklist.resolve_key(report("Comet Lake-S")), "intel_desktop_skylake_cometlake")

    def test_haswell_laptop(self):
        self.assertEqual(self.checklist.resolve_key(report("Haswell", platform="Laptop")), "intel_laptop_haswell_broadwell")

    def test_kaby_lake_x(self):
        self.assertEqual(self.checklist.resolve_key(report("Kaby Lake-X", name="Intel(R) Core(TM) i7-7740X")), "intel_hedt_skylake_x_cascade_lake")

    def test_bloomfield_non_xeon(self):
        self.assertEqual(self.checklist.resolve_key(report("Bloomfield", name="Intel(R) Core(TM) i7 CPU 920")), "intel_hedt_nehalem_westmere")

    def test_unknown_codename(self):
        self.assertIsNone(self.checklist.resolve_key(report("Totally Unknown Lake")))


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.checklist = bios_checklist.BIOSChecklist()
        self.common = self.checklist.data["common"]

    def test_unknown_codename_returns_common_lists(self):
        result = self.checklist.build(report("Totally Unknown Lake"))
        self.assertIsNone(result["key"])
        for field in ("disable", "enable", "notes"):
            self.assertEqual(result[field], list(self.common.get(field, [])))
        for field in ("disable", "enable", "notes"):
            for item in self.common.get(field, []):
                self.assertIn(item, result[field])

    def test_secure_boot_disabled_removes_entry(self):
        base = self.checklist.build(report("Comet Lake-S"))
        self.assertTrue(any("Secure Boot" in item for item in base["disable"]))
        result = self.checklist.build(report("Comet Lake-S", BIOS={"Secure Boot": "Disabled"}))
        self.assertFalse(any("Secure Boot" in item for item in result["disable"]))
        self.assertEqual(result["notes"], base["notes"])

    def test_resizable_bar_enabled_removes_above_4g(self):
        base = self.checklist.build(report("Comet Lake-S"))
        self.assertTrue(any("Above 4G Decoding" in item for item in base["enable"]))
        result = self.checklist.build(report("Comet Lake-S", GPU={"GPU": {"Resizable BAR": "Enabled"}}))
        self.assertFalse(any("Above 4G Decoding" in item for item in result["enable"]))
        self.assertEqual(result["notes"], base["notes"])


def firmware(**values):
    data = {
        "machine": {"Manufacturer": "Test", "Model": "Board", "BoardManufacturer": "Test", "BoardProduct": "Board", "BiosManufacturer": "Test", "BiosVersion": "F1", "Hypervisor": False},
        "vmx_capable": True, "virt_enabled": None, "device_guard": [], "devices": [], "disk_bus": [],
        "smt_cores": 0, "dmar": {"present": False}, "ivrs": {"present": False}, "dma_guard": 0, "pf_virt": None,
        "sgx": False, "firmware_type": 2, "cpu": None, "tpm": {"present": False}, "bars": []
    }
    data.update(values)
    return data


def device(device_id, hardware=(), compatible=(), error=0, name="Device"):
    return {"id": device_id, "name": name, "hardware": list(hardware), "compatible": list(compatible), "error": error}


def cpu(vendor="GenuineIntel", **values):
    data = {"vendor": vendor, "hypervisor": False, "vmx": vendor == "GenuineIntel", "svm": vendor == "AuthenticAMD", "hv_root": None, "smt_width": None}
    data.update(values)
    return data


def bars(device_id, ranges, display=False, bridge=False):
    return {"id": device_id, "bridge": bridge, "compatible": ["PCI\\CC_0300"] if display else ["PCI\\CC_0C03"], "ranges": ranges}


DMAR_ON = {"present": True, "drhd": 2, "units": 2, "rmrr": 0}
AMD = report("Zen 2", name="AMD Ryzen 5 3600", BIOS={"Version": "F1"})
AMD["CPU"]["Manufacturer"] = "AMD"


class StateTest(unittest.TestCase):
    def setUp(self):
        self.checklist = bios_checklist.BIOSChecklist()
        self.report = report("Comet Lake-S", BIOS={"Version": "F1"})

    def item(self, setting, want="off", secure_boot="off", hardware_report=None, **values):
        field = "disable" if want == "off" else "enable"
        info = {"secure_boot": secure_boot, "firmware": firmware(**values)}
        state = self.checklist.state({field: [setting]}, info, hardware_report or self.report)
        self.assertFalse(state["other_pc"])
        item = state["items"][0]
        self.assertEqual((item["setting"], item["want"]), (setting, want))
        return item["status"], item["evidence"]

    def test_secure_boot(self):
        self.assertEqual(self.item("Secure Boot", secure_boot="on"), ("change", "Windows reports it is on. OpenCore will not start while it is on."))
        self.assertEqual(self.item("Secure Boot")[0], "ok")
        self.assertEqual(self.item("Secure Boot", secure_boot=None), ("unknown", "Windows could not read it."))

    def test_csm(self):
        self.assertEqual(self.item("Compatibility Support Module (CSM)", firmware_type=1), ("change", "Windows started in Legacy (CSM) mode, so CSM is on."))
        self.assertEqual(self.item("Compatibility Support Module (CSM)", secure_boot="on")[0], "ok")
        self.assertEqual(self.item("Compatibility Support Module (CSM) (or Legacy Support, or Hybrid Boot)")[0], "unknown")
        self.assertEqual(self.item("UEFI mode (disable Legacy/CSM (Compatibility Support Module))", want="on", firmware_type=1)[0], "change")
        self.assertEqual(self.item("UEFI mode (disable Legacy/CSM (Compatibility Support Module))", want="on", secure_boot="on")[0], "ok")

    def test_vtd_is_handled_by_the_build_on_intel(self):
        status, text = self.item("VT-d", dmar=DMAR_ON, dma_guard=1)
        self.assertEqual(status, "ok")
        self.assertTrue(text.startswith("The BIOS gives Windows a VT-d table (called DMAR), and Windows uses it"))
        self.assertIn("DisableIoMapper", text)
        self.assertIn("most likely on", self.item("VT-d", dmar=DMAR_ON)[1])
        self.assertIn("most likely on", self.item("VT-d", dmar=DMAR_ON, device_guard=[2, 5])[1])
        self.assertNotIn("most likely", self.item("VT-d", dmar=DMAR_ON, device_guard=[2, 3])[1])
        self.assertEqual(self.item("VT-d")[0], "ok")
        self.assertIn("broken VT-d table", self.item("VT-d", dmar={"present": True, "drhd": 1, "units": 0, "rmrr": 0})[1])

    def test_vtd_with_aquantia_is_not_called_handled(self):
        aquantia = report("Comet Lake-S", BIOS={"Version": "F1"}, Network={"AQC107": {"Device ID": "1D6A-07B1"}})
        status, text = self.item("VT-d", dmar=DMAR_ON, hardware_report=aquantia)
        self.assertEqual(status, "unknown")
        self.assertIn("Aquantia", text)
        self.assertNotIn("you can leave it", text)

    def test_vtd_wanted_on(self):
        status, text = self.item("VT-d", want="on")
        self.assertEqual((status, text), ("change", "The BIOS gives Windows no VT-d table (DMAR), so VT-d is off."))
        self.assertEqual(self.item("VT-d", want="on", dmar=DMAR_ON)[0], "ok")
        self.assertIn("DisableIoMapperMapping", self.item("VT-d", want="on", dmar=dict(DMAR_ON, rmrr=1))[1])
        self.assertEqual(self.item("VT-d", want="on", dmar=None)[0], "unknown")
        self.assertEqual(self.item("VT-d", want="on", dmar={"present": True, "drhd": 1, "units": 0, "rmrr": 0})[0], "unknown")

    def test_vtd_hidden_by_a_hypervisor_or_opencore(self):
        self.assertEqual(self.item("VT-d", want="on", machine=dict(firmware()["machine"], Hypervisor=True))[0], "unknown")
        self.assertEqual(self.item("VT-d", want="on", machine=dict(firmware()["machine"], BiosManufacturer="Acidanthera"))[0], "unknown")
        self.assertEqual(self.item("VT-d", want="on", dma_guard=1)[0], "unknown")

    def test_amd_iommu(self):
        status, text = self.item("IOMMU", ivrs={"present": True}, hardware_report=AMD)
        self.assertEqual(status, "change")
        self.assertIn("has no workaround for it on AMD", text)
        self.assertNotIn("handles", text)
        self.assertEqual(self.item("IOMMU", hardware_report=AMD), ("ok", "The BIOS gives Windows no AMD IOMMU table (IVRS), so the IOMMU is off."))
        self.assertEqual(self.item("IOMMU", hardware_report=AMD, machine=dict(firmware()["machine"], Hypervisor=True))[0], "unknown")
        self.assertEqual(self.item("IOMMU", hardware_report=AMD, ivrs=None)[0], "unknown")

    def test_vtx_from_cpuid(self):
        setting = "VT-x (Virtualization Support)"
        self.assertEqual(self.item(setting, "on", cpu=cpu(), pf_virt=False), ("change", "Windows reports it is off in the BIOS."))
        self.assertEqual(self.item(setting, "on", cpu=cpu(), pf_virt=True)[0], "ok")
        self.assertEqual(self.item(setting, "on", cpu=cpu(vmx=False), pf_virt=False), ("ok", "This CPU has no VT-x, so there is nothing to set."))
        self.assertIn("Hyper-V", self.item(setting, "on", cpu=cpu(hypervisor=True, hv_root=True), pf_virt=False)[1])
        self.assertEqual(self.item(setting, "on", cpu=cpu(hypervisor=True, hv_root=False))[0], "unknown")
        self.assertEqual(self.item(setting, "on", cpu=cpu(), pf_virt=None, virt_enabled=False)[0], "change")

    def test_vtx_without_cpuid(self):
        self.assertEqual(self.item("VT-x", "on", virt_enabled=False)[0], "change")
        self.assertEqual(self.item("VT-x", "on", virt_enabled=True)[0], "ok")
        self.assertEqual(self.item("VT-x", "on", vmx_capable=False)[0], "ok")
        self.assertEqual(self.item("VT-x", "on", machine=dict(firmware()["machine"], Hypervisor=True))[0], "ok")
        self.assertEqual(self.item("VT-x", "on"), ("unknown", "Windows could not read it."))

    def test_hyper_threading(self):
        self.assertEqual(self.item("Hyper-Threading", "on", smt_cores=8)[0], "ok")
        self.assertEqual(self.item("Hyper-Threading", "on", cpu=cpu(smt_width=2))[0], "change")
        self.assertEqual(self.item("Hyper-Threading", "on", cpu=cpu(smt_width=1)), ("ok", "This CPU has no Hyper-Threading, so there is nothing to set."))
        self.assertEqual(self.item("Hyper-Threading", "on", cpu=cpu(smt_width=None))[0], "unknown")
        self.assertEqual(self.item("Hyper-Threading", "on", cpu=cpu(hypervisor=True, smt_width=2))[0], "unknown")
        self.assertEqual(self.item("Hyper-Threading", "on", cpu=cpu("AuthenticAMD", smt_width=None), hardware_report=AMD)[0], "unknown")
        self.assertEqual(self.item("Hyper-Threading", "on", smt_cores=None)[0], "unknown")

    def test_sgx(self):
        self.assertEqual(self.item("Intel SGX", sgx=True), ("change", "Windows reports SGX is active."))
        self.assertEqual(self.item("Intel SGX", sgx=False)[0], "ok")
        self.assertIn("Software Controlled", self.item("Intel SGX", sgx=False)[1])
        self.assertEqual(self.item("Intel SGX", sgx=None)[0], "unknown")
        arrow_lake = report("Arrow Lake-HX", BIOS={"Version": "F1"})
        self.assertEqual(self.item("Intel SGX", sgx=None, hardware_report=arrow_lake), ("ok", "This CPU has no SGX, so the option will not be there."))
        xeon = report("Raptor Lake-E", name="Intel(R) Xeon(R) E-2488", BIOS={"Version": "F1"})
        self.assertEqual(self.item("Intel SGX", sgx=True, hardware_report=xeon)[0], "change")

    def test_platform_trust(self):
        status, text = self.item("Intel Platform Trust", tpm={"present": True, "version": 2, "maker": "INTC"})
        self.assertEqual(status, "ok")
        self.assertIn("BitLocker recovery key", text)
        self.assertEqual(self.item("Intel Platform Trust"), ("ok", "Windows sees no TPM, so Intel PTT is off."))
        self.assertEqual(self.item("Intel Platform Trust", tpm={"present": True, "version": 2, "maker": "IFX"})[1], "The TPM is a separate chip (IFX), not Intel PTT.")
        self.assertEqual(self.item("Intel Platform Trust", tpm={"present": True, "version": 1, "maker": None})[0], "ok")
        self.assertEqual(self.item("Intel Platform Trust", tpm={"present": True, "version": 2, "maker": None})[0], "unknown")

    def test_platform_trust_from_devices(self):
        intel = device("ACPI\\INTC7001\\0", ["ACPI\\VEN_INTC&DEV_7001", "ACPI\\INTC7001"], ["ACPI\\MSFT0101"])
        self.assertIn("BitLocker", self.item("Intel Platform Trust", tpm=None, devices=[intel])[1])
        self.assertEqual(self.item("Intel Platform Trust", tpm=None, devices=[device("ACPI\\MSFT0101\\0", ["ACPI\\MSFT0101"], ["ACPI\\MSFT0101"])])[0], "unknown")
        self.assertEqual(self.item("Intel Platform Trust", tpm=None)[0], "ok")
        self.assertEqual(self.item("Intel Platform Trust", tpm=None, devices=None)[0], "unknown")

    def test_serial_port(self):
        port = device("ACPI\\PNP0501\\0", ["ACPI\\PNP0501"])
        self.assertEqual(self.item("Serial/COM Port", devices=[port]), ("change", "Windows sees a COM (serial) port from the firmware, so it is on."))
        self.assertEqual(self.item("Serial/COM Port", devices=[dict(port, error=22)])[0], "change")
        self.assertEqual(self.item("Serial/COM Port", devices=[dict(port, error=29)])[0], "ok")
        self.assertEqual(self.item("Serial/COM Port", devices=[dict(port, error=10)])[0], "unknown")
        renamed = device("ACPI\\VEN_PNP&DEV_0501\\1", ["ACPI\\VEN_PNP&DEV_0501", "ACPI\\PNP0501"])
        self.assertEqual(self.item("Serial/COM Port", devices=[renamed])[0], "change")
        usb = device("USB\\VID_043E&PID_9A39&MI_01\\1", ["USB\\VID_043E&PID_9A39&MI_01"], ["USB\\CLASS_02"], name="USB Serial Device (COM3)")
        self.assertEqual(self.item("Serial/COM Port", devices=[usb])[0], "ok")
        self.assertEqual(self.item("Serial/COM Port", devices=None)[0], "unknown")

    def test_parallel_port(self):
        self.assertEqual(self.item("Parallel Port", devices=[device("ACPI\\PNP0401\\0", ["ACPI\\PNP0401"])])[0], "change")
        self.assertIn("nothing to change", self.item("Parallel Port")[1])

    def test_thunderbolt(self):
        router = device("PCI\\VEN_8086&DEV_7EC2&SUBSYS_3D6C17AA&REV_10&USB4_MS_CM\\3", ["PCI\\VEN_8086&DEV_7EC2&CC_0C0340"], ["PCI\\CC_0C0340"])
        status, text = self.item("Thunderbolt (if available)", devices=[router])
        self.assertEqual(status, "change")
        self.assertIn("first install only", text)
        self.assertEqual(self.item("Thunderbolt", devices=[device("PCI\\VEN_8086&DEV_15EB&SUBSYS_00000000\\1")])[0], "change")
        self.assertEqual(self.item("Thunderbolt")[0], "unknown")

    def test_sata_mode(self):
        def controller(class_code, vendor="8086"):
            return device("PCI\\VEN_{}&DEV_A352&SUBSYS_00000000\\3".format(vendor), [], ["PCI\\VEN_{}&DEV_A352".format(vendor), "PCI\\VEN_{}&CC_{}01".format(vendor, class_code), "PCI\\CC_{}01".format(class_code)])
        self.assertEqual(self.item("SATA Mode: AHCI", "on", devices=[controller("0106")]), ("ok", "The SATA controller is in AHCI mode."))
        status, text = self.item("SATA Mode: AHCI", "on", devices=[controller("0106"), controller("0104")])
        self.assertEqual(status, "change")
        self.assertIn("bcdedit /set {current} safeboot minimal", text)
        self.assertIn("IDE mode", self.item("SATA Mode: AHCI", "on", devices=[controller("0101")])[1])
        ider = device("PCI\\VEN_8086&DEV_8C3C&SUBSYS_05A41028\\3", [], ["PCI\\VEN_8086&DEV_8C3C", "PCI\\VEN_8086&CC_010185", "PCI\\VEN_8086&CC_0101", "PCI\\CC_010185", "PCI\\CC_0101"])
        self.assertEqual(self.item("SATA Mode: AHCI", "on", devices=[controller("0106"), ider])[0], "ok")
        self.assertEqual(self.item("SATA Mode: AHCI", "on", devices=[ider])[0], "ok")
        pata = device("PCI\\VEN_8086&DEV_2850&SUBSYS_00000000\\3", [], ["PCI\\VEN_8086&DEV_2850", "PCI\\VEN_8086&CC_01018A", "PCI\\CC_01018A"])
        self.assertEqual(self.item("SATA Mode: AHCI", "on", devices=[pata, controller("0106")])[0], "ok")
        self.assertIn("IDE mode", self.item("SATA Mode: AHCI", "on", devices=[pata, controller("0101")])[1])
        self.assertEqual(self.item("SATA Mode: AHCI", "on", devices=[controller("0104", vendor="1B21")])[0], "ok")
        self.assertEqual(self.item("SATA Mode: AHCI", "on", devices=[controller("0104", vendor="1022")])[0], "change")
        vmd = device("PCI\\VEN_8086&DEV_A77F&SUBSYS_00000000\\3", name="Intel RST VMD Controller A77F")
        self.assertIn("VMD", self.item("SATA Mode: AHCI", "on", devices=[vmd])[1])
        self.assertEqual(self.item("SATA Mode: AHCI", "on", devices=[device("PCI\\VEN_8086&DEV_FFFF\\1", name="Volume Management Device NVMe RAID Controller")])[0], "change")
        nvme = device("PCI\\VEN_1C5C&DEV_1959&SUBSYS_00000000\\4", [], ["PCI\\VEN_1C5C&CC_010802", "PCI\\CC_010802"])
        self.assertIn("no SATA controller", self.item("SATA Mode: AHCI", "on", devices=[nvme])[1])
        self.assertEqual(self.item("SATA Mode: AHCI", "on", devices=[nvme], disk_bus=[8])[0], "unknown")

    def test_above_4g(self):
        setting = "Above 4G Decoding"
        gpu = bars("PCI\\VEN_10DE&DEV_2F58\\4", [(0x80000000, 0x83FFFFFF, 0x83), (0x6000000000, 0x63FFFFFFFF, 0x87)], display=True, bridge=True)
        self.assertIn("The graphics card uses memory addresses above 4 GB", self.item(setting, "on", bars=[gpu])[1])
        self.assertEqual(self.item(setting, "on", bars=[bars("PCI\\VEN_1B21&DEV_2142\\1", [(0x4000000000, 0x4000007FFF, 0x83)], bridge=True)])[0], "ok")
        soc = bars("PCI\\VEN_8086&DEV_7F4D\\3", [(0x5FDFFFB000, 0x5FDFFFBFFF, 0x83)])
        self.assertIn("probably on", self.item(setting, "on", bars=[soc])[1])
        window = bars("PCI\\VEN_8086&DEV_AE4D\\3", [(0x6000000000, 0x6401FFFFFF, 0x47)])
        self.assertEqual(self.item(setting, "on", bars=[window]), ("unknown", "No device uses memory addresses above 4 GB. That does not prove the setting is off, so check Above 4G Decoding in the BIOS."))
        self.assertEqual(self.item(setting, "on", bars=None)[0], "unknown")

    def test_resizable_bar(self):
        setting = "Resizable BAR/Smart Access Memory"
        big = bars("PCI\\VEN_10DE&DEV_2F58\\4", [(0x6000000000, 0x63FFFFFFFF, 0x87)], display=True, bridge=True)
        status, text = self.item(setting, bars=[big])
        self.assertEqual(status, "change")
        self.assertIn("16 GB memory window", text)
        small = bars("PCI\\VEN_1002&DEV_73BF\\4", [(0xE0000000, 0xEFFFFFFF, 0x87)], display=True, bridge=True)
        self.assertEqual(self.item(setting, bars=[small])[0], "ok")
        igpu = bars("PCI\\VEN_8086&DEV_9BC8\\3", [(0x4000000000, 0x400FFFFFFF, 0x87)], display=True)
        self.assertEqual(self.item(setting, bars=[igpu]), ("ok", "There is no separate graphics card, so this setting does not matter."))

    def test_os_type(self):
        asus = dict(firmware()["machine"], BoardManufacturer="ASUSTeK COMPUTER INC.")
        status, text = self.item("OS type: Windows 8.1/10 UEFI Mode", "on", secure_boot="on", machine=asus)
        self.assertEqual(status, "ok")
        self.assertIn("Other OS", text)
        self.assertEqual(self.item("OS type: Windows 8.1/10 UEFI Mode", "on", machine=asus)[0], "unknown")
        self.assertIn("ASUS boards", self.item("OS type: Windows 8.1/10 UEFI Mode", "on", secure_boot="on")[1])

    def test_settings_windows_cannot_see(self):
        for setting, want in (("Fast Boot", "off"), ("Execute Disable Bit", "on"), ("DVMT Pre-Allocated (iGPU Memory): 64MB or higher", "on"), ("Something New", "off")):
            self.assertEqual(self.item(setting, want)[0], "unknown")
        self.assertIn("ReleaseUsbOwnership", self.item("EHCI/XHCI Hand-off", "on")[1])
        self.assertEqual(self.item("EHCI/XHCI Hand-off", "on")[0], "ok")
        status, text = self.item("CFG Lock (MSR 0xE2 write protection)")
        self.assertEqual(status, "ok")
        self.assertIn("AppleXcpmCfgLock", text)
        self.assertIn("AppleCpuPmCfgLock", self.item("CFG Lock", hardware_report=report("Ivy Bridge", BIOS={"Version": "F1"}))[1])
        self.assertEqual(self.item("CFG Lock", hardware_report=AMD)[0], "unknown")

    def test_report_from_another_pc(self):
        info = {"secure_boot": "on", "firmware": firmware()}
        bios = {"disable": ["Secure Boot"], "enable": []}
        self.assertEqual(self.checklist.state(bios, info, report("Comet Lake-S", BIOS={"Version": "F2"})), {"other_pc": True, "items": []})
        other_board = report("Comet Lake-S", BIOS={"Version": "F1"})
        other_board["Motherboard"]["Name"] = "ASUS PRIME Z490-A"
        self.assertTrue(self.checklist.state(bios, info, other_board)["other_pc"])
        self.assertFalse(self.checklist.state(bios, {"secure_boot": "on", "firmware": firmware(machine=None)}, other_board)["other_pc"])
        self.assertEqual(bios_checklist.OTHER_PC, "This hardware report is from another PC, so the app cannot check that PC's BIOS from here. Run the app on that PC to check it, or go through the list below in its BIOS yourself.")

    def test_no_state_without_readings(self):
        self.assertIsNone(self.checklist.state({"disable": ["Secure Boot"]}, None, self.report))
        self.assertIsNone(self.checklist.state({"disable": ["Secure Boot"]}, {"secure_boot": "on"}, self.report))

    def test_groups(self):
        info = {"secure_boot": "on", "firmware": firmware(cpu=cpu(), pf_virt=True)}
        state = self.checklist.state({"disable": ["Secure Boot", "Fast Boot"], "enable": ["VT-x"]}, info, self.report)
        self.assertEqual(bios_checklist.state_groups(state), [
            ("Change these in the BIOS before you boot from the stick", ["Secure Boot: should be Off. Windows reports it is on. OpenCore will not start while it is on."]),
            ("Nothing to do (already right, not on this PC, or handled by this build)", ["VT-x: should be On. Windows reports it is on."]),
            ("Check these yourself in the BIOS (the app could not tell from Windows)", ["Fast Boot: should be Off. Windows cannot see this setting. Find Fast Boot in the BIOS and turn it off."])
        ])
        self.assertEqual(bios_checklist.item_line({"setting": "SATA Mode: AHCI", "want": "on", "evidence": "The SATA controller is in AHCI mode."}),
                         "SATA Mode: should be AHCI. The SATA controller is in AHCI mode.")

    def test_readings(self):
        info = {"secure_boot": "on", "firmware": firmware(cpu=cpu(), pf_virt=False, dmar=DMAR_ON)}
        rows = dict(self.checklist.readings(info, self.report))
        self.assertEqual(rows["VT-x (virtualization)"], "Windows reports it is off in the BIOS.")
        self.assertIn("Intel SGX (secure enclaves)", rows)
        amd_rows = dict(self.checklist.readings({"secure_boot": "on", "firmware": firmware(cpu=cpu("AuthenticAMD"))}, AMD))
        self.assertIn("IOMMU (device memory protection)", amd_rows)
        self.assertNotIn("Intel SGX (secure enclaves)", amd_rows)
        self.assertEqual(self.checklist.readings({"secure_boot": "on"}), [])


if __name__ == "__main__":
    unittest.main()
