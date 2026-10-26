from Scripts.datasets import os_data
from Scripts.datasets import chipset_data
from Scripts.gui import session
from Scripts.sniffer import hardware_sniffer
from Scripts import acpi_guru
from Scripts import app_paths
from Scripts import bios_checklist
from Scripts import compatibility_checker
from Scripts import config_prodigy
from Scripts import explain
from Scripts import gathering_files
from Scripts import hardware_customizer
from Scripts import kext_maestro
from Scripts import log_analyzer
from Scripts import postinstall_kit
from Scripts import preflight
from Scripts import recovery_fetcher
from Scripts import report_validator
from Scripts import smbios
from Scripts import usb_mapper
from Scripts import usb_writer
from Scripts import utils
from Scripts import validator
import os
import plistlib
import sys
import re
import json
import shutil
import traceback
import time

class OCPE:
    def __init__(self):
        self.u = utils.Utils("MacBootsmith")
        self.u.clean_temporary_dir()
        self.ac = acpi_guru.ACPIGuru()
        self.c = compatibility_checker.CompatibilityChecker()
        self.co = config_prodigy.ConfigProdigy()
        self.o = gathering_files.gatheringFiles()
        self.h = hardware_customizer.HardwareCustomizer()
        self.k = kext_maestro.KextMaestro()
        self.s = smbios.SMBIOS()
        self.v = report_validator.ReportValidator()
        self.rf = recovery_fetcher.RecoveryFetcher()
        self.val = validator.ConfigValidator()
        self.usb = usb_writer.USBWriter()
        self.pf = preflight.Preflight()
        self.usb_mapper = usb_mapper.USBMapper()
        self.usb_map_override = None
        self.usb_map_auto = True
        self.usb_map_source = None
        self.bios_checklist = bios_checklist.BIOSChecklist()
        self.pk = postinstall_kit.PostInstallKit()
        self.build_summary = None
        self.debug_build = False
        self.acpi_dump_error = None
        self.result_dir = os.path.join(app_paths.app_dir(), "Results")

    def export_hardware_report(self):
        if os.name != "nt":
            return None, None, None

        sniffer = hardware_sniffer.HardwareSniffer(os.path.join(app_paths.app_dir(), "SysReport"))

        try:
            sniffer.collect()
        except Exception as e:
            return None, None, "Error collecting hardware: {}".format(e)

        try:
            report_path = sniffer.export_hardware_report()
        except Exception as e:
            return None, None, "Error writing the hardware report: {}".format(e)

        self.pf.add_bios_vendor(report_path)

        self.acpi_dump_error = None
        try:
            acpi_dir = sniffer.dump_acpi_tables()
        except Exception as e:
            acpi_dir = None
            self.acpi_dump_error = str(e)
            print("")
            print("\033[1;93mWarning:\033[0m The ACPI tables were not dumped. {}".format(e))
            print("The hardware report was kept in {}.".format(report_path))
            print("")

        return report_path, acpi_dir, None

    def select_hardware_report(self):
        self.ac.dsdt = self.ac.acpi.acpi_tables = None
        self.ac.skip_acpi_tables = False

        while True:
            self.u.head("Select hardware report")
            print("")
            if os.name == "nt":
                print("\033[1;93mNote:\033[0m")
                print("- The hardware scan will not collect information related to Resizable BAR option of GPU (disabled by default) and monitor connections in Windows PE.")
                print("")
                print("E. Export hardware report (Recommended)")
                print("")
            print("Q. Quit")
            print("")
        
            user_input = self.u.request_input("Drag and drop your hardware report here (.JSON){}: ".format(" or type \"E\" to export" if os.name == "nt" else ""))
            if user_input.lower() == "q":
                self.u.exit_program()
            if user_input.lower() == "e":
                report_path, acpitables_dir, error_message = self.export_hardware_report()

                if error_message:
                    print("")
                    print("Could not export the hardware report. {}".format(error_message))
                    print("Please try again.")
                    print("")
                    self.u.request_input()
                    continue

                if not report_path:
                    continue

                if not acpitables_dir:
                    print("You will be asked for an ACPI tables folder before the build.")
                    print("")
                    self.u.request_input()

                report_data = self.u.read_file(report_path)
                self.ac.read_acpi_tables(acpitables_dir)

                return report_path, report_data
                
            path = self.u.normalize_path(user_input)
            
            is_valid, errors, warnings, data = self.v.validate_report(path)
            
            self.v.show_validation_report(path, is_valid, errors, warnings)
            if not is_valid or errors:
                print("")
                print("\033[32mSuggestion:\033[0m Please re-export the hardware report and try again.")
                print("")
                self.u.request_input("Press Enter to go back...")
            else:
                return path, data
            
    def oclp_declined_message(self, hardware_report, macos_version):
        name = os_data.get_macos_name_by_darwin(macos_version)
        parts = ["{} needs OpenCore Legacy Patcher on this hardware and it was declined.".format(name)]
        devices = self.k.oclp_devices(hardware_report, macos_version)
        if devices:
            names = [device for device, _ in devices]
            listed = names[0] if len(names) == 1 else "{} and {}".format(", ".join(names[:-1]), names[-1])
            native = [limit for _, limit in devices if limit]
            if native:
                newest = os_data.get_macos_name_by_darwin(min(native, key=self.u.parse_darwin_version))
                parts.append("{} {} no driver past {}.".format(listed, "has" if len(names) == 1 else "have", newest))
                parts.append("Pick {} or older, or answer yes to the root patches.".format(newest))
            else:
                parts.append("{} only {} with root patches.".format(listed, "works" if len(names) == 1 else "work"))
        for option, instead in self.k.oclp_options:
            parts.append("{} needs root patches on {}; pick {} instead.".format(option, name, instead))
        return " ".join(parts)

    def show_oclp_warning(self, macos_version):
        is_tahoe = self.u.parse_darwin_version(macos_version) >= self.u.parse_darwin_version("25.0.0")

        while True:
            self.u.head("OpenCore Legacy Patcher Warning")
            print("")
            print("\033[1;96mWhy it is required:\033[0m")
            print("- Restores compatibility for GPUs and Broadcom WiFi.")
            if is_tahoe:
                print("- Rolls back AppleHDA to restore audio support through AppleALC.")
            print("")
            print("\033[1;93mRisks and limitations:\033[0m")
            print("- Disables macOS security features, including SIP and AMFI.")
            print("- macOS updates require full installers, apps may crash or the system may become unstable.")
            print("- OpenCore Legacy Patcher does not officially support Hackintosh systems.")
            print("")
            print("\033[1;91mReview these risks before continuing.\033[0m")
            print("")
            option = self.u.request_input("Continue with OpenCore Legacy Patcher? (yes/No): ").strip().lower()
            if option in ("y", "yes"):
                return True
            elif option in ("", "n", "no"):
                return False

    def select_macos_version(self, hardware_report, native_macos_version, ocl_patched_macos_version):
        suggested_macos_version = native_macos_version[1]
        version_pattern = re.compile(r'^(\d+)(?:\.(\d+)(?:\.(\d+))?)?$')

        for device_type in ("GPU", "Network", "Bluetooth", "SD Controller"):
            if device_type in hardware_report:
                for device_name, device_props in hardware_report[device_type].items():
                    if device_props.get("Compatibility", (None, None)) != (None, None):
                        if device_type == "GPU" and device_props.get("Device Type") == "Integrated GPU":
                            device_id = device_props.get("Device ID", ""*8)[5:]

                            if device_props.get("Manufacturer") == "AMD" or device_id.startswith(("59", "87C0")):
                                suggested_macos_version = "22.99.99"
                            elif device_id.startswith(("09", "19")):
                                suggested_macos_version = "21.99.99"

                        if self.u.parse_darwin_version(suggested_macos_version) > self.u.parse_darwin_version(device_props.get("Compatibility")[0]):
                            suggested_macos_version = device_props.get("Compatibility")[0]

        while True:
            if "Beta" in os_data.get_macos_name_by_darwin(suggested_macos_version):
                suggested_macos_version = "{}{}".format(int(suggested_macos_version[:2]) - 1, suggested_macos_version[2:])
            else:
                break

        while True:
            self.u.head("Select macOS Version")
            if native_macos_version[1][:2] != suggested_macos_version[:2]:
                print("")
                print("\033[1;36mSuggested macOS version:\033[0m")
                print("- For better compatibility and stability, we suggest you to use only {} or older.".format(os_data.get_macos_name_by_darwin(suggested_macos_version)))
            print("")
            print("Available macOS versions:")
            print("")

            oclp_min = int(ocl_patched_macos_version[-1][:2]) if ocl_patched_macos_version else 99
            oclp_max = int(ocl_patched_macos_version[0][:2]) if ocl_patched_macos_version else 0
            min_version = min(int(native_macos_version[0][:2]), oclp_min)
            max_version = max(int(native_macos_version[-1][:2]), oclp_max)

            for darwin_version in range(min_version, max_version + 1):
                name = os_data.get_macos_name_by_darwin(str(darwin_version))
                label = " (\033[1;93mRequires OpenCore Legacy Patcher\033[0m)" if oclp_min <= darwin_version <= oclp_max else ""
                print("   {}. {}{}".format(darwin_version, name, label))

            print("")
            print("\033[1;93mNote:\033[0m")
            print("- To select a major version, enter the number (e.g., 19).")
            print("- To specify a full version, use the Darwin version format (e.g., 22.4.6).")
            print("")
            print("Q. Quit")
            print("")
            option = self.u.request_input("Please enter the macOS version you want to use (default: {}): ".format(os_data.get_macos_name_by_darwin(suggested_macos_version))) or suggested_macos_version
            if option.lower() == "q":
                self.u.exit_program()

            match = version_pattern.match(option)
            if match:
                target_version = "{}.{}.{}".format(match.group(1), match.group(2) if match.group(2) else 99, match.group(3) if match.group(3) else 99)
                
                if ocl_patched_macos_version and self.u.parse_darwin_version(ocl_patched_macos_version[-1]) <= self.u.parse_darwin_version(target_version) <= self.u.parse_darwin_version(ocl_patched_macos_version[0]):
                    return target_version
                elif self.u.parse_darwin_version(native_macos_version[0]) <= self.u.parse_darwin_version(target_version) <= self.u.parse_darwin_version(native_macos_version[-1]):
                    return target_version

    def select_usb_map_source(self, hardware_report, announce=True):
        def say(text):
            if announce:
                print(text)

        override = self.usb_map_override
        if override is False:
            return None

        explicit = bool(override)
        path = override if explicit else self.usb_mapper.map_path
        if explicit and os.path.isdir(path):
            plist_path = os.path.join(path, "Contents", "Info.plist")
            if not os.path.isfile(plist_path):
                raise Exception("The USB map {} has no Contents/Info.plist.".format(path))
            if not os.path.basename(os.path.normpath(path)).lower().endswith(".kext"):
                raise Exception("The USB map folder {} must have a name ending in .kext.".format(path))
            try:
                with open(plist_path, "rb") as plist_file:
                    info = plistlib.load(plist_file)
            except Exception:
                raise Exception("The USB map {} has an unreadable Info.plist.".format(path))
            if usb_mapper.USBTOOLBOX_IDENTIFIER not in (info.get("OSBundleLibraries") or {}):
                raise Exception("The USB map {} does not depend on {}; only USBToolBox maps are supported.".format(path, usb_mapper.USBTOOLBOX_IDENTIFIER))
            say("Using the USB map kext {}.".format(path))
            return {"kind": "kext", "path": path}

        if explicit and not os.path.isfile(path):
            raise Exception("The USB map {} does not exist.".format(path))
        data = self.usb_mapper.load_saved(path)
        if data is None:
            if explicit:
                raise Exception("Could not load the USB map {}.".format(path))
            return None

        errors = usb_mapper.validate_selection(data)
        if errors:
            if explicit:
                raise Exception("The USB map {} is not usable: {}".format(path, " ".join(errors)))
            say("Ignoring the saved USB map {}: {}".format(path, " ".join(errors)))
            return None

        matched, unmatched = usb_mapper.report_matches(data, hardware_report)
        if not matched and not explicit:
            say("The saved USB map {} does not match the USB controllers in this hardware report, so UTBDefault is used instead.".format(path))
            return None
        for controller in unmatched:
            say("Note: {} from the USB map is not listed in the hardware report.".format(controller.get("name")))
        say("Using the USB map {}.".format(path))
        return {"kind": "json", "path": path, "data": data}

    def prepare_usb_map(self, hardware_report, macos_version, announce=True):
        self.usb_map_source = self.select_usb_map_source(hardware_report, announce)
        self.k.apply_usb_map(macos_version, self.usb_map_source is not None)
        return self.usb_map_source

    def install_usb_map(self, kexts_directory):
        source = self.usb_map_source
        if not source:
            return None
        self.u.create_folder(kexts_directory)
        if source["kind"] == "kext":
            destination = os.path.join(kexts_directory, os.path.basename(os.path.normpath(source["path"])))
            shutil.copytree(source["path"], destination, dirs_exist_ok=True)
            return destination
        return self.usb_mapper.build_kext(source["data"], kexts_directory)

    def usb_port_mapping(self, hardware_report):
        self.u.head("USB Port Mapping")
        print("")
        if os.name != "nt":
            print(self.usb_mapper.unsupported_message)
            print("")
            self.u.request_input("Press Enter to go back...")
            return None

        print("1. Automatic map from firmware data (recommended first try)")
        print("2. Map by plugging a device into each port")
        print("")
        print("Any other key to go back")
        print("")
        option = self.u.request_input("Select an option: ").strip()
        if option == "1":
            selection = self.usb_mapper.auto_map()
        elif option == "2":
            selection = self.usb_mapper.discover()
        else:
            return None
        if selection:
            matched, unmatched = usb_mapper.report_matches(selection, hardware_report)
            if not matched:
                print("\033[1;93mWarning:\033[0m the mapped controllers do not match the selected hardware report.")
                print("Map the ports on the machine the hardware report came from.")
                print("")
            self.u.request_input("Press Enter to main menu...")
        return selection

    def select_debug_build(self):
        answer = self.u.request_input("Build a DEBUG EFI with logging? Use this only to diagnose a boot that fails; it is slower and writes log files to the EFI partition. (yes/No): ")
        self.debug_build = answer.strip().lower() in ("y", "yes")
        return self.debug_build

    def debug_build_notes(self):
        return [
            "This EFI uses the DEBUG OpenCore build with logging on (Misc -> Debug: AppleDebug, ApplePanic, Target 67). Booting takes a few seconds longer.",
            "Every boot writes opencore-YYYY-MM-DD-hhmmss.txt to the root of the EFI partition it booted from (the USB drive when you boot from it), next to the EFI folder.",
            "To collect it, boot until it fails, then plug the USB drive into this PC (or mount the internal EFI partition) and keep the newest opencore-*.txt.",
            "Option 11 (Analyze Boot Log) reads the newest log from a drive letter, folder or file and names the stage where the boot stopped.",
            "Once macOS boots, build again and answer No to the DEBUG question to get the faster RELEASE EFI."
        ]

    def build_opencore_efi(self, hardware_report, disabled_devices, smbios_model, macos_version, needs_oclp, debug=False):
        steps = [
            "Copying EFI base to results folder",
            "Applying ACPI patches",
            "Copying kexts and snapshotting to config.plist",
            "Generating config.plist",
            "Cleaning up unused drivers, resources, and tools"
        ]
        
        title = "Building OpenCore EFI"

        self.build_summary = None
        self.pk.invalidate(os.path.join(self.result_dir, postinstall_kit.KIT_FOLDER))
        stale_summary = os.path.join(self.result_dir, postinstall_kit.SUMMARY_NAME)
        if os.path.isfile(stale_summary):
            try:
                os.remove(stale_summary)
            except OSError as e:
                print("Could not remove {}: {}".format(stale_summary, e))

        self.u.progress_bar(title, steps, 0)
        self.u.create_folder(self.result_dir)
        self.u.create_folder(os.path.join(self.result_dir, "EFI"), remove_content=True)

        if not os.path.exists(self.k.ock_files_dir):
            raise Exception("Directory '{}' does not exist.".format(self.k.ock_files_dir))
        
        source_efi_dir = os.path.join(self.k.ock_files_dir, gathering_files.OPENCORE_DEBUG_PRODUCT if debug else gathering_files.OPENCORE_PRODUCT)
        if debug and not os.path.isdir(os.path.join(source_efi_dir, "EFI")):
            raise Exception("The DEBUG OpenCore package is missing from {}. Gather the files again with the DEBUG build selected.".format(source_efi_dir))
        shutil.copytree(source_efi_dir, self.result_dir, dirs_exist_ok=True)

        config_file = os.path.join(self.result_dir, "EFI", "OC", "config.plist")
        config_data = self.u.read_file(config_file)
        
        if not config_data:
            raise Exception("Error: The file {} does not exist.".format(config_file))
        
        self.u.progress_bar(title, steps, 1)
        config_data["ACPI"]["Add"] = []
        config_data["ACPI"]["Delete"] = []
        config_data["ACPI"]["Patch"] = []
        if self.ac.ensure_dsdt():
            self.ac.hardware_report = hardware_report
            self.ac.disabled_devices = disabled_devices
            self.ac.acpi_directory = os.path.join(self.result_dir, "EFI", "OC", "ACPI")
            self.ac.smbios_model = smbios_model
            self.ac.lpc_bus_device = self.ac.get_lpc_name()

            for patch in self.ac.patches:
                if patch.checked:
                    if patch.name == "BATP":
                        patch.checked = getattr(self.ac, patch.function_name)()
                        self.k.kexts[kext_maestro.kext_data.kext_index_by_name.get("ECEnabler")].checked = patch.checked
                        continue

                    acpi_load = getattr(self.ac, patch.function_name)()

                    if not isinstance(acpi_load, dict):
                        continue

                    config_data["ACPI"]["Add"].extend(acpi_load.get("Add", []))
                    config_data["ACPI"]["Delete"].extend(acpi_load.get("Delete", []))
                    config_data["ACPI"]["Patch"].extend(acpi_load.get("Patch", []))
        
        config_data["ACPI"]["Patch"].extend(self.ac.dsdt_patches)
        config_data["ACPI"]["Patch"] = self.ac.apply_acpi_patches(config_data["ACPI"]["Patch"])

        self.u.progress_bar(title, steps, 2)
        kexts_directory = os.path.join(self.result_dir, "EFI", "OC", "Kexts")
        self.prepare_usb_map(hardware_report, macos_version, announce=False)
        try:
            usb_map_kext = self.install_usb_map(kexts_directory)
        except ValueError as error:
            raise Exception("Could not build the USB map: {}".format(error))
        self.k.install_kexts_to_efi(macos_version, kexts_directory)
        config_data["Kernel"]["Add"] = self.k.load_kexts(hardware_report, macos_version, kexts_directory)
        if usb_map_kext:
            usb_map_bundle = os.path.basename(usb_map_kext)
            if not any(entry.get("BundlePath") == usb_map_bundle for entry in config_data["Kernel"]["Add"]):
                raise Exception("{} was copied to {} but is missing from Kernel -> Add.".format(usb_map_bundle, kexts_directory))

        self.u.progress_bar(title, steps, 3)
        self.co.genarate(hardware_report, disabled_devices, smbios_model, macos_version, needs_oclp, self.k.kexts, config_data, self.k.whatevergreen_first_boot_off, os.path.join(self.result_dir, "EFI", "OC", "ACPI"))
        self.co.apply_debug_settings(config_data, debug)
        self.u.write_file(config_file, config_data)

        self.u.progress_bar(title, steps, 4)
        files_to_remove = []

        drivers_directory = os.path.join(self.result_dir, "EFI", "OC", "Drivers")
        driver_list = self.u.find_matching_paths(drivers_directory, extension_filter=".efi")
        driver_loaded = [kext.get("Path") for kext in config_data.get("UEFI").get("Drivers")]
        for driver_path, type in driver_list:
            if not driver_path in driver_loaded:
                files_to_remove.append(os.path.join(drivers_directory, driver_path))

        resources_audio_dir = os.path.join(self.result_dir, "EFI", "OC", "Resources", "Audio")
        if os.path.exists(resources_audio_dir):
            files_to_remove.append(resources_audio_dir)

        picker_variant = config_data.get("Misc", {}).get("Boot", {}).get("PickerVariant")
        if picker_variant in (None, "Auto"):
            picker_variant = "Acidanthera/GoldenGate" 
        if os.name == "nt":
            picker_variant = picker_variant.replace("/", "\\")

        resources_image_dir = os.path.join(self.result_dir, "EFI", "OC", "Resources", "Image")
        available_picker_variants = self.u.find_matching_paths(resources_image_dir, type_filter="dir")

        for variant_name, variant_type in available_picker_variants:
            variant_path = os.path.join(resources_image_dir, variant_name)
            if ".icns" in ", ".join(os.listdir(variant_path)):
                if picker_variant not in variant_name:
                    files_to_remove.append(variant_path)

        tools_directory = os.path.join(self.result_dir, "EFI", "OC", "Tools")
        tool_list = self.u.find_matching_paths(tools_directory, extension_filter=".efi")
        tool_loaded = [tool.get("Path") for tool in config_data.get("Misc").get("Tools")]
        for tool_path, type in tool_list:
            if not tool_path in tool_loaded:
                files_to_remove.append(os.path.join(tools_directory, tool_path))

        if "manifest.json" in os.listdir(self.result_dir):
            files_to_remove.append(os.path.join(self.result_dir, "manifest.json"))

        for file_path in files_to_remove:
            try:
                if os.path.isdir(file_path):
                    shutil.rmtree(file_path)
                else:
                    os.remove(file_path)
            except Exception as e:
                print("Failed to remove file: {}".format(e))
        
        self.u.progress_bar(title, steps, len(steps), done=True)

        print("Validating config.plist with ocvalidate...")
        try:
            is_valid, validation_output = self.val.validate(config_file)
        except Exception as e:
            is_valid, validation_output = None, "Unable to run ocvalidate: {}".format(e)
        if validation_output:
            print(validation_output)
        print("")
        if is_valid is True:
            print("\033[92mconfig.plist passed ocvalidate.\033[0m")
        elif is_valid is False:
            print("\033[1;93mWarning: ocvalidate reported errors in config.plist. The EFI may not boot.\033[0m")
            print("")
            self.u.request_input("Press Enter to continue...")
        else:
            print("config.plist was not validated (ocvalidate is unavailable). This does not mean the config has errors.")
            print("")
            self.u.request_input("Press Enter to continue...")

        self.record_build_summary(hardware_report, config_data, smbios_model, macos_version, needs_oclp, is_valid, debug=debug)

        print("OpenCore EFI build complete{}.".format(" (DEBUG)" if debug else ""))
        time.sleep(2)
        return is_valid

    def record_build_summary(self, hardware_report, config_data, smbios_model, macos_version, needs_oclp, is_valid, debug=False):
        usb_map_kext = None
        if self.usb_map_source:
            usb_map_kext = os.path.basename(os.path.normpath(self.usb_map_source["path"])) if self.usb_map_source["kind"] == "kext" else usb_mapper.KEXT_NAME
        kext_index = kext_maestro.kext_data.kext_index_by_name
        self.build_summary = postinstall_kit.make_build_summary(
            hardware_report, config_data, smbios_model, macos_version, needs_oclp,
            ocvalidate={True: "passed", False: "errors"}.get(is_valid, "unavailable"),
            whatevergreen_first_boot=getattr(self.co, "whatevergreen_first_boot", None),
            applealc=self.k.kexts[kext_index["AppleALC"]].checked,
            simplehda=self.k.kexts[kext_index["SimpleHDA"]].checked,
            bcmc=self.k.kexts[kext_index["AppleBCMWLANCompanion"]].checked,
            usb_map_kext=usb_map_kext,
            debug=debug,
            airportitlwm=self.k.kexts[kext_index["AirportItlwm"]].checked
        )
        self.pk.save_summary(self.build_summary, os.path.join(self.result_dir, postinstall_kit.SUMMARY_NAME))
        kit_dir = os.path.join(self.result_dir, postinstall_kit.KIT_FOLDER)
        if self.pk.invalidate(kit_dir):
            print("The previous post-install kit in {} was made for an older build and was removed; prepare it again with option 9.".format(kit_dir))
        return self.build_summary

    def prepare_post_install_kit(self, mode=None):
        summary = self.build_summary or self.pk.load_summary(os.path.join(self.result_dir, postinstall_kit.SUMMARY_NAME))
        self.u.head("Prepare Post-Install Kit")
        print("")
        if not summary:
            summary_path = os.path.join(self.result_dir, postinstall_kit.SUMMARY_NAME)
            if self.pk.summary_outdated(summary_path):
                print("\033[1;93m{} is from an older version of this app; rebuild the EFI (option 7).\033[0m".format(summary_path))
            else:
                print("\033[1;93mBuild the OpenCore EFI first (option 7). The kit is made from {}.\033[0m".format(summary_path))
            return None

        if mode is None:
            print("Build: {} with {}, made {}".format(summary.get("macos_name"), summary.get("smbios_model"), summary.get("created")))
            print("")
            print("1. Full kit: checklist, helper script, smbios.txt and tools (Hackintool, ProperTree, MountEFI and the ones this build needs)")
            print("2. Docs only: checklist, helper script and smbios.txt, nothing is downloaded")
            print("")
            print("Any other key to go back")
            print("")
            mode = {"1": "full", "2": "docs-only"}.get(self.u.request_input("Select an option: ").strip())
            if not mode:
                return None
            print("")

        result = self.pk.build(summary, os.path.join(self.result_dir, postinstall_kit.KIT_FOLDER), mode, config_path=os.path.join(self.result_dir, "EFI", "OC", "config.plist"))
        print("Option 10 (Create USB Installer) copies this folder to the USB drive as PostInstall.")
        return result
        
    def check_bios_requirements(self, org_hardware_report, hardware_report, overrides=None):
        disable = []
        enable = []

        org_firmware_type = org_hardware_report.get("BIOS", {}).get("Firmware Type", "Unknown")
        firmware_type = hardware_report.get("BIOS", {}).get("Firmware Type", "Unknown")
        if org_firmware_type == "Legacy" and firmware_type == "UEFI":
            enable.append("UEFI mode (disable Legacy/CSM (Compatibility Support Module))")

        secure_boot = hardware_report.get("BIOS", {}).get("Secure Boot", "Unknown")
        if secure_boot != "Disabled":
            disable.append("Secure Boot")

        resizable_bar_enabled = any(gpu_props.get("Resizable BAR", "Disabled") == "Enabled" for gpu_props in hardware_report.get("GPU", {}).values())
        cpu = hardware_report.get("CPU", {})
        hedt_npci = not resizable_bar_enabled and self.bios_checklist.is_intel_hedt_cpu(cpu.get("Processor Name", ""), cpu.get("Codename", ""))

        if hardware_report.get("Motherboard", {}).get("Platform") == "Desktop" and hardware_report.get("Motherboard", {}).get("Chipset") in chipset_data.IntelChipsets[112:]:
            if not resizable_bar_enabled and not hedt_npci:
                enable.append("Above 4G Decoding")

        if resizable_bar_enabled:
            notes = ["Resizable BAR may stay enabled; ResizeAppleGpuBars in the generated config handles it."]
        else:
            notes = []
            disable.append("Resizable BAR/Smart Access Memory")

        checklist = self.bios_checklist.build(hardware_report, overrides)
        keywords = ("Secure Boot", "Above 4G", "CSM")
        handled = [word for word in keywords if any(word in item for item in disable + enable)]
        if hedt_npci:
            handled.append("Above 4G")

        for items, extra, filtered in ((disable, checklist["disable"], True), (enable, checklist["enable"], True), (notes, checklist["notes"], False)):
            for item in extra:
                if item in items:
                    continue
                if filtered and any(word in item for word in handled):
                    continue
                items.append(item)

        for note in getattr(getattr(self, "co", None), "bios_notes", None) or []:
            if note not in notes:
                notes.append(note)

        return {"disable": disable, "enable": enable, "notes": notes}

    def first_boot_notes(self, hardware_report):
        notes = []
        first_boot = getattr(self.co, "whatevergreen_first_boot", None)
        if first_boot:
            notes.append("WhateverGreen.kext is in EFI/OC/Kexts but disabled for the first boot (AMD connector patching can panic on macOS 26).")
            notes.append("After installing macOS, open config.plist and set Kernel -> Add -> WhateverGreen.kext -> Enabled to true.")
            if first_boot.get("agdp_patch"):
                notes.append("Then remove the Kernel -> Patch entry \"AGDP board-id patch (pikera) for first boot without WhateverGreen\" and add agdpmod=pikera back to boot-args.")

        if self.k.kexts[kext_maestro.kext_data.kext_index_by_name.get("AppleBCMWLANCompanion")].checked:
            for network_name, network_props in hardware_report.get("Network", {}).items():
                firmware = config_prodigy.bcmc_firmwares.get(network_props.get("Device ID"))
                if not firmware:
                    continue
                chip = firmware[0].split("-")[0].replace("brcmfmac", "BCM")
                notes.append("Wi-Fi ({}) stays off until the firmware is installed: after installing macOS, download {} from https://github.com/0xFireWolf/AppleBCMWLANCompanion/tree/main/Firmwares/{} into {}.".format(network_name, firmware[0], chip, config_prodigy.bcmc_firmware_dir))
            notes.append("AppleBCMWLANCompanion panics during macOS updates: disable it in Kernel -> Add before updating and enable it again afterwards.")
        return notes

    def post_install_notes(self):
        return ["After installing, copy the EFI folder to the internal disk's EFI partition. Then set Misc -> Boot -> LauncherOption to Full (Short on Insyde firmware) so OpenCore registers its own firmware boot entry; this build leaves it Disabled so that testing the USB does not change the firmware boot order."]

    def before_using_efi(self, org_hardware_report, hardware_report):
        self.u.head("Before Using EFI")
        print("")                 
        print("\033[93mPlease complete the following steps:\033[0m")
        print("")

        print("* Windows Preflight (this PC, before changing firmware settings):")
        self.pf.show(org_hardware_report, hardware_report)
        print("")
        
        bios_requirements = self.check_bios_requirements(org_hardware_report, hardware_report, self.k.bios_overrides())
        print("* BIOS/UEFI Settings Required:")
        for title, field in (("Disable", "disable"), ("Enable", "enable"), ("Notes", "notes")):
            if bios_requirements[field]:
                print("    {}:".format(title))
                for item in bios_requirements[field]:
                    print("    - {}".format(item))
        print("")
        
        kexts_folder = "EFI\\OC\\Kexts" if os.name == "nt" else "EFI/OC/Kexts"
        print("* USB Mapping:")
        if self.usb_map_source:
            map_kext = os.path.basename(os.path.normpath(self.usb_map_source["path"])) if self.usb_map_source["kind"] == "kext" else usb_mapper.KEXT_NAME
            print("    - {} from your USB map is in the {} folder and replaces UTBDefault.kext.".format(map_kext, kexts_folder))
            print("    - After installing macOS, check that every mapped port works. If one does not, map again with option 6 and rebuild.")
        else:
            print("    - Map USB ports with option 6 (USB Port Mapping) before building, then build again.")
            print("    - Until then UTBDefault.kext enables every port, which can go past the 15-port limit per controller.")
        print("    - XhciPortLimit does not work on macOS 11.3 and newer, so do not rely on it to lift the 15-port limit.")
        print("")
        first_boot_notes = self.first_boot_notes(hardware_report)
        if first_boot_notes:
            print("* First Boot:")
            for note in first_boot_notes:
                print("    - {}".format(note))
            print("")
        if self.debug_build:
            print("* DEBUG Build:")
            for note in self.debug_build_notes():
                print("    - {}".format(note))
            print("")
        print("* After Installing macOS:")
        for note in self.post_install_notes():
            print("    - {}".format(note))
        print("    - Option 9 (Prepare Post-Install Kit) writes the post-install steps for this build as a checklist; option 10 copies it to the USB drive.")
        print("")
        self.u.request_input()
        self.u.open_folder(self.result_dir)

    def resolve_headless_path(self, path):
        path = os.path.expanduser(str(path))
        if not os.path.isabs(path):
            candidate = os.path.join(app_paths.app_dir(), path)
            if app_paths.is_frozen() and not os.path.exists(candidate):
                candidate = os.path.join(os.getcwd(), path)
            path = candidate
        return os.path.normpath(path)

    def resolve_headless_macos_version(self, value):
        value = str(value).strip()
        if re.match(r'^\d+(?:\.\d+){0,2}$', value):
            return value

        name = value.lower()
        if name.startswith("macos"):
            name = name[len("macos"):].strip()
        for data in os_data.macos_versions:
            if name in (data.name.lower(), "{} {}".format(data.name, data.macos_version).lower()):
                return str(data.darwin_version)

        raise Exception("Unknown macOS version '{}'.".format(value))

    def print_headless_summary(self, exit_code, smbios_model=None, validation_status="not run"):
        print("")
        print("Headless build result: exit={} folder={} smbios={} ocvalidate={}".format(exit_code, self.result_dir, smbios_model or "none", validation_status))
        return exit_code

    def print_bios_requirements(self, bios_requirements):
        print("")
        print("BIOS/UEFI settings:")
        for title, field in (("Disable", "disable"), ("Enable", "enable"), ("Notes", "notes")):
            for item in bios_requirements[field]:
                print("    {}: {}".format(title, item))

    def apply_headless_usb_map(self, usb_map):
        self.usb_map_auto = isinstance(usb_map, str) and usb_map.strip().lower() == "auto"
        if self.usb_map_auto:
            return
        if usb_map is False or (isinstance(usb_map, str) and usb_map.strip().lower() == "none"):
            self.usb_map_override = False
        elif isinstance(usb_map, str) and usb_map.strip():
            self.usb_map_override = self.resolve_headless_path(usb_map)
        elif usb_map is not None:
            raise Exception('usb_map must be a path, false or "none".')

    def express(self, report_path=None, acpi_dir=None, kit_mode="full", recovery=True):
        express = session.Session(ocpe_factory=lambda: self)
        express.usb_map_disabled = self.usb_map_override is False
        express.usb_auto = self.usb_map_auto
        try:
            return express, express.express(report_path, acpi_dir, kit_mode, recovery), None
        except session.StageError as e:
            print("")
            print("\033[91m{}\033[0m".format(e))
            for line in e.details:
                print("  - {}".format(line))
            return express, None, e
        except utils.GuiExit as e:
            print("")
            print("Stopped.")
            return express, None, e
        except Exception as e:
            print("")
            print("\033[91mError: {}\033[0m".format(e))
            return express, None, e

    def print_summary_lines(self, summary):
        for line in session.summary_lines(summary):
            print(explain.wrap(line, 68, "    " if line.startswith("  - ") else "  "))

    def express_menu(self):
        express, summary, _ = self.express()
        if summary:
            self.u.head(session.EXPRESS_TITLE)
            print("")
            self.print_summary_lines(summary)
            self.print_bios_requirements(summary["bios"])
            print("")
            print("Next: 10. Create USB Installer")
        print("")
        self.u.request_input("Press Enter to main menu...")
        return express

    def show_glossary(self):
        entries = explain.render_glossary()
        for start in range(0, len(entries), 8):
            self.u.head("Glossary")
            print("")
            for entry in entries[start:start + 8]:
                print(entry)
            print("")
            self.u.request_input("Press Enter to main menu..." if start + 8 >= len(entries) else "Press Enter for more...")

    def run_headless_express(self, answers):
        self.u.set_headless(answers)
        self.apply_headless_usb_map(answers.get("usb_map"))
        report = answers.get("report") or "sniff"
        report_path = None if str(report).lower() == "sniff" else self.resolve_headless_path(report)
        acpi_dir = self.resolve_headless_path(answers["acpi"]) if answers.get("acpi") else None
        recovery = not answers.get("skip_recovery", True)
        express, summary, error = self.express(report_path, acpi_dir, answers.get("postinstall_kit") or None, recovery)
        if not summary:
            return self.print_headless_summary(3 if isinstance(error, session.UnsupportedHardware) else 1)
        print("")
        self.print_summary_lines(summary)
        self.print_bios_requirements(summary["bios"])
        return self.print_headless_summary(0 if summary["ocvalidate"] == "passed" else 2, summary["smbios"], summary["ocvalidate"])

    def run_headless(self, answers):
        kit_mode = answers.get("postinstall_kit", False)
        if kit_mode not in (False, None) and kit_mode not in postinstall_kit.MODES:
            raise Exception('postinstall_kit must be "full", "docs-only" or false.')

        debug_answer = answers.get("debug")
        if debug_answer not in (None, True, False):
            raise Exception("debug must be true or false.")

        if answers.get("express"):
            return self.run_headless_express(answers)

        macos_answer = self.resolve_headless_macos_version(answers.get("macos_version", ""))
        prompts = {"Please enter the macOS version": macos_answer}
        if debug_answer is not None:
            prompts["Build a DEBUG EFI"] = "yes" if debug_answer else "no"
        for key, value in (answers.get("prompts") or {}).items():
            if key.lower() == "please enter the macos version" or (debug_answer is not None and key.lower() == "build a debug efi"):
                continue
            prompts[key] = value
        answers["prompts"] = prompts
        self.u.set_headless(answers)

        self.ac.dsdt = self.ac.acpi.acpi_tables = None

        self.apply_headless_usb_map(answers.get("usb_map"))
        if self.usb_map_auto:
            raise Exception('usb_map "auto" needs "express": true.')

        report = answers.get("report")
        acpi_path = answers.get("acpi")
        if not report:
            raise Exception("The answers file does not name a hardware report.")

        if str(report).lower() == "sniff":
            report_path, acpitables_dir, error_message = self.export_hardware_report()
            if error_message or not report_path:
                raise Exception("Could not export the hardware report. {}".format(error_message or "The hardware scan is only available on Windows."))
            if not acpi_path:
                if not acpitables_dir:
                    raise Exception("The hardware report was saved to {}, but the ACPI tables could not be dumped (see above). Add \"acpi\" with a tables folder to the answers file, or fix the dump and retry.".format(report_path))
                acpi_path = acpitables_dir
        else:
            report_path = self.resolve_headless_path(report)

        is_valid, errors, warnings, hardware_report = self.v.validate_report(report_path)
        self.v.show_validation_report(report_path, is_valid, errors, warnings)
        if not is_valid or errors:
            raise Exception("Hardware report {} failed validation.".format(report_path))

        if acpi_path:
            acpi_path = self.resolve_headless_path(acpi_path)
            self.ac.read_acpi_tables(acpi_path)
            if not self.ac._ensure_dsdt():
                raise Exception("Could not load a DSDT from {}.".format(acpi_path))
        else:
            self.ac.skip_acpi_tables = True

        try:
            hardware_report, native_macos_version, ocl_patched_macos_version = self.c.check_compatibility(hardware_report)
        except utils.HeadlessExit:
            print("")
            print("The hardware is not compatible with macOS.")
            return self.print_headless_summary(3)

        version_match = re.match(r'^(\d+)(?:\.(\d+)(?:\.(\d+))?)?$', macos_answer)
        target_version = self.u.parse_darwin_version("{}.{}.{}".format(version_match.group(1), version_match.group(2) or 99, version_match.group(3) or 99))
        in_native = self.u.parse_darwin_version(native_macos_version[0]) <= target_version <= self.u.parse_darwin_version(native_macos_version[-1])
        in_oclp = bool(ocl_patched_macos_version) and self.u.parse_darwin_version(ocl_patched_macos_version[-1]) <= target_version <= self.u.parse_darwin_version(ocl_patched_macos_version[0])
        if not in_native and not in_oclp:
            print("")
            print("macOS {} is outside the supported range for this hardware (native {} to {}{}).".format(
                macos_answer, native_macos_version[0], native_macos_version[-1],
                ", OCLP {} to {}".format(ocl_patched_macos_version[-1], ocl_patched_macos_version[0]) if ocl_patched_macos_version else ""))
            return self.print_headless_summary(3)

        macos_version = self.select_macos_version(hardware_report, native_macos_version, ocl_patched_macos_version)
        customized_hardware, disabled_devices, needs_oclp = self.h.hardware_customization(hardware_report, macos_version)
        smbios_model = self.s.select_smbios_model(customized_hardware, macos_version)
        self.ac.ensure_dsdt()
        self.ac.select_acpi_patches(customized_hardware, disabled_devices)
        needs_oclp = self.k.select_required_kexts(customized_hardware, macos_version, needs_oclp, self.ac.patches)
        self.s.smbios_specific_options(customized_hardware, smbios_model, macos_version, self.ac.patches, self.k)

        if needs_oclp and not self.show_oclp_warning(macos_version):
            print("")
            print(self.oclp_declined_message(customized_hardware, macos_version))
            return self.print_headless_summary(3, smbios_model)

        print("")
        self.select_debug_build()
        self.prepare_usb_map(customized_hardware, macos_version)
        if not self.o.gather_bootloader_kexts(self.k.kexts, macos_version, debug=self.debug_build):
            raise Exception("Could not gather OpenCorePkg and kexts.")

        is_valid = self.build_opencore_efi(customized_hardware, disabled_devices, smbios_model, macos_version, needs_oclp, debug=self.debug_build)

        config_file = os.path.join(self.result_dir, "EFI", "OC", "config.plist")
        if not os.path.exists(config_file):
            raise Exception("The build did not produce {}.".format(config_file))

        print("")
        if self.usb_map_source:
            print("USB map: {} -> {}".format(self.usb_map_source["path"], os.path.join(self.result_dir, "EFI", "OC", "Kexts")))
        else:
            print("USB map: none, UTBDefault.kext is used")

        print("")
        print("Windows preflight:")
        self.pf.show(hardware_report, customized_hardware)

        self.print_bios_requirements(self.check_bios_requirements(hardware_report, customized_hardware, self.k.bios_overrides()))

        first_boot_notes = self.first_boot_notes(customized_hardware)
        if first_boot_notes:
            print("")
            print("First boot:")
            for note in first_boot_notes:
                print("    {}".format(note))

        if self.debug_build:
            print("")
            print("DEBUG build:")
            for note in self.debug_build_notes():
                print("    {}".format(note))

        print("")
        print("After installing:")
        for note in self.post_install_notes():
            print("    {}".format(note))

        if kit_mode:
            print("")
            if not self.prepare_post_install_kit(kit_mode):
                raise Exception("The post-install kit could not be prepared.")

        if not answers.get("skip_recovery", True):
            print("")
            if not self.rf.download(macos_version, self.result_dir):
                raise Exception("macOS recovery download failed.")

        if is_valid is True:
            return self.print_headless_summary(0, smbios_model, "passed")
        return self.print_headless_summary(2, smbios_model, "errors" if is_valid is False else "unavailable")

    def analyze_boot_log(self, location=None):
        self.u.head("Analyze Boot Log")
        print("")
        if location is None:
            print("A DEBUG EFI writes opencore-YYYY-MM-DD-hhmmss.txt to the root of the EFI partition it booted from.")
            print("Enter the USB drive letter (for example E), a folder, or a log file (a QEMU serial.log works too).")
            print("")
            location = self.u.request_input("Drive, folder or log file (Enter to go back): ").strip().strip('"')
            if not location:
                return None

        analyzer = log_analyzer.LogAnalyzer()
        if not analyzer.load(location):
            print("")
            print("\033[1;93mNo OpenCore log (opencore-*.txt or serial.log) found at {}.\033[0m".format(location))
            print("Build a DEBUG EFI (option 7, answer yes to the DEBUG question), boot it once, then try again.")
            return None

        report = analyzer.analyze()
        analyzer.render(report)
        return report

    def main(self):
        hardware_report_path = None
        native_macos_version = None
        disabled_devices = None
        macos_version = None
        ocl_patched_macos_version = None
        needs_oclp = False
        smbios_model = None
        express = None

        while True:
            self.u.head()
            print("")
            print("  Hardware Report: {}".format(hardware_report_path or 'Not selected'))
            if hardware_report_path:
                print("")
                print("  macOS Version:   {}".format(os_data.get_macos_name_by_darwin(macos_version) if macos_version else 'Not selected') + (' (' + macos_version + ')' if macos_version else '') + ('. \033[1;93mRequires OpenCore Legacy Patcher\033[0m' if needs_oclp else ''))
                print("  SMBIOS:          {}".format(smbios_model or 'Not selected'))
                if disabled_devices:
                    print("  Disabled Devices:")
                    for device, _ in disabled_devices.items():
                        print("    - {}".format(device))
            if express and express.kit_result:
                print("")
                print("  Next: 10. Create USB Installer")
            print("")

            print("A. Build everything with recommended choices")
            print("")
            print("1. Select Hardware Report")
            print("2. Select macOS Version")
            print("3. Customize ACPI Patch")
            print("4. Customize Kexts")
            print("5. Customize SMBIOS Model")
            print("6. USB Port Mapping")
            print("7. Build OpenCore EFI")
            print("8. Download macOS Recovery")
            print("9. Prepare Post-Install Kit")
            print("10. Create USB Installer")
            print("11. Analyze Boot Log")
            print("")
            print("G. Glossary")
            print("Q. Quit")
            print("")

            option = self.u.request_input("Select an option: ")
            if option.lower() == "q":
                self.u.exit_program()

            if option.lower() == "g":
                self.show_glossary()
                continue

            if option.lower() == "a":
                express = self.express_menu()
                if express.configured:
                    hardware_report_path = express.report_path
                    hardware_report = express.hardware_report
                    native_macos_version = express.native_macos_version
                    ocl_patched_macos_version = express.ocl_patched_macos_version
                    macos_version = express.macos_version
                    customized_hardware = express.customized_hardware
                    disabled_devices = express.disabled_devices
                    needs_oclp = express.needs_oclp
                    smbios_model = express.smbios_model
                else:
                    # the run reset the loaded report, so start over as on a fresh launch
                    hardware_report_path = hardware_report = native_macos_version = ocl_patched_macos_version = None
                    macos_version = customized_hardware = disabled_devices = smbios_model = None
                    needs_oclp = False
                continue

            if option in ("1", "2", "3", "4", "5"):
                express = None

            if option == "1":
                hardware_report_path, hardware_report = self.select_hardware_report()
                hardware_report, native_macos_version, ocl_patched_macos_version = self.c.check_compatibility(hardware_report)
                macos_version = self.select_macos_version(hardware_report, native_macos_version, ocl_patched_macos_version)
                customized_hardware, disabled_devices, needs_oclp = self.h.hardware_customization(hardware_report, macos_version)
                smbios_model = self.s.select_smbios_model(customized_hardware, macos_version)
                if not self.ac.ensure_dsdt():
                    self.ac.select_acpi_tables()
                self.ac.select_acpi_patches(customized_hardware, disabled_devices)
                needs_oclp = self.k.select_required_kexts(customized_hardware, macos_version, needs_oclp, self.ac.patches)
                self.s.smbios_specific_options(customized_hardware, smbios_model, macos_version, self.ac.patches, self.k)

            if option == "8":
                if not macos_version:
                    self.u.head()
                    print("\n\n")
                    print("\033[1;93mPlease select a macOS version first.\033[0m")
                    print("\n\n")
                    self.u.request_input("Press Enter to go back...")
                    continue

                self.u.head("Download macOS Recovery")
                print("")
                recovery_path = self.rf.download(macos_version, self.result_dir)
                print("")
                if recovery_path:
                    print("macOS recovery files are ready at:")
                    print("\t{}".format(recovery_path))
                    print("")
                    print("Copy the com.apple.recovery.boot folder to the root of the FAT32 USB partition, next to the EFI folder.")
                    print("")
                self.u.request_input("Press Enter to main menu...")
                continue

            if option == "9":
                self.prepare_post_install_kit()
                print("")
                self.u.request_input("Press Enter to main menu...")
                continue

            if option == "10":
                self.u.head("Create USB Installer")
                print("")
                print("1. Format a USB drive and write the installer")
                print("2. Copy EFI to an existing USB drive")
                print("")
                print("Any other key to go back")
                print("")
                usb_option = self.u.request_input("Select an option: ").strip()
                if usb_option == "1":
                    self.usb.create_installer(self.result_dir, os.path.join(self.result_dir, "com.apple.recovery.boot"), dry_run="--dry-run-usb" in sys.argv)
                    print("")
                    self.u.request_input("Press Enter to main menu...")
                elif usb_option == "2":
                    self.usb.copy_efi_to_existing(self.result_dir)
                    print("")
                    self.u.request_input("Press Enter to main menu...")
                continue

            if option == "11":
                self.analyze_boot_log()
                print("")
                self.u.request_input("Press Enter to main menu...")
                continue

            if not hardware_report_path:
                self.u.head()
                print("\n\n")
                print("\033[1;93mPlease select a hardware report first.\033[0m")
                print("\n\n")
                self.u.request_input("Press Enter to go back...")
                continue

            if option == "2":
                macos_version = self.select_macos_version(hardware_report, native_macos_version, ocl_patched_macos_version)
                customized_hardware, disabled_devices, needs_oclp = self.h.hardware_customization(hardware_report, macos_version)
                smbios_model = self.s.select_smbios_model(customized_hardware, macos_version)
                needs_oclp = self.k.select_required_kexts(customized_hardware, macos_version, needs_oclp, self.ac.patches)
                self.s.smbios_specific_options(customized_hardware, smbios_model, macos_version, self.ac.patches, self.k)
            elif option == "3":
                self.ac.customize_patch_selection()
            elif option == "4":
                self.k.kext_configuration_menu(macos_version)
            elif option == "5":
                smbios_model = self.s.customize_smbios_model(customized_hardware, smbios_model, macos_version)
                self.s.smbios_specific_options(customized_hardware, smbios_model, macos_version, self.ac.patches, self.k)
            elif option == "6":
                self.usb_port_mapping(hardware_report)
            elif option == "7":
                if needs_oclp and not self.show_oclp_warning(macos_version):
                    macos_version = self.select_macos_version(hardware_report, native_macos_version, ocl_patched_macos_version)
                    customized_hardware, disabled_devices, needs_oclp = self.h.hardware_customization(hardware_report, macos_version)
                    smbios_model = self.s.select_smbios_model(customized_hardware, macos_version)
                    needs_oclp = self.k.select_required_kexts(customized_hardware, macos_version, needs_oclp, self.ac.patches)
                    self.s.smbios_specific_options(customized_hardware, smbios_model, macos_version, self.ac.patches, self.k)
                    continue

                try:
                    print("")
                    self.select_debug_build()
                    self.prepare_usb_map(customized_hardware, macos_version)
                    self.o.gather_bootloader_kexts(self.k.kexts, macos_version, debug=self.debug_build)
                except Exception as e:
                    print("\033[91mError: {}\033[0m".format(e))
                    print("")
                    self.u.request_input("Press Enter to continue...")
                    continue
                
                self.build_opencore_efi(customized_hardware, disabled_devices, smbios_model, macos_version, needs_oclp, debug=self.debug_build)
                self.before_using_efi(hardware_report, customized_hardware)

                self.u.head("Result")
                print("")
                print("Your OpenCore EFI for {} has been built at:".format(customized_hardware.get("Motherboard").get("Name")))
                print("\t{}".format(self.result_dir))
                print("")
                self.u.request_input("Press Enter to main menu...")

def run_headless(answers_path):
    if hasattr(sys.stdout, "reconfigure") and not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

    try:
        with open(answers_path, "r", encoding="utf-8") as answers_file:
            answers = json.load(answers_file)
        utils.Utils.set_headless(answers)
        return OCPE().run_headless(answers)
    except (Exception, utils.HeadlessExit, utils.HeadlessPromptLoop):
        print("")
        print(traceback.format_exc())
        print("Headless build result: exit=1")
        return 1
    finally:
        utils.Utils.set_headless(None)

def analyze_log(path):
    if hasattr(sys.stdout, "reconfigure") and not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

    analyzer = log_analyzer.LogAnalyzer()
    if not analyzer.load(path):
        print("No OpenCore log (opencore-*.txt or serial.log) found at {}.".format(path))
        return 1
    print(analyzer.format_report(analyzer.analyze()))
    return 0

def redirect_missing_streams():
    # pythonw and the windowed exe have no console, so output goes to a log beside the app
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        handle = open(os.path.join(app_paths.app_dir(), "MacBootsmith.log"), "w", encoding="utf-8", buffering=1)
    except OSError:
        handle = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = handle
    if sys.stderr is None:
        sys.stderr = handle

def option_value(argv, flag):
    index = argv.index(flag)
    return argv[index + 1] if index + 1 < len(argv) else None

def run_terminal():
    o = OCPE()
    while True:
        try:
            o.main()
        except EOFError:
            return 0
        except Exception as e:
            o.u.head("An Error Occurred")
            print("")
            print(traceback.format_exc())
            o.u.request_input()

def main(argv):
    redirect_missing_streams()

    if "--analyze-log" in argv:
        location = option_value(argv, "--analyze-log")
        if not location:
            print("Usage: python MacBootsmith.py --analyze-log <log file, folder or drive>")
            return 1
        return analyze_log(location)

    if "--headless" in argv:
        answers = option_value(argv, "--headless")
        if not answers:
            print("Usage: python MacBootsmith.py --headless <answers.json>")
            return 1
        return run_headless(answers)

    # the window has its own dry-run checkbox, the flag only applies to the menu
    if "--terminal" in argv or "--dry-run-usb" in argv:
        return run_terminal()

    try:
        import tkinter
    except ImportError:
        print("tkinter is not available, starting the terminal menu instead.")
        return run_terminal()

    from Scripts.gui import app
    utils.Utils.install_output_proxy()
    return app.main(argv)

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
