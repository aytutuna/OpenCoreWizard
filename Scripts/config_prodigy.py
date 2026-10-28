from Scripts.datasets import chipset_data
from Scripts.datasets import cpu_data
from Scripts.datasets import mac_model_data
from Scripts.datasets import kext_data
from Scripts.datasets import os_data
from Scripts.datasets import pci_data
from Scripts.datasets import codec_layouts
from Scripts.datasets import vendor_quirks
from Scripts import codec_match
from Scripts import gathering_files
from Scripts import smbios
from Scripts import utils
import os
import re

bcmc_firmwares = {
    "14E4-43A3": ("brcmfmac4350-pcie_7.35.180.119.bin", "5691d1e0ceb70baf18efb7a0ec6cb84feb9edd2d0700c525b42930c4e7e4b845", "40000000"),
    "14E4-43BA": ("brcmfmac43602-pcie_7.35.177.61.bin", "bf4cfc23ee952a3d82ef33a0f5f87853201c98f1bed034876a910f354f37862d", "00000000")
}
bcmc_firmware_dir = "/usr/local/share/firmware/wifi/"
cfg_lock_note = "CFG Lock: in the boot picker press Space to show hidden entries and run ControlMsrE2. If it reports UNLOCKED, the AppleXcpmCfgLock/AppleCpuPmCfgLock quirks can be turned off later; if LOCKED, leave them on."
debug_target = 67
debug_display_level = 2147483714
vendor_quirk_fields = {
    "motherboard": ("Motherboard", "Name"),
    "chipset": ("Motherboard", "Chipset"),
    "bios_vendor": ("BIOS", "Vendor"),
    "cpu": ("CPU", "Processor Name")
}

class ConfigProdigy:
    def __init__(self):
        self.g = gathering_files.gatheringFiles()
        self.smbios = smbios.SMBIOS()
        self.utils = utils.Utils()
        self.bios_notes = []
        self.layout_reason = None
        self.cpuids = {
            "Ivy Bridge": "A9060300",
            "Haswell": "C3060300",
            "Broadwell": "D4060300",
            "Coffee Lake": "EB060800",
            "Comet Lake": "55060A00",
            "Ice Lake": "E5060700"
        }

    def mmio_whitelist(self, motherboard_chipset):
        booter_mmiowhitelist = []

        if "Ice Lake" in motherboard_chipset:
            booter_mmiowhitelist.append({
                "Address": 4284481536,
                "Comment": "MMIO 0xFF600000",
                "Enabled": True
            })
        elif "B650" in motherboard_chipset or "X670" in motherboard_chipset:
            booter_mmiowhitelist.append({
                "Address": 4244635648,
                "Comment": "MMIO 0xFD000000",
                "Enabled": True
            })
        
        return booter_mmiowhitelist
    
    def add_booter_patch(self, smbios_model, macos_version):
        booter_patch = []

        mac_device = mac_model_data.get_mac_device_by_name(smbios_model)
        if not self.utils.parse_darwin_version(mac_device.initial_support) <= self.utils.parse_darwin_version(macos_version) <= self.utils.parse_darwin_version(mac_device.last_supported_version):
            booter_patch.append({
                "Arch": "x86_64",
                "Comment": "Skip Board ID check",
                "Count": 0,
                "Enabled": True,
                "Find": self.utils.hex_to_bytes("0050006C006100740066006F0072006D0053007500700070006F00720074002E0070006C006900730074"),
                "Identifier": "Apple",
                "Limit": 0,
                "Mask": self.utils.hex_to_bytes(""),
                "Replace": self.utils.hex_to_bytes("002E002E002E002E002E002E002E002E002E002E002E002E002E002E002E002E002E002E002E002E002E"),
                "ReplaceMask": self.utils.hex_to_bytes(""),
                "Skip": 0
            })

        return booter_patch

    def is_low_end_intel_cpu(self, processor_name):
        return any(cpu_branding in processor_name for cpu_branding in ("Celeron", "Pentium"))
    
    def igpu_properties(self, platform, integrated_gpu, monitor, macos_version):
        igpu_properties = {}

        device_id = integrated_gpu[1].get("Device ID")[5:]

        if device_id.startswith(("0042", "0046")):
            igpu_properties["framebuffer-patch-enable"] = "01000000"
            igpu_properties["framebuffer-singlelink"] = "01000000"
        elif device_id.startswith("01") and not device_id[-2] in ("5", "6"):
            native_supported_ids = ("0106", "1106", "1601", "0116", "0126", "0102")
            if not device_id in native_supported_ids:
                igpu_properties["device-id"] = "26010000"
            igpu_properties["AAPL,snb-platform-id"] = "10000300"
            if platform == "Desktop":
                if not any(monitor_info.get("Connected GPU") == integrated_gpu[0] for monitor_name, monitor_info in monitor.items() if monitor_info.get("Connector Type") != "VGA"):
                    igpu_properties["AAPL,snb-platform-id"] = "00000500"
                    igpu_properties["device-id"] = "02010000"
            elif platform == "Laptop":
                if any(tuple(map(int, "1600x900".split("x"))) <= tuple(map(int, monitor_info.get("Resolution").split("x"))) for monitor_name, monitor_info in monitor.items()):
                    igpu_properties["AAPL00,DualLink"] = "01000000"
                igpu_properties["AAPL,snb-platform-id"] = "00000100"
        elif device_id.startswith("01"):
            native_supported_ids = ("0152", "0156", "0162", "0166")
            if not device_id in native_supported_ids:
                igpu_properties["device-id"] = "62010000"
            if platform == "Desktop":
                if not any(monitor_info.get("Connected GPU") == integrated_gpu[0] for monitor_name, monitor_info in monitor.items() if monitor_info.get("Connector Type") != "VGA"):
                    igpu_properties["AAPL,ig-platform-id"] = "07006201"
                igpu_properties["AAPL,ig-platform-id"] = "0A006601"
            elif platform == "NUC":
                igpu_properties["AAPL,ig-platform-id"] = "0B006601"
            elif platform == "Laptop":
                igpu_properties["AAPL,ig-platform-id"] = "03006601"
                if any(tuple(map(int, "1600x900".split("x"))) <= tuple(map(int, monitor_info.get("Resolution").split("x"))) for monitor_name, monitor_info in monitor.items()):
                    igpu_properties["AAPL,ig-platform-id"] = "04006601"
                    igpu_properties["framebuffer-memorycount"] = "02000000"
                    igpu_properties["framebuffer-pipecount"] = "02000000"
                    igpu_properties["framebuffer-portcount"] = "04000000"
                    igpu_properties["framebuffer-stolenmem"] = "00000004"
                    igpu_properties["framebuffer-con1-enable"] = "01000000"
                    igpu_properties["framebuffer-con1-alldata"] = "020500000004000007040000030400000004000081000000040600000004000081000000"
        elif device_id.startswith(("04", "0A", "0C", "0D")):
            native_supported_ids = ("0D26", "0A26", "0A2E", "0D22", "0412")
            if not device_id in native_supported_ids:
                igpu_properties["device-id"] = "12040000"
            if platform == "Desktop":
                if not any(monitor_info.get("Connected GPU") == integrated_gpu[0] for monitor_name, monitor_info in monitor.items() if monitor_info.get("Connector Type") != "VGA"):
                    igpu_properties["AAPL,ig-platform-id"] = "04001204"
                    return igpu_properties
                igpu_properties["AAPL,ig-platform-id"] = "0300220D"
                igpu_properties["framebuffer-stolenmem"] = "00003001"
                igpu_properties["framebuffer-fbmem"] = "00009000"
            elif platform == "NUC":
                igpu_properties["AAPL,ig-platform-id"] = "0300220D"
                igpu_properties["framebuffer-cursormem"] = "00009000"
            elif platform == "Laptop":
                igpu_properties["AAPL,ig-platform-id"] = "0600260A"
                if device_id.startswith(("0A2", "0D2")):
                    igpu_properties["AAPL,ig-platform-id"] = "0500260A"
                igpu_properties["framebuffer-cursormem"] = "00009000"
        elif device_id.startswith(("0B", "16")):
            native_supported_ids = ("0BD1", "0BD2", "0BD3", "1606", "160E", "161E", "1626", "1622", "1612", "162B")
            if not device_id in native_supported_ids:
                igpu_properties["device-id"] = "26160000"
            if platform == "Desktop":
                igpu_properties["AAPL,ig-platform-id"] = "07002216"
            elif platform == "NUC":
                igpu_properties["AAPL,ig-platform-id"] = "02001616"
            elif platform == "Laptop":
                igpu_properties["AAPL,ig-platform-id"] = "06002616"
            igpu_properties["framebuffer-stolenmem"] = "00003001"
            igpu_properties["framebuffer-fbmem"] = "00009000"
        elif device_id.startswith(("09", "19")) and self.utils.parse_darwin_version(macos_version) < self.utils.parse_darwin_version("22.0.0"):
            native_supported_ids = ("1916", "191E", "1926", "1927", "1912", "1932", "1902", "1917", "193B", "191B")
            if not device_id in native_supported_ids:
                igpu_properties["device-id"] = "1B190000"
            if device_id in ("1906", "190B"):
                igpu_properties["device-id"] = "02190000"
            if device_id.startswith("191E"):
                igpu_properties["AAPL,ig-platform-id"] = "00001E19"
            if platform == "Desktop":
                if not any(monitor_info.get("Connected GPU") == integrated_gpu[0] for monitor_name, monitor_info in monitor.items()):
                    igpu_properties["AAPL,ig-platform-id"] = "01001219"
                    return igpu_properties
                igpu_properties["AAPL,ig-platform-id"] = "00001219"
            elif platform == "NUC":
                igpu_properties["AAPL,ig-platform-id"] = "02001619"
                if device_id.startswith(("1926", "1927")):
                    igpu_properties["AAPL,ig-platform-id"] = "02002619"
                elif device_id.startswith(("1932", "193B", "193A", "193D")):
                    igpu_properties["AAPL,ig-platform-id"] = "05003B19"
            elif platform == "Laptop":
                igpu_properties["AAPL,ig-platform-id"] = "00001619"
                if device_id.startswith(("1902", "1906", "190B")):
                    igpu_properties["AAPL,ig-platform-id"] = "00001B19"
            igpu_properties["framebuffer-stolenmem"] = "00003001"
            igpu_properties["framebuffer-fbmem"] = "00009000"
        elif device_id.startswith(("09", "19", "59", "87C0")):
            native_supported_ids = ("5912", "5916", "591B", "591C", "591E", "5926", "5927", "5923", "87C0")
            if not device_id in native_supported_ids:
                igpu_properties["device-id"] = "16590000"
            if device_id.startswith(("5917", "5916", "5921")):
                igpu_properties["AAPL,ig-platform-id"] = "00001659"
            if platform == "Desktop":
                if not any(monitor_info.get("Connected GPU") == integrated_gpu[0] for monitor_name, monitor_info in monitor.items()):
                    igpu_properties["AAPL,ig-platform-id"] = "03001259"
                    return igpu_properties
                igpu_properties["AAPL,ig-platform-id"] = "00001259"
            elif platform == "NUC":
                igpu_properties["AAPL,ig-platform-id"] = "00001E59"
                if device_id.startswith(("5912", "591B", "591A", "591D")):
                    igpu_properties["AAPL,ig-platform-id"] = "00001B59"
                elif device_id.startswith(("5926", "5927")):
                    igpu_properties["AAPL,ig-platform-id"] = "02002659"
            elif platform == "Laptop":
                igpu_properties["AAPL,ig-platform-id"] = "00001B59"
                if device_id.startswith(("5917", "87C0")):
                    igpu_properties["AAPL,ig-platform-id"] = "00001659"
                else:
                    igpu_properties["framebuffer-con1-alldata"] = "01050A00000800008701000002040A000008000087010000"
                    igpu_properties["framebuffer-con1-enable"] = "01000000"
                    igpu_properties["#framebuffer-con1-alldata"] = "01050A00000800008701000003060A000004000087010000"
                    igpu_properties["#framebuffer-con1-enable"] = "01000000"
            igpu_properties["framebuffer-stolenmem"] = "00003001"
            igpu_properties["framebuffer-fbmem"] = "00009000"
        elif device_id.startswith(("3E", "87", "9B")):
            native_supported_ids = ("3E9B", "3EA5", "3EA6", "3E92", "3E91", "3E98", "9BC8", "9BC5", "9BC4")
            if not device_id in native_supported_ids:
                igpu_properties["device-id"] = "9B3E0000"
            if platform == "Desktop":
                if not any(monitor_info.get("Connected GPU") == integrated_gpu[0] for monitor_name, monitor_info in monitor.items()):
                    igpu_properties["AAPL,ig-platform-id"] = "0300913E" if not device_id.startswith("9B") else "0300C89B"
                    return igpu_properties
                igpu_properties["AAPL,ig-platform-id"] = "07009B3E" if self.utils.parse_darwin_version(macos_version) < self.utils.parse_darwin_version("19.5.0") else "00009B3E"
                igpu_properties["framebuffer-stolenmem"] = "00003001"
            elif platform == "NUC":
                igpu_properties["AAPL,ig-platform-id"] = "07009B3E"
                if device_id.startswith(("3EA5", "3EA8")):
                    igpu_properties["AAPL,ig-platform-id"] = "0000A53E"
            elif platform == "Laptop":
                igpu_properties["AAPL,ig-platform-id"] = "0900A53E"
                if device_id.startswith(("3EA9", "3EA0")):
                    igpu_properties["AAPL,ig-platform-id"] = "00009B3E"
                igpu_properties["framebuffer-stolenmem"] = "00003001"
                igpu_properties["framebuffer-fbmem"] = "00009000"
        elif device_id.startswith("8A"):
            native_supported_ids = ("FF05", "8A70", "8A71", "8A51", "8A5C", "8A5D", "8A52", "8A53", "8A5A", "8A5B")
            if not device_id in native_supported_ids:
                igpu_properties["device-id"] = "528A0000"
            igpu_properties["AAPL,ig-platform-id"] = "0200518A"
            igpu_properties["enable-dbuf-early-optimizer"] = "01000000"
            igpu_properties["enable-dvmt-calc-fix"] = "01000000"
            igpu_properties["enable-cdclk-frequency-fix"] = "01000000"
            igpu_properties["framebuffer-stolenmem"] = "00003001"
            igpu_properties["framebuffer-fbmem"] = "00009000"

        if any(tuple(map(int, "3840x2160".split("x"))) <= tuple(map(int, monitor_info.get("Resolution").split("x"))) for monitor_name, monitor_info in monitor.items()):
            if platform == "Laptop":
                igpu_properties["enable-max-pixel-clock-override"] = "01000000"
            if igpu_properties.get("framebuffer-stolenmem"):
                del igpu_properties["framebuffer-stolenmem"]
            if igpu_properties.get("framebuffer-fbmem"):
                del igpu_properties["framebuffer-fbmem"]

        for key in igpu_properties.keys():
            if key not in ("AAPL,ig-platform-id", "device-id"):
                igpu_properties["framebuffer-patch-enable"] = "01000000"
                break

        return dict(sorted(igpu_properties.items(), key=lambda item: item[0]))
    
    def select_audio_codec_layout(self, hardware_report, config=None, controller_required=False):
        try:
            for device_properties in config["DeviceProperties"]["Add"].values():
                if device_properties.get("layout-id"):
                    return None, None
        except:
            pass

        codec_id = None
        subsystem_id = None
        audio_controller_properties = None

        for codec_properties in hardware_report.get("Sound", {}).values():
            if codec_properties.get("Device ID") in codec_layouts.data:
                codec_id = codec_properties.get("Device ID")
                subsystem_id = codec_properties.get("Subsystem ID")

                if codec_properties.get("Controller Device ID"):
                    for device_name, device_properties in hardware_report.get("System Devices").items():
                        if device_properties.get("Device ID") == codec_properties.get("Controller Device ID"):
                            audio_controller_properties = device_properties
                            break
                break

        if not codec_id:
            return None, None
        
        if controller_required and not audio_controller_properties:
            return None, None
        
        available_layouts = codec_layouts.data.get(codec_id)

        motherboard = hardware_report.get("Motherboard") or {}
        ranked = codec_match.rank_layouts(available_layouts, motherboard.get("Name"), subsystem_id, motherboard.get("Platform"))
        reasons = {layout.id: reason for layout, _, reason in ranked}
        default_layout = ranked[0][0]

        while True:
            contents = []
            contents.append("")
            contents.append("List of Codec Layouts:")
            contents.append("")
            contents.append("ID   Comment")
            contents.append("------------------------------------------------------------------")
            for layout in available_layouts:
                line = "{:<4} {}".format(layout.id, layout.comment[:60])
                if layout == default_layout:
                    contents.append("\033[1;32m{}\033[0m".format(line))
                else:
                    contents.append(line)
            contents.append("")
            contents.append("Default layout {}: {}".format(default_layout.id, reasons[default_layout.id]))
            contents.append("")
            contents.append("\033[1;93mNote:\033[0m")
            contents.append("- The default layout may not be optimal.")
            contents.append("- Test different layouts to find what works best for your system.")
            contents.append("")
            content = "\n".join(contents)

            self.utils.adjust_window_size(content)
            self.utils.head("Choosing Codec Layout ID", resize=False)
            print(content)
            selected_layout_id = self.utils.request_input(f"Enter the ID of the codec layout you want to use (default: {default_layout.id}): ") or default_layout.id

            try:
                selected_layout_id = int(selected_layout_id)

                for layout in available_layouts:
                    if layout.id == selected_layout_id:
                        self.layout_reason = reasons[layout.id] or None
                        return selected_layout_id, audio_controller_properties
            except:
                continue
  
    def deviceproperties(self, hardware_report, disabled_devices, macos_version, kexts):
        deviceproperties_add = {}

        def add_device_property(pci_path, properties):
            if not pci_path or not properties:
                return

            if not deviceproperties_add.get(pci_path):
                deviceproperties_add[pci_path] = {}

            deviceproperties_add[pci_path].update(properties)

        if kexts[kext_data.kext_index_by_name.get("WhateverGreen")].checked:
            for gpu_name, gpu_info in hardware_report.get("GPU", {}).items():
                if gpu_info.get("Device Type") == "Integrated GPU":
                    if "Intel" in gpu_info.get("Manufacturer"):
                        add_device_property(gpu_info.get("PCI Path", "PciRoot(0x0)/Pci(0x2,0x0)"), self.igpu_properties(
                            "NUC" if "NUC" in hardware_report.get("Motherboard").get("Name") else hardware_report.get("Motherboard").get("Platform"), 
                            (gpu_name, gpu_info),
                            hardware_report.get("Monitor", {}),
                            macos_version
                        ))
                        if deviceproperties_add[gpu_info.get("PCI Path", "PciRoot(0x0)/Pci(0x2,0x0)")]:
                            if gpu_info.get("Codename") in ("Sandy Bridge", "Ivy Bridge"):
                                for device_name, device_info in hardware_report.get("System Devices", {}).items():
                                    device_id = device_info.get("Device ID")

                                    if not device_id in ("8086-1C3A", "8086-1E3A"):
                                        continue

                                    if "Sandy Bridge" in gpu_info.get("Codename") and device_id in "8086-1E3A":
                                        add_device_property(device_info.get("PCI Path", "PciRoot(0x0)/Pci(0x16,0x0)"), {"device-id": "3A1C0000"})
                                    elif "Ivy Bridge" in gpu_info.get("Codename") and device_id in "8086-1C3A":
                                        add_device_property(device_info.get("PCI Path", "PciRoot(0x0)/Pci(0x16,0x0)"), {"device-id": "3A1E0000"})
                elif gpu_info.get("Device Type") == "Discrete GPU":
                    if not gpu_info.get("Device ID") in pci_data.SpoofGPUIDs:
                        continue

                    add_device_property(gpu_info.get("PCI Path"), {
                        "device-id": self.utils.to_little_endian_hex(pci_data.SpoofGPUIDs.get(gpu_info.get("Device ID")).split("-")[-1]),
                        "model": gpu_name
                    })
        
        if kexts[kext_data.kext_index_by_name.get("AppleALC")].checked:
            selected_layout_id, audio_controller_properties = self.select_audio_codec_layout(hardware_report, controller_required=True)

            if selected_layout_id and audio_controller_properties:
                add_device_property(audio_controller_properties.get("PCI Path"), {"layout-id": selected_layout_id})

        for network_name, network_props in hardware_report.get("Network", {}).items():
            device_id = network_props.get("Device ID")

            if device_id in pci_data.AtherosWiFiIDs[6:8]:
                add_device_property(network_props.get("PCI Path"), {
                    "IOName": "pci168c,2a",
                    "device-id": self.utils.hex_to_bytes("2A000000")
                })
            elif device_id in pci_data.BroadcomWiFiIDs[:18]:
                add_device_property(network_props.get("PCI Path"), {
                    "IOName": "pci14e4,43a0"
                })
            elif device_id in pci_data.BroadcomBCM57XXIDs[:-5]:
                add_device_property(network_props.get("PCI Path"), {
                    "IOName": "pci14e4,16b4",
                    "device-id": self.utils.hex_to_bytes("B4160000")
                })
            elif device_id in pci_data.AquantiaAqtionIDs[:2]:
                add_device_property(network_props.get("PCI Path"), {
                    "IOName": "1D6A-91B1"
                })
            elif device_id in pci_data.IntelWiFiIDs:
                if all((kexts[kext_data.kext_index_by_name.get("AirportItlwm")].checked, self.utils.parse_darwin_version(macos_version) >= self.utils.parse_darwin_version("24.0.0"))):
                    if all((kexts[kext_data.kext_index_by_name.get("IOSkywalkFamily")].checked, kexts[kext_data.kext_index_by_name.get("IO80211FamilyLegacy")].checked)):
                        add_device_property(network_props.get("PCI Path"), {"IOName": "pci14e4,43a0"})

            if not network_props.get("ACPI Path"):
                add_device_property(network_props.get("PCI Path"), {"built-in": "01"})

        for device_name, device_props in hardware_report.get("Storage Controllers", {}).items():
            if not device_props.get("ACPI Path"):
                add_device_property(device_props.get("PCI Path"), {"built-in": "01"})

        for device_name, device_props in disabled_devices.items():
            if "GPU" in device_name and not device_props.get("Disabled", False):
                add_device_property(device_props.get("PCI Path"), {"disable-gpu": True})

        for key, value in deviceproperties_add.items():
            for key_child, value_child in value.items():
                if isinstance(value_child, str):
                    deviceproperties_add[key][key_child] = self.utils.hex_to_bytes(deviceproperties_add[key][key_child])

        if kexts[kext_data.kext_index_by_name.get("AppleBCMWLANCompanion")].checked:
            for network_name, network_props in hardware_report.get("Network", {}).items():
                firmware = bcmc_firmwares.get(network_props.get("Device ID"))
                if not firmware or not network_props.get("PCI Path"):
                    continue
                add_device_property(network_props.get("PCI Path"), {
                    "bcmc-firmware-path": bcmc_firmware_dir + firmware[0],
                    "bcmc-firmware-hash": self.utils.hex_to_bytes(firmware[1]),
                    "bcmc-srom-slide": self.utils.hex_to_bytes(firmware[2])
                })

        return deviceproperties_add

    def block_kext_bundle(self, kexts):
        kernel_block = []

        if kexts[kext_data.kext_index_by_name.get("IOSkywalkFamily")].checked and not kexts[kext_data.kext_index_by_name.get("AppleBCMWLANCompanion")].checked:
            kernel_block.append({
                "Arch": "x86_64",
                "Comment": "Allow IOSkywalk Downgrade",
                "Enabled": True,
                "Identifier": "com.apple.iokit.IOSkywalkFamily",
                "MaxKernel": "",
                "MinKernel": "23.0.0",
                "Strategy": "Exclude"
            })
        
        return kernel_block

    def is_low_end_haswell_plus(self, processor_name, cpu_codename):
        return self.is_low_end_intel_cpu(processor_name) and cpu_codename in cpu_data.IntelCPUGenerations[:39]

    def is_intel_hedt_cpu(self, processor_name, cpu_codename):
        if cpu_codename in cpu_data.IntelCPUGenerations[45:66]:
            return cpu_codename.endswith(("-X", "-P", "-W", "-E", "-EP", "-EX"))
        
        if cpu_codename in cpu_data.IntelCPUGenerations[66:]:
            return "Xeon" in processor_name
        
        return False
    
    def spoof_cpuid(self, processor_name, cpu_codename, macos_version):
        if self.is_low_end_haswell_plus(processor_name, cpu_codename):
            return self.cpuids.get("Ivy Bridge")
        elif "Haswell" in cpu_codename and self.is_intel_hedt_cpu(processor_name, cpu_codename):
            return self.cpuids.get("Haswell")
        elif "Broadwell" in cpu_codename and self.is_intel_hedt_cpu(processor_name, cpu_codename):
            return self.cpuids.get("Broadwell")
        elif "Tiger Lake" in cpu_codename:
            return self.cpuids.get("Ice Lake")
        elif "Ice Lake" not in cpu_codename and cpu_codename in cpu_data.IntelCPUGenerations[:34]:
            if not "Comet Lake" in cpu_codename:
                return self.cpuids.get("Comet Lake")
            if self.utils.parse_darwin_version(macos_version) < self.utils.parse_darwin_version("19.0.0"):
                return self.cpuids.get("Coffee Lake")
            
        return None
    
    def load_kernel_patch(self, motherboard_chipset, cpu_manufacturer, cpu_codename, cpu_cores, gpu_manufacturer, networks, macos_version, kexts, provide_current_cpu_info):
        kernel_patch = []

        if "AMD" in cpu_manufacturer:
            kernel_patch.extend(self.g.get_kernel_patches("AMD Vanilla Patches", self.g.amd_vanilla_patches_url))

        if any(network_props.get("Device ID") in pci_data.AquantiaAqtionIDs for network_props in networks.values()):
            kernel_patch.extend(self.g.get_kernel_patches("Aquantia macOS Patches", self.g.aquantia_macos_patches_url))

        if provide_current_cpu_info and kexts[kext_data.kext_index_by_name.get("CpuTopologyRebuild")].checked:
            kernel_patch.extend(self.g.get_kernel_patches("Hyper Threading Patches", self.g.hyper_threading_patches_url))
        elif kexts[kext_data.kext_index_by_name.get("ForgedInvariant")].checked:
            if not "AMD" in cpu_manufacturer:
                kernel_patch.extend(self.g.get_kernel_patches("AMD Vanilla Patches", self.g.amd_vanilla_patches_url)[-6:-4])
        elif kexts[kext_data.kext_index_by_name.get("CatalinaBCM5701Ethernet")].checked:
            kernel_patch.append({
                "Arch": "Any",
                "Base": "",
                "Comment": "Broadcom BCM577XX Patch",
                "Count": 1,
                "Enabled": True,
                "Find": self.utils.hex_to_bytes("E8CA9EFFFF66898300050000"),
                "Identifier": "com.apple.iokit.CatalinaBCM5701Ethernet",
                "Limit": 0,
                "Mask": self.utils.hex_to_bytes(""),
                "MaxKernel": "",
                "MinKernel": "20.0.0",
                "Replace": self.utils.hex_to_bytes("B8B416000066898300050000"),
                "ReplaceMask": self.utils.hex_to_bytes(""),
                "Skip": 0
            })

        latest_darwin_version = (os_data.get_latest_darwin_version(), os_data.get_latest_darwin_version(include_beta=False))

        for patch in kernel_patch:
            if "AppleEthernetAquantiaAqtion" in patch["Identifier"]:
                patch["Enabled"] = patch["Base"] != ""
            elif "cpuid_cores_per_package" in patch["Comment"]:
                patch["Replace"] = patch["Replace"].hex()
                patch["Replace"] = self.utils.hex_to_bytes(patch["Replace"][:2] + self.utils.int_to_hex(int(cpu_cores)) + patch["Replace"][4:])
            elif "IOPCIIsHotplugPort" in patch["Comment"]:
                if motherboard_chipset in chipset_data.AMDChipsets[17:] or cpu_codename in ("Raphael", "Storm Peak", "Phoenix", "Granite Ridge"):
                    patch["Enabled"] = True
            elif "_mtrr_update_action" in patch["Comment"]:
                if "TRX" in motherboard_chipset.upper():
                    patch["Enabled"] = False
                elif "AMD" in gpu_manufacturer:
                    if "algrey" in patch["Comment"].lower():
                        patch["Enabled"] = False
                    elif "shaneee" in patch["Comment"].lower():
                        patch["Enabled"] = True

            if "Beta" in os_data.get_macos_name_by_darwin(macos_version):
                if patch["MaxKernel"] in latest_darwin_version:
                    patch["MaxKernel"] = latest_darwin_version[0]
        
        return kernel_patch

    def boot_args(self, hardware_report, macos_version, needs_oclp, kexts, config):
        boot_args = [
            "-v",
            "debug=0x100",
            "keepsyms=1"
        ]

        if needs_oclp and self.utils.parse_darwin_version(macos_version) >= self.utils.parse_darwin_version("25.0.0"):
            boot_args.append("-amfipassbeta")

        if config["Booter"]["Quirks"]["ResizeAppleGpuBars"] != 0 and self.is_intel_hedt_cpu(hardware_report.get("CPU").get("Processor Name"), hardware_report.get("CPU").get("Codename")):
            boot_args.append("npci=0x2000")

        for kext in kexts:
            if not kext.checked:
                continue

            if "Lilu" in kext.requires_kexts and not self.utils.parse_darwin_version(kext.min_darwin_version) <= self.utils.parse_darwin_version(macos_version) <= self.utils.parse_darwin_version(kext.max_darwin_version):
                if not "-lilubetaall" in boot_args:
                    boot_args.append("-lilubetaall")

            if kext.name == "WhateverGreen":
                if  any(tuple(map(int, "3840x2160".split("x"))) <= tuple(map(int, monitor_info.get("Resolution").split("x"))) for monitor_name, monitor_info in hardware_report.get("Monitor", {}).items()) and \
                    self.utils.parse_darwin_version(macos_version) < self.utils.parse_darwin_version("20.0.0"):
                    boot_args.append("-cdfon")

                if  "Intel" in hardware_report.get("CPU").get("Manufacturer") and \
                    "Integrated GPU" in list(hardware_report.get("GPU").items())[-1][-1].get("Device Type"):
                    intergrated_gpu = list(hardware_report.get("GPU").items())[-1]
                    if intergrated_gpu[-1].get("OCLP Compatibility"):
                        boot_args.append("ipc_control_port_options=0")

                    if "Ice Lake" in intergrated_gpu[-1].get("Codename"):
                        boot_args.extend(("-noDC9", "-igfxblr"))

                    if "Desktop" in hardware_report.get("Motherboard").get("Platform"):
                        if any(monitor_info.get("Connected GPU") == intergrated_gpu[0] for monitor_name, monitor_info in hardware_report.get("Monitor", {}).items()) and intergrated_gpu[-1].get("Device ID")[5:].startswith(("3E", "87", "9B")) and self.utils.parse_darwin_version(macos_version) >= self.utils.parse_darwin_version("19.4.0"):
                            boot_args.append("igfxonln=1")
                        if any(monitor_info.get("Connector Type") in ("DVI", "HDMI") for monitor_name, monitor_info in hardware_report.get("Monitor", {}).items() if monitor_info.get("Connected GPU") == intergrated_gpu[0]):
                            boot_args.append("-igfxvesa")
                    elif "Laptop" in hardware_report.get("Motherboard").get("Platform"):
                        if intergrated_gpu[-1].get("Device ID")[5:].startswith(("59", "8C", "3E", "87", "9B")) and not intergrated_gpu[-1].get("Device ID").endswith("5917"):
                            boot_args.append("-igfxbl{}".format("t" if self.utils.parse_darwin_version(macos_version) >= self.utils.parse_darwin_version("22.5.0") else "r"))

                discrete_gpu = list(hardware_report.get("GPU").items())[0][-1]
                if discrete_gpu.get("Device Type") == "Discrete GPU":
                    if "Navi" in discrete_gpu.get("Codename"):
                        boot_args.append("agdpmod=pikera")

                    if discrete_gpu.get("Device ID") in pci_data.SpoofGPUIDs:
                        boot_args.append("-radcodec")

                    if discrete_gpu.get("Device ID") in ("1002-6610", "1002-682B", "1002-6837", "1002-683D", "1002-683F"):
                        boot_args.append("radpg=15")

                    if discrete_gpu.get("Device ID") in ("1002-67B0", "1002-67B1", "1002-67B8", "1002-6810", "1002-6811"):
                        boot_args.append("-raddvi")

                    if discrete_gpu.get("OCLP Compatibility"):
                        if discrete_gpu.get("Manufacturer") == "AMD":
                            boot_args.append("-radvesa" if self.utils.parse_darwin_version(macos_version) < self.utils.parse_darwin_version("23.0.0") else "-amd_no_dgpu_accel")
                        elif discrete_gpu.get("Manufacturer") == "NVIDIA" and not "Kepler" in discrete_gpu.get("Codename"):
                            boot_args.extend(("nvda_drv_vrl=1", "ngfxcompat=1", "ngfxgl=1"))
            elif kext.name == "AppleALC":
                selected_layout_id, _ = self.select_audio_codec_layout(hardware_report, config)

                if selected_layout_id:
                    boot_args.append("alcid={}".format(selected_layout_id))
            elif kext.name == "VoodooI2C":
                boot_args.append("-vi2c-force-polling")
            elif kext.name == "CpuTopologyRebuild":
                boot_args.append("ctrsmt=full")
            elif kext.name == "AppleBCMWLANCompanion":
                boot_args.append("wlan.pcie.detectsabotage=0")

        if self.utils.parse_darwin_version(macos_version) >= self.utils.parse_darwin_version("25.0.0") and \
            any(kexts[kext_data.kext_index_by_name.get(name)].checked for name in ("IntelBluetoothFirmware", "IntelBTPatcher")):
            boot_args.append("-ibtcompatbeta")

        return " ".join(boot_args)

    def disable_io_mapper(self, hardware_report, kexts):
        if "AMD" in hardware_report.get("CPU").get("Manufacturer"):
            return False
        return not kexts[kext_data.kext_index_by_name.get("AppleBCMWLANCompanion")].checked

    def agdp_pikera_patch(self):
        return {
            "Arch": "Any",
            "Base": "",
            "Comment": "AGDP board-id patch (pikera) for first boot without WhateverGreen",
            "Count": 1,
            "Enabled": True,
            "Find": self.utils.hex_to_bytes("626F6172642D696400"),
            "Identifier": "com.apple.driver.AppleGraphicsDevicePolicy",
            "Limit": 0,
            "Mask": self.utils.hex_to_bytes(""),
            "MaxKernel": "",
            "MinKernel": "25.0.0",
            "Replace": self.utils.hex_to_bytes("626F6172642D697800"),
            "ReplaceMask": self.utils.hex_to_bytes(""),
            "Skip": 0
        }

    def disable_whatevergreen_for_first_boot(self, config):
        entries = [entry for entry in config["Kernel"]["Add"] if entry.get("BundlePath", "").split("/")[-1] == "WhateverGreen.kext"]
        if not entries:
            return None

        for entry in entries:
            entry["Enabled"] = False

        nvram = config["NVRAM"]["Add"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"]
        boot_args = nvram.get("boot-args", "").split()
        agdp_patch = "agdpmod=pikera" in boot_args
        if agdp_patch:
            nvram["boot-args"] = " ".join(arg for arg in boot_args if arg != "agdpmod=pikera")
            config["Kernel"]["Patch"].append(self.agdp_pikera_patch())

        return {"agdp_patch": agdp_patch}

    def apply_first_boot_options(self, config, whatevergreen_first_boot_off):
        if not whatevergreen_first_boot_off:
            return None
        return self.disable_whatevergreen_for_first_boot(config)
    
    def cfg_lock_tool(self, config):
        quirks = config["Kernel"]["Quirks"]
        if not (quirks.get("AppleCpuPmCfgLock") or quirks.get("AppleXcpmCfgLock")):
            return None
        return {
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
        }

    def match_vendor_rule(self, rule, hardware_report):
        match = rule.get("match") or {}
        if not match:
            return False
        for field, patterns in match.items():
            section, key = vendor_quirk_fields[field]
            value = str((hardware_report.get(section) or {}).get(key) or "")
            if not value or not any(re.search(pattern, value, re.IGNORECASE) for pattern in patterns):
                return False
        return True

    def matching_vendor_rules(self, hardware_report):
        return [rule for rule in vendor_quirks.rules if self.match_vendor_rule(rule, hardware_report)]

    def set_config_value(self, config, dotted_key, value):
        node = config
        parts = dotted_key.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def apply_vendor_quirks(self, hardware_report, config, acpi_directory=None):
        notes = []
        for rule in self.matching_vendor_rules(hardware_report):
            for dotted_key, value in rule.get("config", {}).items():
                self.set_config_value(config, dotted_key, value)

            drop_drivers = rule.get("drop_drivers", [])
            if drop_drivers:
                config["UEFI"]["Drivers"] = [driver for driver in config["UEFI"]["Drivers"] if driver.get("Path") not in drop_drivers]

            alternative = rule.get("unless_acpi")
            has_alternative = bool(alternative) and any(entry.get("Path") == alternative and entry.get("Enabled", True) for entry in config["ACPI"]["Add"])

            missing_tables = []
            for table, source_url in ({} if has_alternative else rule.get("acpi", {})).items():
                if acpi_directory and os.path.isfile(os.path.join(acpi_directory, table)):
                    if not any(entry.get("Path") == table for entry in config["ACPI"]["Add"]):
                        config["ACPI"]["Add"].append({
                            "Comment": table,
                            "Enabled": True,
                            "Path": table
                        })
                else:
                    missing_tables.append("{} is not in the OpenCorePkg package, so this build does not include it: download it from {} into EFI/OC/ACPI and add it to ACPI -> Add.".format(table, source_url))

            for note in (rule.get("unless_notes", []) if has_alternative else rule.get("notes", []) + missing_tables):
                if note not in notes:
                    notes.append(note)
        return notes

    def apply_debug_settings(self, config, debug):
        if not debug:
            return config

        debug_settings = config["Misc"]["Debug"]
        debug_settings["AppleDebug"] = True
        debug_settings["ApplePanic"] = True
        debug_settings["DisableWatchDog"] = True
        debug_settings["Target"] = debug_target
        debug_settings["DisplayLevel"] = debug_display_level
        debug_settings["SysReport"] = False
        return config

    def csr_active_config(self, macos_version, needs_oclp):
        # kexts injected by OpenCore load with SIP on; only root patches need it lowered
        if not needs_oclp:
            return "00000000"
        if self.utils.parse_darwin_version(macos_version) >= self.utils.parse_darwin_version("20.0.0"):
            return "030A0000"
        elif self.utils.parse_darwin_version(macos_version) >= self.utils.parse_darwin_version("18.0.0"):
            return "FF070000"
        else:
            return "FF030000"
        
    def load_drivers(self, firmware_type, cpu_codename, macos_version, picker_mode):
        uefi_drivers = []
        required_drivers = ["HfsPlus.efi" if not cpu_codename in cpu_data.IntelCPUGenerations[64:] else "HfsPlusLegacy.efi", "OpenRuntime.efi", "ResetNvramEntry.efi"]
        
        if firmware_type != "UEFI":
            required_drivers.append("OpenUsbKbDxe.efi")
        
        if picker_mode == "External":
            required_drivers.append("OpenCanopy.efi")

        if self.utils.parse_darwin_version(macos_version) >= self.utils.parse_darwin_version("25.0.0"):
            required_drivers.append("apfs_aligned.efi")

        required_drivers = sorted(required_drivers, key=lambda x: x.lower())

        for driver in required_drivers:
            uefi_drivers.append({
                "Arguments": "",
                "Comment": "",
                "Enabled": True,
                "HideVerbose": "apfs_aligned.efi" in required_drivers,
                "LoadEarly": False,
                "Path": driver
            })
        
        return uefi_drivers

    def genarate(self, hardware_report, disabled_devices, smbios_model, macos_version, needs_oclp, kexts, config, whatevergreen_first_boot_off=False, acpi_directory=None):
        self.bios_notes = []
        del config["#WARNING - 1"]
        del config["#WARNING - 2"]
        del config["#WARNING - 3"]
        del config["#WARNING - 4"]

        config["Booter"]["MmioWhitelist"] = self.mmio_whitelist(hardware_report.get("Motherboard").get("Chipset"))
        config["Booter"]["Patch"] = self.add_booter_patch(smbios_model, macos_version) or config["Booter"]["Patch"]
        config["Booter"]["Quirks"]["AvoidRuntimeDefrag"] = not (hardware_report.get("BIOS").get("Firmware Type") == "Legacy" and self.utils.parse_darwin_version(macos_version) != self.utils.parse_darwin_version("20.0.0"))
        config["Booter"]["Quirks"]["DevirtualiseMmio"] = len(config["Booter"]["MmioWhitelist"]) != 0 or \
            hardware_report.get("Motherboard").get("Chipset") in chipset_data.IntelChipsets[112:] + chipset_data.IntelChipsets[90:100] or \
            hardware_report.get("Motherboard").get("Chipset") in chipset_data.IntelChipsets[104:112] and ("Desktop" in hardware_report.get("Motherboard").get("Platform") or not "-8" in hardware_report.get("CPU").get("Processor Name")) or \
            hardware_report.get("Motherboard").get("Chipset") == chipset_data.AMDChipsets[16]
        config["Booter"]["Quirks"]["EnableSafeModeSlide"] = hardware_report.get("BIOS").get("Firmware Type") == "UEFI"
        config["Booter"]["Quirks"]["EnableWriteUnprotector"] = not (hardware_report.get("BIOS").get("Firmware Type") == "Legacy" or "AMD" in hardware_report.get("CPU").get("Manufacturer") or hardware_report.get("Motherboard").get("Chipset") in chipset_data.IntelChipsets[91:100] + chipset_data.IntelChipsets[112:])
        config["Booter"]["Quirks"]["ProtectMemoryRegions"] = "GOOGLE" in hardware_report.get("Motherboard").get("Name") or any(device_props.get("Device ID") in pci_data.ChromebookIDs and device_props.get("Subsystem ID") in pci_data.ChromebookIDs[device_props.get("Device ID")] for device_props in hardware_report.get("System Devices", {}).values())
        config["Booter"]["Quirks"]["FixupAppleEfiImages"] = not (hardware_report.get("BIOS").get("Firmware Type") == "Legacy" and self.utils.parse_darwin_version("17.0.0") < self.utils.parse_darwin_version(macos_version))
        config["Booter"]["Quirks"]["ProtectUefiServices"] = hardware_report.get("Motherboard").get("Chipset") in chipset_data.IntelChipsets[112:] or \
            hardware_report.get("Motherboard").get("Chipset") in chipset_data.IntelChipsets[104:112] and (not "-8" in hardware_report.get("CPU").get("Processor Name") or hardware_report.get("Motherboard").get("Chipset") == chipset_data.IntelChipsets[109])
        config["Booter"]["Quirks"]["ProvideCustomSlide"] = hardware_report.get("BIOS").get("Firmware Type") == "UEFI"
        config["Booter"]["Quirks"]["RebuildAppleMemoryMap"] = not config["Booter"]["Quirks"]["EnableWriteUnprotector"] if hardware_report.get("CPU").get("Codename") not in cpu_data.IntelCPUGenerations[-11:] else self.utils.parse_darwin_version(macos_version) < self.utils.parse_darwin_version("10.0.0")
        config["Booter"]["Quirks"]["ResizeAppleGpuBars"] = 0 if any(gpu_props.get("Resizable BAR", "Disabled") == "Enabled" for gpu_name, gpu_props in hardware_report.get("GPU", {}).items()) else -1
        config["Booter"]["Quirks"]["SetupVirtualMap"] = hardware_report.get("BIOS").get("Firmware Type") == "UEFI" and not hardware_report.get("Motherboard").get("Chipset") in chipset_data.AMDChipsets[11:17] + chipset_data.IntelChipsets[90:100]
        config["Booter"]["Quirks"]["SyncRuntimePermissions"] = "AMD" in hardware_report.get("CPU").get("Manufacturer") or hardware_report.get("Motherboard").get("Chipset") in chipset_data.IntelChipsets[90:100] + chipset_data.IntelChipsets[104:]

        config["DeviceProperties"]["Add"] = self.deviceproperties(hardware_report, disabled_devices, macos_version, kexts)

        config["Kernel"]["Block"] = self.block_kext_bundle(kexts)
        spoof_cpuid = self.spoof_cpuid(
            hardware_report.get("CPU").get("Processor Name"), 
            hardware_report.get("CPU").get("Codename"), 
            macos_version
        )
        if spoof_cpuid:
            config["Kernel"]["Emulate"]["Cpuid1Data"] = self.utils.hex_to_bytes("{}{}".format(spoof_cpuid, "0"*8*3))
            config["Kernel"]["Emulate"]["Cpuid1Mask"] = self.utils.hex_to_bytes("{}{}".format("F"*8, "0"*8*3))
        config["Kernel"]["Emulate"]["DummyPowerManagement"] = "AMD" in hardware_report.get("CPU").get("Manufacturer") or \
            self.is_low_end_intel_cpu(hardware_report.get("CPU").get("Processor Name"))
        config["Kernel"]["Force"] = []
        config["Kernel"]["Quirks"]["AppleCpuPmCfgLock"] = hardware_report.get("CPU").get("Codename") in cpu_data.IntelCPUGenerations[62:]
        config["Kernel"]["Quirks"]["AppleXcpmCfgLock"] = False if "AMD" in hardware_report.get("CPU").get("Manufacturer") else not config["Kernel"]["Quirks"]["AppleCpuPmCfgLock"]
        config["Kernel"]["Quirks"]["AppleXcpmExtraMsrs"] = self.is_intel_hedt_cpu(hardware_report.get("CPU").get("Processor Name"), hardware_report.get("CPU").get("Codename")) and hardware_report.get("CPU").get("Codename") in cpu_data.IntelCPUGenerations[50:]
        config["Kernel"]["Quirks"]["AppleXcpmForceBoost"] = hardware_report.get("CPU").get("Codename") in cpu_data.IntelCPUGenerations[:4]
        config["Kernel"]["Quirks"]["CustomSMBIOSGuid"] = True
        config["Kernel"]["Quirks"]["DisableIoMapper"] = self.disable_io_mapper(hardware_report, kexts)
        config["Kernel"]["Quirks"]["DisableRtcChecksum"] = "ASUS" in hardware_report.get("Motherboard").get("Name") or "HP " in hardware_report.get("Motherboard").get("Name")
        config["Kernel"]["Quirks"]["ForceAquantiaEthernet"] = any(network_props.get("Device ID") in pci_data.AquantiaAqtionIDs for network_props in hardware_report.get("Network", {}).values())
        config["Kernel"]["Quirks"]["LapicKernelPanic"] = "HP " in hardware_report.get("Motherboard").get("Name")
        config["Kernel"]["Quirks"]["PanicNoKextDump"] = config["Kernel"]["Quirks"]["PowerTimeoutKernelPanic"] = True
        config["Kernel"]["Quirks"]["ProvideCurrentCpuInfo"] = "AMD" in hardware_report.get("CPU").get("Manufacturer") or hardware_report.get("CPU").get("Codename") in cpu_data.IntelCPUGenerations[4:20]
        config["Kernel"]["Patch"] = self.load_kernel_patch(
            hardware_report.get("Motherboard").get("Chipset"),
            hardware_report.get("CPU").get("Manufacturer"),
            hardware_report.get("CPU").get("Codename"), 
            hardware_report.get("CPU").get("Core Count"), 
            list(hardware_report.get("GPU").items())[0][-1].get("Manufacturer"),
            hardware_report.get("Network", {}),
            macos_version,
            kexts,
            config["Kernel"]["Quirks"]["ProvideCurrentCpuInfo"]
        )

        config["Misc"]["BlessOverride"] = []
        config["Misc"]["Boot"]["LauncherOption"] = "Disabled"
        config["Misc"]["Boot"]["LauncherPath"] = "Default"
        config["Misc"]["Boot"]["PickerMode"] = "Builtin"
        # OpenCore counts the installer on the stick (com.apple.recovery.boot) as an auxiliary entry: hidden, the
        # picker showed only Windows and started it after the timeout, as if the stick did nothing. The menu waits
        # instead; the post-install helper sets a timeout once OpenCore is on the internal disk
        config["Misc"]["Boot"]["HideAuxiliary"] = False
        config["Misc"]["Boot"]["Timeout"] = 0
        config["Misc"]["Debug"]["AppleDebug"] = config["Misc"]["Debug"]["ApplePanic"] = False
        config["Misc"]["Debug"]["DisableWatchDog"] = True
        config["Misc"]["Entries"] = []
        config["Misc"]["Security"]["AllowSetDefault"] = True
        config["Misc"]["Security"]["ScanPolicy"] = 0
        config["Misc"]["Security"]["SecureBootModel"] = "Default" if not needs_oclp and self.utils.parse_darwin_version("20.0.0") <= self.utils.parse_darwin_version(macos_version) < self.utils.parse_darwin_version("23.0.0") else "Disabled"
        config["Misc"]["Security"]["Vault"] = "Optional"
        config["Misc"]["Tools"] = []
        cfg_lock_tool = self.cfg_lock_tool(config)
        if cfg_lock_tool:
            config["Misc"]["Tools"].append(cfg_lock_tool)
            self.bios_notes.append(cfg_lock_note)

        del config["NVRAM"]["Add"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"]["#INFO (prev-lang:kbd)"]
        config["NVRAM"]["Add"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"]["boot-args"] = self.boot_args(hardware_report, macos_version, needs_oclp, kexts, config)
        config["NVRAM"]["Add"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"]["csr-active-config"] = self.utils.hex_to_bytes(self.csr_active_config(macos_version, needs_oclp))
        config["NVRAM"]["Delete"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"].append("csr-active-config")
        config["NVRAM"]["Add"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"]["prev-lang:kbd"] = self.utils.hex_to_bytes("")

        config["PlatformInfo"]["Generic"].update(self.smbios.generate_smbios(smbios_model))
        config["PlatformInfo"]["Generic"]["ROM"] = self.utils.hex_to_bytes(config["PlatformInfo"]["Generic"]["ROM"])
        config["PlatformInfo"]["UpdateSMBIOSMode"] = "Custom"

        if self.utils.parse_darwin_version(macos_version) >= self.utils.parse_darwin_version("25.0.0"):
            config["UEFI"]["APFS"]["EnableJumpstart"] = False
        config["UEFI"]["APFS"]["MinDate"] = config["UEFI"]["APFS"]["MinVersion"] = -1
        config["UEFI"]["Drivers"] = self.load_drivers(hardware_report.get("BIOS").get("Firmware Type"), hardware_report.get("CPU").get("Codename"), macos_version, config["Misc"]["Boot"]["PickerMode"])
        config["UEFI"]["Input"]["KeySupport"] = hardware_report.get("BIOS").get("Firmware Type") == "UEFI"
        config["UEFI"]["Quirks"]["ForceOcWriteFlash"] = any(device_props.get("Device ID") in pci_data.ThinkPadTWX30IDs and device_props.get("Subsystem ID") in pci_data.ThinkPadTWX30IDs[device_props.get("Device ID")] for device_props in hardware_report.get("System Devices", {}).values())
        config["UEFI"]["Quirks"]["EnableVectorAcceleration"] = not hardware_report.get("CPU").get("Codename") in cpu_data.IntelCPUGenerations[:20]
        config["UEFI"]["Quirks"]["IgnoreInvalidFlexRatio"] = hardware_report.get("CPU").get("Codename") in cpu_data.IntelCPUGenerations[50:]
        config["UEFI"]["Quirks"]["ReleaseUsbOwnership"] = True
        config["UEFI"]["Quirks"]["RequestBootVarRouting"] = True
        config["UEFI"]["Quirks"]["UnblockFsConnect"] = "HP " in hardware_report.get("Motherboard").get("Name")
        config["UEFI"]["ReservedMemory"] = []

        if kexts[kext_data.kext_index_by_name.get("BlueToolFixup")].checked:
            config["NVRAM"]["Add"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"]["bluetoothExternalDongleFailed"] = self.utils.hex_to_bytes("00")
            config["NVRAM"]["Add"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"]["bluetoothInternalControllerInfo"] = self.utils.hex_to_bytes("0000000000000000000000000000")
            config["NVRAM"]["Delete"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"].extend(["bluetoothExternalDongleFailed", "bluetoothInternalControllerInfo"])

        if kexts[kext_data.kext_index_by_name.get("RestrictEvents")].checked:
            revpatch = []
            revblock = []
            if self.utils.parse_darwin_version(macos_version) > self.utils.parse_darwin_version("23.0.0") or \
                len(config["Booter"]["Patch"]) and self.utils.parse_darwin_version(macos_version) > self.utils.parse_darwin_version("20.4.0"):
                revpatch.append("sbvmm")
            if  not (" Core" in hardware_report.get("CPU").get("Processor Name") and \
                hardware_report.get("CPU").get("Codename") in cpu_data.IntelCPUGenerations[28:]):
                config["NVRAM"]["Add"]["4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102"]["revcpu"] = 1
                config["NVRAM"]["Add"]["4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102"]["revcpuname"] = hardware_report.get("CPU").get("Processor Name")
                if self.utils.parse_darwin_version(macos_version) > self.utils.parse_darwin_version("23.0.0"):
                    revpatch.append("cpuname")
                config["PlatformInfo"]["Generic"]["ProcessorType"] = 1537 if int(hardware_report.get("CPU").get("Core Count")) < 8 else 3841
            if  "Intel" in hardware_report.get("CPU").get("Manufacturer") and \
                "Integrated GPU" in list(hardware_report.get("GPU").items())[-1][-1].get("Device Type"):
                intergrated_gpu = list(hardware_report.get("GPU").items())[-1][-1]
                if intergrated_gpu.get("OCLP Compatibility"):
                    config["NVRAM"]["Add"]["4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102"]["OCLP-Settings"] = "-allow_amfi"
                    if self.utils.parse_darwin_version(macos_version) >= self.utils.parse_darwin_version("20.4.0"):
                        if intergrated_gpu.get("Codename") in ("Broadwell", "Haswell", "Ivy Bridge", "Sandy Bridge"):
                            revblock.append("media")
                        if intergrated_gpu.get("Codename") in ("Kaby Lake", "Skylake", "Broadwell", "Haswell"):
                            revpatch.append("asset")
                        elif intergrated_gpu.get("Codename") in ("Ivy Bridge", "Sandy Bridge"):
                            revpatch.append("f16c")
            if revpatch:
                config["NVRAM"]["Add"]["4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102"]["revpatch"] = ",".join(revpatch)
            if revblock:
                config["NVRAM"]["Add"]["4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102"]["revblock"] = ",".join(revblock)
        
        config["NVRAM"]["Delete"]["4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102"] = list(config["NVRAM"]["Add"]["4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102"].keys())

        for note in self.apply_vendor_quirks(hardware_report, config, acpi_directory):
            if note not in self.bios_notes:
                self.bios_notes.append(note)

        self.whatevergreen_first_boot = self.apply_first_boot_options(config, whatevergreen_first_boot_off)

        return config
