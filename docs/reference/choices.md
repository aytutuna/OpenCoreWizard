# Prompts and what to answer

Generated from `Scripts/datasets/explanations.py` by `tests/ci/render_choices.py`; change the dataset and run the script again instead of editing this file. The app shows the same text before each prompt in the terminal, in the window's question dialogs and pages, and in the express summary for the answers it took for you.

## Which macOS to install

Asked at the prompt containing `Please enter the macOS version`.

macOS comes in yearly versions. Each one drops support for some older hardware and changes what the bootloader has to do.

The list only shows versions your hardware can run. The suggested one is the newest version where every part of this PC still has a working driver without root patches.

| Answer | Means | Pick it when |
|---|---|---|
| Enter | the suggested version | Almost always. It is the newest one that works without extra patching. |
| older number | an older macOS | When a program you need does not run on the newest version, or when the newest is marked as needing OpenCore Legacy Patcher. |
| newer number | a newer version from the list | Only when you know why the suggestion held back. A newer unmarked version is there when the suggestion stepped back from a beta or was capped for one device; a version marked Requires OpenCore Legacy Patcher needs root patches: lower security, no normal updates, patches redone after each update. |

Default: Press Enter. The suggestion already steps back from betas and from versions that would need patching.

## Root patches with OpenCore Legacy Patcher

Asked at the prompt containing `Continue with OpenCore Legacy Patcher`.

A root patch copies driver files that Apple removed back into macOS after it is installed. OpenCore Legacy Patcher (OCLP) is the tool that does it. Everything else the bootloader does happens outside macOS; a root patch changes macOS itself.

Some of your hardware has no driver in the macOS you chose. Without the patch that device will not work: usually the graphics card, Wi-Fi or, on macOS 26, sound.

| Answer | Means | Pick it when |
|---|---|---|
| yes | continue with root patches | When the device that needs them matters to you and you accept the cost: System Integrity Protection off, macOS updates arrive as full installers, patches redone after every update. |
| no | do not use root patches | When you can live without that device, or when you would rather pick an older macOS where it still works natively. The build stops so you can change the version. |

Default: No, unless you chose root patches on purpose: for a graphics card or Wi-Fi card you need, or the AppleHDA audio option. No stops the build so you can pick an older macOS or the other audio or Wi-Fi option.

## Sound on macOS 26

Asked at the prompt containing `Select audio kext for your system`.

Apple removed AppleHDA, the driver for the sound chip on the motherboard, from macOS 26. AppleALC, the add-on that makes PC sound chips work, needs that driver underneath it. So something has to put a driver back.

There are two ways to do that, and they differ in how much they change your system.

| Answer | Means | Pick it when |
|---|---|---|
| 1 | AppleALC + OCLP root patch | When this build already needs root patches for the graphics card or Wi-Fi. The patch puts Apple's own AppleHDA back, so every jack and the microphone work as before, and it costs nothing extra once patching is needed anyway. |
| 2 | AppleALC + SimpleHDA | When nothing else needs root patches. SimpleHDA is a replacement driver that the bootloader loads, so macOS system files stay untouched and SIP stays on, so updates install normally. It is a younger project; on a few boards a jack or the internal microphone is missing. |

Default: The prompt suggests 1 when root patches are already needed and 2 otherwise. Take it.

## Codec layout ID

Asked at the prompt containing `Enter the ID of the codec layout`.

Your sound chip has several jacks and an internal speaker wired in a way only the board maker knows. A layout is a map of that wiring. AppleALC ships many layouts per chip, each contributed by someone with a specific board, and the number picks one.

A wrong layout means sound works but a jack is dead, or headphones do not switch. The app pre-selects the layout whose comment names your board when one exists. Otherwise the green default is a layout made for a similar board with the same chipset, then, on desktops, one made for another board of the same maker, then the first layout by one of a few well known authors, or the first in the list when none of them made one for this chip.

| Answer | Means | Pick it when |
|---|---|---|
| Enter | the default layout | First install. It is right most of the time. |
| other number | another layout from the list | After install, if a jack or the microphone does not work. Try the ones whose comment names your board or laptop model. |

Default: Press Enter. To change it later, edit layout-id under DeviceProperties -> PciRoot(...)/Pci(...) of the audio controller in config.plist (or the alcid= boot argument) and reboot; no rebuild.

## Driver helper for your AMD graphics card

Asked at the prompt containing `Select kext for your AMD`.

macOS has drivers for these AMD cards, but a PC presents the card slightly differently than a Mac does. A helper kext fixes that up at boot. Two helpers exist.

They use different firmware and behave differently on some cards.

| Answer | Means | Pick it when |
|---|---|---|
| 1 | NootRX | When WhateverGreen gives you a black screen or missing display outputs. It loads newer card firmware. It cannot be used together with an Intel graphics chip. |
| 2 | WhateverGreen | First install. It uses the firmware macOS ships and is what most guides assume. |

Default: WhateverGreen. Swap to NootRX after install if the display misbehaves.

## Turn the graphics helper off for the first boot

Asked at the prompt containing `Disable WhateverGreen for the first boot`.

WhateverGreen is the helper kext that makes graphics cards behave on a PC. On macOS 26 its handling of display connectors can crash the installer on some AMD cards.

Installing with it off avoids that crash. Once macOS is on the disk you turn it back on, which the post-install checklist explains.

| Answer | Means | Pick it when |
|---|---|---|
| yes | disable it for the first boot | When the build says it applies to you: AMD graphics card, macOS 26, monitor on that card. |
| no | keep it enabled | When you already know this card installs fine with it on. |

Default: Yes.

## Broadcom Wi-Fi on macOS 14 and newer

Asked at the prompt containing `Select Broadcom Wi-Fi option`.

Intel Macs used Broadcom Wi-Fi cards, which is why the same cards worked in a hackintosh without any extra driver. macOS 14 removed the driver for these older Broadcom cards.

Two ways exist to get the card working again, and they differ in how much they change your system.

| Answer | Means | Pick it when |
|---|---|---|
| 1 | OCLP root patches | The proven way. The patch puts Apple's old Wi-Fi driver back into macOS. Costs: SIP lowered, updates as full installers, patch redone after each update. |
| 2 | AppleBCMWLANCompanion | A kext loaded by the bootloader instead of a patch, so macOS system files stay untouched. Needs an Intel CPU with VT-d turned on in the BIOS, and its author still calls it unstable. Try it if you want SIP on and normal updates; fall back to 1 if Wi-Fi does not show up. |

Default: 1.

## Intel Wi-Fi driver

Asked at the prompt containing `Select Intel Wi-Fi kext`.

Apple never used Intel Wi-Fi cards, so macOS has no driver for them. Volunteers wrote one, and it comes in three flavours.

They differ in whether Wi-Fi appears in the normal menu bar and whether macOS has to be patched. Type the number shown in the list next to the one you want.

| Answer | Means | Pick it when |
|---|---|---|
| AirportItlwm-laobamac | native menu, no patches | First choice when it is listed. Wi-Fi works from the menu bar, with Handoff and Continuity, and macOS system files stay untouched. |
| AirportItlwm | native menu, needs root patches on macOS 15 and 26 | On macOS 13 and older. Works without patches on 14 but is less reliable; needs root patches on 15 and 26. |
| itlwm | separate app, most stable | The fallback. Wi-Fi is managed by the HeliPort app instead of the menu bar. No Handoff or other Apple continuity features, but it rarely breaks. |

Default: The prompt suggests the laobamac build when it is listed, AirportItlwm on macOS 13 and older, and itlwm otherwise. Take it.

## Which devices macOS should see

Asked at the prompt containing `combination (1-`.

This PC has more than one device of the same kind, usually two graphics chips. macOS can only use some combinations, so the others are hidden from it.

Each line shows which macOS versions that combination supports. Hidden devices are only hidden for macOS; Windows keeps using them.

| Answer | Means | Pick it when |
|---|---|---|
| number | a line from the list | Every line already works with the macOS you chose. Pick the one that keeps the devices you use: on a desktop with a supported AMD card, the line that includes it; on a laptop, the Intel graphics. Prefer the wider range only between lines that keep the same devices. |

Default: None. The list is not sorted best first, so read the devices on each line.

## Which device to keep

Asked at the prompt containing `device (1-`.

Two devices of the same kind were found and macOS can only use one of them. The other is hidden from macOS, not from Windows.

Every device listed already works with the macOS you chose, so the choice is about which one you use.

| Answer | Means | Pick it when |
|---|---|---|
| number | the device to keep | The one you use: on a desktop with a supported AMD card, that card; on a laptop, the Intel graphics. Prefer the wider range only between devices you would use equally. |

Default: None. Choose by what you use, not by position.

## Loading a kext on a macOS it does not declare support for

Asked at the prompt containing `Do you want to force load`.

Every kext says which macOS versions it was tested on. This one does not list the version you chose.

Forcing it can work, because many kexts simply were not updated, but a kext that truly does not fit can stop the boot with a kernel panic.

| Answer | Means | Pick it when |
|---|---|---|
| no | leave it out | First install. If the feature it provides is missing afterwards, come back and try with it. |
| yes | load it anyway | When you know from the kext's own page that it works on this version. |

Default: No.

## Normal or DEBUG build

Asked at the prompt containing `Build a DEBUG EFI`.

A DEBUG build uses the debug version of OpenCore, which writes everything it does to a text file on the EFI partition and prints it on screen.

When a boot fails before macOS starts, that file is the only way to see why. The app can read it for you (menu 11). The cost is a slower boot and a lot of text.

| Answer | Means | Pick it when |
|---|---|---|
| no | normal build | First attempt, and for daily use. |
| yes | DEBUG build | After a failed boot, to collect the log. Rebuild normal once it boots. |

Default: No.

## UEFI or legacy BIOS boot

Asked at the prompt containing `Build EFI for UEFI`.

Your firmware reported that it is running in legacy BIOS mode, the way PCs booted before about 2012. OpenCore can boot either way, but nearly all guides, fixes and this app assume UEFI.

UEFI mode is a firmware setting on most boards made after 2012. Legacy mode works but every later step is harder to get help with.

| Answer | Means | Pick it when |
|---|---|---|
| yes | build for UEFI | When your BIOS has a UEFI or CSM setting you can change. The checklist tells you where. |
| no | build for legacy BIOS | Only on a board that truly cannot do UEFI. |

Default: Yes.

## Copying your saved Wi-Fi networks

Asked at the prompt containing `Would you like to scan for WiFi profiles`.

With the itlwm driver, Wi-Fi is not in the macOS menu bar; the HeliPort app manages it. This step reads the networks and passwords Windows has saved and writes them into the itlwm driver's settings inside the EFI folder, so the driver connects by itself at boot.

It gives you internet already in the installer, where there is no HeliPort yet, and saves typing passwords after install. Nothing leaves this PC, but the passwords sit in plain text in the EFI folder on the stick.

| Answer | Means | Pick it when |
|---|---|---|
| yes | copy them | When you use itlwm and need internet during the install, for example to download macOS in the recovery installer. |
| no | skip | When Ethernet is available, or when you do not want passwords on the stick. |

Default: Your call. Skipping costs one password entry later.

## Which recovery image to download

No prompt of its own; the window shows it on the matching page.

A recovery image is Apple's small installer, about 1 GB, downloaded from Apple's own servers the same way a real Mac does it. On the USB it boots to a screen where you erase the disk and start the install; the full macOS is downloaded during installation.

Apple's server hands out recovery images per Mac board, either 'default' (a fixed release) or 'latest' (the newest that board can run). The app asks with a board that gets the macOS version you picked; it does not have to match the Mac model your build pretends to be.

| Answer | Means | Pick it when |
|---|---|---|
| default | the fixed release for that board | macOS 14 and older, where the fixed release is the version you asked for. |
| latest | the newest release for that board | macOS 15 and 26. On those boards 'default' returns an older point release (15.4 for macOS 15), so the app asks for the newest. |

Default: You do not answer this: the app uses default for macOS 14 and older and latest for macOS 15 and 26.

## Post-install kit

No prompt of its own; the window shows it on the matching page.

A folder on the USB with a checklist for what to do after macOS boots, your build's details, and the tools you will need inside macOS: ProperTree to edit the config, Hackintool to check ports and audio, MountEFI to move the EFI to the internal disk, and the root patch tool if the build needs it.

Inside a fresh macOS you may have no Wi-Fi yet and no idea which tool to fetch. Having them on the stick avoids that.

| Answer | Means | Pick it when |
|---|---|---|
| 1 | full kit | When you are online now. The tools are downloaded and checked against known hashes. |
| 2 | docs only | When offline, or when you already have the tools. |

Default: Full.

## USB port mapping

No prompt of its own; the window shows it on the matching page.

macOS allows at most 15 USB ports per controller and needs to be told which physical port is which. Windows already knows, so the app reads the port list here and writes it into a small kext for macOS. You plug a device into each port so the app can tell which ones really exist.

Without a map, macOS guesses. Usually it boots, but some ports stay dead, sleep may not work, and on some boards USB stops after a while.

| Answer | Means | Pick it when |
|---|---|---|
| auto | automatic map from firmware data | First try. Windows already knows which ports exist and what connector they have; the app selects them without plugging anything in. On some boards every port is reported as external even when it is not, so if a port is dead or sleep fails afterwards, do the manual map. |
| map | map the ports on this PC | Always, when you are building on the PC that will run macOS. |
| skip | use the generic default | When you build on another PC for this one, or just want a first boot quickly. Rebuild with a map later. |

Default: Automatic first. Map by plugging devices in if a port is dead or sleep fails.

## Write a new installer or only update the EFI

Asked at the prompt containing `how to write the USB drive`.

A USB installer holds three things: the macOS recovery image, the EFI folder with OpenCore, and the post-install kit.

The first time, the whole stick has to be prepared. After that only the EFI folder changes when you build again, so the stick does not need to be erased.

| Answer | Means | Pick it when |
|---|---|---|
| 1 | format the drive and write the installer | The first time, or when the stick is new or holds something else. It erases the whole stick, copies all three things onto it and checks every file. Needs administrator rights. |
| 2 | copy the EFI to an existing USB drive | After you build again, for example after saving a USB map, changing an option or making a DEBUG build. It erases nothing: it replaces the EFI folder and the post-install kit on a stick made with option 1 and keeps the recovery image. |

Default: 1 the first time, 2 after every rebuild.

## Which drive becomes the installer

Asked at the prompt containing `Enter the disk number to use`.

The list shows only drives attached over USB. Internal disks, the Windows disk and the boot disk are never listed, so you cannot pick them by mistake.

The chosen drive is erased completely: every file on it is gone. Check the size and the name before typing the number.

| Answer | Means | Pick it when |
|---|---|---|
| number | the stick you plugged in for this | Only a stick with nothing on it you want to keep. |

Default: There is no default on purpose.

## Last check before erasing

Asked at the prompt containing `Type ERASE`.

Typing the word is the final confirmation. Nothing has been written yet.

Erasing cannot be undone, so the app wants a word, not a key press.

| Answer | Means | Pick it when |
|---|---|---|
| ERASE | erase and write the installer | When the disk number and size above are the stick you mean. |
| anything else | stop | Whenever unsure. Nothing happens. |

Default: Stop.

## Glossary

**OpenCore**: The bootloader. It starts before macOS, makes the PC look enough like a Mac for macOS to boot, and loads the kexts. It lives in the EFI folder on the USB stick and later on your disk.

**EFI**: A small hidden partition every modern PC boots from, and also the name of the folder OpenCore lives in. The app's output is that folder.

**kext**: Short for kernel extension, a driver for macOS. The app downloads the ones your hardware needs and OpenCore loads them at boot.

**Lilu**: A kext that other kexts plug into. WhateverGreen, AppleALC and most others need it.

**WhateverGreen**: The kext that makes graphics cards behave on a PC: correct outputs, correct names, working sleep.

**AppleALC**: The kext that makes the sound chip on a PC motherboard work with Apple's audio driver.

**SimpleHDA**: A replacement for Apple's audio driver, used on macOS 26 where Apple removed it, so sound works without patching macOS.

**VirtualSMC**: Pretends to be the Apple chip that macOS asks about temperatures and fans. Without it macOS refuses to boot.

**SMBIOS**: The identity the PC presents to macOS: a Mac model name such as iMac20,1, a serial number and a board ID. macOS checks it; the app picks the closest Mac to your hardware and generates fresh serials.

**ACPI**: Tables the firmware hands to the operating system describing the hardware. Windows tolerates PC quirks in them; macOS does not, so the app adds small patch files called SSDTs that fix the parts macOS needs.

**SSDT**: One ACPI patch file. For example SSDT-PLUG tells macOS it may manage CPU power, and SSDT-EC gives it the embedded controller it expects.

**config.plist**: OpenCore's settings file. Everything the app decided ends up in it. ocvalidate checks it against the OpenCore version it ships with.

**ocvalidate**: A checker that comes with OpenCore. It compares config.plist with what that OpenCore version expects: missing, unknown or misspelled keys and values that do not fit together. Passing does not prove the PC boots; failing means OpenCore may stop or ignore a setting, so the app shows the errors.

**root patch**: Copying driver files that Apple removed back into the installed macOS. Done inside macOS by OpenCore Legacy Patcher or the app's own patch tool. It requires lower security and has to be repeated after every macOS update.

**OCLP**: OpenCore Legacy Patcher, the tool that applies root patches. Made for old Macs, used by hackintoshes for the same files.

**SIP**: System Integrity Protection, the macOS feature that stops anything from changing system files. The app leaves it on unless the build needs root patches, which cannot work with it on.

**layout-id**: The number that tells AppleALC how the jacks on your board are wired.

**iGPU**: The graphics built into the processor. dGPU is a separate graphics card. Laptops often have both; macOS usually gets the iGPU.

**Metal**: Apple's graphics interface. A graphics card without Metal support has no acceleration in modern macOS, which makes it unusable for daily work.

**recovery image**: Apple's small installer, downloaded from Apple. It boots from the USB and downloads the full macOS during installation.

**DEBUG build**: A version of OpenCore that logs everything to a text file. Used only to find out why a boot fails.

**NVRAM**: A small memory in the firmware where macOS and OpenCore keep settings such as boot arguments. Some boards need a workaround for it to survive reboots.

**boot-args**: Text switches passed to the macOS kernel at boot, for example -v for verbose text instead of the Apple logo.

**Secure Boot**: A firmware feature that only starts signed bootloaders. OpenCore is not signed by Microsoft, so it must be off.

**CSM**: Compatibility Support Module, the firmware's legacy BIOS mode. Off for OpenCore.

**VT-d**: Intel's device isolation feature. Usually off for a hackintosh, but the Broadcom companion kext needs it on.

**CFG Lock**: A firmware setting that locks a CPU register macOS wants to write. Unlocking it in the BIOS is best; the app turns on a workaround by default so the build also boots while it is locked.

**USB mapping**: Telling macOS which USB ports exist and what kind they are, because it stops counting at 15 and guesses badly.

**post-install**: Everything after macOS first boots: moving the EFI to the internal disk, turning WhateverGreen back on, root patches, checking ports and sound.

**KDK**: Kernel Debug Kit, a download from Apple that root patching needs on recent macOS versions to rebuild the kernel's driver collection.
