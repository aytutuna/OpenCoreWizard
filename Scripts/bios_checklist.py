from Scripts.datasets import bios_settings
from Scripts.datasets import cpu_data
from Scripts.datasets import pci_data
import re

FOUR_GB = 0x100000000
OTHER_PC = "This hardware report is from another PC, so the app cannot check that PC's BIOS from here. Run the app on that PC to check it, or go through the list below in its BIOS yourself."
GROUPS = (
    ("change", "Change these in the BIOS before you boot from the stick"),
    ("ok", "Nothing to do (already right, not on this PC, or handled by this build)"),
    ("unknown", "Check these yourself in the BIOS (the app could not tell from Windows)")
)
RULES = (
    ("Secure Boot", "secure_boot"),
    ("CSM", "csm"),
    ("VT-d", "vtd"),
    ("IOMMU", "iommu"),
    ("VT-x", "vtx"),
    ("Hyper-Threading", "ht"),
    ("SGX", "sgx"),
    ("Platform Trust", "ptt"),
    ("Serial", "serial"),
    ("Parallel", "parallel"),
    ("Thunderbolt", "thunderbolt"),
    ("SATA Mode", "sata"),
    ("Above 4G", "above4g"),
    ("Resizable BAR", "rebar"),
    ("OS type", "os_type"),
    ("Hand-off", "handoff"),
    ("CFG Lock", "cfg_lock"),
    ("Fast Boot", "fast_boot"),
    ("Execute Disable", "xd"),
    ("DVMT", "dvmt")
)
FIXED = {
    "fast_boot": "Windows cannot see this setting. Find Fast Boot in the BIOS and turn it off.",
    "xd": "Windows always switches this on for itself while it starts, so it cannot tell what the BIOS says. macOS needs it on.",
    "dvmt": "This is how much memory the BIOS gives the graphics built into the processor. Windows cannot read it, because the Intel graphics driver reports a made-up number. Set it if your BIOS has the option. If it does not, this build patches the graphics memory size on the chips that need it."
}
SAFE_MODE = ("Windows on this PC will not start after this change unless you prepare it first. 1. Open Command Prompt as administrator and run bcdedit /set {current} safeboot minimal. "
             "2. Restart, open the BIOS and change the setting. 3. Let Windows start; it starts in Safe Mode. 4. In Command Prompt as administrator, run bcdedit /deletevalue {current} safeboot and restart.")
CLASS_CODE = re.compile(r"^PCI\\(?:VEN_[0-9A-F]{4}&)?CC_([0-9A-F]{4})([0-9A-F]{2})?")
VMD_IDS = re.compile(r"^PCI\\VEN_8086&DEV_(9A0B|4C3D|467F|A77F|7D0B|AD0B|B60B|B06F|B07F|D70B|D73B|201D|28C0|28C1|09AB)&")
THUNDERBOLT_IDS = re.compile(r"^PCI\\VEN_8086&DEV_(1547|1566|1568|156A|156C|1575|1577|157D|15BF|15D2|15D9|15DC|15DD|15DE|15E8|15EB|8A17|8A0D|1134|1137)&")

class BIOSChecklist:
    def __init__(self):
        self.data = bios_settings.data

    def is_intel_hedt_cpu(self, processor_name, cpu_codename):
        if cpu_codename in cpu_data.IntelCPUGenerations[45:66]:
            return cpu_codename.endswith(("-X", "-P", "-W", "-E", "-EP", "-EX"))

        if cpu_codename in cpu_data.IntelCPUGenerations[66:]:
            return "Xeon" in processor_name

        return False

    def resolve_key(self, hardware_report):
        cpu = hardware_report.get("CPU", {})
        motherboard = hardware_report.get("Motherboard", {})
        codename = cpu.get("Codename", "")
        processor_name = cpu.get("Processor Name", "")
        platform = "Laptop" if "NUC" in motherboard.get("Name", "") else motherboard.get("Platform", "")
        platform = "Laptop" if platform == "NUC" else platform

        if "AMD" in cpu.get("Manufacturer", "") or "AMD" in processor_name:
            prefix = "amd"
        elif self.is_intel_hedt_cpu(processor_name, codename):
            prefix = "intel_hedt"
        elif "Laptop" in platform:
            prefix = "intel_laptop"
        else:
            prefix = "intel_desktop"

        if not codename:
            return None

        keys = [key for key in self.data if key != "common"]

        def in_family(key):
            return key == prefix or key.startswith(prefix + "_")

        def pick(matches):
            if not matches:
                return None
            for key in matches:
                if in_family(key):
                    return key
            return matches[0]

        exact = [key for key in keys if codename in self.data[key].get("codenames", [])]
        if exact:
            return pick(exact)

        if prefix == "intel_hedt" or codename.endswith("-X"):
            keys = [key for key in keys if key.startswith("intel_hedt")]

        partial = [key for key in keys if any(name in codename or codename in name for name in self.data[key].get("codenames", []))]
        return pick(partial)

    def merge(self, field, key):
        merged = []
        entries = [self.data.get("common", {})]
        if key:
            entries.append(self.data[key])
        for entry in entries:
            for item in entry.get(field, []):
                if item not in merged:
                    merged.append(item)
        return merged

    def apply_overrides(self, disable, enable, notes, overrides):
        for setting, (state, note) in (overrides or {}).items():
            disable = [item for item in disable if setting not in item]
            enable = [item for item in enable if setting not in item]
            notes = [item for item in notes if setting not in item]
            (enable if state == "enable" else disable).append(setting)
            if note:
                notes.append(note)
        return disable, enable, notes

    def build(self, hardware_report, overrides=None):
        key = self.resolve_key(hardware_report)
        disable = self.merge("disable", key)
        enable = self.merge("enable", key)
        notes = self.merge("notes", key)

        bios = hardware_report.get("BIOS", {})
        if bios.get("Secure Boot") == "Disabled":
            disable = [item for item in disable if "Secure Boot" not in item]
        if any(gpu.get("Resizable BAR") == "Enabled" for gpu in hardware_report.get("GPU", {}).values()):
            enable = [item for item in enable if "Above 4G Decoding" not in item]

        disable, enable, notes = self.apply_overrides(disable, enable, notes, overrides)

        return {"key": key, "disable": disable, "enable": enable, "notes": notes}

    def same_pc(self, hardware_report, machine):
        if not machine:
            return True
        version = str(hardware_report.get("BIOS", {}).get("Version") or "").strip().upper()
        live_version = str(machine.get("BiosVersion") or "").strip().upper()
        if version and version != "UNKNOWN" and live_version and version != live_version:
            return False
        name = str(hardware_report.get("Motherboard", {}).get("Name") or "").upper()
        models = [str(machine.get(key) or "").strip().upper() for key in ("Model", "BoardProduct")]
        models = [model for model in models if model]
        if name and name != "UNKNOWN" and models and not any(model in name for model in models):
            return False
        return True

    def context(self, hardware_report, firmware, info):
        cpu = hardware_report.get("CPU", {})
        vendor = cpu.get("Manufacturer", "") + cpu.get("Processor Name", "")
        if not vendor:
            vendor = (firmware.get("cpu") or {}).get("vendor", "")
        return {
            "vendor": "AMD" if "AMD" in vendor else "Intel" if "Intel" in vendor else None,
            "codename": cpu.get("Codename", ""),
            "processor": cpu.get("Processor Name", ""),
            "aquantia": any(device.get("Device ID") in pci_data.AquantiaAqtionIDs for device in hardware_report.get("Network", {}).values()),
            "secure_boot": info.get("secure_boot")
        }

    def masked(self, firmware):
        machine = firmware.get("machine") or {}
        vendors = "{} {}".format(machine.get("Manufacturer") or "", machine.get("BiosManufacturer") or "").upper()
        return machine.get("Hypervisor") is True or "APPLE" in vendors or "ACIDANTHERA" in vendors

    def dma_protection(self, firmware):
        return firmware.get("dma_guard") == 1 or 3 in (firmware.get("device_guard") or [])

    def read_secure_boot(self, firmware, context):
        return {
            "on": ("on", "Windows reports it is on. OpenCore will not start while it is on."),
            "off": ("off", "Windows reports it is off.")
        }.get(context["secure_boot"], (None, "Windows could not read it."))

    def read_csm(self, firmware, context):
        kind = firmware.get("firmware_type")
        if kind == 1:
            return "on", "Windows started in Legacy (CSM) mode, so CSM is on."
        if kind == 2 and context["secure_boot"] == "on":
            return "off", "Secure Boot is on, and boards only allow that with CSM off."
        if kind == 2:
            return None, "Windows started in UEFI mode (the modern way to start), but CSM (support for the old way) can still be on as well. Check it in the BIOS and turn it off."
        return None, "Windows could not tell how it started."

    def read_vtd(self, firmware, context):
        dmar = firmware.get("dmar")
        if dmar is None:
            return None, "Windows could not read the BIOS tables, so the app cannot tell."
        if dmar["present"] and dmar["units"]:
            if self.dma_protection(firmware):
                return "on", "The BIOS gives Windows a VT-d table (called DMAR), and Windows uses it to block unsafe memory access, so VT-d is on."
            return "on", "The BIOS gives Windows a VT-d table (called DMAR), so VT-d is most likely on."
        if dmar["present"]:
            return None, "The BIOS gives Windows a broken VT-d table (DMAR) with no working VT-d unit in it, so the app cannot tell whether VT-d is on. Check it in the BIOS."
        if self.masked(firmware):
            return None, "Windows sees no VT-d table (DMAR). But Windows runs on a hypervisor (Hyper-V, used by WSL2 and Memory Integrity, or a virtual machine) or was started through OpenCore, and both can hide the table. Check VT-d in the BIOS."
        if self.dma_protection(firmware):
            return None, "Windows sees no VT-d table (DMAR) but still reports memory protection. These do not match, so the app cannot tell. Check VT-d in the BIOS."
        return "off", "The BIOS gives Windows no VT-d table (DMAR), so VT-d is off."

    def read_iommu(self, firmware, context):
        ivrs = firmware.get("ivrs")
        if ivrs is None:
            return None, "Windows could not read the BIOS tables, so the app cannot tell."
        if ivrs["present"]:
            if self.dma_protection(firmware):
                return "on", "The BIOS gives Windows an AMD IOMMU table (called IVRS), and Windows uses it to block unsafe memory access, so the IOMMU is on."
            return "on", "The BIOS gives Windows an AMD IOMMU table (called IVRS), so the IOMMU is most likely on."
        if self.masked(firmware) or self.dma_protection(firmware):
            return None, "Windows sees no AMD IOMMU table (IVRS), but a hypervisor, OpenCore or Windows memory protection may be hiding it, so the app cannot tell. Check IOMMU in the BIOS."
        return "off", "The BIOS gives Windows no AMD IOMMU table (IVRS), so the IOMMU is off."

    def read_vtx(self, firmware, context):
        cpu = firmware.get("cpu")
        name = "SVM" if context["vendor"] == "AMD" else "VT-x"
        if cpu:
            if cpu.get("hypervisor"):
                if cpu.get("hv_root"):
                    return "on", "Windows runs Hyper-V (used by WSL2, Memory Integrity and similar), which only works with {} on.".format(name)
                return None, "Windows itself runs inside a virtual machine, so it cannot see the real BIOS."
            if not (cpu.get("svm") if cpu.get("vendor") == "AuthenticAMD" else cpu.get("vmx")):
                return "none", "This CPU has no {}, so there is nothing to set.".format(name)
            enabled = firmware.get("pf_virt")
        else:
            if (firmware.get("machine") or {}).get("Hypervisor") is True:
                return "on", "Windows runs a hypervisor, which needs {} on.".format(name)
            if firmware.get("vmx_capable") is False:
                return "none", "This CPU has no {}, so there is nothing to set.".format(name)
            enabled = None
        if enabled is None:
            enabled = firmware.get("virt_enabled")
        if enabled is True:
            return "on", "Windows reports it is on."
        if enabled is False:
            return "off", "Windows reports it is off in the BIOS."
        return None, "Windows could not read it."

    def read_ht(self, firmware, context):
        cpu = firmware.get("cpu") or {}
        if firmware.get("smt_cores"):
            return "on", "Windows sees two threads on some CPU cores, so it is on."
        if firmware.get("smt_cores") is None:
            return None, "Windows could not read the CPU layout."
        if cpu.get("hypervisor"):
            return None, "Windows sees one thread per core, but it runs under a hypervisor, so it cannot tell why."
        if cpu.get("vendor") == "AuthenticAMD":
            return None, "Windows sees one thread per core. On AMD it cannot tell whether SMT (AMD's name for Hyper-Threading) is off in the BIOS or the CPU has none. Check SMT in the BIOS."
        width = cpu.get("smt_width")
        if width and width >= 2:
            return "off", "The CPU supports it, but Windows sees only one thread per core, so it is off in the BIOS."
        if width == 1:
            return "none", "This CPU has no Hyper-Threading, so there is nothing to set."
        return None, "Windows sees one thread per core and could not tell whether the CPU supports more."

    def read_sgx(self, firmware, context):
        generations = cpu_data.IntelCPUGenerations
        newer = context["codename"] in generations[:generations.index("Ice Lake-U")]
        if context["vendor"] == "AMD" or (newer and not context["codename"].endswith("-E") and "Xeon" not in context["processor"]):
            return "none", "This CPU has no SGX, so the option will not be there."
        active = firmware.get("sgx")
        if active is True:
            return "on", "Windows reports SGX is active."
        if active is False:
            return "off", "SGX is not active, which is what macOS needs. In the BIOS it may show as Disabled, or as Software Controlled (it stays off until a program turns it on). Either is fine."
        return None, "Windows could not tell (this needs Windows 10 or newer)."

    def read_ptt(self, firmware, context):
        tpm = firmware.get("tpm")
        if tpm is None:
            devices = firmware.get("devices")
            if devices is None:
                return None, "Windows could not read the TPM."
            chips = [device for device in devices if any(item in ("ACPI\\MSFT0101", "ACPI\\PNP0C31") for item in device["compatible"])]
            if not chips:
                return "off", "Windows sees no TPM, so Intel PTT is off."
            if any(item.startswith("ACPI\\INTC") for device in chips for item in device["hardware"]):
                return "on", "Intel PTT is on (Windows uses it as its TPM)."
            return None, "Windows sees a TPM but cannot tell who made it."
        if not tpm.get("present"):
            return "off", "Windows sees no TPM, so Intel PTT is off."
        if tpm.get("version") == 1:
            return "off", "The TPM is a separate TPM 1.2 chip, not Intel PTT."
        maker = tpm.get("maker")
        if maker == "INTC":
            return "on", "Intel PTT is on (Windows uses it as its TPM)."
        if maker == "AMD":
            return "off", "The TPM is AMD's firmware TPM, not Intel PTT."
        if maker == "MSFT":
            return "off", "The TPM is Microsoft Pluton, not Intel PTT."
        if maker:
            return "off", "The TPM is a separate chip ({}), not Intel PTT.".format(maker)
        return None, "Windows sees a TPM 2.0 but could not ask who made it."

    def read_port(self, firmware, ids, label):
        devices = firmware.get("devices")
        if devices is None:
            return None, "Windows could not list the devices."
        matches = [device for device in devices if any(device["id"].startswith("ACPI\\{}\\".format(code)) or "ACPI\\" + code in device["hardware"] for code in ids)]
        if any(device["error"] in (0, 22) for device in matches):
            return "on", "Windows sees a {} from the firmware, so it is on.".format(label)
        if matches and all(device["error"] == 29 for device in matches):
            # Code 29: the firmware did not give the device its resources
            return "off", "Windows sees the {}, but the firmware has turned it off.".format(label)
        if matches:
            return None, "Windows sees a {} with a problem, so it cannot tell.".format(label)
        return "none", "The firmware shows no {} to Windows (it is off, or the board has none), so there is nothing to change.".format(label)

    def read_serial(self, firmware, context):
        return self.read_port(firmware, ("PNP0500", "PNP0501"), "COM (serial) port")

    def read_parallel(self, firmware, context):
        return self.read_port(firmware, ("PNP0400", "PNP0401"), "printer (parallel) port")

    def read_thunderbolt(self, firmware, context):
        devices = firmware.get("devices")
        if devices is None:
            return None, "Windows could not list the devices."
        # Windows lists the USB4 class as PCI\VEN_8086&DEV_7EC2&CC_0C0340 as well as PCI\CC_0C0340
        if any(THUNDERBOLT_IDS.match(device["id"]) or any(item.endswith("CC_0C0340") for item in device["hardware"] + device["compatible"]) for device in devices):
            return "on", "Windows sees a Thunderbolt or USB4 controller, so it is on."
        return None, "Windows sees no Thunderbolt controller. If your PC has Thunderbolt ports, check the BIOS."

    def read_sata(self, firmware, context):
        devices = firmware.get("devices")
        if devices is None:
            return None, "Windows could not list the devices."
        vmd = [device for device in devices if VMD_IDS.match(device["id"]) or "VMD" in device["name"].upper() or "VOLUME MANAGEMENT" in device["name"].upper()]
        if vmd:
            return "off", "Intel VMD is on: Windows sees an Intel RST VMD controller, which puts the drives behind Intel's own storage driver. macOS cannot see drives behind it. In the BIOS, set SATA Mode to AHCI and also turn off the separate VMD option."
        classes = set()
        for device in devices:
            if device in vmd or not device["id"].startswith(("PCI\\VEN_8086&", "PCI\\VEN_1022&")):
                continue
            for item in device["compatible"]:
                match = CLASS_CODE.match(item)
                if match:
                    # Prog-if 85 is the AMT IDE-redirection function, not a disk controller
                    if match.group(2) != "85":
                        classes.add(match.group(1))
                    break
        # Older ICH chips keep a PATA function (class 0101) next to the AHCI one
        if "0101" in classes and "0106" not in classes:
            return "off", "The SATA controller is in IDE mode."
        if "0104" in classes:
            return "off", "The SATA controller is in RAID (Intel RST) mode, which macOS cannot use."
        if "0106" in classes:
            return "on", "The SATA controller is in AHCI mode."
        if 8 in (firmware.get("disk_bus") or []):
            return None, "Windows reports a disk in RAID mode. In the BIOS, set SATA Mode to AHCI and turn Intel VMD (Intel's storage controller mode) off if your BIOS has it."
        return "none", "Windows sees no SATA controller in the chipset, so this setting does not matter for macOS right now."

    def is_display(self, device):
        return any(item.upper().startswith("PCI\\CC_03") for item in device["compatible"])

    def read_above4g(self, firmware, context):
        bars = firmware.get("bars")
        if bars is None:
            return None, "Windows could not read where the devices sit in memory."
        high = [device for device in bars if any(flags & 0x80 and base >= FOUR_GB for base, end, flags in device["ranges"])]
        if any(self.is_display(device) for device in high):
            return "on", "The graphics card uses memory addresses above 4 GB, which only works with Above 4G Decoding on."
        if any(device.get("bridge") for device in high):
            return "on", "A plug-in card uses memory addresses above 4 GB, which only works with Above 4G Decoding on."
        if high:
            return "on", "Built-in parts of the processor use memory addresses above 4 GB, so it is probably on. Recent Intel chips do this by themselves, so check the BIOS to be sure."
        return None, "No device uses memory addresses above 4 GB. That does not prove the setting is off, so check Above 4G Decoding in the BIOS."

    def read_rebar(self, firmware, context):
        bars = firmware.get("bars")
        if bars is None:
            return None, "Windows could not read where the devices sit in memory."
        cards = [device for device in bars if self.is_display(device) and not (device["id"].upper().startswith("PCI\\VEN_8086&") and not device.get("bridge"))]
        if not cards:
            return "none", "There is no separate graphics card, so this setting does not matter."
        # 0x84: a prefetchable device BAR, not a bridge window
        size = max([end - base + 1 for device in cards for base, end, flags in device["ranges"] if flags & 0x84 == 0x84] or [0])
        if size > 256 << 20:
            shown = "{} GB".format(size >> 30) if size >= 1 << 30 else "{} MB".format(size >> 20)
            return "on", "The graphics card uses a {} memory window, so Resizable BAR is on.".format(shown)
        return "off", "The graphics card uses a normal memory window (256 MB or less), so Resizable BAR is not active."

    def read_os_type(self, firmware, context):
        machine = firmware.get("machine") or {}
        if "ASUSTEK" not in "{} {}".format(machine.get("BoardManufacturer") or "", machine.get("Manufacturer") or "").upper():
            return None, "Windows cannot see this setting. It is mostly found on ASUS boards. If your BIOS has it, set it as listed; if not, skip it."
        if firmware.get("firmware_type") == 2 and context["secure_boot"] == "on":
            return "on", "Secure Boot is on, which on ASUS boards means OS Type is set to Windows UEFI mode. Secure Boot still has to go off. On ASUS, do that by switching OS Type to Other OS, or by clearing the Secure Boot keys under Key Management and leaving OS Type as it is."
        return None, "Windows cannot tell Other OS from Windows UEFI mode here. Either works as long as Secure Boot ends up off."

    def item_status(self, setting, want, firmware, context):
        rule = next((name for word, name in RULES if word in setting), None)
        if rule in FIXED:
            return "unknown", FIXED[rule]
        if rule == "handoff":
            return "ok", "Windows cannot see it. That is fine: this build tells OpenCore to take control of the USB ports itself (ReleaseUsbOwnership), so it works either way. Turn it on if your BIOS has the option."
        if rule == "cfg_lock":
            if context["vendor"] != "Intel":
                return "unknown", "Windows cannot read it."
            quirk = "AppleCpuPmCfgLock" if context["codename"] in cpu_data.IntelCPUGenerations[62:] else "AppleXcpmCfgLock"
            return "ok", "Windows cannot read it. That is fine: this build turns on {} in config.plist, which lets macOS start with CFG Lock on. To see the real state, start OpenCore from the stick, press Space to show the hidden tools, and run ControlMsrE2.".format(quirk)
        if rule is None:
            return "unknown", "Windows cannot see this setting."

        seen, text = getattr(self, "read_" + rule)(firmware, context)
        if rule == "csm":
            want = "off"
        status = "unknown" if seen is None else "ok" if seen in (want, "none") else "change"

        if rule == "vtd" and want == "off" and context["aquantia"]:
            return "unknown", text + " Your Aquantia network card needs VT-d on and the DisableIoMapper setting off. This build turns DisableIoMapper on, so the card may not work in macOS. If you need the card in macOS, leave VT-d on and set Kernel -> Quirks -> DisableIoMapper to false in config.plist."
        if rule == "vtd" and want == "off" and context["vendor"] == "Intel":
            return "ok", text + " This build tells macOS to ignore VT-d (the DisableIoMapper setting), so you can leave it as it is."
        if rule == "vtd" and want == "on" and seen == "on" and firmware["dmar"]["rmrr"]:
            text += " The table also reserves some memory for devices. If you run macOS 13.3 or newer with more than 16 GB of memory and the graphics built into the processor turned on, you may also need the DisableIoMapperMapping setting and a patched DMAR table. This build sets neither, so add them yourself if you run into problems."
        if rule == "iommu" and status == "change":
            text += " Turn it off. This build has no workaround for it on AMD. macOS usually ignores it, so if the boot stalls, check this setting first."
        if rule == "ptt" and seen == "on" and want == "off":
            return "ok", text + " That is fine: macOS ignores the TPM. Leave it on if Windows uses BitLocker, Device Encryption or Windows Hello, because turning it off makes Windows ask for the BitLocker recovery key and Windows Hello has to be set up again."
        if rule == "thunderbolt" and status == "change":
            text += " The OpenCore install guide suggests turning it off for the first install only. On laptops, turning it off can also stop USB-C data or video, so turn it back on once macOS is installed."
        if rule == "sata" and status == "change":
            text += " " + SAFE_MODE
        if rule == "rebar" and status == "change":
            text += " This build expects it off. Turn it off, or set Booter -> Quirks -> ResizeAppleGpuBars to 0 in config.plist."
        return status, text

    def state(self, bios, info, hardware_report):
        firmware = (info or {}).get("firmware")
        if not bios or not firmware:
            return None
        if not self.same_pc(hardware_report or {}, firmware.get("machine")):
            return {"other_pc": True, "items": []}
        context = self.context(hardware_report or {}, firmware, info)
        items = []
        for want, field in (("off", "disable"), ("on", "enable")):
            for setting in bios.get(field) or []:
                status, evidence = self.item_status(setting, want, firmware, context)
                items.append({"setting": setting, "want": want, "status": status, "evidence": evidence})
        return {"other_pc": False, "items": items}

    def readings(self, info, hardware_report=None):
        firmware = (info or {}).get("firmware")
        if not firmware:
            return []
        if not hardware_report or not self.same_pc(hardware_report, firmware.get("machine")):
            hardware_report = {}
        context = self.context(hardware_report, firmware, info)
        amd = context["vendor"] == "AMD"
        rows = [
            ("SVM (virtualization)" if amd else "VT-x (virtualization)", "vtx"),
            ("IOMMU (device memory protection)" if amd else "VT-d (device memory protection)", "iommu" if amd else "vtd"),
            ("Hyper-Threading / SMT", "ht"),
            ("CSM (legacy boot)", "csm"),
            ("Above 4G Decoding", "above4g"),
            ("Resizable BAR", "rebar"),
            ("SATA mode", "sata"),
            ("Thunderbolt / USB4", "thunderbolt"),
            ("TPM (security chip)", "ptt"),
            ("COM (serial) port", "serial"),
            ("Printer (parallel) port", "parallel")
        ]
        if not amd:
            rows.insert(3, ("Intel SGX (secure enclaves)", "sgx"))
        return [(label, getattr(self, "read_" + rule)(firmware, context)[1]) for label, rule in rows]


def split_setting(setting, want):
    # "SATA Mode: AHCI" or "DVMT ...: 64MB or higher" already name the value they want
    if ":" in setting:
        name, value = setting.split(":", 1)
        return name, value.strip()
    return setting, want.capitalize()


def item_line(item):
    name, value = split_setting(item["setting"], item["want"])
    # the guide's target would contradict evidence like "on, and this build handles it"
    if item.get("status") == "ok":
        return "{}: {}".format(name, item["evidence"])
    return "{}: should be {}. {}".format(name, value, item["evidence"])


def state_groups(state):
    groups = []
    for status, title in GROUPS:
        lines = [item_line(item) for item in state["items"] if item["status"] == status]
        if lines:
            groups.append((title, lines))
    return groups
