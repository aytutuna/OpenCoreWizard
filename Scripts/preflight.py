from Scripts import firmware_probe
from Scripts import run
from Scripts import utils
import json
import os
import re
import subprocess

class Preflight:
    esp_guid = "{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}"
    min_esp_mb = 150
    powershell_timeout = 30
    unknown_bitlocker = "unknown (run as administrator to check)"
    bitlocker_warning = "Suspend BitLocker or save your recovery key before changing TPM or Secure Boot settings; otherwise Windows will demand the key on the next boot."
    bitlocker_unknown_warning = "Could not read the BitLocker status (run as administrator to check). If BitLocker or Device Encryption is on, suspend it or save your recovery key before changing TPM or Secure Boot settings."
    mbr_warning = "Windows is installed in legacy MBR mode; OpenCore cannot boot it and the machine must switch to UEFI."
    esp_warning = "The Windows EFI partition is small ({} MB); macOS installs expect about 200 MB if you plan to share it. Installing macOS on its own disk avoids this."
    legacy_warning = "The firmware runs in Legacy mode: enable UEFI mode (disable Legacy/CSM (Compatibility Support Module)) before booting OpenCore."
    secure_boot_warning = "Secure Boot is turned on in the BIOS right now. OpenCore will not start while it is on; turn it off before booting from the stick."
    shell_protection = {
        "1": "on",
        "3": "on",
        "6": "on",
        "5": "suspended",
        "0": "off",
        "2": "off",
        "4": "off",
        "8": "off"
    }

    def __init__(self):
        self.utils = utils.Utils()
        self.r = run.Run()
        self.windows = os.name == "nt"
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        self.powershell_exe = os.path.join(system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        self.ps_prefix = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
        self.info = None

    def _powershell(self, command):
        args = [self.powershell_exe, "-NoProfile", "-NonInteractive", "-Command", self.ps_prefix + command]
        try:
            completed = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=self.powershell_timeout)
        except subprocess.TimeoutExpired:
            return ("", "PowerShell did not answer within {} seconds".format(self.powershell_timeout), 1)
        return (completed.stdout, completed.stderr, completed.returncode)

    def is_admin(self):
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    def script(self, admin):
        parts = ["$r = [ordered]@{};"]
        shell_query = "$p = (New-Object -ComObject Shell.Application).NameSpace($env:SystemDrive + '\\').Self.ExtendedProperty('System.Volume.BitLockerProtection'); if ($null -ne $p) { $r.BitLocker = [ordered]@{Source='Shell'; ProtectionStatus=[string]$p; VolumeStatus=''} } else { $r.BitLockerError = 'no BitLocker status reported' }"
        if admin:
            parts.append(
                "try { $v = Get-BitLockerVolume -MountPoint $env:SystemDrive -ErrorAction Stop; $r.BitLocker = [ordered]@{Source='Get-BitLockerVolume'; ProtectionStatus=[string]$v.ProtectionStatus; VolumeStatus=[string]$v.VolumeStatus} } "
                "catch { try { $v = Get-CimInstance -Namespace root/cimv2/Security/MicrosoftVolumeEncryption -ClassName Win32_EncryptableVolume -Filter (\"DriveLetter='\" + $env:SystemDrive + \"'\") -ErrorAction Stop; "
                "if ($v) { $r.BitLocker = [ordered]@{Source='Win32_EncryptableVolume'; ProtectionStatus=[string]$v.ProtectionStatus; VolumeStatus=[string]$v.ConversionStatus} } else { $r.BitLockerError = 'no encryptable volume found' } } "
                "catch { try { " + shell_query + " } catch { $r.BitLockerError = $_.Exception.Message } } }"
            )
        else:
            parts.append("try { " + shell_query + " } catch { $r.BitLockerError = $_.Exception.Message }")
        parts.append("try { $d = Get-Disk -ErrorAction Stop | Where-Object IsSystem | Select-Object -First 1; if ($d) { $r.Disk = [ordered]@{Number=$d.Number; PartitionStyle=[string]$d.PartitionStyle} } else { $r.DiskError = 'no system disk found' } } catch { $r.DiskError = $_.Exception.Message }")
        parts.append("try { $r.Esp = @(Get-Partition -ErrorAction Stop | Where-Object GptType -eq '" + self.esp_guid + "' | ForEach-Object { [ordered]@{DiskNumber=$_.DiskNumber; Size=[int64]$_.Size} }) } catch { $r.EspError = $_.Exception.Message }")
        parts.append("try { $r.SecureBoot = [int](Get-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\SecureBoot\\State' -ErrorAction Stop).UEFISecureBootEnabled } catch { $r.SecureBootError = $_.Exception.Message }")
        parts.append("try { $cs = Get-CimInstance Win32_ComputerSystem -ErrorAction Stop; $b = Get-CimInstance Win32_BIOS -ErrorAction Stop | Select-Object -First 1; $bb = Get-CimInstance Win32_BaseBoard -ErrorAction Stop | Select-Object -First 1; "
                     "$r.Machine = [ordered]@{Manufacturer=[string]$cs.Manufacturer; Model=[string]$cs.Model; Hypervisor=$cs.HypervisorPresent; BoardManufacturer=[string]$bb.Manufacturer; BoardProduct=[string]$bb.Product; BiosManufacturer=[string]$b.Manufacturer; BiosVersion=[string]$b.SMBIOSBIOSVersion} } catch { $r.MachineError = $_.Exception.Message }")
        parts.append("try { $c = Get-CimInstance Win32_Processor -ErrorAction Stop | Select-Object -First 1; $r.Virtualization = [ordered]@{Capable=$c.VMMonitorModeExtensions; Enabled=$c.VirtualizationFirmwareEnabled} } catch { }")
        parts.append("try { $r.DeviceGuard = @((Get-CimInstance -Namespace root/Microsoft/Windows/DeviceGuard -ClassName Win32_DeviceGuard -ErrorAction Stop).AvailableSecurityProperties | ForEach-Object { [int]$_ }) } catch { }")
        parts.append("try { $r.Devices = @(Get-CimInstance Win32_PnPEntity -ErrorAction Stop | Where-Object { $_.PNPDeviceID -like 'PCI\\*' -or ($_.HardwareID -match 'PNP040[01]|PNP050[01]') -or ($_.CompatibleID -match 'MSFT0101|PNP0C31') } | "
                     "ForEach-Object { [ordered]@{Id=[string]$_.PNPDeviceID; Name=[string]$_.Name; Hardware=@($_.HardwareID); Compatible=@($_.CompatibleID); Error=$_.ConfigManagerErrorCode} }) } catch { $r.DevicesError = $_.Exception.Message }")
        parts.append("try { $r.DiskBus = @(Get-CimInstance -Namespace root/Microsoft/Windows/Storage -ClassName MSFT_PhysicalDisk -ErrorAction Stop | ForEach-Object { [int]$_.CimInstanceProperties['BusType'].Value }) } catch { }")
        parts.append("$r | ConvertTo-Json -Compress -Depth 5")
        return " ".join(parts)

    def bitlocker_state(self, bitlocker):
        if not isinstance(bitlocker, dict):
            return self.unknown_bitlocker
        source = bitlocker.get("Source")
        protection = str(bitlocker.get("ProtectionStatus") or "").strip()
        if source == "Shell":
            return self.shell_protection.get(protection, self.unknown_bitlocker)
        if protection.lower() in ("on", "1"):
            return "on"
        if protection.lower() in ("off", "0"):
            volume = str(bitlocker.get("VolumeStatus") or "").strip()
            if volume and volume not in ("FullyDecrypted", "0"):
                return "off (volume encrypted, protection suspended)"
            return "off"
        return self.unknown_bitlocker

    def parse(self, data):
        info = {
            "bitlocker": self.bitlocker_state(data.get("BitLocker")),
            "bitlocker_source": (data.get("BitLocker") or {}).get("Source"),
            "partition_style": None,
            "esp_mb": None,
            "secure_boot": None
        }
        if data.get("SecureBoot") in (0, 1):
            info["secure_boot"] = "on" if data["SecureBoot"] else "off"

        disk = data.get("Disk")
        if isinstance(disk, dict) and disk.get("PartitionStyle"):
            info["partition_style"] = str(disk.get("PartitionStyle")).upper()

        partitions = data.get("Esp") or []
        if isinstance(partitions, dict):
            partitions = [partitions]
        partitions = [partition for partition in partitions if isinstance(partition, dict) and partition.get("Size")]
        if partitions:
            system_number = disk.get("Number") if isinstance(disk, dict) else None
            on_system = [partition for partition in partitions if partition.get("DiskNumber") == system_number]
            chosen = (on_system or partitions)[0]
            info["esp_mb"] = int(chosen.get("Size")) // (1024 * 1024)
        info["firmware"] = self.parse_firmware(data)
        return info

    def as_list(self, value):
        if value is None:
            return []
        if isinstance(value, dict) and "value" in value:
            value = value["value"]
        return value if isinstance(value, list) else [value]

    def parse_firmware(self, data):
        def flag(value):
            return value if isinstance(value, bool) else None

        def strings(value):
            return [str(item).upper() for item in self.as_list(value) if isinstance(item, str) and item]

        machine = data.get("Machine") if isinstance(data.get("Machine"), dict) else None
        if machine:
            machine = dict(machine, Hypervisor=flag(machine.get("Hypervisor")))
        virtualization = data.get("Virtualization") if isinstance(data.get("Virtualization"), dict) else {}
        devices = None
        if "Devices" in data:
            devices = [{
                "id": str(device.get("Id") or "").upper(),
                "name": str(device.get("Name") or ""),
                "hardware": strings(device.get("Hardware")),
                "compatible": strings(device.get("Compatible")),
                "error": device.get("Error") if isinstance(device.get("Error"), int) else None
            } for device in self.as_list(data["Devices"]) if isinstance(device, dict)]
        return {
            "machine": machine,
            "vmx_capable": flag(virtualization.get("Capable")),
            "virt_enabled": flag(virtualization.get("Enabled")),
            "device_guard": [item for item in self.as_list(data.get("DeviceGuard")) if isinstance(item, int)] if "DeviceGuard" in data else None,
            "devices": devices,
            "disk_bus": [item for item in self.as_list(data.get("DiskBus")) if isinstance(item, int)] if "DiskBus" in data else None
        }

    def collect(self):
        if not self.windows:
            return None
        output = self._powershell(self.script(self.is_admin()))
        if output[2] != 0 or not output[0].strip():
            raise Exception((output[1] or output[0]).strip() or "PowerShell exited with code {}".format(output[2]))
        data = json.loads(output[0])
        if not isinstance(data, dict):
            raise Exception("unexpected PowerShell output")
        info = self.parse(data)
        info["firmware"].update(firmware_probe.collect())
        return info

    def warnings(self, info, org_hardware_report=None, hardware_report=None):
        warnings = []
        if info:
            if info.get("bitlocker") == "on":
                warnings.append(self.bitlocker_warning)
            elif info.get("bitlocker") == self.unknown_bitlocker:
                warnings.append(self.bitlocker_unknown_warning)
            if info.get("secure_boot") == "on":
                warnings.append(self.secure_boot_warning)
            if info.get("partition_style") == "MBR":
                warnings.append(self.mbr_warning)
            esp_mb = info.get("esp_mb")
            if esp_mb is not None and esp_mb < self.min_esp_mb:
                warnings.append(self.esp_warning.format(esp_mb))

        org_firmware = (org_hardware_report or {}).get("BIOS", {}).get("Firmware Type")
        firmware = (hardware_report or org_hardware_report or {}).get("BIOS", {}).get("Firmware Type")
        if org_firmware and org_firmware != "UEFI" and firmware == "UEFI":
            warnings.append(self.legacy_warning)
        return warnings

    def run(self, org_hardware_report=None, hardware_report=None):
        self.info = None
        self.info = self.collect()
        return self.info, self.warnings(self.info, org_hardware_report, hardware_report)

    def show(self, org_hardware_report=None, hardware_report=None, indent="    "):
        try:
            info, warnings = self.run(org_hardware_report, hardware_report)
        except Exception as e:
            print("{}preflight could not run: {}".format(indent, e))
            return None
        if info:
            print("{}BitLocker on the system drive: {}".format(indent, info.get("bitlocker")))
            print("{}Secure Boot: {}".format(indent, info.get("secure_boot") or "unknown"))
            print("{}System disk partition style: {}".format(indent, info.get("partition_style") or "unknown"))
            print("{}Windows EFI partition: {}".format(indent, "{} MB".format(info.get("esp_mb")) if info.get("esp_mb") is not None else "not found"))
        for warning in warnings:
            print("{}\033[91m- {}\033[0m".format(indent, warning))
        if not info and not warnings:
            print("{}Nothing to check on this system.".format(indent))
        return warnings

    def combine_bios_vendor(self, manufacturer, bios_versions):
        vendor = str(manufacturer or "").strip()
        if isinstance(bios_versions, str):
            bios_versions = [bios_versions]
        for entry in bios_versions or []:
            core = re.split(r"\s+-\s+", str(entry).strip())[0].strip()
            if re.search(r"insyde|american megatrends|phoenix", core, re.IGNORECASE) and core.lower() not in vendor.lower():
                vendor = "{} ({})".format(vendor, core) if vendor else core
                break
        return vendor or None

    def bios_vendor(self):
        if not self.windows:
            return None
        output = self._powershell("Get-CimInstance -ClassName Win32_BIOS -ErrorAction Stop | Select-Object Manufacturer,BIOSVersion | ConvertTo-Json -Compress")
        if output[2] != 0 or not output[0].strip():
            return None
        data = json.loads(output[0])
        if isinstance(data, list):
            data = data[0] if data else {}
        return self.combine_bios_vendor(data.get("Manufacturer"), data.get("BIOSVersion"))

    def add_bios_vendor(self, report_path):
        try:
            report = self.utils.read_file(report_path)
            if not isinstance(report, dict) or not isinstance(report.get("BIOS"), dict) or report["BIOS"].get("Vendor"):
                return False
            vendor = self.bios_vendor()
            if not vendor:
                return False
            report["BIOS"]["Vendor"] = vendor
            self.utils.write_file(report_path, report)
            return True
        except Exception:
            return False
