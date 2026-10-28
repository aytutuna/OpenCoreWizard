from Scripts import explain
from Scripts import run
from Scripts import utils
import hashlib
import json
import os
import re
import shutil
import time

class USBWriter:
    postinstall_folder = "PostInstall"
    postinstall_marker = "POST-INSTALL.md"

    def __init__(self):
        self.utils = utils.Utils()
        self.r = run.Run()
        self.unsupported_message = "USB creation is only supported on Windows for now. See the Dortania guide for macOS/Linux."
        self.max_fat32_mb = 32000
        self.min_disk_bytes = 1000 ** 3
        self.large_disk_bytes = 256 * 1000 ** 3
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        self.diskpart_exe = os.path.join(system_root, "System32", "diskpart.exe")
        self.powershell_exe = os.path.join(system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        self.ps_prefix = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "

    def _supported(self):
        if os.name != "nt":
            print(self.unsupported_message)
            return False
        return True

    def _powershell(self, command):
        return self.r.run({"args": [self.powershell_exe, "-NoProfile", "-NonInteractive", "-Command", self.ps_prefix + command]})

    def is_admin(self):
        if not self._supported():
            return None
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    def _disk_identity(self, item):
        return str(item.get("UniqueId") or item.get("SerialNumber") or "").strip()

    def list_usb_disks(self):
        if not self._supported():
            return None
        command = "Get-Disk | Select-Object Number,FriendlyName,Size,BusType,PartitionStyle,IsBoot,IsSystem,IsOffline,IsReadOnly,UniqueId,SerialNumber | ConvertTo-Json -Compress"
        out = self._powershell(command)
        if out[2] != 0:
            print("Could not list the disks (code {}):".format(out[2]))
            print(out[1].strip() or out[0].strip())
            return None
        try:
            data = json.loads(out[0]) if out[0].strip() else []
            if isinstance(data, dict):
                data = [data]
            disks = []
            for item in data:
                if str(item.get("BusType")).upper() != "USB":
                    continue
                if item.get("IsBoot") or item.get("IsSystem"):
                    continue
                if item.get("IsOffline") or item.get("IsReadOnly"):
                    continue
                if int(item.get("Size") or 0) < self.min_disk_bytes:
                    continue
                disks.append({
                    "number": int(item["Number"]),
                    "friendly_name": str(item.get("FriendlyName") or "").strip(),
                    "size_bytes": int(item.get("Size") or 0),
                    "bus_type": "USB",
                    "partition_style": str(item.get("PartitionStyle")),
                    "is_boot": bool(item.get("IsBoot")),
                    "is_system": bool(item.get("IsSystem")),
                    "identity": self._disk_identity(item)
                })
            return disks
        except Exception as e:
            print("Could not read the disk list: {}".format(e))
            if out[1]:
                print(out[1])
            return None

    def get_disk(self, number):
        if not self._supported():
            return None
        command = "Get-Disk -Number {} | Select-Object Number,FriendlyName,Size,BusType,PartitionStyle,IsBoot,IsSystem,IsOffline,IsReadOnly,UniqueId,SerialNumber | ConvertTo-Json -Compress".format(int(number))
        out = self._powershell(command)
        if out[2] != 0 or not out[0].strip():
            return None
        try:
            data = json.loads(out[0])
            if isinstance(data, list):
                data = data[0] if data else None
            return data
        except Exception:
            return None

    def check_disk_unchanged(self, disk):
        current = self.get_disk(disk["number"])
        if not current:
            return "Disk {} could not be queried again.".format(disk["number"])
        if str(current.get("BusType")).upper() != "USB":
            return "Disk {} is no longer a USB disk.".format(disk["number"])
        if current.get("IsBoot") or current.get("IsSystem"):
            return "Disk {} is now a boot or system disk.".format(disk["number"])
        if int(current.get("Size") or 0) != disk["size_bytes"]:
            return "The size of disk {} changed.".format(disk["number"])
        if self._disk_identity(current) != disk.get("identity", ""):
            return "The identity of disk {} changed.".format(disk["number"])
        return None

    def build_diskpart_script(self, disk_number, disk_size_mb, label="OPENCORE"):
        label = (label or "OPENCORE")[:11]
        if int(disk_size_mb) > self.max_fat32_mb:
            create = "create partition primary size={}".format(self.max_fat32_mb)
        else:
            create = "create partition primary"
        return "\n".join([
            "select disk {}".format(int(disk_number)),
            "clean",
            "convert gpt",
            create,
            "format fs=fat32 quick label={}".format(label),
            "assign",
            "exit"
        ]) + "\n"

    def prepare_disk(self, disk, dry_run=False):
        if not self._supported():
            return None
        size_mb = int(disk["size_bytes"] // (1024 * 1024))
        script = self.build_diskpart_script(disk["number"], size_mb, "OPENCORE")
        if dry_run:
            print("Dry run, diskpart script that would be executed:")
            print("")
            print(script)
            return (0, script)
        script_path = os.path.join(self.utils.get_temporary_dir(), "diskpart_script.txt")
        with open(script_path, "w") as f:
            f.write(script)
        out = self.r.run({"args": [self.diskpart_exe, "/s", script_path]})
        return (out[2], out[0] + out[1])

    def wait_for_drive_letter(self, disk_number, timeout_s=20):
        if not self._supported():
            return None
        command = "Get-Partition -DiskNumber {} | Where-Object DriveLetter | Select-Object -ExpandProperty DriveLetter".format(int(disk_number))
        deadline = time.time() + timeout_s
        while True:
            out = self._powershell(command)
            letters = [l.strip() for l in out[0].splitlines() if re.match(r"^[A-Za-z]$", l.strip())]
            if letters:
                return "{}:".format(letters[0].upper())
            if time.time() >= deadline:
                return None
            time.sleep(1)

    def _sha256(self, path):
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def _snapshot(self, root):
        files = {}
        for dirpath, _, filenames in os.walk(root):
            for name in filenames:
                full = os.path.join(dirpath, name)
                files[os.path.relpath(full, root)] = os.path.getsize(full)
        return files

    def _verify(self, src, dst):
        mismatches = []
        src_files = self._snapshot(src)
        dst_files = self._snapshot(dst)
        for rel, size in src_files.items():
            if rel not in dst_files:
                mismatches.append("Missing: {}".format(os.path.join(dst, rel)))
            elif dst_files[rel] != size:
                mismatches.append("Size differs: {}".format(os.path.join(dst, rel)))
            else:
                try:
                    same = self._sha256(os.path.join(src, rel)) == self._sha256(os.path.join(dst, rel))
                except Exception as e:
                    mismatches.append("Could not hash {}: {}".format(os.path.join(dst, rel), e))
                    continue
                if not same:
                    mismatches.append("SHA-256 differs: {}".format(os.path.join(dst, rel)))
        for rel in dst_files:
            if rel not in src_files:
                mismatches.append("Unexpected: {}".format(os.path.join(dst, rel)))
        return mismatches

    def _drive_root(self, drive):
        return drive + "\\"

    def copy_payload(self, drive, efi_dir, recovery_dir=None):
        if not self._supported():
            return None
        drive = str(drive).upper()
        if not re.match(r"^[A-Z]:$", drive):
            return (False, ["Invalid drive"])
        root = self._drive_root(drive)
        efi_source = os.path.join(efi_dir, "EFI")
        if not os.path.isdir(efi_source):
            efi_source = efi_dir
        jobs = [(efi_source, os.path.join(root, "EFI"))]
        if recovery_dir:
            jobs.append((recovery_dir, os.path.join(root, "com.apple.recovery.boot")))
        postinstall_source = self.postinstall_source(efi_dir)
        if postinstall_source:
            jobs.append((postinstall_source, os.path.join(root, self.postinstall_folder)))
        mismatches = []
        for src, dst in jobs:
            try:
                if os.path.basename(dst) in ("EFI", self.postinstall_folder) and os.path.isdir(dst):
                    shutil.rmtree(dst)
                print("Copying {} -> {}".format(src, dst))
                shutil.copytree(src, dst, dirs_exist_ok=True)
                mismatches.extend(self._verify(src, dst))
            except Exception as e:
                mismatches.append("Copy failed for {}: {}".format(dst, e))
        return (not mismatches, mismatches)

    def postinstall_source(self, efi_dir):
        path = os.path.join(efi_dir, self.postinstall_folder)
        if os.path.isfile(os.path.join(path, self.postinstall_marker)):
            return path
        return None

    def _confirm(self, prompt):
        return self.utils.request_input(prompt).strip().lower() in ("y", "yes")

    def _print_verification(self, ok, mismatches):
        if ok:
            print("Verification passed: all files match.")
        else:
            print("\033[91mVerification found {} problem(s):\033[0m".format(len(mismatches)))
            for m in mismatches[:20]:
                print("  - {}".format(m))
            if len(mismatches) > 20:
                print("  ...and {} more".format(len(mismatches) - 20))

    def create_installer(self, efi_dir, recovery_dir, dry_run=False, expected_disk=None):
        if not self._supported():
            return None
        print("")
        print(explain.wrap(explain.stages["usb_write"], 68))
        print("")
        if dry_run:
            print("Dry run: a real run needs administrator rights.")
        elif not self.is_admin():
            print("\033[91mAdministrator rights are required. Close this window and start OpenCore Wizard as administrator.\033[0m")
            return
        if not os.path.isfile(os.path.join(efi_dir, "EFI", "OC", "config.plist")):
            print("\033[91mBuild the OpenCore EFI first.\033[0m")
            return
        has_recovery = bool(recovery_dir) and os.path.isfile(os.path.join(recovery_dir, "BaseSystem.dmg"))
        if not has_recovery:
            print("\033[1;93mNo BaseSystem.dmg found in the recovery folder. The USB will boot OpenCore but cannot install macOS.\033[0m")
            print("")
            if not self._confirm("Continue anyway? (y/N): "):
                return
            print("")

        disks = self.list_usb_disks()
        if disks is None:
            return
        if not disks:
            print("No USB disks found. Insert a USB drive and try again.")
            return
        print("{:<8}{:<36}{:>10}  {}".format("Disk", "Name", "Size", "Style"))
        for d in disks:
            print("{:<8}{:<36}{:>7.1f} GB  {}".format(d["number"], d["friendly_name"][:34], d["size_bytes"] / 1e9, d["partition_style"]))
        print("")
        choice = self.utils.request_input("Enter the disk number to use: ").strip()
        disk = next((d for d in disks if choice.isdecimal() and d["number"] == int(choice)), None)
        if not disk:
            print("Invalid disk number.")
            return
        if expected_disk is not None and (disk["size_bytes"] != expected_disk.get("size_bytes") or disk.get("identity", "") != expected_disk.get("identity", "")):
            print("[91mDisk {} is not the drive that was selected. Aborted, nothing was changed.[0m".format(disk["number"]))
            return
        print("")
        print("\033[91mWARNING: ALL DATA on disk {} will be erased.\033[0m".format(disk["number"]))
        print("  {} ({:.1f} GB)".format(disk["friendly_name"], disk["size_bytes"] / 1e9))
        print("")
        if disk["size_bytes"] > self.large_disk_bytes:
            if not self._confirm("This looks like an external hard drive. Continue? (yes/No): "):
                print("Aborted.")
                return
            print("")
        if self.utils.request_input("Type ERASE to continue: ").strip() != "ERASE":
            print("Aborted.")
            return
        print("")

        problem = self.check_disk_unchanged(disk)
        if problem:
            print("\033[91m{} Aborted, nothing was changed.\033[0m".format(problem))
            return

        result = self.prepare_disk(disk, dry_run=dry_run)
        if dry_run:
            print("Dry run finished, nothing was changed.")
            return
        if result[0] != 0:
            print("\033[91mdiskpart failed (code {}):\033[0m".format(result[0]))
            print(result[1])
            print("\033[91mDisk {} may now be blank or partly erased.\033[0m".format(disk["number"]))
            return
        drive = self.wait_for_drive_letter(disk["number"])
        if not drive:
            print("\033[91mThe new partition did not get a drive letter.\033[0m")
            return
        print("USB drive ready at {}".format(drive))
        ok, mismatches = self.copy_payload(drive, efi_dir, recovery_dir if has_recovery else None)
        print("")
        self._print_verification(ok, mismatches)
        if not ok:
            print("\033[91mDo not use this USB drive until the copy has been repeated successfully.\033[0m")
            return
        print("")
        print("On {} you now have:".format(drive))
        print("  - EFI (OpenCore)")
        if has_recovery:
            print("  - com.apple.recovery.boot (macOS recovery)")
        if self.postinstall_source(efi_dir):
            print("  - PostInstall (post-install checklist, helper script and tools)")
        print("")
        print("Next: boot the target PC from this USB drive, then pick the recovery entry in the OpenCore picker.")

    def copy_efi_to_existing(self, efi_dir):
        if not self._supported():
            return None
        print("")
        if not os.path.isfile(os.path.join(efi_dir, "EFI", "OC", "config.plist")):
            print("\033[91mBuild the OpenCore EFI first.\033[0m")
            return
        letter = self.utils.request_input("Enter the USB drive letter (for example E): ").strip().rstrip(":\\/").upper()
        if len(letter) != 1 or not letter.isalpha():
            print("Invalid drive letter.")
            return
        out = self._powershell("Get-Partition -DriveLetter {} | Get-Disk | Select-Object Number,BusType,IsBoot,IsSystem | ConvertTo-Json -Compress".format(letter))
        info = None
        if out[2] == 0 and out[0].strip():
            try:
                info = json.loads(out[0])
                if isinstance(info, list):
                    info = info[0] if info else None
            except Exception:
                info = None
        if not info:
            print("Drive {}: was not found.".format(letter))
            if out[2] != 0 and out[1].strip():
                print(out[1].strip())
            return
        if str(info.get("BusType")).upper() != "USB" or info.get("IsBoot") or info.get("IsSystem"):
            print("Drive {}: is not an external USB drive (bus: {}).".format(letter, info.get("BusType")))
            return
        drive = "{}:".format(letter)
        existing = [name for name in ("EFI", self.postinstall_folder) if os.path.isdir(os.path.join(drive + "\\", name))]
        if self.postinstall_source(efi_dir):
            replaced = existing
        else:
            replaced = [name for name in existing if name == "EFI"]
            if self.postinstall_folder in existing:
                print("\033[1;93m{0}\\{1} is from an older build and the current build has no post-install kit (use option 9). It is left as it is and does not match the new EFI.\033[0m".format(drive, self.postinstall_folder))
        if replaced:
            if not self._confirm("{} will be replaced on {}. Continue? (y/N): ".format(", ".join(replaced), drive)):
                print("Aborted.")
                return
        ok, mismatches = self.copy_payload(drive, efi_dir)
        print("")
        self._print_verification(ok, mismatches)
        if ok and self.postinstall_source(efi_dir):
            print("PostInstall was copied to {} as well.".format(drive))
