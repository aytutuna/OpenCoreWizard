# Plain-language help shown next to every choice the app asks for.
# Written for someone who has never built a hackintosh: short sentences, no
# assumed knowledge. Keys are matched against the prompt text by substring.

decisions = {
    "macos_version": {
        "prompt": "Please enter the macOS version",
        "title": "Which macOS to install",
        "what": "macOS comes in yearly versions. Each one drops support for some older hardware and changes what the bootloader has to do.",
        "why": "The list only shows versions your hardware can run. The suggested one is the newest version where every part of this PC still has a working driver without root patches.",
        "options": [
            ("Enter", "the suggested version", "Almost always. It is the newest one that works without extra patching."),
            ("older number", "an older macOS", "When a program you need does not run on the newest version, or when the newest is marked as needing OpenCore Legacy Patcher."),
            ("newer number", "a newer version from the list", "Only when you know why the suggestion held back. A newer unmarked version is there when the suggestion stepped back from a beta or was capped for one device; a version marked Requires OpenCore Legacy Patcher needs root patches: lower security, no normal updates, patches redone after each update."),
        ],
        "default": "Press Enter. The suggestion already steps back from betas and from versions that would need patching.",
    },
    "oclp_warning": {
        "prompt": "Continue with OpenCore Legacy Patcher",
        "title": "Root patches with OpenCore Legacy Patcher",
        "what": "A root patch copies driver files that Apple removed back into macOS after it is installed. OpenCore Legacy Patcher (OCLP) is the tool that does it. Everything else the bootloader does happens outside macOS; a root patch changes macOS itself.",
        "why": "Some of your hardware has no driver in the macOS you chose. Without the patch that device will not work: usually the graphics card, Wi-Fi or, on macOS 26, sound.",
        "options": [
            ("yes", "continue with root patches", "When the device that needs them matters to you and you accept the cost: System Integrity Protection off, macOS updates arrive as full installers, patches redone after every update."),
            ("no", "do not use root patches", "When you can live without that device, or when you would rather pick an older macOS where it still works natively. The build stops so you can change the version."),
        ],
        "default": "No, unless you chose root patches on purpose: for a graphics card or Wi-Fi card you need, or the AppleHDA audio option. No stops the build so you can pick an older macOS or the other audio or Wi-Fi option.",
    },
    "audio": {
        "prompt": "Select audio kext for your system",
        "title": "Sound on macOS 26",
        "what": "Apple removed AppleHDA, the driver for the sound chip on the motherboard, from macOS 26. AppleALC, the add-on that makes PC sound chips work, needs that driver underneath it. So something has to put a driver back.",
        "why": "There are two ways to do that, and they differ in how much they change your system.",
        "options": [
            ("1", "AppleALC + OCLP root patch", "When this build already needs root patches for the graphics card or Wi-Fi. The patch puts Apple's own AppleHDA back, so every jack and the microphone work as before, and it costs nothing extra once patching is needed anyway."),
            ("2", "AppleALC + SimpleHDA", "When nothing else needs root patches. SimpleHDA is a replacement driver that the bootloader loads, so macOS system files stay untouched and SIP stays on, so updates install normally. It is a younger project; on a few boards a jack or the internal microphone is missing."),
        ],
        "default": "The prompt suggests 1 when root patches are already needed and 2 otherwise. Take it.",
    },
    "codec_layout": {
        "prompt": "Enter the ID of the codec layout",
        "title": "Codec layout ID",
        "what": "Your sound chip has several jacks and an internal speaker wired in a way only the board maker knows. A layout is a map of that wiring. AppleALC ships many layouts per chip, each contributed by someone with a specific board, and the number picks one.",
        "why": "A wrong layout means sound works but a jack is dead, or headphones do not switch. The app pre-selects the layout whose comment names your board when one exists. Otherwise the green default is a layout made for a similar board with the same chipset, then, on desktops, one made for another board of the same maker, then the first layout by one of a few well known authors, or the first in the list when none of them made one for this chip.",
        "options": [
            ("Enter", "the default layout", "First install. It is right most of the time."),
            ("other number", "another layout from the list", "After install, if a jack or the microphone does not work. Try the ones whose comment names your board or laptop model."),
        ],
        "default": "Press Enter. To change it later, edit layout-id under DeviceProperties -> PciRoot(...)/Pci(...) of the audio controller in config.plist (or the alcid= boot argument) and reboot; no rebuild.",
    },
    "amd_navi": {
        "prompt": "Select kext for your AMD",
        "title": "Driver helper for your AMD graphics card",
        "what": "macOS has drivers for these AMD cards, but a PC presents the card slightly differently than a Mac does. A helper kext fixes that up at boot. Two helpers exist.",
        "why": "They use different firmware and behave differently on some cards.",
        "options": [
            ("1", "NootRX", "When WhateverGreen gives you a black screen or missing display outputs. It loads newer card firmware. It cannot be used together with an Intel graphics chip."),
            ("2", "WhateverGreen", "First install. It uses the firmware macOS ships and is what most guides assume."),
        ],
        "default": "WhateverGreen. Swap to NootRX after install if the display misbehaves.",
    },
    "whatevergreen": {
        "prompt": "Disable WhateverGreen for the first boot",
        "title": "Turn the graphics helper off for the first boot",
        "what": "WhateverGreen is the helper kext that makes graphics cards behave on a PC. On macOS 26 its handling of display connectors can crash the installer on some AMD cards.",
        "why": "Installing with it off avoids that crash. Once macOS is on the disk you turn it back on, which the post-install checklist explains.",
        "options": [
            ("yes", "disable it for the first boot", "When the build says it applies to you: AMD graphics card, macOS 26, monitor on that card."),
            ("no", "keep it enabled", "When you already know this card installs fine with it on."),
        ],
        "default": "Yes.",
    },
    "broadcom": {
        "prompt": "Select Broadcom Wi-Fi option",
        "title": "Broadcom Wi-Fi on macOS 14 and newer",
        "what": "Intel Macs used Broadcom Wi-Fi cards, which is why the same cards worked in a hackintosh without any extra driver. macOS 14 removed the driver for these older Broadcom cards.",
        "why": "Two ways exist to get the card working again, and they differ in how much they change your system.",
        "options": [
            ("1", "OCLP root patches", "The proven way. The patch puts Apple's old Wi-Fi driver back into macOS. Costs: SIP lowered, updates as full installers, patch redone after each update."),
            ("2", "AppleBCMWLANCompanion", "A kext loaded by the bootloader instead of a patch, so macOS system files stay untouched. Needs an Intel CPU with VT-d turned on in the BIOS, and its author still calls it unstable. Try it if you want SIP on and normal updates; fall back to 1 if Wi-Fi does not show up."),
        ],
        "default": "1.",
    },
    "intel_wifi": {
        "prompt": "Select Intel Wi-Fi kext",
        "title": "Intel Wi-Fi driver",
        "what": "Apple never used Intel Wi-Fi cards, so macOS has no driver for them. Volunteers wrote one, and it comes in three flavours.",
        "why": "They differ in whether Wi-Fi appears in the normal menu bar and whether macOS has to be patched. Type the number shown in the list next to the one you want.",
        "options": [
            ("AirportItlwm-laobamac", "native menu, no patches", "First choice when it is listed. Wi-Fi works from the menu bar, with Handoff and Continuity, and macOS system files stay untouched."),
            ("AirportItlwm", "native menu, needs root patches on macOS 15 and 26", "On macOS 13 and older. Works without patches on 14 but is less reliable; needs root patches on 15 and 26."),
            ("itlwm", "separate app, most stable", "The fallback. Wi-Fi is managed by the HeliPort app instead of the menu bar. No Handoff or other Apple continuity features, but it rarely breaks."),
        ],
        "default": "The prompt suggests the laobamac build when it is listed, AirportItlwm on macOS 13 and older, and itlwm otherwise. Take it.",
    },
    "device_combination": {
        "prompt": "combination (1-",
        "title": "Which devices macOS should see",
        "what": "This PC has more than one device of the same kind, usually two graphics chips. macOS can only use some combinations, so the others are hidden from it.",
        "why": "Each line shows which macOS versions that combination supports. Hidden devices are only hidden for macOS; Windows keeps using them.",
        "options": [
            ("number", "a line from the list", "Every line already works with the macOS you chose. Pick the one that keeps the devices you use: on a desktop with a supported AMD card, the line that includes it; on a laptop, the Intel graphics. Prefer the wider range only between lines that keep the same devices."),
        ],
        "default": "None. The list is not sorted best first, so read the devices on each line.",
    },
    "device_choice": {
        "prompt": " device (1-",
        "title": "Which device to keep",
        "what": "Two devices of the same kind were found and macOS can only use one of them. The other is hidden from macOS, not from Windows.",
        "why": "Every device listed already works with the macOS you chose, so the choice is about which one you use.",
        "options": [
            ("number", "the device to keep", "The one you use: on a desktop with a supported AMD card, that card; on a laptop, the Intel graphics. Prefer the wider range only between devices you would use equally."),
        ],
        "default": "None. Choose by what you use, not by position.",
    },
    "force_load": {
        "prompt": "Do you want to force load",
        "title": "Loading a kext on a macOS it does not declare support for",
        "what": "Every kext says which macOS versions it was tested on. This one does not list the version you chose.",
        "why": "Forcing it can work, because many kexts simply were not updated, but a kext that truly does not fit can stop the boot with a kernel panic.",
        "options": [
            ("no", "leave it out", "First install. If the feature it provides is missing afterwards, come back and try with it."),
            ("yes", "load it anyway", "When you know from the kext's own page that it works on this version."),
        ],
        "default": "No.",
    },
    "debug": {
        "prompt": "Build a DEBUG EFI",
        "title": "Normal or DEBUG build",
        "what": "A DEBUG build uses the debug version of OpenCore, which writes everything it does to a text file on the EFI partition and prints it on screen.",
        "why": "When a boot fails before macOS starts, that file is the only way to see why. The app can read it for you (menu 11). The cost is a slower boot and a lot of text.",
        "options": [
            ("no", "normal build", "First attempt, and for daily use."),
            ("yes", "DEBUG build", "After a failed boot, to collect the log. Rebuild normal once it boots."),
        ],
        "default": "No.",
    },
    "uefi": {
        "prompt": "Build EFI for UEFI",
        "title": "UEFI or legacy BIOS boot",
        "what": "Your firmware reported that it is running in legacy BIOS mode, the way PCs booted before about 2012. OpenCore can boot either way, but nearly all guides, fixes and this app assume UEFI.",
        "why": "UEFI mode is a firmware setting on most boards made after 2012. Legacy mode works but every later step is harder to get help with.",
        "options": [
            ("yes", "build for UEFI", "When your BIOS has a UEFI or CSM setting you can change. The checklist tells you where."),
            ("no", "build for legacy BIOS", "Only on a board that truly cannot do UEFI."),
        ],
        "default": "Yes.",
    },
    "wifi_profiles": {
        "prompt": "Would you like to scan for WiFi profiles",
        "title": "Copying your saved Wi-Fi networks",
        "what": "With the itlwm driver, Wi-Fi is not in the macOS menu bar; the HeliPort app manages it. This step reads the networks and passwords Windows has saved and writes them into the itlwm driver's settings inside the EFI folder, so the driver connects by itself at boot.",
        "why": "It gives you internet already in the installer, where there is no HeliPort yet, and saves typing passwords after install. Nothing leaves this PC, but the passwords sit in plain text in the EFI folder on the stick.",
        "options": [
            ("yes", "copy them", "When you use itlwm and need internet during the install, for example to download macOS in the recovery installer."),
            ("no", "skip", "When Ethernet is available, or when you do not want passwords on the stick."),
        ],
        "default": "Your call. Skipping costs one password entry later.",
    },
    "recovery": {
        "prompt": None,
        "title": "Which recovery image to download",
        "what": "A recovery image is Apple's small installer, about 1 GB, downloaded from Apple's own servers the same way a real Mac does it. On the USB it boots to a screen where you erase the disk and start the install; the full macOS is downloaded during installation.",
        "why": "Apple's server hands out recovery images per Mac board, either 'default' (a fixed release) or 'latest' (the newest that board can run). The app asks with a board that gets the macOS version you picked; it does not have to match the Mac model your build pretends to be.",
        "options": [
            ("default", "the fixed release for that board", "macOS 14 and older, where the fixed release is the version you asked for."),
            ("latest", "the newest release for that board", "macOS 15 and 26. On those boards 'default' returns an older point release (15.4 for macOS 15), so the app asks for the newest."),
        ],
        "default": "You do not answer this: the app uses default for macOS 14 and older and latest for macOS 15 and 26.",
    },
    "postinstall_kit": {
        "prompt": None,
        "title": "Post-install kit",
        "what": "A folder on the USB with a checklist for what to do after macOS boots, your build's details, and the tools you will need inside macOS: ProperTree to edit the config, Hackintool to check ports and audio, MountEFI to move the EFI to the internal disk, and the root patch tool if the build needs it.",
        "why": "Inside a fresh macOS you may have no Wi-Fi yet and no idea which tool to fetch. Having them on the stick avoids that.",
        "options": [
            ("1", "full kit", "When you are online now. The tools are downloaded and checked against known hashes."),
            ("2", "docs only", "When offline, or when you already have the tools."),
        ],
        "default": "Full.",
    },
    "usb_map": {
        "prompt": None,
        "title": "USB port mapping",
        "what": "macOS allows at most 15 USB ports per controller and needs to be told which physical port is which. Windows already knows, so the app reads the port list here and writes it into a small kext for macOS. You plug a device into each port so the app can tell which ones really exist.",
        "why": "Without a map, macOS guesses. Usually it boots, but some ports stay dead, sleep may not work, and on some boards USB stops after a while.",
        "options": [
            ("auto", "automatic map from firmware data", "First try. Windows already knows which ports exist and what connector they have; the app selects them without plugging anything in. On some boards every port is reported as external even when it is not, so if a port is dead or sleep fails afterwards, do the manual map."),
            ("map", "map the ports on this PC", "Always, when you are building on the PC that will run macOS."),
            ("skip", "use the generic default", "When you build on another PC for this one, or just want a first boot quickly. Rebuild with a map later."),
        ],
        "default": "Automatic first. Map by plugging devices in if a port is dead or sleep fails.",
    },
    "usb_disk": {
        "prompt": "Enter the disk number to use",
        "title": "Which drive becomes the installer",
        "what": "The list shows only drives attached over USB. Internal disks, the Windows disk and the boot disk are never listed, so you cannot pick them by mistake.",
        "why": "The chosen drive is erased completely: every file on it is gone. Check the size and the name before typing the number.",
        "options": [
            ("number", "the stick you plugged in for this", "Only a stick with nothing on it you want to keep."),
        ],
        "default": "There is no default on purpose.",
    },
    "usb_erase": {
        "prompt": "Type ERASE",
        "title": "Last check before erasing",
        "what": "Typing the word is the final confirmation. Nothing has been written yet.",
        "why": "Erasing cannot be undone, so the app wants a word, not a key press.",
        "options": [
            ("ERASE", "erase and write the installer", "When the disk number and size above are the stick you mean."),
            ("anything else", "stop", "Whenever unsure. Nothing happens."),
        ],
        "default": "Stop.",
    },
}

# Terms that appear in prompts, pages and the checklist.
glossary = {
    "OpenCore": "The bootloader. It starts before macOS, makes the PC look enough like a Mac for macOS to boot, and loads the kexts. It lives in the EFI folder on the USB stick and later on your disk.",
    "EFI": "A small hidden partition every modern PC boots from, and also the name of the folder OpenCore lives in. The app's output is that folder.",
    "kext": "Short for kernel extension, a driver for macOS. The app downloads the ones your hardware needs and OpenCore loads them at boot.",
    "Lilu": "A kext that other kexts plug into. WhateverGreen, AppleALC and most others need it.",
    "WhateverGreen": "The kext that makes graphics cards behave on a PC: correct outputs, correct names, working sleep.",
    "AppleALC": "The kext that makes the sound chip on a PC motherboard work with Apple's audio driver.",
    "SimpleHDA": "A replacement for Apple's audio driver, used on macOS 26 where Apple removed it, so sound works without patching macOS.",
    "VirtualSMC": "Pretends to be the Apple chip that macOS asks about temperatures and fans. Without it macOS refuses to boot.",
    "SMBIOS": "The identity the PC presents to macOS: a Mac model name such as iMac20,1, a serial number and a board ID. macOS checks it; the app picks the closest Mac to your hardware and generates fresh serials.",
    "ACPI": "Tables the firmware hands to the operating system describing the hardware. Windows tolerates PC quirks in them; macOS does not, so the app adds small patch files called SSDTs that fix the parts macOS needs.",
    "SSDT": "One ACPI patch file. For example SSDT-PLUG tells macOS it may manage CPU power, and SSDT-EC gives it the embedded controller it expects.",
    "config.plist": "OpenCore's settings file. Everything the app decided ends up in it. ocvalidate checks it against the OpenCore version it ships with.",
    "ocvalidate": "A checker that comes with OpenCore. It compares config.plist with what that OpenCore version expects: missing, unknown or misspelled keys and values that do not fit together. Passing does not prove the PC boots; failing means OpenCore may stop or ignore a setting, so the app shows the errors.",
    "root patch": "Copying driver files that Apple removed back into the installed macOS. Done inside macOS by OpenCore Legacy Patcher or the app's own patch tool. It requires lower security and has to be repeated after every macOS update.",
    "OCLP": "OpenCore Legacy Patcher, the tool that applies root patches. Made for old Macs, used by hackintoshes for the same files.",
    "SIP": "System Integrity Protection, the macOS feature that stops anything from changing system files. The app leaves it on unless the build needs root patches, which cannot work with it on.",
    "layout-id": "The number that tells AppleALC how the jacks on your board are wired.",
    "iGPU": "The graphics built into the processor. dGPU is a separate graphics card. Laptops often have both; macOS usually gets the iGPU.",
    "Metal": "Apple's graphics interface. A graphics card without Metal support has no acceleration in modern macOS, which makes it unusable for daily work.",
    "recovery image": "Apple's small installer, downloaded from Apple. It boots from the USB and downloads the full macOS during installation.",
    "DEBUG build": "A version of OpenCore that logs everything to a text file. Used only to find out why a boot fails.",
    "NVRAM": "A small memory in the firmware where macOS and OpenCore keep settings such as boot arguments. Some boards need a workaround for it to survive reboots.",
    "boot-args": "Text switches passed to the macOS kernel at boot, for example -v for verbose text instead of the Apple logo.",
    "Secure Boot": "A firmware feature that only starts signed bootloaders. OpenCore is not signed by Microsoft, so it must be off.",
    "CSM": "Compatibility Support Module, the firmware's legacy BIOS mode. Off for OpenCore.",
    "VT-d": "Intel's device isolation feature. Usually off for a hackintosh, but the Broadcom companion kext needs it on.",
    "CFG Lock": "A firmware setting that locks a CPU register macOS wants to write. Unlocking it in the BIOS is best; the app turns on a workaround by default so the build also boots while it is locked.",
    "USB mapping": "Telling macOS which USB ports exist and what kind they are, because it stops counting at 15 and guesses badly.",
    "post-install": "Everything after macOS first boots: moving the EFI to the internal disk, turning WhateverGreen back on, root patches, checking ports and sound.",
    "KDK": "Kernel Debug Kit, a download from Apple that root patching needs on recent macOS versions to rebuild the kernel's driver collection.",
}

# What each stage of the build does, shown in the log while it runs.
stages = {
    "scan": "Reads what hardware this PC has: processor, graphics, sound chip, network, USB controllers, disks. Nothing is changed.",
    "verdict": "Looks up every device in the compatibility tables and says which macOS versions can run on this PC, and which devices would need root patches.",
    "macos": "Picks the newest macOS where every important device still has an Apple driver.",
    "acpi": "Generates the small ACPI patch files macOS needs for power management, the embedded controller, USB power and similar.",
    "kexts": "Downloads OpenCore and the kexts chosen for this hardware from their official releases and copies them into the EFI folder.",
    "smbios": "Picks the Mac model closest to this hardware and generates a fresh serial number and identifiers for it.",
    "config": "Writes config.plist, OpenCore's settings file, with every decision above.",
    "validate": "Runs ocvalidate, the checker that ships with OpenCore, against the finished config.",
    "recovery": "Downloads Apple's recovery image for the chosen macOS version from Apple's servers.",
    "kit": "Writes the post-install checklist and collects the tools you will need inside macOS onto the USB.",
    "usb_map": "Reads the USB port list from Windows and writes it into a kext so macOS knows the ports.",
    "usb_write": "Erases the chosen USB stick, formats it, copies the recovery image, the EFI folder and the kit, and checks the copy.",
}

install_steps = [
    "Write the stick (USB page in the window, menu option 10 in the terminal). It erases the stick and copies the recovery image, the EFI folder and the post-install kit onto it.",
    "Set the BIOS as the build output lists it (Secure Boot off, CSM off, and the rest; the Post-install page in the window explains each one), and fix what the Windows preflight warned about, such as BitLocker.",
    "Plug the stick in, start the PC and open the BIOS boot menu (usually F12, F11, F8 or Esc). Pick the stick's UEFI entry. OpenCore's menu appears; choose the recovery entry, called macOS Base System.",
    "If the screen stays black or the PC restarts, nothing on the PC has changed: unplug the stick and Windows starts as before. Build again as DEBUG and run the boot log analyzer (Finish page, or menu option 11) to see where it stopped.",
    "In Disk Utility choose View, Show All Devices, select the disk or partition for macOS and erase it as APFS with a GUID partition map. A separate SSD is the safe choice. To share the Windows disk, shrink Windows in Disk Management first, then add a partition in the free space here and format it APFS. BitLocker must be off.",
    "Choose Reinstall macOS. The stick only holds a small recovery image, so the installer downloads the full macOS from Apple. Use an Ethernet cable; Wi-Fi may not work yet.",
    "The install restarts three or four times. Each time pick the stick in the BIOS boot menu again and choose macOS Installer in OpenCore's menu, later the name of the new macOS volume.",
    "Finish the macOS setup screens. Then follow the After installing macOS notes and POST-INSTALL.md in the kit (move the EFI folder to the internal disk, turn on LauncherOption). Run root patches only if the build output said this build needs them.",
]
