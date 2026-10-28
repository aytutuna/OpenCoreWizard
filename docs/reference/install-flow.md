# Install flow (OpenCore install guide, Windows path)

The guide's recommended path for a machine prepared on Windows, ending at the "Setup your Mac" screen, plus the troubleshooting entries most likely to be hit. Post-install work is in `post-install.md`; BIOS options are in `bios-settings.md`.

Sources are given per section. Where the guide is silent this is said explicitly.

## 1. Before you start

- [ ] Know the hardware: CPU name and generation, GPUs, storage (HDD/SSD, NVMe/AHCI/RAID/IDE), OEM model, Ethernet chipset, WLAN/Bluetooth chipset.
- [ ] A machine compatible per the hardware limits page (CPU generation, SSE4.2 for macOS 10.14+, AVX2 for macOS 13+, supported GPU, no laptop dGPU reliance).
- [ ] USB stick: 4GB minimum when made from Windows or Linux (16GB minimum when made on macOS).
- [ ] 15GB free on the Windows system drive (30GB on macOS).
- [ ] Ethernet connection (the Windows-made installer is a recovery installer and downloads macOS). WiFi dongles are not suggested; Ethernet USB adapters may work; a compatible WiFi card also works; Android USB tethering with HoRNDIS is the fallback.
- [ ] Latest BIOS (see `bios-settings.md`).
- [ ] Python 3 for macrecovery.py; patience.

Sources: `_ref/dortania-install-guide/prerequisites.md`, `macos-limits.md`, `installer-guide/README.md`.

Express: "Build everything with recommended choices" on the app's Welcome page (option A in the terminal menu) runs the preflight, hardware scan, verdict, EFI build with ocvalidate, recovery download and full post-install kit in one go. It takes the suggested macOS version and every default shown as (default: ...) (see `choices.md`); yes/no questions, device choices and the OpenCore Legacy Patcher warning are still asked. It skips the recovery download when ocvalidate reports errors, and stops before section 3: writing the USB stays a separate, explicit step. It does not map USB ports; a map saved earlier is used, otherwise UTBDefault.kext is.

## 2. Build the OpenCore folder (EFI)

1. Use the OpenCorePkg release. Folder `X64` for 64-bit firmware, `IA32` for 32-bit firmware. DEBUG gives much more log detail at 3-5 seconds extra boot time; RELEASE boots faster with almost no logging. Switch to RELEASE after install.
2. Put the `EFI` folder at the root of the FAT32 volume.
3. Clean the stock folder:
   - `EFI/OC/Drivers`: keep only what is needed. `OpenRuntime.efi` is required. `ResetNvramEntry.efi` is required for "Reset NVRAM" in the picker. `OpenPartitionDxe.efi` only for macOS 10.7-10.9 recovery. `OpenUsbKbDxe.efi` only for non-UEFI (DuetPkg) systems; the guide calls it harmful on Ivy Bridge and newer. Remove `OpenCanopy.efi` until post-install GUI setup. `HfsPlus.efi` is marked Required on the gathering-files page (`HfsPlusLegacy.efi` instead, never both, for CPUs without RDRAND: Sandy Bridge and older, low-end Ivy Bridge i3/Celeron).
   - `EFI/OC/Tools`: keep `OpenShell.efi` (optional, for debugging); everything else may go.
4. Add your kexts (`.kext` into `EFI/OC/Kexts`), compiled SSDTs (`.aml` into `EFI/OC/ACPI`), UEFI drivers (`.efi` into `EFI/OC/Drivers`). Do not include a DSDT, source `.dsl` files, or Clover drivers (EmuVariableUEFI, AptioMemoryFix, OsxAptioFixDrv).
5. `config.plist` goes in `EFI/OC/`. It must match the folder contents exactly: files deleted but still listed make OpenCore stop ("Driver XXX.efi at 0 cannot be found", "Plist only kext has CFBundleExecutable key"). Re-snapshot after edits.
6. `OpenCore.efi`, `BOOTx64.efi` and `OpenRuntime.efi` must come from the same build ("OCABC: Incompatible OpenRuntime r4, require r10").
7. Validate with `ocvalidate` matching the OpenCore build (the post-install update page says so; it cannot detect every flaw).

Good EFI vs bad EFI (guide checklist): EFI folder present on the ESP; ACPI files compiled (`.aml`); DSDT not included; unneeded drivers and tools removed; only `.kext` bundles in Kexts (no source or folders); `config.plist` under `EFI/OC`; only needed kexts.

Sources: `_ref/dortania-install-guide/installer-guide/opencore-efi.md`, `ktext.md`, `installation/installation-process.md`, `config.plist/README.md`, `troubleshooting/extended/opencore-issues.md`, `_ref/dortania-post-install/universal/update.md`.

## 3. Create the USB (Windows)

1. Download the macOS recovery with `macrecovery.py` from OpenCorePkg `Utilities/macrecovery/` (needs Python 3). Result is `BaseSystem.dmg`/`RecoveryImage.dmg` plus a `.chunklist` file (both are required).
2. Format the USB:
   - Disk Management: delete all partitions, create one simple volume, FAT32 (name "EFI" is suggested), or
   - Rufus: Boot selection "not bootable", file system "Large FAT32" (for sticks larger than 16GB), then delete any autorun files, or
   - diskpart: `clean`, `convert gpt` (MBR fails with the BOOTICE/DuetPkg bug; skip it when the stick is already GPT: there `convert gpt` after `clean` fails with "not MBR formatted", and without it the stick comes out MBR, which UEFI firmware boots from as well), `create partition primary`, `format fs=fat32 quick override`, `assign letter=<free letter>`. The wizard runs the format as a second diskpart script (`select partition <n>` first) once Windows lists the new volume: in the same script it failed with "There is no volume selected". `override` dismounts the new volume first: Windows mounts it as soon as it is created, and if Explorer or an antivirus opens it the format fails with "Access is denied" (the System log shows "Cannot zero sectors ... Error code: 5"). Each script is retried twice; the set-up script rereads the stick's layout each time.
3. At the root of the USB create the folder `com.apple.recovery.boot` and copy both the `.dmg` and the `.chunklist` into it.
4. Copy the prepared `EFI` folder to the root of the USB, alongside `com.apple.recovery.boot`.
5. Legacy (non-UEFI) firmware only: diskpart route with BOOTICE (Restore MBR with `boot0`, Restore PBR with `boot1f32` from `Utilities/LegacyBoot/`), then place `bootx64` or `bootia32` from `Utilities/LegacyBoot/` at the root renamed to `boot`.

macrecovery board IDs given by the guide (Mac-board `-b`, `-m` model serial; all `-m 00000000000000000` from Catalina on):

| macOS | board (`-b`) |
| :--- | :--- |
| Big Sur (11) | Mac-42FD25EABCABB274 |
| Monterey (12) | Mac-FFE5EF870D7BA81A |
| Ventura (13) | Mac-4B682C642B45593E |
| Sonoma (14) | Mac-226CB3C6A851A671 |
| "Latest" (guide example: Sequoia 15) | Mac-937A206F2EE63C01 |
| Catalina (10.15) | Mac-00BE6ED71E35EB86 |
| Mojave (10.14) | Mac-7BA5B2DFE22DDD8C (`-m 00000000000KXPG00`) |

The guide's list predates Tahoe and gives no Tahoe board. (The app picks boards itself; see `Scripts/datasets/recovery_boards.py`.)

macOS 12 and above: the guide strongly recommends mapping USB ports (USBToolBox) before installing. With macOS 11.3 and newer `XhciPortLimit` is broken (boot loops, "AppleUSBHostPort::createDevice: failed to create device" reboot), so a mapped, `XhciPortLimit` disabled config is the working path.

Windows or Linux can only produce an online (recovery) installer; the full offline installer can only be made on macOS.

Sources: `_ref/dortania-install-guide/installer-guide/windows-install.md`, `installer-guide/README.md`, `troubleshooting/extended/kernel-issues.md`.

## 4. First boot: picker and NVRAM

1. Boot from the USB. Most laptops and some desktops default to the internal drive, so use the firmware boot menu key (Esc, F2, F10 or F12 are typical; look it up for the model).
2. The OpenCore picker normally shows: Windows, `macOS Base System (External)` / `Install macOS ... (External)` / the USB name, `OpenShell.efi`, `Reset NVRAM`.
3. Newer OpenCore enables `HideAuxiliary`, so press Space to reveal the installer/tools. The wizard's build turns it off and sets `Timeout` to 0: with the installer hidden, the picker offered only Windows and started it after 5 seconds, so the stick seemed to do nothing. The post-install helper sets `Timeout` 5 and `HideAuxiliary` back on when it moves the EFI to the internal disk.
4. Pick the installer entry (the macOS base system / "Install macOS" / USB-name entry marked External).
5. Reset NVRAM from the picker (`Reset NVRAM`, needs `ResetNvramEntry.efi` and `Misc -> Security -> AllowNvramReset`) when the installer shows the wrong language or after a failed install attempt. The language is stored in `prev-lang:kbd` (`NVRAM -> Add -> 7C436110-AB2A-4BBB-A880-FE41995C9F82`); `656e2d55533a30` is en-US:0. ThinkPads can be semi-bricked by an NVRAM reset; update the BIOS instead on those.
6. Verbose boot text is normal; it is what the troubleshooting section keys on.

Sources: `_ref/dortania-install-guide/installation/installation-process.md`, `troubleshooting/extended/userspace-issues.md`, `_ref/dortania-post-install/multiboot/bootstrap.md`.

## 5. Inside the installer

1. Check networking before anything else: Utilities, Network Utility (or `ping -c3 www.google.com` in Terminal). If the NIC does not appear, a kext is missing or wrong.
2. Disk Utility. Press Cmd+2 (View, Show All Devices) and erase the whole physical disk, not just the volume:
   - Scheme: GUID Partition Map.
   - Format: APFS. Use macOS Journaled (HFS+) for High Sierra on HDD and for all Sierra installs.
   - The disk needs a 200MB EFI partition; Disk Utility creates it on a freshly formatted disk. A 100MB Windows-made ESP triggers "MediaKit reports not enough space".
3. If erase fails: the first erase sometimes fails (try again), disable SATA hot-plug, update the drive firmware, or the drive is bad. SATA drives not shown means AHCI mode or a missing SATA kext.
4. Leave Disk Utility and run "Install macOS" onto the new volume. The recovery installer downloads the OS from Apple servers. If "The recovery server cannot get contacted" appears, retest networking; if networking works, retry later.

Sources: `_ref/dortania-install-guide/installation/installation-process.md`, `troubleshooting/extended/userspace-issues.md`.

## 6. Reboots during install (which entry to pick)

The guide does not give an exact reboot count. Documented behaviour:

1. After the first copy phase the machine restarts. Boot OpenCore again, but pick the macOS installer that is now on the internal drive (not the USB entry again).
2. Apple logo, then after a few minutes a progress timer ("x minutes remaining"); this takes a long time.
3. It may restart "a couple more times"; the guide only says to boot into OpenCore each time and not the USB installer. It does not name the later entries.
4. On Big Sur the log can pause at `Forcing CS_RUNTIME for entitlement` (system volume sealing): do not restart.

A successful install: installer restarts a couple of times, the progress timer finishes, and the "Setup your Mac" screen appears. Then go on to post-install (move EFI to the internal drive, USB map, audio, iServices).

Sources: `_ref/dortania-install-guide/installation/installation-process.md`, `troubleshooting/extended/kernel-issues.md` ("Forcing CS_RUNTIME").

### 6a. Re-enable WhateverGreen after a Tahoe install (AMD GPUs)

On macOS 26 with a discrete AMD GPU the app asks "Disable WhateverGreen for the first boot?" (default Yes), because WhateverGreen's connector patching can panic on Tahoe. In that mode `WhateverGreen.kext` stays in `EFI/OC/Kexts`, but its `Kernel -> Add` entry has `Enabled` false. If the build needed `agdpmod=pikera` (Navi cards), that boot-arg is removed and a `Kernel -> Patch` entry named "AGDP board-id patch (pikera) for first boot without WhateverGreen" does the same job (`com.apple.driver.AppleGraphicsDevicePolicy`, `board-id` to `board-ix`, MinKernel 25.0.0).

After macOS is installed and boots from the internal drive:

1. Open `EFI/OC/config.plist` (ProperTree or any plist editor).
2. Set `Kernel -> Add -> WhateverGreen.kext -> Enabled` to true.
3. If the AGDP patch entry is present, delete it from `Kernel -> Patch` and add `agdpmod=pikera` back to `NVRAM -> Add -> 7C436110-AB2A-4BBB-A880-FE41995C9F82 -> boot-args`.
4. Reboot. If it panics, set `Enabled` back to false and boot again; the GPU runs without WhateverGreen until a fixed release is available.

Sources: `docs/reference/tahoe.md` ("WhateverGreen on Tahoe"), `_ref/dortania-install-guide/extras/tahoe.md`.

## 7. Troubleshooting quick table

Boot stage reference: OpenCore loads (BOOTx64.efi, OpenCore.efi, drivers, ACPI, SMBIOS) then the picker; boot.efi handoff (EndRandomSeed, EXITBS); kernel (kext loading, `[ PCI configurations begin ]`, USB setup, DSMOS, audio, GPU); userspace (installer UI). Read the last verbose line before choosing a fix; use a DEBUG build with `Target` 67 for OpenCore-stage problems.

In the app: build again with option 7 and answer yes to "Build a DEBUG EFI with logging?", boot once from the USB drive, then use option 11 (Analyze Boot Log) with the USB drive letter. It picks the newest `opencore-*.txt`, reports the furthest stage reached (config loaded, drivers loaded, picker shown, entry chosen, booter handoff, kernel started), matches the log against the rows of this table and prints the cause and fix of each match plus the last 15 lines. From a terminal: `python OpenCoreWizard.py --analyze-log E:` (a file, folder or QEMU `serial.log` also works). The pattern list is `Scripts/datasets/boot_log_patterns.py`.

| Symptom | Documented cause | Fix |
| :--- | :--- | :--- |
| Black screen before picker | OpenCore not loading drivers, wrong folder layout, non-UEFI firmware, or a driver hanging | DEBUG build with `Target` 67; check log tail; fix `EFI` layout; `HfsPlusLegacy.efi` for HfsPlus load failures; remove `HiiDatabase.efi` if it conflicts; DuetPkg for non-UEFI |
| Reboots to BIOS | `EFI` folder missing from the ESP root | Make sure all OpenCore files are under `EFI` on the ESP |
| `no vault provided!` / `Invalid Vault mode` | Vault not Optional while `vault.plist`/signature absent; or wrong case | Set `Misc -> Security -> Vault` to `Optional` (exact case); restore a clean `OpenCore.efi` if `sign.command` was run |
| `OCB: System has no boot entries`, `OcScanForBootEntries failure - Not Found`, `failed to match a default boot option`, can't see macOS partitions | ScanPolicy blocks the drive, missing HfsPlus, wrong SATA mode, APFS driver settings | `Misc -> Security -> ScanPolicy` = 0; add `HfsPlus.efi`; SATA = AHCI; `UEFI -> APFS` `EnableJumpstart` YES, `HideVerbose` NO; `MinDate`/`MinVersion` -1 for High Sierra before 10.13.6; `UnblockFsConnect` for HP |
| `OCABC: Incompatible OpenRuntime r4, require r10` | Mismatched OpenCore versions | Use BOOTx64.efi, OpenCore.efi and OpenRuntime.efi from the same build |
| `Failed to open OpenCore image - Access Denied` (Surface) | Newer Surface firmware blocks the image even with Secure Boot off | `UEFI -> Quirks -> DisableSecurityPolicy` = YES |
| Can't select anything in the picker | Keyboard driver mismatch or missing PS/2 driver | Try `KeySupport` YES (and drop `OpenUsbKbDxe`), or the reverse; add `Ps2KeyboardDxe.efi` for PS/2 |
| SSDTs not added | iASL table length does not equal file size | Rebuild with a newer iASL or Acidanthera MaciASL |
| `OCS: No schema for DSDT, KernelAndKextPatch ...` | Clover config or configurator-edited plist | Start from the OpenCore `Sample.plist` |
| `Failed to parse real field of type 1` | `HaltLevel` written as `<real>` | Change to `<integer>2147483648` |
| `Buffer Too Small` | Above 4G Decoding off | Enable Above 4G Decoding |
| `OCABC: Memory pool allocation failure - Not Found` | BIOS settings | Above 4G on, CSM off (WHQL / Windows 8.1/10 mode may work), BIOS updated; some laptops need CSM on |
| Stuck on `[EB|#LOG:EXITBS:START]` / `EndRandomSeed` / after selecting macOS | Booter quirks wrong for firmware | See Booter list below |
| `Couldn't allocate runtime area` | Memory layout / slide issue | KASLR slide fix (extras/kaslr-fix.md) |
| `[EB|LD:OFS] Err(0xE)` for prelinkedkernel | Preboot volume not updated | Enable `JumpstartHotplug`, boot Recovery, run `diskutil apfs updatePreboot` on the Preboot volume |
| `OCB: LoadImage failed - Security Violation` | Missing Apple Secure Boot manifests on Preboot (macOS bug) with `SecureBootModel` set | Set `SecureBootModel` Disabled, or reinstall latest macOS, or copy `/usr/standalone/i386` contents into the Preboot `System/Library/CoreServices` |
| Stuck at `RTC...`, `PCI Configuration Begins`, `Previous Shutdown`, `HPET`, `HID: Legacy`, `apfs_module_start`, `Waiting on ... IOResources` | Missing EC SSDT, IRQ conflict, PCI allocation, NVMe/SATA issue, broken NVRAM or RTC | EC SSDT present and enabled; SSDT-HPET + patches; update BIOS, Above 4G or `npci`; NVMe firmware/model; SSDT-PMC (300-series) or emulated NVRAM (X99); SSDT-AWAC (300+ series) / SSDT-RTC0-RANGE (X99/X299) |
| Stuck at ACPI loading on B550/A520 | CPU object missing | Add SSDT-CPUR.aml (both `EFI/OC/ACPI` and `ACPI -> Add`); only for B550/A520 |
| "Waiting for Root Device", prohibited/stop sign | USB: 15-port limit; firmware ownership; or SATA | USB map; `ReleaseUsbOwnership` YES; XHCI hand-off in BIOS; try USB 2.0 port; SSDT-RHUB (Comet/Ice Lake, AMD); SATA AHCI; `SATA-unsupported`/Catalina AHCI kext |
| `IOConsoleUsers: gIOScreenLock` stall | GPU not initialising | UEFI-capable GPU; CSM off (laptops sometimes on); verify ig-platform-id/device-id (desktop UHD 630 may need `00009B3E`); `-igfxmlr`; Coffee Lake `igfxonln=1` on 10.15.4+; laptops: SSDT-PNLF, `-igfxblr` |
| Kernel panic `Cannot perform kext summary`, `Invalid frame pointer` | Kext order, missing/extra executable, duplicate kexts | Lilu before plugins, master before plugins; plist-only kexts without executables; no duplicate VoodooInput |
| Kernel panic `AppleIntelCPUPowerManagement` | Faulty NullCPUPowerManagement | Remove it, enable `Kernel -> Emulate -> DummyPowerManagement`; Penryn and older: HPET/IRQ fix |
| Frozen right before login | TSC out of sync | Add CpuTscSync; Skylake-X firmware: TSCAdjustReset |
| `kextd stall[0]: AppleACPICPU` | Missing/broken SMC emulator | Lilu and VirtualSMC enabled, Lilu first; never both VirtualSMC and FakeSMC |
| `cckprng_int_gen: generator has already been sealed` | No VirtualSMC, or SSDT-CPUR used on non-B550/A520 | Add VirtualSMC; use SSDT-CPUR only on B550/A520 |
| Stuck at `ramrod` loop (Big Sur) | Broken SMC emulator | Latest Lilu/VirtualSMC and `vsmcgen=1`, or FakeSMC for Big Sur only |
| `Stuck at 2 minutes remaining` | NVRAM writes failing | SSDT-PMC (300-series); or `LegacyEnable`, `LegacyOverwrite`, `WriteFlash` all YES |
| `Your Mac needs a firmware update ...` | Outdated or unsupported SMBIOS data | `PlatformInfo -> Automatic` YES; `UpdateSMBIOSMode` Create (Dell/VAIO: Custom with `CustomSMBIOSGuid`); supported SMBIOS; no `-no_compat_check`; latest OpenCore |
| `This version of Mac OS X is not supported: Reason Mac...` | SMBIOS no longer supported by this macOS | Choose a supported SMBIOS with `Automatic` enabled |
| macOS installer is Russian | Sample `prev-lang:kbd` | Set `656e2d55533a30`, reset NVRAM; or delete the key then set `prev-lang:kbd` string |
| Installer "damaged" | Expired certificate on pre-Oct 2019 installer | Download a newer installer, or set date `date 0901000019` in the recovery Terminal with networking off |
| Frozen in installer after 30 seconds | Faulty NullCPUPowerManagement | Remove it; `DummyPowerManagement` |
| DiskUtility errors / `MediaKit reports not enough space` | ESP too small, or only the partition was selected | Show all devices (Cmd+2) and erase the whole disk; expand ESP to 200MB |
| Wrong keyboard/mouse in Big Sur on legacy hardware | HID driver | Apply the `_isSingleUser` IOHIDFamily kernel patch from the userspace page |
| Reboot with `AppleUSBHostPort::createDevice: failed to create device` (11.3+) | `XhciPortLimit` broken | Disable `XhciPortLimit` after mapping USB ports |

Booter list for EXITBS/EndRandomSeed stalls: `DevirtualiseMmio` (TRx40 on; X99 off; otherwise use `MmioWhitelist`), `SetupVirtualMap` (needed for most firmwares, Z390 and older; must be off on Ice Lake, Comet Lake, B550/A520, late-2020 B450/X470 and X570, TRx40, X299 2020+ BIOS, QEMU), `EnableWriteUnprotector` (off with `RebuildAppleMemoryMap` and `SyncRuntimePermissions` on for MAT firmware, `OCABC: MAT support is 1`; reverse for older firmware), `AvoidRuntimeDefrag` for `max_cpus_from_firmware not yet initialized`, `ProvideConsoleGop` YES, `IgnoreInvalidFlexRatio` for Broadwell and older Intel only (not AMD, not Skylake+), CFG-Lock quirks, `AppleXcpmExtraMsrs` for Pentium/HEDT/odd CPUs, AMD kernel patches current. Some laptops (Inspiron 5370) halt even with MAT support: use the old quirk combo or `DevirtualiseMmio` with a whitelist.

Sources: `_ref/dortania-install-guide/troubleshooting/{boot.md,debug.md,troubleshooting.md}`, `troubleshooting/extended/{opencore,kernel,userspace,misc}-issues.md`.

## 8. DEBUG logging recipe

Swap `BOOTx64.efi`, `OpenCore.efi`, `OpenRuntime.efi` (and `OpenCanopy.efi` if used) for the DEBUG build. Then set `Misc -> Debug`: `AppleDebug` YES, `ApplePanic` YES, `DisableWatchDog` YES, `Target` 67 (0x01 log + 0x02 onscreen + 0x40 to file; 75 adds 0x08 serial, which the QEMU boot test uses), `DisplayLevel` 2147483714 (0x80000042). Boot-arg `keepsyms=1` keeps panic symbols. The log is a file `opencore-YYYY-MM-DD-hhmmss.txt` at the root of the boot drive. To turn off: replace files with RELEASE, `AppleDebug` NO, `ApplePanic` NO, `Target` 0. Serial logging needs a serial header/port and a second machine.

The app does all of this when you answer yes to the DEBUG question in option 7: the whole base EFI comes from the DEBUG OpenCorePkg of the same version (cached as `OCK_Files/OpenCorePkg-DEBUG`, checked against the SHA-256 published with it), the `Misc -> Debug` values above are set (`SysReport` stays NO), ocvalidate still runs, and `build_summary.json` records `"debug": true`. The RELEASE build is unchanged when the answer is No. Option 11 reads the resulting log.

Sources: `_ref/dortania-install-guide/troubleshooting/debug.md`, `troubleshooting/kernel-debugging.md`.
