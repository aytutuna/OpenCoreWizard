# OpenCore Legacy Patcher (OCLP): what applies to a hackintosh

Summary of the OCLP documentation set (`_ref/oclp/docs`) with an explicit split between what is generic OpenCore behaviour, what is Mac-only, and what the app already borrows. Statements about Hackintosh use come from the install guide (OCLP docs themselves never mention Hackintosh).

## 1. What OCLP is and is not

- OCLP is a "dumb" front end: it edits a `config.plist` and moves files; the actual work is OpenCorePkg. It runs only on macOS (the app needs Yosemite 10.10+; making installers needs El Capitan 10.11+ for Ventura and High Sierra 10.13+ for Sonoma and newer, because of `createinstallmedia`).
- Targets real Intel Macs only (identifiers in `MODELS.md`); PowerPC and Apple Silicon are excluded. Even natively supported Macs can use it.
- Supported macOS: designed for Big Sur 11 to Sequoia 15; other versions "may work in a broken state" with no support. Tahoe (macOS 26) and any fork are not mentioned anywhere in the OCLP docs (see section 6).
- It does what OpenCore does: patches in memory at boot instead of flashing firmware ("zero firmware patching"). Everything changed on disk is a root patch (section 3) and is separate from the bootloader.
- It does not: support betas (patch development happens in beta; no help given), downgrade macOS without a wipe, restore Apple Intelligence (needs an NPU) or iPhone Mirroring (needs T2 attestation), support T2 Macs that Sequoia dropped (MacBookAir8,1/8,2 panic in `AppleKeyStore` timeouts), enable Intel GMA GPUs, or make AMD Navi work in Mac Pro 2008-2012 on Ventura and newer (needs AVX2).
- Models with 2GB RAM cannot install Sonoma/Sequoia; 3GB+ required.

Sources: `README.md`, `HOW.md`, `FAQ.md`, `MODELS.md`, `SEQUOIA-DROP.md`, `SONOMA-DROP.md`.

## 2. OpenCore-level behaviour OCLP configures

These are the settings OCLP puts in the config.plist (PATCHEXPLAIN.md). Most are generic OpenCore techniques; the "Mac-only" column says whether they make sense on a PC.

| Item | What it does | Hackintosh relevance |
| :--- | :--- | :--- |
| `Booter -> Patch` reroute `HW_BID` to `OC_BID` | Lets installers run on unsupported models | VMM-spoofing only; not needed for a properly matched SMBIOS |
| `Booter -> Quirks -> ForceBooterSignature` | Required for hibernation (boot.efi thinks OpenCore is Apple firmware) | Mac only |
| `Kernel -> Patch` SMC patch + `SMC-Spoof.kext` | Spoofs `smc-version` 9.9999 to avoid SMC firmware updates | Mac only (real SMC) |
| `Kernel -> Patch` reroute `kern.hv_vmm_present` | Makes userspace see a VM so unsupported hardware can install and update | Same technique as RestrictEvents `revpatch=sbvmm` (used by the install guide for OTA updates) |
| "Force FileVault on Broken Seal" patch | Lets FileVault work on root-patched systems | Needed only with root patches |
| "Disable Library Validation Enforcement" patch | Non-Metal root patches fail library validation | Non-Metal GPUs only |
| `SurPlus` patch | macOS 11.3-12.0.1 need RDRAND; forces a value | Pre-Ivy Bridge CPUs |
| `Kernel -> Quirks -> ThirdPartyDrives` | Avoids hibernation wake issues on third-party drives | Mac only |
| `Misc -> Security -> SecureBootModel` | Sets the T2 Secure Enclave identifier so native OS updates work on T2 model spoofs | Same setting as the install guide's Apple Secure Boot |
| NVRAM boot args | `amfi=0x80` (disables AMFI for unsigned root patches), `-revasset`, debug args `-v keepsyms=1 debug=0x100`, `-liludbgall`, `msgbuf=1048576`, `agdpmod=pikera` (MacBookPro9,x), `shikigva=80 unfairgva=1` (upgraded AMD Metal GPUs DRM) | `amfi=0x80` and `agdpmod=pikera` are generic; the rest Mac model specific |
| ACPI | `SSDT-PCI` (Sandy/Ivy 32-bit allocation limit for Windows audio), `SSDT-CPBG`, `SSDT-DGPU`, `EHCx`/`XHC1` renames | Mac-model specific; the USB renames mirror the install guide's USB prerequisites |
| `UEFI -> ProtocolOverrides -> GopPassThrough` | UGA firmware with GOP GPU | Old Macs |
| Injected kexts | Lilu, WhateverGreen, CPUFriend, AirportBrcmFixup, BlueToolFixup, Bluetooth-Spoof, FeatureUnlock, RestrictEvents, CryptexFixup (non-AVX2 cryptex), AutoPkgInstaller (auto root patching), NVMeFix, RSRHelper, NVIDIA/Marvell/Broadcom/Intel legacy Ethernet kexts, FireWire, USBMap, AAMouSSE and telemetrap (SSE4.1 CPUs), IO80211ElCap/corecaptureElCap, AppleBackLightFixup, BacklightInjector, Innie, KDKlessWorkaround, MonteAHCIPort, NoAVXFSCompressionTypeZlib, SimpleMSR, etc. | Lilu/WhateverGreen/CPUFriend/AirportBrcmFixup/BlueToolFixup/RestrictEvents/CryptexFixup/NVMeFix are the generic ones |

Sources: `_ref/oclp/docs/PATCHEXPLAIN.md`, `HOW.md`.

## 3. Root patches (on-disk patches)

### What they are

Root patches (post-install volume patches) are files copied onto the macOS system volume to bring back drivers Apple removed. Because the system volume is sealed, applying them breaks the seal, which is why:

- macOS stops offering small delta updates and downloads the full installer (~12GB) for each update ("Deltas require the root volume to be unmodified").
- Patches are wiped by every macOS update and have to be reinstalled.
- Automatic update download/staging must be turned off: macOS now modifies the system volume while *downloading*, producing "System version mismatch" errors during patching (`SystemVersion.plist build version mismatch: found 15.4 (24E247), expected 13.7.5 (22H527)`). Fixes: finish the staged update (if supported), reinstall macOS, or use the PurgePendingUpdate tool from the OCLP Discord.

### Hardware classes patched

| Class | Examples in the docs | macOS versions |
| :--- | :--- | :--- |
| Non-Metal GPU acceleration | TeraScale 1/2 (HD 2000-6000), NVIDIA Tesla (patch lists; Fermi is non-Metal per the FAQ chart), Intel Iron Lake, Sandy Bridge (HD 3000) | Big Sur and later. Uses Mojave frameworks (CoreDisplay, IOSurface, OpenGL, GPUSupport, SkyLight) |
| NVIDIA Kepler | GTX 600-700 series, Quadro K | Monterey and later (12.0+), Metal compiler downgrade needed on 13.3+ |
| Intel Ivy Bridge (HD 4000) | `AppleIntelHD4000*`, `AppleIntelFramebufferCapri`, GVA | Monterey and later |
| Intel Haswell (HD 4400/4600/5000, Iris) | `AppleIntelHD5000*`, `AppleIntelFramebufferAzul`, HSW VA | Ventura and later (13.0+) |
| Intel Broadwell (HD 5300-6000, Iris 6100/Pro 6200) | `AppleIntelBDW*` | Ventura and later |
| Intel Skylake (HD/Iris 5xx) | `AppleIntelSKL*` | Ventura and later |
| AMD GCN 1-3 (HD 7000, R9, FirePro D300/500/700) | `AMD7000Controller` ... `AMD10000Controller`, `AMDRadeonX4000*` | Ventura and later; needs KDK |
| AMD Polaris (RX 4xx/5xx) when the CPU lacks AVX2 | `AMDRadeonX4000*`, `AMDMTLBronzeDriver` | Ventura and later |
| AMD Vega (legacy) | `AMDRadeonX5000*` | Ventura and later |
| Broadcom/Atheros Wi-Fi (BCM94328, BCM94322, Atheros) | `IO80211ElCap`, `corecaptureElCap`, `WiFiAgent.app`, `airportd` | Monterey and later |
| Bluetooth, Touch Bar/T1, iSight, USB 1.1 (OHCI/UHCI), SDXC, audio on iMac7,1/8,1 | various | Ventura and later for USB 1.1, T1 fix in OCLP 1.1.0+, `AppleHDA` from El Capitan for the 2007/2008 iMacs |

GPU classes per OS (docs list): Ventura/Sonoma/Sequoia patch NVIDIA Tesla and Kepler, AMD TeraScale, GCN 1-3, Polaris without AVX2, Intel Iron Lake, Sandy Bridge, Ivy Bridge, Haswell, Broadwell, Skylake; Monterey patches Tesla, Kepler, TeraScale, Iron Lake, Sandy Bridge, Ivy Bridge; Big Sur patches Tesla, TeraScale, Iron Lake, Sandy Bridge. Broadcom Wi-Fi support was removed in Sonoma; the install guide's workaround on Sequoia and Tahoe is `AppleBCMWLANCompanion` instead of root patches.

Sources: `_ref/oclp/docs/PATCHEXPLAIN.md`, `POST-INSTALL.md`, `FAQ.md`, `_ref/dortania-install-guide/extras/{ventura,monterey,tahoe}.md`.

### Security prerequisites

- **SIP**: must be lowered when root patching. OCLP computes the value from OS version and model. Ventura and newer: all unsupported systems need lowered SIP. Monterey: most 2013+ systems can keep full SIP; pre-2012 (non-Metal), Kepler and HD 4000 need it lowered. Big Sur: Metal-capable systems from 2012 on can keep full SIP; non-Metal cannot. After root patching, SIP cannot be re-enabled without risking the install. OCLP overwrites the SIP value on every boot so an NVRAM reset cannot brick the install; the SIP value is changed in OCLP's Security settings, not in recoveryOS.
- **AMFI**: disabled through `amfi=0x80` for unsigned root patches. On Tahoe the app adds `-amfipassbeta` (`config_prodigy.py`); neither the OCLP docs nor the install guide mention it.
- **Apple Secure Boot / `SecureBootModel`**: the install guide states that using OCLP requires "SIP, Apple Secure Boot, and AMFI disabled" (Monterey and Ventura notes). OCLP itself sets `SecureBootModel` only to support OS updates on T2 model spoofs.
- **Library validation** disabled for non-Metal patches; **FileVault** needs the "force on broken seal" patch.

### Downloads needed during patching (internet required)

| Package | Needed for | Notes |
| :--- | :--- | :--- |
| KDK (KDKSupportPkg, Kernel Debug Kit) | Macs with AMD Legacy GCN GPUs (HD 7000, R9, FirePro D300/500/700) on Ventura and newer; real examples iMac 2014-2015, MacPro6,1, MacBookPro11,5; upgraded MacPro3,1-5,1 and iMac10,1-12,1 with GCN GPUs | Use the KDK version closest to the installed OS; OCLP downloads it automatically when the background process is on |
| MetallibSupportPkg | NVIDIA Kepler, Intel Ivy Bridge and Haswell (3802 GPUs) on Sequoia or newer | Build must match the OS version exactly; without internet OCLP offers a Wi-Fi-only patch first, then you must reboot, connect and run patching again. Ethernet avoids the split |

An internet connection is therefore a prerequisite for the final patch run.

Sources: `_ref/oclp/docs/POST-INSTALL.md`, `PROCESS.md`, `UPDATE.md`, `TROUBLESHOOT-APP.md`.

## 4. Behaviour after macOS updates

Order OCLP documents:

- Major upgrades (for example Sonoma to Sequoia): update OCLP; rebuild OpenCore to the internal disk; download and create a USB installer with OCLP, boot it, install without using Disk Utility if data must be kept; reinstall root patches afterwards. In-place OTA from System Settings may work but is "not recommended".
- Minor/dot updates: update OCLP; rebuild OpenCore to the internal disk; run the update in System Settings (OCLP downloads KDK in the background if needed); reinstall root patches afterwards. Root patches are wiped by the update.
- Disable "Download new updates when available" before anything else (System Settings, General, Software Update, Automatic Updates (i); Big Sur/Monterey: Software Update, Advanced). Sequoia 15.4+ nags to re-enable it.
- The OCLP background process: monitors OpenCore and root-patch status, prompts to reinstall patches, warns when OpenCore is booted from USB, starts KDK downloads.
- Recovery recipes: stuck after root patching (Monterey+): boot recovery, `mount -uw "/Volumes/Macintosh HD"`, revert the snapshot (Big Sur cannot revert snapshots); infinite recovery loop: caused by Apple Secure Boot NVRAM values when not booting OpenCore: uninstall OpenCore and reset NVRAM; "Unable to resolve dependencies, error code 71": clear `/Library/Extensions` except HighPoint*/SoftRAID* and `/Library/Developer/KDKs`; "Your Mac needs a firmware update" needs Moderate SMBIOS patching or higher.

Sources: `_ref/oclp/docs/UPDATE.md`, `FAQ.md`, `TROUBLESHOOT-APP.md`, `TROUBLESHOOT-MISC.md`, `UNINSTALL.md`.

## 5. Real Mac only versus hackintosh

| Part of OCLP | Real Mac | Hackintosh |
| :--- | :--- | :--- |
| App, installer creation (`createinstallmedia`), "Build and Install OpenCore" | Yes | No. The app cannot run on a PC; the equivalent steps are done by this project |
| Model selection / SMBIOS patching levels (Minimal/Moderate/Advanced spoofing) | Yes (hardware is the real model) | Not applicable; the SMBIOS is chosen by the install guide rules |
| Mac-specific ACPI/kexts (SMC spoof, ForceBooterSignature, Innie, FireWire, dual-socket workarounds, T1/T2 fixes) | Yes | No |
| `amfi=0x80`, SIP lowered, Secure Boot disabled, root patching of GPU/Wi-Fi/Bluetooth | Yes | Yes in principle, same mechanism. The install guide: "No support is provided for Hackintoshes using OCLP!", you lose delta updates, and it requires SIP, Apple Secure Boot and AMFI disabled |
| Who needs it | Macs dropped by Apple | PCs whose GPU/iGPU/Wi-Fi/audio lost native drivers: Ventura+ pre-Haswell iGPUs (Haswell, Broadwell, Skylake iGPUs), Monterey+ Ivy Bridge iGPU and Kepler, non-AVX2 Polaris, Broadcom Wi-Fi on Sonoma+, AppleHDA on Tahoe |
| Alternatives the guide prefers over root patching | n/a | Spoof Skylake iGPU as Kaby Lake (WhateverGreen 1.6.1+), `AppleBCMWLANCompanion` for Broadcom Wi-Fi on 15/26 (needs VT-d), VoodooHDA for Tahoe audio (`csr-active-config` `03000000`) |
| Time Machine / Migration Assistant guidance | Yes (`TIMEMACHINE.md`) | Same macOS behaviour |
| Installing Windows in UEFI mode (`WINDOWS.md`) | Yes (UEFI Windows on old Macs; Windows 11 needs the TPM 2.0 requirement bypassed) | Only the advice transfers: keep OpenCore on its own 200MB FAT (MS-DOS FAT) partition so Windows cannot overwrite it; only UEFI Windows shows in the picker |

The install guide for Ventura and Monterey also notes that Haswell/Ivy Bridge hack SMBIOS conversions exist so an unsupported iGPU can stay on a supported SMBIOS: Ventura "All unsupported laptops (Haswell, Broadwell, Skylake) should use MacBookPro14,1" and "All desktops with unsupported iGPUs should use iMac18,1"; "All desktops with dGPUs should use iMac18,2, MacPro7,1 or iMacPro1,1".

Sources: `_ref/dortania-install-guide/extras/{ventura,monterey,tahoe}.md`, `_ref/oclp/docs/{PATCHEXPLAIN,POST-INSTALL,FAQ}.md`.

## 6. The lzhoang2801 fork and Tahoe

The OCLP docs in `_ref/oclp/docs` contain no mention of the lzhoang2801 fork, of Hackintosh use, or of macOS 26 Tahoe; their newest OS page is `SEQUOIA-DROP.md` (OCLP 2.0.0 early Sequoia support). What the sources do say about Tahoe is in the install guide (`extras/tahoe.md`), not OCLP:

- AppleHDA.kext was removed; AppleALC cannot work until AppleHDA is reinjected by root patching (same caveats as OCLP) or VoodooHDA is used.
- Broadcom Wi-Fi: root patches no longer needed with AppleBCMWLANCompanion (not feature complete, requires VT-d).
- Intel Bluetooth: `-ibtcompatbeta`.
- OTA updates: RestrictEvents `revpatch=sbvmm` plus `SecureBootModel` `Disabled` (or iBridged).
- WhateverGreen has AMD connector patching problems on macOS 26.
- hackintosh.com's page calls Tahoe the last Intel-supporting macOS (macOS 27 "Golden Gate" is Apple Silicon only).

The app (`OpenCoreWizard.py show_oclp_warning`, `kext_maestro.py`) already says on Tahoe that OCLP "rolls back AppleHDA" and selects `AirPortBrcmNIC-Tahoe`, `-amfipassbeta` and similar; those behaviours belong to the fork and have no counterpart in the official OCLP docs. Differences between official OCLP and the fork are therefore not documented in the listed sources.

Sources: absence checked with a repository-wide search of `_ref/oclp/docs` for "lzhoang", "Tahoe", "hackintosh", "fork"; `_ref/dortania-install-guide/extras/tahoe.md`; `_ref/hackintosh-com/index.html`.

### Status of the fork (checked 2026-10-21)

The fork the kit points to, laobamac/OCLP-Mod, was archived on 2026-06-13. Its README says existing releases keep working but the project's own download servers (SimpleHacAPI) are being shut down, and asks users to disable SimpleHacAPI in the settings. Release 3.1.9 (2026-04-15) is the last one and is what `postinstall_tools.py` pins. Mainline dortania/OpenCore-Legacy-Patcher was last pushed 2026-10-11 and is the fallback; whether its Tahoe root patches behave the same on a hackintosh built by this app is untested. Upstream OpCore-Simplify (2026-10-15) still refers to the fork, so watch what it switches to.

Because the fork is archived, the post-install kit now ships its own root patcher, MacPatch (section 7), and keeps OCLP-Mod 3.1.9 as the fallback.

## 7. MacPatch: the kit's root patcher

Mainline OCLP has the patch sets a hackintosh build needs, including the Tahoe AppleHDA restore (`sys_patch/patchsets/hardware/misc/modern_audio.py`), but its detection is written for real Macs. `ModernAudio.present()` only returns true when `smbios_data` lists the model with `SecureBootModel` None, so an iMac20,1 build is never offered the audio patch, and Wi-Fi detection reads the card through OCLP's own device probe. The build already knows which patches the machine needs, so the kit writes them to `PostInstall/patches.json` and MacPatch (`postinstall/macpatch`, copied to `PostInstall/Tools/MacPatch`) applies exactly those, with OCLP's engine logic and OCLP's payload.

Status: unverified. Nothing in it has run on a real Mac or hackintosh. The unit tests run its logic against canned command output on Windows, Linux and the macOS CI runner, and CI runs a dry run on the runner, where preflight fails by design (SIP is enabled and AMFI is not relaxed). When anything under `postinstall/` changes, and weekly, CI also downloads the pinned payload, mounts it read-only and checks that every source path MacPatch would use exists for eight macOS builds from 12.7.3 to 26.0.1 (`tests/ci/check_payload.py`).

### patches.json

| Key | Meaning |
| :--- | :--- |
| `version` | 1 |
| `created`, `macos_version`, `macos_name`, `smbios_model`, `sip`, `secure_boot_model`, `boot_args` | Copied from `build_summary.json`, for the log and the plan |
| `root_patches` | List of `{"set": ...}` entries. `modern_audio`; `modern_wireless` and `legacy_wireless` with `device` (PCI ID as in the report, e.g. `14E4-43A0`) and `name`; `graphics` with `codename`, `device`, `name` and `"supported": false`. When a graphics entry exists the kit leaves MacPatch and patches.json out and the checklist sends everything to OCLP-Mod, because the two patchers must not be mixed on one system |
| `payload` | `psp_version`, `url`, `sha256`, `size` of PatcherSupportPkg's `Universal-Binaries.dmg` (2.0.3: 790749184 bytes, SHA-256 `cf2a80deafe153ed4a9aa32c63463032e02718f32713e9b3f0a6e09f9a6ffbab`) |

Which sets the Windows side writes: `modern_audio` when the target is Darwin 25 or newer and AppleALC was chosen without SimpleHDA; `modern_wireless` for Broadcom cards in OCLP's AirportBrcmNIC, AirPortBrcm4360 and AirPortBrcmNICThirdParty lists (43BA, 43A3, 43A0, 4357, 43B1, 43B2, 4331, 4353) whose OCLP compatibility covers the target, Darwin 23 or newer, unless AppleBCMWLANCompanion was chosen, and for Intel cards when the plain AirportItlwm kext (not the laobamac fork, not itlwm) was chosen on Darwin 24 or newer; `legacy_wireless` for OCLP's AirPortBrcm4331, AppleAirPortBrcm43224 and AirPortAtheros40 lists on Darwin 21 or newer. Atheros IDs our checker marks as OCLP-patchable but OCLP's list lacks (106B-0086, 168C-002B, 168C-002E) get no entry. MacPatch still decides per set whether the running macOS needs it, so the same manifest works after an update or on an older release.

### What was ported from OCLP 3.0.0

- `sys_patch/sys_patch.py`: the sanity check (the mounted `SystemVersion.plist` build must match the running build), the install loop for Overwrite, Merge and Remove on the system volume and Execute, the source-file preflight, the kernel collection rebuild and snapshot order. MacPatch writes `/System/Library/CoreServices/MacPatch.plist` (patch sets, PatcherSupportPkg version, KDK used) where OCLP writes `OpenCore-Legacy-Patcher.plist`.
- `mount/mount.py`, `mount/snapshot.py`: root volume identifier from `diskutil info -plist /` (snapshot suffix stripped), `mount -o nobrowse -t apfs /dev/<disk> /System/Volumes/Update/mnt1`, `bless --folder <mnt>/System/Library/CoreServices --bootefi --create-snapshot`, revert with `bless --mount <mnt> --bootefi --last-sealed-snapshot`.
- `utilities/files.py`: overwrite (delete, then `cp -R`) versus merge (`rsync -r -i -a`), removal, `chmod 755` and `chown root:wheel`.
- `kernelcache/kernel_collection/boot_system.py`: `kmutil create --allow-missing-kdk --volume-root <mnt> --update-all --variant-suffix release` on Ventura and newer, `kmutil install` on Big Sur and Monterey.
- `utilities/kdk_merge.py`, `support/kdk_handler.py`: installed KDK for the exact build under `/Library/Developer/KDKs` (validated against the pkg receipt or OCLP's kext list), otherwise the KdkSupportPkg manifest (exact build, then the closest same-major build no newer than the host and at most one minor behind), offline fallback to installed KDKs matching the version, download, SHA-256 and size from the manifest (OCLP itself only runs `hdiutil verify`), `installer -pkg`, then `rsync` of the KDK's `System/Library/Extensions` onto the mounted root.
- `utilities/dmg_mount.py`, `support/validation.py`: `Universal-Binaries.dmg` is downloaded to `/Library/Caches/OpenCoreWizard` (or taken from the MacPatch folder when a copy with the pinned size and hash is there) and attached with OCLP's arguments (`-noverify -nobrowse -shadow ... -passphrase password`); its folders are the source root for the patch files.
- Patch sets, data copied verbatim: `modern_audio.py` (AppleHDA from `26.0 Beta 1`, native before Darwin 25 and on Tahoe Beta 1 `25A5279m`, KDK required on Darwin 25+), `modern_wireless.py` (`13.7.2-<xnu>`, the Extended part only up to Sonoma), `legacy_wireless.py` (`11.7.10` or `11.7.10-Sandbox` after the CVE-2024-23227 releases, Extended from Ventura with OCLP's per-release version strings).
- `patchsets/detect.py` and `detections/amfi_detect.py`: unsupported host (outside Darwin 20 to 25), missing network (only when the KDK or the payload must be downloaded; offline with the payload present it patches Wi-Fi first, as OCLP does), FileVault, SIP bits from `patchsets/hardware/base.py` (0x803 on Ventura and newer) read with `csr_get_active_config`, AMFI level with the AMFIPass 1.2.1+ override, repatching (an existing OCLP patch record, or a MacPatch record with more than the Wi-Fi sets, needs a revert first), and a root volume that is not an APFS snapshot. Revert only needs a supported host and lowered SIP, and does not read patches.json. The build number comes from `/System/Library/CoreServices/SystemVersion.plist` as in OCLP's `os_probe.py`, because `sw_vers` reports the Rapid Security Response build.

### Deliberately left out

- Graphics patch sets, MetallibSupportPkg, the non-Metal and Skylight cleanups: phase two.
- The auxiliary kernel collection and `/Library/Extensions` handling. None of the three sets installs a kext outside `/System/Library/Extensions`; on Ventura and newer, when no KDK is needed (Wi-Fi only), OCLP rebuilds only the auxiliary collection, so MacPatch rebuilds nothing and just creates the snapshot.
- RSRRepair, which OCLP installs after a Boot/System KC rebuild to resync the Preboot copy after a Rapid Security Response; it is a binary from OCLP's payloads.
- KDK `.pkg` backups and their restore, the "KDK already merged" shortcut, the IOHIDFamily signature backup (USB 1.1 only), mkext and prelinked kernels (pre-Big Sur), Apple Silicon `bless` arguments, the launch agents and the privileged helper (MacPatch re-runs itself with `sudo`).
- Real-Mac checks: SecureBootModel, WhateverGreen, nvda_drv, OCLP-Settings NVRAM flags.
- Python packages: `requests` and `packaging` are replaced by `curl` (system trust store, progress bar) and tuple comparisons; the tool needs only Python 3.9 from Apple's Command Line Tools.

### Exit codes

0 patched (or dry run passed), 1 error, 2 preflight failed, 3 nothing to do on this macOS.

### Not verified

Everything that touches macOS: the mount, bless, kmutil, rsync and hdiutil calls on a real system volume, whether `csr_get_active_config` reports the OpenCore-set value, the payload folder names inside `Universal-Binaries.dmg` 2.0.3 until the CI payload check has run once, the KDK install and merge on Tahoe, whether AppleHDA from `26.0 Beta 1` loads with AppleALC on a hackintosh, the AirportItlwm plus modern-wireless combination, the `.command` launchers in Finder from a FAT32 drive, and the recoveryOS revert command in the checklist.
