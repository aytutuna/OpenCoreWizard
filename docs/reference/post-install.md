# Post-install (OpenCore post-install guide)

What to do after the macOS installer finishes, in the order that matters for a Windows-prepared machine. Troubleshooting entries from the install guide that apply after install are marked "(install guide)".

Sources are given per section. "Silent" means the sources do not cover it.

Suggested order: (1) move the EFI to the internal drive, (2) USB map, (3) audio, (4) iServices prerequisites, (5) power management/sleep, (6) security settings, (7) cosmetics, (8) updates.

## Post-install kit (what the app generates)

Main-menu option 9 (Prepare Post-Install Kit) turns this page into a checklist for one build. Every EFI build writes `Results\build_summary.json` (macOS version, SMBIOS model, whether root patches are needed and which patch sets, WhateverGreen first-boot mode, codec and layout-id with the codec's other layouts, AppleBCMWLANCompanion devices, USB map, Insyde firmware, SIP, SecureBootModel, revpatch), so the kit can also be made in a later session without rebuilding. A rebuild removes the generated files of an older kit so a stale checklist is never copied. Headless runs make it with `"postinstall_kit": "full"` or `"docs-only"` in the answers file.

`Results\PostInstall` then holds:

- `POST-INSTALL.md`: first boot checks, re-enabling WhateverGreen, moving the EFI with MountEFI and setting `LauncherOption` (`Short` when the BIOS vendor is Insyde), root patches with MacPatch (OCLP-Mod as the fallback), audio layouts to try, the Broadcom firmware copy, USB map checks, the Apple Account prerequisites from section 4 (en0, NVRAM test), the pmset lines from section 7, OTA updates, and what to do if it does not boot. Sections that do not apply to the build are left out.
- `START HERE.html`: the same steps in plain language for someone new to OpenCore, with the Terminal line to copy, the boot-menu key for the board's maker, and what to do when something does not work. It links to `POST-INSTALL.md` for the details.
- `post-install.command`: a bash script for macOS, run from the stick with `bash /Volumes/OPENCORE/PostInstall/post-install.command` (FAT32 keeps no executable bit). It asks before each change, in this order:
  - WhateverGreen: turned back on in the stick's config.plist when the build left it off, with the AGDP patch removed and `agdpmod=pikera` added when that variant was used. It keeps `config-before-graphics.plist` and stops so a boot from the stick can be tested.
  - Sleep: the pmset lines.
  - Broadcom firmware: copied after checking its SHA-256.
  - EFI move: finds the physical disk behind `/` (`APFS Physical Store`) and its EFI partition, refusing when that is the stick itself. It checks the free space, saves the partition's old `EFI` folder on the Desktop, and replaces only `EFI/OC` and `EFI/BOOT`, so `EFI/Microsoft` stays. It sets `LauncherOption` (`Short` on Insyde), `Timeout` 5 and `HideAuxiliary` (the stick's menu waits and shows the installer; the installed system starts by itself), compares the copy with the stick and unmounts what it mounted.
  - Apple Account check: reports whether en0 is built-in.

  Tested against stand-ins for `diskutil`, `plutil`, `ioreg` and `sudo` (`HelperRunTest`), not yet on a real Mac.
- `smbios.txt`: SystemProductName, SystemSerialNumber, MLB, SystemUUID and ROM from the generated config.plist.
- `patches.json` and `Tools\MacPatch` (only when the build needs a root patch MacPatch covers and no graphics patch, in both modes because nothing is downloaded): the manifest lists the patch sets this build needs (`modern_audio` for AppleHDA on Tahoe without SimpleHDA, `modern_wireless` for Broadcom cards in OCLP's AirPortBrcmNIC/BCM4360 lists that did not get AppleBCMWLANCompanion and for AirportItlwm on Sequoia and Tahoe, `legacy_wireless` for BCM4328/4322-class Broadcom and AR9xxx Atheros cards) plus the pinned PatcherSupportPkg payload; GPUs OCLP could patch are listed as `graphics` with `"supported": false`, because MacPatch does not port the graphics sets yet; such a build gets OCLP-Mod instructions for all patches and no MacPatch, since the two patchers must not be mixed. MacPatch is a Python 3.9 tool for macOS built from OpenCore Legacy Patcher 3.0.0's sys_patch code: `Root Patch.command` runs it (`bash "Root Patch.command" --dry-run` only checks), `Revert Root Patch.command` goes back to the sealed snapshot. It has not been run on a real machine yet; see section 7 of oclp.md.
- `Tools` (full mode only): Hackintool, ProperTree, MountEFI and the companion firmware, downloaded with SHA-256 checks against hashes stored in the app (release digests, or pinned commit archives for ProperTree and MountEFI; no branch heads), and `Tools\manifest.json` records what was fetched so a repeat build reuses the files. `Docs` holds local copies of the post-install and install-flow pages for use on the Mac before it has internet. OCLP-Mod is only linked with its release hash, because its package carries root-patch payloads taken from older macOS releases; for the same reason MacPatch downloads OCLP's payload on the Mac instead of the kit shipping it.

The USB writer (option 10) copies the folder to `<drive>\PostInstall` and verifies it like the EFI.

## 1. Move OpenCore from the USB to the internal drive

The guide documents the macOS method only.

macOS method (MountEFI by CorpNewt):

1. Mount the USB's EFI partition with MountEFI and copy the `EFI` folder somewhere safe.
2. Eject the USB's EFI mount. Keep only one EFI mounted at a time; several mounted EFI partitions confuse macOS.
3. Mount the internal drive's EFI. Catalina and newer split macOS into System and Data volumes, so MountEFI may list several entries (`diskXsY`), but they share one ESP (the UEFI spec allows one EFI partition per drive).
4. The internal ESP may contain a folder named `APPLE` (firmware-update data for real Macs, no effect here). Everything on the ESP can be wiped and replaced by the USB's `EFI`.
5. Legacy (non-UEFI) machines must also rerun `BootInstallARCH.tool` on the macOS drive to write the boot sectors.
6. Remove the USB, boot the internal drive, choose OpenCore once in the firmware boot menu so the firmware remembers it. Keep a copy of the working EFI.

Windows method: silent. The only hints are:

- A USB made with gibMacOS MakeInstall.bat defaults to an MBR partition map, so there is no dedicated ESP; the FAT32 partition is named `BOOT` and is what macOS mounts.
- On Windows the USB's EFI partition appears as the volume named `BOOT` (config.plist README).
- If Windows and macOS share a drive, the ESP must be the first partition for Startup Disk (see "multiboot" below) and the Windows-created ESP is usually 100MB where macOS expects 200MB (install guide, "MediaKit reports not enough space").

Windows or Linux updating the firmware boot entry can overwrite `EFI/BOOT/BOOTx64.efi`. The guide's remedy is `LauncherOption` (OpenCore 0.6.6+): `Misc -> Boot -> LauncherOption` = `Full` (use `Short` on Insyde firmware, commonly laptops), `UEFI -> Quirks -> RequestBootVarRouting` = YES, then OpenCore creates its own firmware boot entry pointing at `EFI/OC/OpenCore.efi` on the first boot (verify in a DEBUG log: `OCB: Have existing option 1, valid 1`). To remove the entry: `Misc -> Security -> AllowNvramReset` YES and `LauncherOption` Disabled, then Reset NVRAM in the picker.

Sources: `_ref/dortania-post-install/universal/oc2hdd.md`, `multiboot/bootstrap.md`, `_ref/dortania-install-guide/config.plist/README.md`, `troubleshooting/extended/userspace-issues.md`.

## 2. USB mapping

### What the app does

Main-menu option 6 (USB Port Mapping, Windows only, on the target machine) lists every USB controller and root-hub port, including empty ones, through Windows' own USB hub interface: port speed (USB 2 or USB 3 half), companion port, Type-C and user-connectable flags, and the device plugged in now. The first choice, the automatic map, selects every port the firmware reports as an external jack plus every port that has or had a device, takes Type-C from the firmware flag, keeps USB 2/USB 3 pairs together and leaves out unused ports when a controller would go over 15; it rates the result "good" when on every controller the firmware marks some ports internal or at least half the ports have seen a device, and "guess" when on at least one controller every port is reported as external and fewer than half have seen a device, or a controller could not be read. The express run does the same before the build when no map is saved yet. The second choice is the manual map: you plug a device into each port and refresh; ports that have seen a device stay marked and are preselected. You can then toggle ports, set the connector type (`t <port> <code>`, same codes as below), mark ports internal, and the app refuses more than 15 ports per controller. The selection is saved as `SysReport/USBMap.json`. Every later build turns it into `UTBMap.kext` (USBToolBox format) in `EFI/OC/Kexts`, keeps `USBToolBox.kext`, drops `UTBDefault.kext` and adds the map to `Kernel -> Add`. A saved map whose controllers do not match the selected hardware report is ignored. Headless builds accept `"usb_map": "<USBMap.json or UTBMap.kext>"` in the answers file.

Still manual: the connector types are guesses from what Windows reports (check Type-C with and without switch, and internal headers), and the map has to be verified after install: boot macOS, plug a USB 2 and a USB 3 device into every port, confirm each one works at the expected speed, then map again and rebuild if a port is missing. Leave `XhciPortLimit` off; it does not work on macOS 11.3 and newer.

### Why it is needed

- macOS guesses port types poorly: ports run at 2.0 instead of 3.x, some ports do not work, Bluetooth may fail, Handoff and sleep can break, hot-plug breaks, and `XhciPortLimit` can even corrupt data.
- 15-port limit: since OS X 10.11 each USB controller may expose at most 15 ports to macOS (`0xF`). Z170 and newer chipsets declare 26 ports in ACPI, including ports with no connector. A USB 3 port counts as two (a 2.0 and a 3.0 personality, "companion" ports). Hubs count as one port and can fan out to 127 devices.
- The `XhciPortLimit` quirk patches the limit upward and is a temporary tool for mapping only. It is broken on macOS 11.3 and newer (boot loops, "AppleUSBHostPort::createDevice: failed to create device" reboot). On macOS 12+, map before installing (USBToolBox from Windows is the guide's suggestion); on 11.3+ disable `XhciPortLimit` once mapped.
- Mapping from macOS is described as the older route; the guide notes macOS 12+ changes to the USB stack make a prior map "highly advisable".

Sources: `_ref/dortania-post-install/usb/README.md`, `_ref/dortania-install-guide/installer-guide/windows-install.md`, `ktext.md`, `troubleshooting/extended/kernel-issues.md`.

### Flow in the post-install guide (outdated; points to USBMap's README)

1. Prerequisites: `USBInjectAll` (Intel only, not AMD) in `EFI/OC/Kexts` and the config; `Kernel -> Quirks -> XhciPortLimit` YES temporarily; ACPI renames if the SMBIOS has its own USB map.
2. Renames (only if `ioreg -l -p IOService -w0 | grep -i XHC1` / `EHC1` / `EHC2` returns something):

   | SMBIOS set | Renames |
   | :--- | :--- |
   | iMac18,x+, MacPro7,1+, Macmini8,1+, MacBook9,x+, MacBookAir8,x+, MacBookPro13,x+ | none |
   | iMacPro1,1, iMac17,x and older, MacBookAir7,x | XHC1 to SHCI |
   | MacPro6,1, Macmini7,1, MacBook8,x, MacBookAir6,x, MacBookPro12,x | XHC1 to SHCI, EHC1 to EH01 |
   | iMac16,x and older, MacPro5,1 and older, Macmini6,x and older, MacBookAir5,x and older, MacBookPro11,x and older | XHC1 to SHCI, EHC1 to EH01, EHC2 to EH02 |

   Pre-made patches: `usb-rename.plist` in the post-install repo.
3. Discover ports (USBMap tool: Discover Ports, plug a device into every port, name them), edit the plist, set port types, build `USBMap.kext`.
4. Types (`_UPC`): `0` USB 2.0 Type-A (default when no map), `3` USB 3.x Type-A, `8` Type-C 2.0 only, `9` Type-C with switch (flipping does not change the port), `10` Type-C without switch (flipping changes the port), `255` proprietary/internal (Bluetooth: macOS expects it internal, for example `T:9:255`).
5. `USRx` ports are USB-R (redirection) ports macOS does not support; leave them out.
6. Stay at 15 ports or fewer per controller (each controller has its own limit). Then remove `XhciPortLimit`.
7. The built map is used alone: no `SSDT-UIAC.aml`, no `USBInjectAll`; only `XhciUnsupported` may accompany it if the controller needs it (H370, B360, H310, Z390 pre-Mojave, X79, X99; ASRock Intel boards, except B460/Z490+; not AMD).
8. Reboot, rerun the tool, confirm the port list shrank.

AMD and third-party controllers: the guide's manual method. Create a plugin kext (AMD-USB-Map.kext sample) with `Model` (the SMBIOS), `IONameMatch` or `IOPathMatch` (when several controllers share a name), `port-count`, `port` (the ACPI `_ADR`), `UsbConnector` (type). Controller names to look for: XHC, XHC0-2, XHCI, XHCX, AS43, PTXH (AMD chipset), PTCP (ASRock X399), PXSX (check that it is a USB device), EHCI/EHC1/EHC2/EUSB/USBE. Broken DSDTs: delete the controller's ports from the DSDT so macOS builds them; duplicate controller names: `IOPathMatch` or SSDT recreation (SSDT-SHC0). AMD cannot use USBInjectAll; run SSDTTime "USB Reset" for SSDT-RHUB.

Mapping needs one USB 2.0 and one USB 3.0 device to tell personalities apart. Bluetooth shows up as a USB device on an internal port.

Sources: `_ref/dortania-post-install/usb/{system-preparation.md,intel-mapping/intel.md,amd-mapping/amd.md,manual/manual.md}`, `_ref/dortania-install-guide/ktext.md`, `AMD/AMD-USB-map.md`.

### UTBDefault vs UTBMap

Not covered by the listed sources. The install guide `ktext.md` describes USBToolBox: a tool for Windows and macOS (Linux in progress); can build a map using either the USBToolBox kext or native Apple kexts (`AppleUSBHostMergeProperties`); several matching methods; companion ports supported on Windows. Outside the listed sources, the vendored `_ref/usbtoolbox/README.md` (step 8) says the built map goes into `EFI/OC/Kexts`, the USBToolBox kext must be present if the map uses it, and `UTBDefault.kext` must be removed. The app follows the same rule when it builds the map itself (see "What the app does" above). The meaning of "UTBDefault" (a placeholder default map) versus "UTBMap" (the user's built map) is therefore taken from the tool and the app, not from the guides.

### Related USB fixes the guide lists

- USB power: Skylake+ SMBIOS (iMac17,x+, MacPro7,1+, iMacPro1,1+, Macmini8,1+, MacBook9,x+, MacBookAir8,x+, MacBookPro13,x+) need a USBX device (`SSDT-USBX`) for USB current on mics, DACs, webcams and Bluetooth dongles.
- Shutdown that restarts: missing S5; FixShutdown-USB-SSDT plus a `_PTS` to `ZPTS` patch.
- Instant wake: `GPRW/UPRW/LANC` patch.
- Keyboard wake needing a second press (100-series and newer): set `acpi-wake-type` Data `01` on the XHCI controller in `DeviceProperties`.

Sources: `_ref/dortania-post-install/usb/misc/*.md`.

## 3. Audio (AppleALC)

1. Verify: `kextstat | grep -E "AppleHDA|AppleALC|Lilu"` shows all three; VoodooHDA, RealtekALC, CloverALC, HDA Blocker, HDAEnabler must not be present.
2. Find the codec: motherboard spec/manual, Windows Device Manager, HWInfo64 (not summary/sensors-only), AIDA64, or `cat /proc/asound/card0/codec#0` on Linux. ALC3xxx names are usually rebranded parts; check the real codec (ALC3601 is an ALC671).
3. Look the codec up in AppleALC's supported-codec list for layout IDs (example ALC1220: layout 1, 2, 3, 5, 7, 11, 13, 15, 16, 21, 27, 28, 29, 34).
4. Test IDs one at a time with boot-arg `alcid=<id>` under `NVRAM -> Add -> 7C436110-AB2A-4BBB-A880-FE41995C9F82 -> boot-args`. Never multiple `alcid` values.
5. Make it permanent: `DeviceProperties -> Add -> <HDEF PciRoot> -> layout-id`. Find the path with `gfxutil -f HDEF`. Value as Data (4 bytes, little endian: layout 11 = `0B000000`, 256 = `00010000`) or as Number (11). Priority: boot-arg `alcid` > `alc-layout-id` (Apple hardware only) > `layout-id`. Remove the `alcid` boot-arg afterwards or it overrides everything.
6. Check patching worked in IORegistryExplorer: an `HDEF` device, `AppleHDAController` attached, `alc-layout-id` present, and `AppleHDAInput` devices listed (few inputs = wrong layout). Never rename the audio device manually.
7. If no layout works: build SSDT-HPET IRQ fixes (SSDTTime FixHPET), needed on laptops and some desktops.
8. Extras: `alcdelay=1000` or property `alc-delay` (max 3000 ms) for hardware that initialises late; `external-audio` Data `01` on a second PCIe sound card's PciRoot; `alctcsel=1` (boot-arg or Data `01000000`) when audio breaks after rebooting from Windows; `Kernel -> Quirks -> PowerTimeoutKernelPanic` YES for 10.15 power-state panics; no microphone on AMD (use a USB DAC or VoodooHDA, which sounds worse).
9. Tahoe (macOS 26): AppleHDA.kext is gone, so AppleALC no longer works for analog out; HDMI/DP/USB audio keep working. Options: restore AppleHDA with root patching (same caveats as OCLP on a hackintosh) or VoodooHDA (`csr-active-config` `03000000`, copy the kext to `/Library/Extensions` and the prefpane to `$HOME/Library/PreferencePanes`, allow it in System Settings, reduced quality).

Sources: `_ref/dortania-post-install/universal/audio.md`, `_ref/dortania-install-guide/extras/tahoe.md`, `troubleshooting/extended/post-issues.md`.

## 4. iServices prerequisites

Order of work (the guide's order):

1. Understand the risk: the Apple ID matters most. An ID with existing Apple hardware usually works with a generated serial set; a brand-new ID almost certainly will not work and may require calling Apple. Using a purchase-date-less serial can conflict if a real Mac with that serial is ever activated.
2. Values needed in `PlatformInfo -> Generic`: `SystemProductName`, `SystemSerialNumber`, `MLB` (board serial), `SystemUUID`, `ROM`.
3. Generate serial and MLB with macserial from OpenCorePkg: `./macserial --num 1 --model "iMacPro1,1"` prints `serial | MLB`. GenSMBIOS is the macOS/Windows wrapper. Generate several (`iMacPro1,1 10`) and pick a serial that, on Apple's Check Coverage page, answers "unable to check coverage" (the guide's preferred result; invalidly pasted serials give the same answer, so copy and paste exactly), or "Purchase Date not Validated" (not recommended). The guide endorses the first result (and, not recommended, the third); it does not endorse a serial that returns a valid purchase date. Do not check too many serials (rate limits).
4. ROM: lowercase hex of the MAC address without colons, for example `c0:7e:bf:c3:af:ff` becomes `c07ebfc3afff`. Use the real MAC of the built-in interface (BIOS, macOS Network Advanced Hardware, or Windows adapter properties), or a made-up MAC with an Apple OUI (for example `00:16:CB`). `ROM` is a `Data` field.
5. UUID: `uuidgen`.
6. `en0` must exist and be marked built-in. Check with Hackintool: System, Peripherals, Network Interfaces, `en0` under BSD has a check under built-in. `en0` may be Wi-Fi, Ethernet or Thunderbolt. If `en0` is missing: delete `/Library/Preferences/SystemConfiguration/NetworkInterfaces.plist` and `preferences.plist`, reboot; otherwise add NullEthernet.kext and `ssdt-rmne.aml`. To mark built-in: `DeviceProperties -> Add -> <NIC PciRoot> -> built-in` Data `01` (find the PciRoot with Hackintool's PCI export).
7. NVRAM must work (iMessage keys are stored in it). Test: `sudo nvram myvar="$(date)"`, reboot, `nvram myvar`. If nothing returns, use SSDT-PMC (B360, B365, H310, H370, Z390) or emulated NVRAM (X99, some X299).
8. Clean out prior attempts: reset NVRAM (`Misc -> Security -> AllowNvramReset`) and remove cached iMessage/iCloud preference files (the guide lists `com.apple.iCloudHelper*`, `com.apple.Messages*`, `com.apple.imfoundation.IMRemoteURLConnectionAgent*`, `com.apple.iChat*`, `com.apple.icloud*`, `com.apple.imagent*`, `com.apple.imessage*`, `com.apple.imservice*`, `com.apple.ids.service*`, `com.apple.madrid.plist*`, `com.apple.imessage.bag.plist*`, `com.apple.identityserviced*`, `com.apple.security*`, `~/Library/Messages`).
9. Verify with `macserial -s` that the config matches.
10. Account hygiene: remove old devices from the Apple ID, enable 2FA, delete stale iServices Keychain entries (`ids: ...`, `iMessage Encryption Key`, `com.apple.facetime: registrationV1`), prefer a spare Apple ID, add a payment method.
11. "Customer Code" error means the ID is blacklisted; the guide says the only route is calling Apple.

Sources: `_ref/dortania-post-install/universal/iservices.md`, `misc/nvram.md`.

## 5. NVRAM emulation (when native NVRAM is broken)

Needed mostly on X99 and some X299; B360, B365, H310, H370 and Z390 use SSDT-PMC instead. The guide's page is flagged "not up to date for OpenCore 0.8.3", so check against current docs.

1. Remove Clover remnants: `EmuVariableUefi-64.efi`, `/Volumes/EFI/nvram.plist`, `/etc/rc.clover.lib`, `/etc/rc.boot.d/*`, `/etc/rc.shutdown.d/*` (listed files).
2. Config: `Booter -> Quirks -> DisableVariableWrite` NO; `Misc -> Security -> ExposeSensitiveData` at least `0x1`; `NVRAM -> LegacyOverwrite` YES; `LegacySchema` filled with the variables to keep; `WriteFlash` YES.
3. EFI: `OpenVariableRuntimeDxe.efi` placed before `OpenRuntime.efi`, both with `LoadEarly` YES.
4. Install the LogoutHook from OpenCorePkg `Utilities/LogoutHook`: `./Launchd.command install`; it saves NVRAM to `nvram.plist` on shutdown.
5. `nvram.plist` is not covered by Vault.

Sources: `_ref/dortania-post-install/misc/nvram.md`.

RTC/CMOS resets ("BIOS Restarted in Safe Mode", reset after reboot): first try `Kernel -> Quirks -> DisableRtcChecksum` (blocks 0x58-0x59 at kernel level only); if that fails, install `RTCMemoryFixup` and boot-arg `rtcfx_exclude=<range>`; find the bad range by halving 00-FF per boot; make it permanent with `NVRAM -> Add -> 4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102 -> rtc-blacklist` (Data, every byte listed) plus an `NVRAM -> Delete` entry. Sources: `misc/rtc.md`.

## 6. Security settings

| Setting | Guide's recommendation | Notes |
| :--- | :--- | :--- |
| SIP (`csr-active-config`, `NVRAM -> Add -> 7C436110-AB2A-4BBB-A880-FE41995C9F82`) | Disable only the specific flags required. Values: `00000000` enabled, `03000000` kext signing + FS protections off, `FF030000` High Sierra all, `FF070000` Mojave/Catalina all, `FF0F0000` Big Sur all | `CSR_ALLOW_UNAUTHENTICATED_ROOT` and `CSR_ALLOW_APPLE_INTERNAL` commonly break OS updates on Big Sur+. OpenCore does not overwrite NVRAM keys unless also listed in `NVRAM -> Delete`. Tools: BitmaskDecode, csrstat |
| `Misc -> Security -> SecureBootModel` | Big Sur to Sonoma 14.3: `Default`. Sonoma 14.4 to Tahoe: `Disabled` to allow OTA updates. High Sierra to Catalina: `Disabled` unless the model is in the T2 table and the installer is new enough. Sierra and older: no effect. Multiple macOS versions: `Disabled` | `Default` currently maps to `x86legacy` (breaks High Sierra to Catalina); the T2 model table: j137 iMacPro1,1 (10.13.2), j680 MacBookPro15,1, j132 MacBookPro15,2 (10.13.6), j174 Macmini8,1 (10.14), j140k MacBookAir8,1 (10.14.1), j780 MacBookPro15,3, j213 MacBookPro15,4, j140a MacBookAir8,2 (10.14.5), j152f MacBookPro16,1, j160 MacPro7,1 (10.15.1), j230k MacBookAir9,1 (10.15.3), j214k MacBookPro16,2, j223 MacBookPro16,3 (10.15.4), j215 MacBookPro16,4 (10.15.5), j185 iMac20,1, j185f iMac20,2 (10.15.6), x86legacy non-T2 and VMs (11.0.1) |
| `Misc -> Security -> DmgLoading` | `Signed` (or `Disabled` for max security). `Any` breaks boot with Apple Secure Boot on | |
| `Misc -> ApECID` | Optional full-security mode: 64-bit random integer (`python3 -c 'import secrets; print(secrets.randbits(64))'`), requires a defined `SecureBootModel`, network at install, and `bless --personalize` for existing installs | |
| `Misc -> Security -> Vault` | `Optional` for normal use. `Basic` needs `vault.plist`; `Secure` needs `vault.plist` and `vault.sig` (run `sign.command`; the signature is written into `OpenCore.efi`). With Insyde set `Booter -> ProtectSecureBoot` YES | `nvram.plist` is not vaulted. Disable by restoring a clean `OpenCore.efi`, setting `Optional`, deleting `vault.plist`/`vault.sig` |
| `Misc -> Security -> ScanPolicy` | `0` shows everything (used in all troubleshooting). A computed bit mask restricts boot sources; default `0x10F0103` (17760515): file system lock, device lock, APFS, SATA, SAS/SEX, SCSI, NVMe, PCI. Add `0x00200000` for USB (result `0x12F0103`, 19857667). Bits: `0x1` FS lock, `0x2` device lock, `0x100` APFS, `0x200` HFS, `0x400` ESP, `0x10000` SATA, `0x20000` SASEX, `0x40000` SCSI, `0x80000` NVMe, `0x100000` ATAPI, `0x200000` USB, `0x400000` FireWire, `0x800000` SD card, `0x1000000` PCI | Windows boot entries need non-restrictive policy (multiboot) |
| Menu password | `ocpasswordgen` (0.6.7+) yields `PasswordHash`, `PasswordSalt`; set `EnablePassword` | Prompts for picker, non-default entries, NVRAM reset, hotkeys |
| FileVault | Needs `OpenRuntime.efi`; `Misc -> Boot -> PollAppleHotKeys` YES (optional); `Misc -> Security -> AuthRestart` YES (optional); `UIScale` `02` on small HiDPI displays; `UEFI -> Input -> KeySupport` YES (builtin input only); `UEFI -> Output -> ProvideConsoleGop` YES; `UEFI -> ProtocolOverrides -> FirmwareVolume` YES, `HashServices` YES for Broadwell and older (including X99); `UEFI -> Quirks -> RequestBootVarRouting` YES; `ExitBootServicesDelay` 3000-5000 for Aptio IV "Still waiting for root device" | Do not add VirtualSMC.efi; the kext is still needed |

Writing to the system volume on Catalina and newer needs extra steps beyond SIP; the install guide has a separate "Writing to the macOS system partition" procedure (post-issues page) that is not summarised here.

Sources: `_ref/dortania-post-install/universal/security.md`, `universal/security/{applesecureboot,filevault,password,scanpolicy,vault}.md`, `_ref/dortania-install-guide/config.plist/security.md`, `troubleshooting/extended/post-issues.md`.

## 7. Sleep, hibernation, power management

Before debugging sleep, in macOS: `sudo pmset autopoweroff 0; powernap 0; standby 0; proximitywake 0; tcpkeepalive 0`. Config: `Misc -> Boot -> HibernateMode` = `None` (the guide avoids S4 hibernation); boot-args `keepsyms=1` and `swd_panic=1`. BIOS: see `bios-settings.md` (Wake on LAN, TPM, Wake on USB off; Wake on Bluetooth on only if used).

Most common culprits in order: USB (mapping, `USBX`, `AppleUSBMergeNub` to `AppleUSBHostMergeProperties` rename on Catalina+ maps), unsupported GPUs (use `igfxonln=1` for iGPU wake on 10.15.4+; desktop UHD 630 `00009B3E` or `0300923E` instead of `07009B3E`; `forceRenderStandby=0` for Kaby/Coffee Lake NVMe panics; `-noDC9` or `-nodisplaysleepDC6` for Ice Lake `Cannot allow DC9 without disallowing DC6`; `CFG,CFG_USE_AGDC` Data `00` on AMD dGPU 4K displays), Thunderbolt (disable in BIOS or patch), NICs (disable Wake on LAN and macOS "Wake for network access"), NVMe (latest firmware, NVMeFix, avoid PM981/PM991/Micron 2200S), CPU power management, and the "other culprits": displays (SSDT-PNLF, `pmset lidwake 0`, HP lid-key spam SSDT), NVRAM, RTC (SSDT-AWAC on 300-series, DisableRtcChecksum, `rtc-blacklist`, RTCMemoryFixup), IRQ conflicts (SSDTTime FixHPET), audio, SMBus (SSDT-SMBS-MCHC), TSC (CpuTscSync for troublesome laptops, VoodooTSCSync for most HEDT, TSCAdjustReset for Skylake X/W/SP and Cascade Lake). Overclocks (CPU, RAM, factory GPU) cause wake failures.

Power management (Haswell and newer, XCPM): in IORegistryExplorer `AppleACPICPU` must have `X86PlatformPlugin` attached; `SSDT-PLUG` sets `plugin-type` `0x1` on the first CPU thread (not needed on macOS 12.3+). Haswell-E and Broadwell-E need a CPUID spoof: Haswell-E `Cpuid1Data` `C3060300 00000000 00000000 00000000`, Broadwell-E `D4060300 00000000 00000000 00000000`, mask `FFFFFFFF 00000000 00000000 00000000`. Sandy/Ivy Bridge use ssdtPRgen; AMD uses `SMCAMDProcessor` (`AMDRyzenCPUPowerManagement.kext`). If the SMBIOS is wrong, change it before using CPUFriend. CPUFriend/CPUFriendFriend values: LFM Broadwell+ laptops `08`, Broadwell+ desktops `0A`, Haswell/Broadwell HEDT `0D`, Skylake+ HEDT `0C`; EPP `0x00-0x3F` max performance to `0xC0-0xFF` max saving (Skylake+ SMBIOS only); regenerate the data provider after every macOS update.

Sources: `_ref/dortania-post-install/universal/{sleep.md,pm.md}`, `usb/misc/*`, `_ref/dortania-install-guide/troubleshooting/extended/post-issues.md`.

## 8. Boot picker and GUI

- OpenCanopy GUI: add the `Resources` folder from OcBinaryData to `EFI/OC`, `OpenCanopy.efi` (same build as OpenCore) to `EFI/OC/Drivers` and `UEFI -> Drivers`. Set `Misc -> Boot -> PickerMode` `External`, `PickerAttributes` `17` (mouse and `.VolumeIcon.icns`), `PickerVariant` `Acidanthera\GoldenGate` (or `Auto`, `Acidanthera\Syrah`, `Acidanthera\Chardonnay`). Boot chime via AudioDxe is also documented.
- Cleaner boot: `Misc -> Debug -> AppleDebug` NO, remove `-v`, `UIScale` `01` (standard) or `02` (HiDPI, needed for FileVault on small displays), `UEFI -> Output -> TextRenderer` `BuiltinGraphics`, `Resolution` `Max`, `ProvideConsoleGop` YES. `ConsoleMode` Max sometimes fails; leaving it empty helps.
- Move to RELEASE builds after install: swap `BOOTx64.efi`, `OpenRuntime.efi`, `OpenCore.efi`, set `Target` 3.
- With the Windows loader on the same drive as OpenCore, set `Misc -> Boot -> Hideself`.

Sources: `_ref/dortania-post-install/cosmetic/{gui.md,verbose.md}`, `_ref/dortania-install-guide/troubleshooting/extended/misc-issues.md`.

## 9. Updating OpenCore, kexts, macOS

1. OpenCore releases ship the first Monday of each month; read `Differences.pdf` before moving.
2. Test on a spare GUID-formatted USB first (its EFI partition is hidden; mount it). Keep the working EFI.
3. Replace `EFI/BOOT/BOOTx64.efi`, `EFI/OC/OpenCore.efi`, `EFI/OC/Drivers/OpenRuntime.efi` (mismatched versions do not boot) and other drivers.
4. Compare `config.plist` against the new `Sample.plist` (OCConfigCompare, `diff`, Meld or WinMerge). Run `ocvalidate` of the same release (`chmod +x` on macOS/Linux). Re-snapshot with ProperTree (Cmd/Ctrl+R).
5. Update kexts the same way (Lilu and Friends to build, Kext Extractor to merge).
6. macOS updates: update everything first, keep a recovery path (Time Machine or an old installer with a known-good EFI), read community reports for the new build. Version notes: 10.15.0 needs a proper EC and AppleMCEReporterDisabler for dual-socket and most AMD; 10.15.4 needs updated AMD `cpuid_set_cpufamily` patches; Ventura drops pre-Haswell CPUs (AVX2) and the Haswell, Broadwell and Skylake iGPU drivers (OCLP can restore them but "no support is provided for Hackintoshes using OCLP"); Monterey drops Ivy Bridge iGPU and Kepler GPUs; AMD kernel patches' core-count `Replace` bytes must be edited per CPU on 12+ (`B8 <cores> 0000 0000`, `BA <cores> 0000 0000`, `BA <cores> 0000 0090`, cores in hex: 04, 06, 08, 0C, 10, 18, 20, 40).
7. Tahoe-specific (install guide): OTA updates on macOS 14.4+ need RestrictEvents with boot-arg `revpatch=sbvmm` and `SecureBootModel` `Disabled` (or iBridged to keep secure boot); Intel Bluetooth needs `-ibtcompatbeta`; Broadwell Wi-Fi cards can use `AppleBCMWLANCompanion` (needs VT-d, so no AMD); WhateverGreen breaks AMD connector patching on macOS 26 (remove it; only `agdpmod=pikera` users must patch AGDP manually); the page's supported SMBIOS list is given by marketing name (MacBook Pro 16-inch 2019, Mac Pro 2019, MacBook Pro 13-inch four-port 2020, iMac 2020), which correspond to MacBookPro16,1, MacPro7,1, MacBookPro16,2 and iMac20,1/20,2 (model IDs are this document's mapping).

Sources: `_ref/dortania-post-install/universal/update.md`, `_ref/dortania-install-guide/extras/{ventura,monterey,tahoe}.md`.

## 10. Multiboot notes

- Windows must be UEFI/GPT; OpenCore cannot boot legacy/MBR Windows (including BootCamp Assistant installs).
- Show Windows: `ScanPolicy` 0; same-drive loaders may need `Misc -> Boot -> Hideself` and a `BlessOverride` of `\EFI\Microsoft\Boot\bootmgfw.efi` (automatic since OpenCore 0.5.9).
- `OCB: StartImage failed - Already started`: put Windows on its own drive or add a custom `BlessOverride` path.
- Windows BSOD (`0xc000000d`) or Linux crashes: enable `SyncRuntimePermissions` on MAT firmware; `ProtectUefiServices` on Z390 and newer; `ProtectMemoryRegions` on 2013-and-older firmware; try combinations.
- "You can't change the startup disk to the selected disk": third-party NTFS drivers (Paragon) or the ESP not being the first partition; fix with `PlatformInfo -> Generic -> AdviseFeatures` YES. Startup Disk not applying: use `PlatformInfo -> Automatic` YES (needs `DevicePathsSupported`).
- BootCamp utilities (Brigadier downloads them; use `--model iMac19,1` or a newer SMBIOS for APFS support; run `BootCamp.msi` as Administrator to skip the model check). Using BootCamp from macOS erases `EFI/BOOT/BOOTx64.efi`; pair with `LauncherOption`.
- Clock: macOS uses UTC, Windows local time. Fix Windows via the registry (RealTimeIsUniversal) or the BootCamp utilities rather than macOS.
- Windows Startup Disk not seeing APFS: BootCamp driver version 6.0; update to 6.1 or newer.

Sources: `_ref/dortania-post-install/multiboot/{bootcamp.md,bootstrap.md}`, `_ref/dortania-install-guide/troubleshooting/extended/{misc-issues,post-issues}.md`.
