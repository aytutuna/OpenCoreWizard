import ast
import contextlib
import importlib.util
import io
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

import repo_path  # noqa: F401
from repo_path import REPO_ROOT
from Scripts import config_prodigy
from Scripts import postinstall_kit
from Scripts import usb_writer
from Scripts.datasets import codec_layouts
from Scripts.datasets import kext_data
from Scripts.datasets import postinstall_tools

APPLE_GUID = postinstall_kit.APPLE_NVRAM_GUID
LILU_GUID = postinstall_kit.LILU_NVRAM_GUID


def load_main_module():
    spec = importlib.util.spec_from_file_location("opcore_simplify_main", os.path.join(REPO_ROOT, "OpenCoreWizard.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canned_report(vendor="American Megatrends International, LLC.", broadcom=False):
    report = {
        "Motherboard": {"Name": "Z490 AORUS ELITE", "Chipset": "Z490", "Platform": "Desktop"},
        "BIOS": {"Firmware Type": "UEFI", "Secure Boot": "Disabled", "Vendor": vendor},
        "Sound": {
            "Realtek High Definition Audio": {"Device ID": "10EC-1220", "Controller Device ID": "8086-A3F0"}
        },
        "Network": {
            "Intel(R) Ethernet Connection (11) I219-V": {"Device ID": "8086-0D4D", "PCI Path": "PciRoot(0x0)/Pci(0x1f,0x6)"}
        }
    }
    if broadcom:
        report["Network"]["Broadcom BCM4350"] = {"Device ID": "14E4-43A3", "PCI Path": "PciRoot(0x0)/Pci(0x1c,0x0)/Pci(0x0,0x0)"}
    return report


def canned_config(layout=7, alcid=None, revpatch="sbvmm,cpuname"):
    boot_args = "keepsyms=1 debug=0x100"
    if alcid is not None:
        boot_args += " alcid={}".format(alcid)
    properties = {}
    if layout is not None:
        properties["PciRoot(0x0)/Pci(0x1f,0x3)"] = {"layout-id": layout}
    return {
        "DeviceProperties": {"Add": properties},
        "Misc": {"Security": {"SecureBootModel": "Disabled"}},
        "NVRAM": {"Add": {
            APPLE_GUID: {"boot-args": boot_args, "csr-active-config": bytes.fromhex("030A0000")},
            LILU_GUID: {"revpatch": revpatch} if revpatch else {}
        }},
        "PlatformInfo": {"Generic": {
            "SystemProductName": "iMac20,1",
            "SystemSerialNumber": "SERIAL123456",
            "MLB": "MLB1234567890ABCD",
            "SystemUUID": "01234567-89AB-CDEF-0123-456789ABCDEF",
            "ROM": bytes.fromhex("112233AABBCC")
        }}
    }


def summary(**overrides):
    options = {
        "needs_oclp": False,
        "whatevergreen_first_boot": None,
        "applealc": True,
        "simplehda": True,
        "bcmc": False,
        "usb_map_kext": None
    }
    vendor = overrides.pop("vendor", "American Megatrends International, LLC.")
    config = overrides.pop("config", None) or canned_config()
    options.update(overrides)
    needs_oclp = options.pop("needs_oclp")
    return postinstall_kit.make_build_summary(
        canned_report(vendor, broadcom=options["bcmc"]), config, "iMac20,1", "25.0.0", needs_oclp,
        ocvalidate="passed", **options)


def section_titles(text):
    return re.findall(r"^## \d+\. (.+)$", text, re.MULTILINE)


class FailingFetcher:
    def fetch_and_parse_content(self, *args, **kwargs):
        raise AssertionError("fetch_and_parse_content must not be called")

    def download_and_save_file(self, *args, **kwargs):
        raise AssertionError("download_and_save_file must not be called")


class WritingFetcher:
    def __init__(self):
        self.calls = []

    def fetch_and_parse_content(self, *args, **kwargs):
        raise AssertionError("no API lookups expected")

    def download_and_save_file(self, url, destination, sha256=None):
        self.calls.append((url, destination, sha256))
        with open(destination, "wb") as output:
            output.write(url.encode("utf-8"))
        return True


class RefusingFetcher:
    def __init__(self):
        self.calls = 0

    def fetch_and_parse_content(self, *args, **kwargs):
        return None

    def download_and_save_file(self, url, destination, sha256=None):
        self.calls += 1
        return False


class ChecklistTest(unittest.TestCase):
    def setUp(self):
        self.kit = postinstall_kit.PostInstallKit(fetcher=FailingFetcher())

    def render(self, **overrides):
        return self.kit.render_checklist(summary(**overrides))

    def test_always_present_sections_in_install_order(self):
        titles = section_titles(self.render())
        for title in ("First boot checks", "Move the EFI to the internal disk", "USB ports",
                      "Before signing in with an Apple Account", "Sleep", "If it does not boot"):
            self.assertIn(title, titles)
        self.assertEqual(titles[0], "First boot checks")
        self.assertEqual(titles[-1], "If it does not boot")

    def test_sections_are_numbered_in_sequence(self):
        text = self.render(needs_oclp=True, bcmc=True, whatevergreen_first_boot={"agdp_patch": True})
        numbers = [int(number) for number in re.findall(r"^## (\d+)\. ", text, re.MULTILINE)]
        self.assertEqual(numbers, list(range(1, len(numbers) + 1)))
        self.assertEqual(len(numbers), 11)

    def test_full_order_matches_the_install(self):
        titles = section_titles(self.render(needs_oclp=True, bcmc=True, whatevergreen_first_boot={"agdp_patch": False}))
        self.assertEqual(titles, [
            "First boot checks", "Re-enable WhateverGreen", "Move the EFI to the internal disk",
            "Root patches", "Audio", "Broadcom Wi-Fi firmware", "USB ports",
            "Before signing in with an Apple Account", "Sleep", "macOS updates", "If it does not boot"
        ])

    def test_oclp_section_toggles(self):
        off = self.render(needs_oclp=False)
        self.assertNotIn("Root patches", section_titles(off))
        self.assertNotIn("OCLP-Mod", off)
        self.assertNotIn("MacPatch", off)
        on = self.render(needs_oclp=True)
        self.assertIn("Root patches", section_titles(on))
        self.assertIn("Root patches: needed (OCLP-Mod)", on)
        self.assertNotIn("Root Patch.command", on)
        oclp = next(tool for tool in postinstall_tools.tools if tool["name"] == "OCLP-Mod")
        self.assertIn(oclp["sha256"], on)
        self.assertIn("FileVault must be off", on)
        self.assertIn("again after every macOS update", on)
        self.assertIn("csr-active-config 030A0000", on)
        self.assertIn("SecureBootModel (Disabled)", on)

    def test_tahoe_audio_through_oclp_is_explained(self):
        text = self.render(needs_oclp=True, simplehda=False)
        self.assertIn("including AppleHDA for analog audio", text)
        self.assertIn("analog audio works only after the AppleHDA root patch", text)

    def test_broadcom_section_toggles(self):
        off = self.render(bcmc=False)
        self.assertNotIn("Broadcom Wi-Fi firmware", section_titles(off))
        self.assertNotIn("/usr/local/share/firmware/wifi", off)
        on = self.render(bcmc=True)
        self.assertIn("Broadcom Wi-Fi firmware", section_titles(on))
        self.assertIn("sudo mkdir -p /usr/local/share/firmware/wifi && sudo cp \"Tools/brcmfmac4350-pcie_7.35.180.119.bin\" /usr/local/share/firmware/wifi/", on)
        self.assertIn("panics during macOS updates", on)
        self.assertNotIn("brcmfmac43602", on)

    def test_whatevergreen_section_toggles(self):
        self.assertNotIn("Re-enable WhateverGreen", section_titles(self.render()))
        plain = self.render(whatevergreen_first_boot={"agdp_patch": False})
        self.assertIn("Re-enable WhateverGreen", section_titles(plain))
        self.assertIn("Kernel -> Add -> WhateverGreen.kext -> Enabled to True", plain)
        self.assertNotIn("agdpmod=pikera", plain)
        agdp = self.render(whatevergreen_first_boot={"agdp_patch": True})
        self.assertIn("AGDP board-id patch (pikera) for first boot without WhateverGreen", agdp)
        self.assertIn("agdpmod=pikera", agdp)

    def test_usb_map_present_and_absent(self):
        mapped = self.render(usb_map_kext="UTBMap.kext")
        self.assertIn("This build uses your USB map (UTBMap.kext)", mapped)
        self.assertNotIn("still uses UTBDefault.kext", mapped)
        default = self.render(usb_map_kext=None)
        self.assertIn("still uses UTBDefault.kext", default)
        self.assertIn("option 6 (USB Port Mapping)", default)

    def test_insyde_uses_short_launcher_option(self):
        insyde = self.render(vendor="Insyde Corp.")
        self.assertIn("LauncherOption to Short", insyde)
        self.assertNotIn("LauncherOption to Full", insyde)
        other = self.render()
        self.assertIn("LauncherOption to Full", other)

    def test_codec_alternatives_are_listed(self):
        text = self.render()
        self.assertIn("Realtek High Definition Audio (10EC-1220)", text)
        self.assertIn("Layout ID in this build: 7", text)
        rows = re.findall(r"^   \| (\d+) \|", text, re.MULTILINE)
        expected = [str(layout.id) for layout in codec_layouts.data["10EC-1220"] if layout.id != 7]
        self.assertEqual(rows, expected)
        self.assertIn("alcid=<id>", text)

    def test_audio_section_absent_without_applealc(self):
        text = self.render(applealc=False)
        self.assertNotIn("Audio", section_titles(text))

    def test_updates_section_needs_sbvmm(self):
        self.assertIn("macOS updates", section_titles(self.render()))
        text = self.render(config=canned_config(revpatch=""))
        self.assertNotIn("macOS updates", section_titles(text))

    def test_apple_account_and_sleep_content(self):
        text = self.render()
        self.assertIn("sudo nvram myvar=test", text)
        self.assertIn("nvram myvar", text)
        self.assertIn("IOBuiltin", text)
        self.assertIn("smbios.txt", text)
        for line in postinstall_kit.PMSET_LINES:
            self.assertIn(line, text)

    def test_plain_ascii(self):
        text = self.render(needs_oclp=True, bcmc=True, whatevergreen_first_boot={"agdp_patch": True})
        self.assertTrue(text.isascii())


class CommandScriptTest(unittest.TestCase):
    def setUp(self):
        self.kit = postinstall_kit.PostInstallKit(fetcher=FailingFetcher())

    def test_pmset_offer_is_confirmed(self):
        script = self.kit.render_command(summary())
        self.assertTrue(script.startswith("#!/bin/bash\n"))
        self.assertIn("if confirm \"Apply these pmset settings now?\"", script)
        self.assertNotIn("firmware/wifi", script)
        self.assertNotIn("WhateverGreen", script)

    def test_firmware_copy_only_with_companion(self):
        script = self.kit.render_command(summary(bcmc=True))
        firmware = next(tool for tool in postinstall_tools.tools if tool["dest"].startswith("brcmfmac4350"))
        self.assertIn(firmware["sha256"], script)
        self.assertIn("elif confirm \"Copy $fw to /usr/local/share/firmware/wifi", script)
        self.assertEqual(script.count("confirm \""), 3)

    def test_efi_move_is_confirmed_backed_up_and_keeps_windows(self):
        script = self.kit.render_command(summary())
        self.assertIn("if confirm \"Copy OpenCore to this disk?\"; then", script)
        self.assertIn("LAUNCHER=\"Full\"", script)
        self.assertIn("plutil -replace Misc.Boot.LauncherOption -string \"$LAUNCHER\"", script)
        self.assertLess(script.index("EFI backup"), script.index("sudo cp -R \"$SOURCE/OC\" \"$SOURCE/BOOT\""))
        self.assertIn("sudo rm -rf \"$mp/EFI/OC\" \"$mp/EFI/BOOT\"", script)
        self.assertNotIn("rm -rf \"$mp/EFI\"", script)
        self.assertIn("[ \"$disk\" = \"$stick\" ]", script)
        self.assertIn("LAUNCHER=\"Short\"", self.kit.render_command(summary(vendor="Insyde Corp.")))

    def test_graphics_step_only_when_whatevergreen_was_left_off(self):
        script = self.kit.render_command(summary(whatevergreen_first_boot={"agdp_patch": False}))
        self.assertIn("plutil -replace \"Kernel.Add.$weg.Enabled\" -bool YES", script)
        self.assertNotIn("agdpmod=pikera", script)
        self.assertLess(script.index("step \"Graphics driver\""), script.index("step \"Start without the USB stick\""))
        script = self.kit.render_command(summary(whatevergreen_first_boot={"agdp_patch": True}))
        self.assertIn(postinstall_kit.AGDP_PATCH_COMMENT, script)
        self.assertIn("agdpmod=pikera", script)


class GuideTest(unittest.TestCase):
    def setUp(self):
        self.kit = postinstall_kit.PostInstallKit(fetcher=FailingFetcher())

    def titles(self, data):
        return [title for title, _ in self.kit.guide_steps(data)]

    def test_steps_follow_the_build(self):
        self.assertEqual(self.titles(summary()), ["Check the basics", "Run the setup helper", "Start without the USB stick", "Sign in with your Apple Account", "Updates"])
        self.assertIn("Turn on the remaining parts", self.titles(summary(needs_oclp=True)))
        helper = dict(self.kit.guide_steps(summary(whatevergreen_first_boot={"agdp_patch": False})))["Run the setup helper"]
        self.assertTrue(any("graphics helper" in item for item in helper[-1]))

    def test_page_is_plain_ascii_html_with_the_command(self):
        page = self.kit.render_guide(summary(needs_oclp=True, bcmc=True, whatevergreen_first_boot={"agdp_patch": True}))
        self.assertTrue(page.isascii())
        self.assertTrue(page.startswith("<!doctype html>"))
        self.assertIn("bash /Volumes/OPENCORE/PostInstall/post-install.command", page)
        self.assertIn("Z490 AORUS ELITE", page)
        self.assertIn("press F12 while the PC starts", page)

    def test_boot_menu_key_follows_the_maker(self):
        data = summary()
        data["motherboard"], data["platform"] = "ASUSTEK ZENBOOK UX534FT_UX534FT", "Laptop"
        steps = dict(self.kit.guide_steps(data))
        self.assertIn("press Esc while the PC starts", steps["Start without the USB stick"][1])


def find_bash():
    if os.name == "nt":
        # only Git for Windows' bash: the System32 one is WSL and would not see this temp folder
        path = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Git", "bin", "bash.exe")
        return path if os.path.isfile(path) else None
    return shutil.which("bash")


FAKE_MAC = r'''#!/bin/bash
# builds a fake macOS around the helper: a USB stick, an EFI partition with Windows on it, and stand-ins for
# diskutil, plutil, ioreg, sudo and uname that answer like macOS does
T="$1"; PY="$2"; HELPER="$3"
STICK="$T/Volumes/OPENCORE"; ESP="$T/esp"; BIN="$T/bin"
mkdir -p "$STICK/EFI/OC/Kexts/Lilu.kext" "$STICK/EFI/BOOT" "$STICK/PostInstall" "$ESP/EFI/Microsoft/Boot" "$ESP/EFI/BOOT" "$BIN" "$T/home/Desktop"
echo opencore > "$STICK/EFI/OC/OpenCore.efi"
echo lilu > "$STICK/EFI/OC/Kexts/Lilu.kext/Info.plist"
echo bootx64-opencore > "$STICK/EFI/BOOT/BOOTx64.efi"
echo bootmgfw > "$ESP/EFI/Microsoft/Boot/bootmgfw.efi"
echo bootx64-windows > "$ESP/EFI/BOOT/BOOTx64.efi"
cp "$HELPER" "$STICK/PostInstall/post-install.command"
printf '#!/bin/bash\necho Darwin\n' > "$BIN/uname"
printf '#!/bin/bash\n[ "$1" = "-v" ] && exit 0\nexec "$@"\n' > "$BIN/sudo"
printf '#!/bin/bash\nexit 0\n' > "$BIN/chown"
printf '#!/bin/bash\nexec "%s" "%s" "$@"\n' "$PY" "$T/plutil.py" > "$BIN/plutil"
cat > "$BIN/ioreg" <<'EOF'
#!/bin/bash
printf '+-o en1  <class IO80211Interface>\n  {\n    "BSD Name" = "en1"\n    "IOBuiltin" = No\n  }\n'
printf '+-o en0  <class IOEthernetInterface>\n  {\n    "IOBuiltin" = Yes\n    "BSD Name" = "en0"\n  }\n'
EOF
cat > "$BIN/diskutil" <<EOF
#!/bin/bash
FLAG="$T/esp.mounted"
case "\$1 \$2" in
  "info /") printf '   Device Identifier:         disk3s1s1\n   APFS Physical Store:       disk0s2\n' ;;
  "info disk0") printf '   Device / Media Name:       Samsung SSD 970 EVO 500GB\n   Disk Size:                 500.1 GB (500107862016 Bytes) (exactly 976773168 512-Byte-Units)\n' ;;
  "info disk0s1") if [ -f "\$FLAG" ]; then printf '   Mount Point:               $ESP\n'; else printf '   Mounted:                   No\n'; fi ;;
  "list disk0") printf '   0:      GUID_partition_scheme                        *500.1 GB   disk0\n   1:                        EFI EFI                     209.7 MB   disk0s1\n   2:                 Apple_APFS Container disk3         499.9 GB   disk0s2\n' ;;
  "mount disk0s1") touch "\$FLAG" ;;
  "unmount disk0s1") rm -f "\$FLAG" ;;
  info*) case "\$2" in *OPENCORE*) printf '   Device Identifier:         disk2s1\n' ;; *) exit 1 ;; esac ;;
  *) exit 1 ;;
esac
EOF
chmod +x "$BIN"/*
'''

FAKE_PLUTIL = r'''import plistlib, sys
args = sys.argv[1:]
if args[0] == "-lint":
    plistlib.load(open(args[1], "rb"))
    sys.exit(0)
path = args[-1]
root = plistlib.load(open(path, "rb"))
parts = args[1].split(".")
try:
    parent = root
    for part in parts[:-1]:
        parent = parent[int(part)] if isinstance(parent, list) else parent[part]
    last = int(parts[-1]) if isinstance(parent, list) else parts[-1]
    if args[0] == "-extract":
        value = parent[last]
        print(("true" if value else "false") if isinstance(value, bool) else value)
        sys.exit(0)
    if args[0] == "-remove":
        del parent[last]
    else:
        parent[last] = args[3] == "YES" if args[2] == "-bool" else int(args[3]) if args[2] == "-integer" else args[3]
except (KeyError, IndexError, ValueError):
    sys.exit(1)
plistlib.dump(root, open(path, "wb"))
'''


@unittest.skipUnless(find_bash(), "needs bash")
class HelperRunTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp, True)
        self.root = self.temp.replace("\\", "/")
        kit = postinstall_kit.PostInstallKit(fetcher=FailingFetcher())
        helper = os.path.join(self.temp, "helper.command")
        with open(helper, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(kit.render_command(summary(whatevergreen_first_boot={"agdp_patch": True})))
        with open(os.path.join(self.temp, "plutil.py"), "w", encoding="utf-8") as handle:
            handle.write(FAKE_PLUTIL)
        with open(os.path.join(self.temp, "fake_mac.sh"), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(FAKE_MAC)
        self.stick_config = os.path.join(self.temp, "Volumes", "OPENCORE", "EFI", "OC", "config.plist")
        self.bash([self.root + "/fake_mac.sh", self.root, sys.executable.replace("\\", "/"), self.root + "/helper.command"])
        with open(self.stick_config, "wb") as handle:
            plistlib.dump({
                "Kernel": {"Add": [{"BundlePath": "Lilu.kext", "Enabled": True}, {"BundlePath": "WhateverGreen.kext", "Enabled": False}],
                           "Patch": [{"Comment": "other"}, {"Comment": postinstall_kit.AGDP_PATCH_COMMENT}]},
                "NVRAM": {"Add": {APPLE_GUID: {"boot-args": "keepsyms=1"}}},
                "Misc": {"Boot": {"LauncherOption": "Disabled"}}
            }, handle)

    def bash(self, args, answers=""):
        env = dict(os.environ, HOME=os.path.join(self.temp, "home"), FAKE_BIN=self.root + "/bin")
        # Git for Windows' bash puts its own folders first in PATH, so the stand-ins go in front from inside bash
        prefix = 'PATH="$(cygpath -u "$FAKE_BIN" 2>/dev/null || echo "$FAKE_BIN"):$PATH" exec bash "$@"'
        # bytes, so Windows does not turn the answers' newlines into CRLF
        result = subprocess.run([find_bash(), "-c", prefix, "bash"] + args, input=answers.encode(), capture_output=True, env=env, timeout=120)
        output = result.stdout.decode("utf-8", "replace")
        self.assertEqual(result.returncode, 0, output + result.stderr.decode("utf-8", "replace"))
        return output

    def run_helper(self, answers):
        return self.bash([self.root + "/Volumes/OPENCORE/PostInstall/post-install.command"], answers)

    def test_graphics_first_then_the_move(self):
        output = self.run_helper("y\n")
        self.assertIn("Restart the PC and start from the USB stick again", output)
        self.assertNotIn("Start without the USB stick", output)
        with open(self.stick_config, "rb") as handle:
            config = plistlib.load(handle)
        self.assertTrue(config["Kernel"]["Add"][1]["Enabled"])
        self.assertEqual([patch["Comment"] for patch in config["Kernel"]["Patch"]], ["other"])
        self.assertEqual(config["NVRAM"]["Add"][APPLE_GUID]["boot-args"], "keepsyms=1 agdpmod=pikera")

        output = self.run_helper("n\ny\n")
        self.assertIn("The graphics helper is on.", output)
        self.assertIn("macOS runs from: Samsung SSD 970 EVO 500GB (500.1 GB), EFI partition disk0s1", output)
        self.assertIn("Done: OpenCore is on the disk macOS runs from", output)
        self.assertIn("Ready: the network port en0 is marked as built-in", output)
        esp = os.path.join(self.temp, "esp", "EFI")
        with open(os.path.join(esp, "BOOT", "BOOTx64.efi")) as handle:
            self.assertEqual(handle.read().strip(), "bootx64-opencore")
        self.assertTrue(os.path.isfile(os.path.join(esp, "Microsoft", "Boot", "bootmgfw.efi")))
        self.assertFalse(os.path.exists(os.path.join(esp, "OC", "config-before-graphics.plist")))
        with open(os.path.join(esp, "OC", "config.plist"), "rb") as handle:
            boot = plistlib.load(handle)["Misc"]["Boot"]
        self.assertEqual(boot["LauncherOption"], "Full")
        self.assertEqual(boot["Timeout"], 5)
        self.assertIs(boot["HideAuxiliary"], True)
        backups = os.listdir(os.path.join(self.temp, "home", "Desktop"))
        self.assertEqual(len(backups), 1)
        with open(os.path.join(self.temp, "home", "Desktop", backups[0], "BOOT", "BOOTx64.efi")) as handle:
            self.assertEqual(handle.read().strip(), "bootx64-windows")
        self.assertFalse(os.path.exists(os.path.join(self.temp, "esp.mounted")))

    def test_declining_changes_nothing(self):
        output = self.run_helper("n\nn\nn\n")
        self.assertIn("Skipped. Keep starting from the USB stick", output)
        self.assertFalse(os.path.exists(os.path.join(self.temp, "esp", "EFI", "OC")))
        self.assertEqual(os.listdir(os.path.join(self.temp, "home", "Desktop")), [])


class BuildSummaryTest(unittest.TestCase):
    def test_values_from_report_and_config(self):
        config = canned_config(layout=bytes.fromhex("0B000000"), alcid=11)
        result = postinstall_kit.make_build_summary(canned_report("Insyde Corp.", broadcom=True), config, "MacBookPro16,2", "25.0.0", True,
                                                    ocvalidate="errors", whatevergreen_first_boot={"agdp_patch": True},
                                                    applealc=True, simplehda=False, bcmc=True, usb_map_kext="UTBMap.kext")
        self.assertEqual(result["audio"]["layout_id"], 11)
        self.assertEqual(result["audio"]["alcid"], 11)
        self.assertNotIn(11, [item["id"] for item in result["audio"]["alternatives"]])
        self.assertTrue(result["insyde"])
        self.assertEqual(result["sip"], "030A0000")
        self.assertEqual(result["revpatch"], ["sbvmm", "cpuname"])
        self.assertEqual(result["broadcom_companion"], [{"device": "Broadcom BCM4350", "device_id": "14E4-43A3", "firmware": "brcmfmac4350-pcie_7.35.180.119.bin"}])
        self.assertEqual(result["usb_map"], {"custom": True, "kext": "UTBMap.kext"})
        self.assertEqual(result["whatevergreen_first_boot"], {"agdp_patch": True})
        self.assertEqual(result["macos_name"], "macOS Tahoe 26")
        json.dumps(result)

    def test_companion_off_lists_no_firmware(self):
        result = summary(bcmc=False)
        self.assertEqual(result["broadcom_companion"], [])
        self.assertEqual(result["usb_map"], {"custom": False, "kext": "UTBDefault.kext"})

    def test_summary_round_trip(self):
        kit = postinstall_kit.PostInstallKit(fetcher=FailingFetcher())
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, postinstall_kit.SUMMARY_NAME)
            kit.save_summary(summary(), path)
            self.assertEqual(kit.load_summary(path)["smbios_model"], "iMac20,1")
            with open(path, "w", encoding="utf-8") as stale:
                json.dump({"version": 0}, stale)
            self.assertIsNone(kit.load_summary(path))
            self.assertIsNone(kit.load_summary(os.path.join(temp, "missing.json")))


class SmbiosTextTest(unittest.TestCase):
    def setUp(self):
        self.kit = postinstall_kit.PostInstallKit(fetcher=FailingFetcher())

    def test_values_from_plist(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "config.plist")
            with open(path, "wb") as config_file:
                plistlib.dump(canned_config(), config_file)
            text = self.kit.render_smbios(self.kit.read_smbios(path))
        lines = text.splitlines()
        self.assertIn("private", lines[0])
        for line in ("SystemProductName: iMac20,1", "SystemSerialNumber: SERIAL123456", "MLB: MLB1234567890ABCD",
                     "SystemUUID: 01234567-89AB-CDEF-0123-456789ABCDEF", "ROM: 112233aabbcc"):
            self.assertIn(line, lines)

    def test_missing_plist(self):
        self.assertIsNone(self.kit.read_smbios(os.path.join(tempfile.gettempdir(), "no-such-config.plist")))
        self.assertIn("could not be read", self.kit.render_smbios(None))


class DatasetTest(unittest.TestCase):
    def test_entries_are_well_formed(self):
        names = set()
        dests = set()
        for tool in postinstall_tools.tools:
            for field in ("name", "purpose", "repo", "asset", "dest", "when", "page"):
                self.assertTrue(tool.get(field), "{} lacks {}".format(tool.get("name"), field))
            self.assertRegex(tool["repo"], r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
            self.assertIn(tool["when"], postinstall_tools.allowed_when)
            self.assertTrue(tool.get("tag") or tool.get("commit") or re.search(r"/[0-9a-f]{40}/", tool.get("url") or ""), tool["name"])
            self.assertNotIn("archive/refs/heads", tool.get("url") or "")
            if tool["download"]:
                self.assertIsNotNone(tool.get("sha256"), tool["name"])
            if tool.get("sha256") is not None:
                self.assertRegex(tool["sha256"], r"^[0-9a-f]{64}$")
            self.assertNotIn("/", tool["dest"])
            self.assertNotIn("\\", tool["dest"])
            self.assertIsInstance(tool.get("download"), bool)
            self.assertTrue(tool["page"].startswith("https://github.com/"))
            if tool.get("url"):
                self.assertTrue(tool["url"].startswith("https://"))
            self.assertNotIn(tool["name"], names)
            self.assertNotIn(tool["dest"], dests)
            names.add(tool["name"])
            dests.add(tool["dest"])
        for name in ("OCLP-Mod", "Hackintool", "ProperTree", "MountEFI"):
            self.assertIn(name, names)

    def test_tagged_assets_have_digests(self):
        for tool in postinstall_tools.tools:
            if tool.get("tag"):
                self.assertIsNotNone(tool["sha256"], tool["name"])

    def test_firmware_matches_build(self):
        firmware = [tool for tool in postinstall_tools.tools if tool["when"] == "broadcom_companion"]
        self.assertEqual(len(firmware), 2)
        for tool in firmware:
            self.assertTrue(tool["url"].startswith("https://raw.githubusercontent.com/0xFireWolf/AppleBCMWLANCompanion/"))
            for device_id in tool["device_ids"]:
                expected = config_prodigy.bcmc_firmwares[device_id]
                self.assertEqual(tool["dest"], expected[0])
                self.assertEqual(tool["sha256"], expected[1])
        covered = set(device_id for tool in firmware for device_id in tool["device_ids"])
        self.assertEqual(covered, set(config_prodigy.bcmc_firmwares))


class KitBuildTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp, "EFI", "OC", "config.plist")
        os.makedirs(os.path.dirname(self.config_path))
        with open(self.config_path, "wb") as config_file:
            plistlib.dump(canned_config(), config_file)
        self.dest = os.path.join(self.temp, postinstall_kit.KIT_FOLDER)

    def tearDown(self):
        shutil.rmtree(self.temp, ignore_errors=True)

    def build(self, kit, mode, data=None):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            result = kit.build(data or summary(bcmc=True, needs_oclp=True), self.dest, mode)
        return result, output.getvalue()

    def test_docs_only_writes_three_files_and_downloads_nothing(self):
        result, _ = self.build(postinstall_kit.PostInstallKit(fetcher=FailingFetcher()), "docs-only")
        self.assertEqual(sorted(os.listdir(self.dest)), sorted(postinstall_kit.GENERATED_FILES + (postinstall_kit.DOCS_FOLDER,)))
        self.assertFalse(os.path.exists(os.path.join(self.dest, postinstall_kit.TOOLS_FOLDER)))
        self.assertNotIn("downloaded", result["tools"].values())
        with open(os.path.join(self.dest, postinstall_kit.SMBIOS_NAME), "r", encoding="utf-8") as smbios_file:
            self.assertIn("SystemSerialNumber: SERIAL123456", smbios_file.read())
        with open(os.path.join(self.dest, postinstall_kit.COMMAND_NAME), "rb") as command_file:
            self.assertNotIn(b"\r\n", command_file.read())

    def test_unknown_mode_rejected(self):
        with self.assertRaises(ValueError):
            postinstall_kit.PostInstallKit(fetcher=FailingFetcher()).build(summary(), self.dest, "everything")

    def test_failed_downloads_still_produce_the_kit(self):
        fetcher = RefusingFetcher()
        result, output = self.build(postinstall_kit.PostInstallKit(fetcher=fetcher), "full")
        self.assertTrue(fetcher.calls)
        self.assertIn("could not be downloaded", output)
        self.assertEqual(result["tools"]["OCLP-Mod"], "link")
        self.assertEqual(result["tools"]["Hackintool"], "failed")
        for name in postinstall_kit.GENERATED_FILES:
            self.assertTrue(os.path.isfile(os.path.join(self.dest, name)))
        with open(os.path.join(self.dest, postinstall_kit.CHECKLIST_NAME), "r", encoding="utf-8") as checklist:
            self.assertIn("https://github.com/benbaker76/Hackintool/releases/tag/4.1.5", checklist.read())
        self.assertEqual([name for name in os.listdir(os.path.join(self.dest, postinstall_kit.TOOLS_FOLDER)) if name.endswith(".part")], [])

    def test_manifest_records_hashes_and_repeat_build_reuses_files(self):
        data = summary(bcmc=True, needs_oclp=True)
        kit = postinstall_kit.PostInstallKit(fetcher=WritingFetcher())
        kit.tools =[dict(tool, sha256=None) if tool["download"] else tool for tool in postinstall_tools.tools]
        result, _ = self.build(kit, "full", data)
        self.assertEqual(result["tools"]["Hackintool"], "downloaded")
        self.assertEqual(result["tools"]["OCLP-Mod"], "link")
        self.assertNotIn("OCLP-Mod", [os.path.basename(call[1]) for call in kit.fetcher.calls])
        self.assertFalse(os.path.exists(os.path.join(self.dest, "Tools", "OCLP-Mod.pkg")))
        with open(os.path.join(self.dest, "Tools", postinstall_kit.MANIFEST_NAME), "r", encoding="utf-8") as manifest_file:
            manifest = json.load(manifest_file)
        self.assertEqual(set(manifest), {"Hackintool.zip", "ProperTree.zip", "MountEFI.zip", "brcmfmac4350-pcie_7.35.180.119.bin"})
        self.assertRegex(manifest["Hackintool.zip"]["sha256"], r"^[0-9a-f]{64}$")

        kit.fetcher = FailingFetcher()
        result, output = self.build(kit, "full", data)
        self.assertEqual(set(result["tools"].values()), {"present", "link"})
        self.assertIn("already in the kit", output)
        with open(os.path.join(self.dest, postinstall_kit.CHECKLIST_NAME), "r", encoding="utf-8") as checklist:
            self.assertIn("`Tools/Hackintool.zip`", checklist.read())

    def test_changed_file_is_downloaded_again(self):
        data = summary()
        kit = postinstall_kit.PostInstallKit(fetcher=WritingFetcher())
        kit.tools = [dict(tool, sha256=None) for tool in postinstall_tools.tools if tool["name"] == "ProperTree"]
        self.build(kit, "full", data)
        with open(os.path.join(self.dest, "Tools", "ProperTree.zip"), "wb") as tampered:
            tampered.write(b"changed")
        result, _ = self.build(kit, "full", data)
        self.assertEqual(result["tools"]["ProperTree"], "downloaded")
        self.assertEqual(len(kit.fetcher.calls), 2)

    def test_wildcard_asset_uses_release_digest(self):
        kit = postinstall_kit.PostInstallKit(fetcher=FailingFetcher())
        kit.fetcher = mock.Mock()
        kit.fetcher.fetch_and_parse_content.return_value = {"assets": [
            {"name": "Other.zip", "browser_download_url": "https://example.invalid/other", "digest": "sha256:" + "1" * 64},
            {"name": "Tool-1.2.zip", "browser_download_url": "https://example.invalid/tool", "digest": "sha256:" + "a" * 64}
        ]}
        tool = {"name": "Tool", "repo": "owner/tool", "tag": "1.2", "branch": None, "asset": "Tool-*.zip", "url": None, "sha256": None}
        self.assertEqual(kit.resolve_download(tool), ("https://example.invalid/tool", "a" * 64))
        kit.fetcher.fetch_and_parse_content.assert_called_once_with("https://api.github.com/repos/owner/tool/releases/tags/1.2", "json")

    def test_download_urls(self):
        kit = postinstall_kit.PostInstallKit(fetcher=FailingFetcher())
        by_name = {tool["name"]: tool for tool in postinstall_tools.tools}
        self.assertEqual(kit.resolve_download(by_name["Hackintool"])[0], "https://github.com/benbaker76/Hackintool/releases/download/4.1.5/Hackintool.zip")
        self.assertEqual(kit.resolve_download(by_name["MountEFI"]), ("https://github.com/corpnewt/MountEFI/archive/5310a71c9a46ab053afb9588cf2abc4d44fccb49.zip", by_name["MountEFI"]["sha256"]))

    def test_docs_are_copied_and_linked_locally(self):
        self.build(postinstall_kit.PostInstallKit(fetcher=FailingFetcher()), "docs-only")
        for name in postinstall_kit.DOCS_FILES:
            self.assertTrue(os.path.isfile(os.path.join(self.dest, "Docs", name)))
        with open(os.path.join(self.dest, postinstall_kit.CHECKLIST_NAME), "r", encoding="utf-8") as checklist:
            text = checklist.read()
        self.assertNotIn("https://github.com/aytutuna", text)
        self.assertIn("Docs/install-flow.md", text)
        postinstall_kit.PostInstallKit(fetcher=FailingFetcher()).invalidate(self.dest)
        self.assertFalse(os.path.exists(os.path.join(self.dest, "Docs")))

    def test_helper_runs_each_pmset_line_and_reports_failures(self):
        text = postinstall_kit.PostInstallKit(fetcher=FailingFetcher()).render_command(summary())
        for line in postinstall_kit.PMSET_LINES:
            self.assertIn("    {} || ".format(line), text)
        self.assertNotIn(" && echo \"Applied.\"", text)
        self.assertIn("Some settings failed", text)

    def test_root_patch_section_is_marked_unverified_with_revert_steps(self):
        kit = postinstall_kit.PostInstallKit(fetcher=FailingFetcher())
        patched = kit.render_checklist(summary(needs_oclp=True))
        self.assertIn("Unverified:", patched)
        self.assertIn("revert", patched)
        self.assertIn("Leave FileVault off on the setup screens", patched)
        plain = kit.render_checklist(summary())
        self.assertNotIn("Unverified:", plain)
        self.assertNotIn("Leave FileVault off", plain)

    def test_broadcom_without_pci_path_is_not_listed(self):
        report = canned_report(broadcom=True)
        del report["Network"]["Broadcom BCM4350"]["PCI Path"]
        data = postinstall_kit.make_build_summary(report, canned_config(), "iMac20,1", "25.0.0", False, bcmc=True)
        self.assertEqual(data["broadcom_companion"], [])

    def test_invalidate_removes_only_generated_files(self):
        self.build(postinstall_kit.PostInstallKit(fetcher=FailingFetcher()), "docs-only")
        tools_dir = os.path.join(self.dest, "Tools")
        os.makedirs(tools_dir)
        open(os.path.join(tools_dir, "keep.zip"), "wb").close()
        removed = postinstall_kit.PostInstallKit(fetcher=FailingFetcher()).invalidate(self.dest)
        self.assertEqual(len(removed), len(postinstall_kit.GENERATED_FILES))
        self.assertEqual(os.listdir(self.dest), ["Tools"])


def wifi_report(*devices, gpu=None):
    report = canned_report()
    for name, device_id, compatibility in devices:
        report["Network"][name] = {"Device ID": device_id, "PCI Path": "PciRoot(0x0)/Pci(0x1c,0x0)/Pci(0x0,0x0)"}
        if compatibility:
            report["Network"][name]["OCLP Compatibility"] = compatibility
    if gpu:
        report["GPU"] = gpu
    return report


class RootPatchManifestTest(unittest.TestCase):
    TAHOE_OCLP = ("25.99.99", "20.0.0")

    def patches(self, report=None, version="25.0.0", simplehda=False, bcmc=False, airportitlwm=False):
        data = postinstall_kit.make_build_summary(report or canned_report(), canned_config(), "iMac20,1", version, True,
                                                  applealc=True, simplehda=simplehda, bcmc=bcmc, airportitlwm=airportitlwm)
        return data["root_patches"]

    def test_tahoe_audio_without_simplehda(self):
        self.assertEqual(self.patches(), [{"set": "modern_audio"}])
        self.assertEqual(self.patches(simplehda=True), [])
        self.assertEqual(self.patches(version="24.0.0"), [])

    def test_no_audio_entry_without_applealc(self):
        data = postinstall_kit.make_build_summary(canned_report(), canned_config(), "iMac20,1", "25.0.0", True, applealc=False)
        self.assertEqual(data["root_patches"], [])

    def test_broadcom_wifi(self):
        report = wifi_report(("Broadcom BCM4360", "14E4-43A0", ("25.99.99", "23.0.0")))
        self.assertEqual(self.patches(report, simplehda=True), [{"set": "modern_wireless", "device": "14E4-43A0", "name": "Broadcom BCM4360"}])
        self.assertEqual(self.patches(report, version="22.0.0", simplehda=True), [])

    def test_companion_gets_no_patch(self):
        self.assertEqual(self.patches(canned_report(broadcom=True), simplehda=True, bcmc=True), [])
        report = canned_report(broadcom=True)
        report["Network"]["Broadcom BCM4350"]["OCLP Compatibility"] = ("25.99.99", "23.0.0")
        self.assertEqual(self.patches(report, simplehda=True, bcmc=True), [])
        self.assertEqual(self.patches(report, simplehda=True), [{"set": "modern_wireless", "device": "14E4-43A3", "name": "Broadcom BCM4350"}])

    def test_legacy_wireless(self):
        report = wifi_report(("Broadcom BCM4328", "14E4-4328", self.TAHOE_OCLP), ("Atheros AR93xx", "168C-0030", self.TAHOE_OCLP),
                             ("Atheros AR9462", "168C-0034", None))
        self.assertEqual([(entry["set"], entry["device"]) for entry in self.patches(report, simplehda=True)],
                         [("legacy_wireless", "14E4-4328"), ("legacy_wireless", "168C-0030")])

    def test_airportitlwm_on_sequoia_and_tahoe(self):
        report = wifi_report(("Intel Wi-Fi 6 AX201", "8086-02F0", None))
        expected = [{"set": "modern_wireless", "device": "8086-02F0", "name": "Intel Wi-Fi 6 AX201"}]
        self.assertEqual(self.patches(report, version="24.0.0", airportitlwm=True), expected)
        self.assertEqual(self.patches(report, simplehda=True, airportitlwm=True), expected)
        self.assertEqual(self.patches(report, version="23.0.0", airportitlwm=True), [])
        self.assertEqual(self.patches(report, simplehda=True), [])

    def test_gpu_is_recorded_as_not_covered(self):
        gpu = {
            "NVIDIA GeForce GTX 680": {"Device ID": "10DE-1180", "Codename": "Kepler", "OCLP Compatibility": ("25.99.99", "21.0.0")},
            "Intel UHD Graphics 630": {"Device ID": "8086-9BC5", "Codename": "Comet Lake"}
        }
        self.assertEqual(self.patches(wifi_report(gpu=gpu), simplehda=True), [
            {"set": "graphics", "codename": "Kepler", "device": "10DE-1180", "name": "NVIDIA GeForce GTX 680", "supported": False}
        ])

    def test_manifest_fields_and_pin(self):
        data = summary(needs_oclp=True, simplehda=False)
        manifest = postinstall_kit.make_patch_manifest(data)
        self.assertEqual(manifest["version"], 1)
        self.assertEqual(manifest["root_patches"], [{"set": "modern_audio"}])
        for key in ("macos_version", "macos_name", "smbios_model", "sip", "secure_boot_model", "boot_args"):
            self.assertEqual(manifest[key], data[key])
        self.assertEqual(manifest["payload"], postinstall_tools.patcher_support_pkg)
        json.dumps(manifest)

    def test_pin_matches_the_tool(self):
        with open(os.path.join(REPO_ROOT, "postinstall", "macpatch", "payload.py"), "r", encoding="utf-8") as source:
            tree = ast.parse(source.read())
        pin = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign) and node.targets[0].id == "PIN")
        self.assertEqual(pin, postinstall_tools.patcher_support_pkg)


class MacPatchKitTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp()
        self.dest = os.path.join(self.temp, postinstall_kit.KIT_FOLDER)
        self.kit = postinstall_kit.PostInstallKit(fetcher=FailingFetcher())

    def tearDown(self):
        shutil.rmtree(self.temp, ignore_errors=True)

    def build(self, data):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.kit.build(data, self.dest, "docs-only", config_path=os.path.join(self.temp, "missing.plist"))

    def test_kit_writes_manifest_and_copies_tool(self):
        result = self.build(summary(needs_oclp=True, simplehda=False))
        patches_path = os.path.join(self.dest, postinstall_kit.PATCHES_NAME)
        self.assertIn(patches_path, result["files"])
        with open(patches_path, "r", encoding="utf-8") as patches_file:
            self.assertEqual(json.load(patches_file)["root_patches"], [{"set": "modern_audio"}])
        tool = os.path.join(self.dest, "Tools", "MacPatch")
        expected = {"__main__.py", "runner.py", "manifest.py", "patchsets.py", "detect.py", "payload.py", "kdk.py", "sysvol.py",
                    "LICENSE", "Root Patch.command", "Revert Root Patch.command"}
        self.assertEqual(set(os.listdir(tool)), expected)
        for name in postinstall_kit.MACPATCH_COMMANDS:
            with open(os.path.join(tool, name), "rb") as launcher:
                self.assertNotIn(b"\r\n", launcher.read())
        with open(os.path.join(self.dest, postinstall_kit.CHECKLIST_NAME), "r", encoding="utf-8") as checklist:
            text = checklist.read()
        self.assertIn("bash \"Root Patch.command\" --dry-run", text)
        self.assertIn("cd \"/Volumes/OPENCORE/PostInstall/Tools/MacPatch\"", text)
        self.assertIn("has not been run on a real machine yet", text)
        self.assertIn("FAT32 has no executable bit", text)
        self.assertIn("rebuilds the kernel cache when a kext is installed", text)
        self.assertIn("If MacPatch refuses or fails, undo anything it changed with Revert Root Patch.command, reboot, then use OCLP-Mod 3.1.9", text)
        oclp = next(tool for tool in postinstall_tools.tools if tool["name"] == "OCLP-Mod")
        self.assertIn(oclp["sha256"], text)
        self.assertIn("Root patches: needed (MacPatch, Tools/MacPatch)", text)
        self.assertIn("Revert Root Patch.command", text)
        self.assertIn("After each update run Root Patch.command", text)
        with open(os.path.join(self.dest, postinstall_kit.COMMAND_NAME), "r", encoding="utf-8") as command:
            self.assertIn("Tools/MacPatch/Root Patch.command", command.read())

    def test_rebuild_removes_tool_but_keeps_downloads(self):
        self.build(summary(needs_oclp=True, simplehda=False))
        open(os.path.join(self.dest, "Tools", "Hackintool.zip"), "wb").close()
        removed = self.kit.invalidate(self.dest)
        self.assertIn(os.path.join(self.dest, postinstall_kit.PATCHES_NAME), removed)
        self.assertEqual(os.listdir(os.path.join(self.dest, "Tools")), ["Hackintool.zip"])

    def test_nothing_for_macpatch_means_no_tool(self):
        self.build(summary(needs_oclp=True))
        self.assertFalse(os.path.exists(os.path.join(self.dest, postinstall_kit.PATCHES_NAME)))
        self.assertFalse(os.path.exists(os.path.join(self.dest, "Tools")))

    def test_graphics_sends_everything_to_oclp(self):
        kepler = {"set": "graphics", "codename": "Kepler", "device": "10DE-1180", "name": "NVIDIA GeForce GTX 680", "supported": False}
        for audio in (True, False):
            data = summary(needs_oclp=True, simplehda=not audio)
            data["root_patches"] = ([{"set": "modern_audio"}] if audio else []) + [kepler]
            self.assertEqual(postinstall_kit.macpatch_entries(data), [])
            text = self.kit.render_checklist(data)
            self.assertIn("This build also needs graphics patches (NVIDIA GeForce GTX 680 (Kepler))", text)
            self.assertIn("Do not mix the two patchers", text)
            self.assertIn("this kit has no MacPatch and no patches.json", text)
            self.assertNotIn("Root Patch.command", text)
            self.assertIn("Open OCLP-Mod, choose Post-Install Root Patch", text)
            self.assertIn("Root patches: needed (OCLP-Mod)", text)
            self.assertTrue(text.isascii())
            self.build(data)
            self.assertFalse(os.path.exists(os.path.join(self.dest, postinstall_kit.PATCHES_NAME)))
            self.assertFalse(os.path.exists(os.path.join(self.dest, "Tools", "MacPatch")))

    def test_outdated_summary_is_recognised(self):
        path = os.path.join(self.temp, postinstall_kit.SUMMARY_NAME)
        with open(path, "w", encoding="utf-8") as old:
            json.dump({"version": 1}, old)
        self.assertIsNone(self.kit.load_summary(path))
        self.assertTrue(self.kit.summary_outdated(path))
        self.kit.save_summary(summary(), path)
        self.assertFalse(self.kit.summary_outdated(path))
        self.assertFalse(self.kit.summary_outdated(os.path.join(self.temp, "missing.json")))


class USBWriterPostInstallTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp()
        self.drive = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.temp, "EFI", "OC"))
        with open(os.path.join(self.temp, "EFI", "OC", "config.plist"), "wb") as config_file:
            config_file.write(b"config")
        self.writer = usb_writer.USBWriter()

    def tearDown(self):
        shutil.rmtree(self.temp, ignore_errors=True)
        shutil.rmtree(self.drive, ignore_errors=True)

    def make_kit(self):
        kit = os.path.join(self.temp, "PostInstall")
        os.makedirs(os.path.join(kit, "Tools"))
        with open(os.path.join(kit, "POST-INSTALL.md"), "w", encoding="utf-8") as checklist:
            checklist.write("# Post-install checklist\n")
        with open(os.path.join(kit, "Tools", "tool.zip"), "wb") as tool:
            tool.write(b"tool")
        return kit

    def copy(self):
        with mock.patch.object(self.writer, "_supported", return_value=True), \
                mock.patch.object(self.writer, "_drive_root", return_value=self.drive), \
                contextlib.redirect_stdout(io.StringIO()):
            return self.writer.copy_payload("Z:", self.temp)

    def test_postinstall_copied_and_verified_when_present(self):
        self.make_kit()
        ok, problems = self.copy()
        self.assertTrue(ok, problems)
        self.assertEqual(open(os.path.join(self.drive, "PostInstall", "Tools", "tool.zip"), "rb").read(), b"tool")
        self.assertTrue(os.path.isfile(os.path.join(self.drive, "EFI", "OC", "config.plist")))

    def test_postinstall_copy_is_verified_by_hash(self):
        self.make_kit()
        real_verify = self.writer._verify

        def tampering_verify(src, dst):
            if os.path.basename(dst) == "PostInstall":
                with open(os.path.join(dst, "Tools", "tool.zip"), "wb") as tool:
                    tool.write(b"tolx")
            return real_verify(src, dst)

        with mock.patch.object(self.writer, "_verify", side_effect=tampering_verify):
            ok, problems = self.copy()
        self.assertFalse(ok)
        self.assertTrue(any("SHA-256 differs" in problem for problem in problems))

    def test_replacing_postinstall_leaves_other_drive_folders_alone(self):
        self.make_kit()
        os.makedirs(os.path.join(self.drive, "PostInstall", "Old"))
        os.makedirs(os.path.join(self.drive, "Photos"))
        with open(os.path.join(self.drive, "Photos", "a.jpg"), "wb") as photo:
            photo.write(b"photo")
        ok, problems = self.copy()
        self.assertTrue(ok, problems)
        self.assertFalse(os.path.exists(os.path.join(self.drive, "PostInstall", "Old")))
        self.assertTrue(os.path.isfile(os.path.join(self.drive, "Photos", "a.jpg")))

    def test_postinstall_skipped_when_absent(self):
        os.makedirs(os.path.join(self.temp, "PostInstall", "Tools"))
        ok, problems = self.copy()
        self.assertTrue(ok, problems)
        self.assertFalse(os.path.exists(os.path.join(self.drive, "PostInstall")))
        self.assertIsNone(self.writer.postinstall_source(self.temp))


class MainIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_main_module()

    def setUp(self):
        self.temp = tempfile.mkdtemp()
        self.ocpe = self.module.OCPE.__new__(self.module.OCPE)
        self.ocpe.u = self.module.utils.Utils()
        self.ocpe.pk = postinstall_kit.PostInstallKit(fetcher=FailingFetcher())
        self.ocpe.result_dir = self.temp
        self.ocpe.build_summary = None

    def tearDown(self):
        self.module.utils.Utils.set_headless(None)
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_menu_lists_kit_before_usb_installer(self):
        with open(os.path.join(REPO_ROOT, "OpenCoreWizard.py"), "r", encoding="utf-8") as source:
            text = source.read()
        self.assertIn('print("8. Download macOS Recovery")', text)
        self.assertIn('print("9. Prepare Post-Install Kit")', text)
        self.assertIn('print("10. Create USB Installer")', text)

    def test_headless_rejects_unknown_kit_mode(self):
        with self.assertRaises(Exception) as context:
            self.ocpe.run_headless({"postinstall_kit": "yes"})
        self.assertIn("postinstall_kit", str(context.exception))

    def test_kit_needs_a_summary(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertIsNone(self.ocpe.prepare_post_install_kit("docs-only"))
        self.assertIn("Build the OpenCore EFI first", output.getvalue())

    def test_kit_from_an_older_summary_asks_for_a_rebuild(self):
        with open(os.path.join(self.temp, postinstall_kit.SUMMARY_NAME), "w", encoding="utf-8") as old:
            json.dump({"version": 1, "macos_name": "macOS Tahoe 26"}, old)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertIsNone(self.ocpe.prepare_post_install_kit("docs-only"))
        self.assertIn("is from an older version of this app; rebuild the EFI (option 7)", output.getvalue())

    def test_kit_from_saved_summary_in_a_later_session(self):
        os.makedirs(os.path.join(self.temp, "EFI", "OC"))
        with open(os.path.join(self.temp, "EFI", "OC", "config.plist"), "wb") as config_file:
            plistlib.dump(canned_config(), config_file)
        self.ocpe.pk.save_summary(summary(), os.path.join(self.temp, postinstall_kit.SUMMARY_NAME))
        self.module.utils.Utils.set_headless({"prompts": {"Select an option": "2"}})
        with contextlib.redirect_stdout(io.StringIO()):
            result = self.ocpe.prepare_post_install_kit()
        self.assertEqual(result["mode"], "docs-only")
        self.assertTrue(os.path.isfile(os.path.join(self.temp, "PostInstall", "POST-INSTALL.md")))

    def test_record_build_summary_persists_and_invalidates_old_kit(self):
        kexts = [types.SimpleNamespace(checked=False) for _ in kext_data.kexts]
        kexts[kext_data.kext_index_by_name["AppleALC"]].checked = True
        self.ocpe.k = types.SimpleNamespace(kexts=kexts)
        self.ocpe.co = types.SimpleNamespace(whatevergreen_first_boot={"agdp_patch": True})
        self.ocpe.usb_map_source = {"kind": "json", "path": "USBMap.json"}
        kit_dir = os.path.join(self.temp, "PostInstall")
        os.makedirs(kit_dir)
        open(os.path.join(kit_dir, "POST-INSTALL.md"), "w").close()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            data = self.ocpe.record_build_summary(canned_report(), canned_config(), "iMac20,1", "25.0.0", False, True)
        self.assertIn("prepare it again with option 9", output.getvalue())
        self.assertFalse(os.path.exists(os.path.join(kit_dir, "POST-INSTALL.md")))
        with open(os.path.join(self.temp, postinstall_kit.SUMMARY_NAME), "r", encoding="utf-8") as saved:
            self.assertEqual(json.load(saved), data)
        self.assertEqual(data["usb_map"]["kext"], "UTBMap.kext")
        self.assertEqual(data["ocvalidate"], "passed")
        self.assertEqual(data["whatevergreen_first_boot"], {"agdp_patch": True})
        self.assertEqual(data["audio"]["codec_id"], "10EC-1220")

    def test_failed_rebuild_removes_stale_kit_and_summary(self):
        kit_dir = os.path.join(self.temp, "PostInstall")
        os.makedirs(kit_dir)
        open(os.path.join(kit_dir, "POST-INSTALL.md"), "w").close()
        self.ocpe.pk.save_summary(summary(), os.path.join(self.temp, postinstall_kit.SUMMARY_NAME))
        self.ocpe.build_summary = summary()
        self.ocpe.k = types.SimpleNamespace(ock_files_dir=os.path.join(self.temp, "missing"))
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(Exception):
                self.ocpe.build_opencore_efi({}, [], "iMac20,1", "25.0.0", False)
        self.assertIsNone(self.ocpe.build_summary)
        self.assertFalse(os.path.exists(os.path.join(kit_dir, "POST-INSTALL.md")))
        self.assertFalse(os.path.exists(os.path.join(self.temp, postinstall_kit.SUMMARY_NAME)))


if __name__ == "__main__":
    unittest.main()
