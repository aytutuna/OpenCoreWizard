import contextlib
import copy
import importlib.util
import os
import sys

from Scripts import app_paths
from Scripts import usb_mapper
from Scripts import utils
from Scripts.datasets import codec_layouts
from Scripts.datasets import os_data
from Scripts.datasets import pci_data
from Scripts.datasets import recovery_boards

MAIN_MODULE_NAME = "macbootsmith_main"
MAIN_SCRIPT = "MacBootsmith.py"
VERDICT_SECTIONS = ("CPU", "GPU", "Sound", "Biometric", "Network", "Storage Controllers", "Bluetooth", "SD Controller")
EXPRESS_TITLE = "Build everything"
EXPRESS_STEPS = ("Windows preflight", "Hardware scan", "Configuration", "USB map", "EFI build", "macOS recovery", "Post-install kit")
# audio stays unset so the prompt's own default applies; whatevergreen only matters when its prompt appears
EXPRESS_OPTIONS = {"audio": None, "broadcom": "1", "whatevergreen": "yes", "debug": False}
USB_MAP_NOTE = "USB ports not mapped: run USB mapping and rebuild for best results."
USB_DIRTY_NOTE = "USB page has an unsaved selection; save it or press Automatic map"


class StageError(Exception):
    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = list(details or [])


class UnsupportedHardware(StageError):
    pass


def load_main_module():
    module = sys.modules.get(MAIN_MODULE_NAME)
    if module is not None:
        return module
    path = os.path.join(app_paths.bundle_dir(), MAIN_SCRIPT)
    spec = importlib.util.spec_from_file_location(MAIN_MODULE_NAME, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[MAIN_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(MAIN_MODULE_NAME, None)
        raise
    return module


def macos_name(version):
    if not version:
        return None
    return os_data.get_macos_name_by_darwin(str(version))


def devices(report):
    for section in VERDICT_SECTIONS:
        data = report.get(section)
        if not data or not isinstance(data, dict):
            continue
        if section == "CPU":
            yield section, data.get("Processor Name") or "CPU", data
            continue
        for name, props in data.items():
            if isinstance(props, dict):
                yield section, name, props


def unsupported_devices(report):
    report = report or {}
    names = []
    cpu = report.get("CPU") or {}
    if "Compatibility" in cpu and not cpu["Compatibility"][0]:
        names.append(cpu.get("Processor Name") or "CPU")
    gpus = [(name, props) for name, props in (report.get("GPU") or {}).items() if isinstance(props, dict) and "Compatibility" in props]
    if gpus and not any(props["Compatibility"][0] for _, props in gpus):
        names.extend(name for name, _ in gpus)
    return names


def usb_summary(path, report):
    if not path:
        return "none, UTBDefault.kext enables every port"
    if not report or not report["possible"]:
        return "saved map {}".format(path)
    text = "automatic map, {} of {} ports on {} controller{}, confidence {}".format(
        report["selected"], report["total"], report["mapped"], "" if report["mapped"] == 1 else "s", report["confidence"])
    if report["confidence"] == "guess":
        text += ": run USB mapping (menu 6 / USB page) if a port is dead or sleep fails"
    if report.get("replaced"):
        text += "; saved map not usable for this PC, replaced by the automatic map"
    return text


def summary_lines(summary):
    lines = [
        "macOS: {}".format(summary["macos_name"]),
        "SMBIOS: {}".format(summary["smbios"]),
        "ocvalidate: {}".format(summary["ocvalidate"]),
        "EFI: {}".format(os.path.join(summary["result_dir"], "EFI")),
        "Recovery: {}".format(summary["recovery"] or "skipped"),
        "Post-install kit: {}".format(summary["kit"] or "not prepared")
    ]
    if summary.get("usb"):
        lines.insert(4, "USB: {}".format(summary["usb"]))
    if summary.get("decisions"):
        lines.append("Decided for you:")
        lines.extend("  - {}".format(line) for line in summary["decisions"])
    if summary["warnings"]:
        lines.append("Warnings:")
        lines.extend("  - {}".format(warning) for warning in summary["warnings"])
    return lines


class Session:
    def __init__(self, preset_hook=None, ocpe_factory=None, defaults_hook=None):
        self.preset_hook = preset_hook or utils.Utils.set_presets
        self.defaults_hook = defaults_hook or utils.Utils.set_accept_defaults
        self.ocpe_factory = ocpe_factory
        self.ocpe = None
        self.preflight_info = None
        self.preflight_warnings = []
        self.preflight_error = None
        self.options = {"audio": None, "broadcom": "1", "whatevergreen": "yes", "debug": False}
        self.usb_history = None
        self.usb_dirty = False
        self.usb_map_disabled = False
        self.usb_auto = True
        self.build_result = None
        self.recovery_path = None
        self.kit_result = None
        self.log_report = None
        self.reset_hardware()

    def reset_hardware(self):
        self.report_path = None
        self.acpi_dir = None
        self.validation = {"errors": [], "warnings": []}
        self.hardware_report = None
        self.native_macos_version = None
        self.ocl_patched_macos_version = None
        self.suggested_macos_version = None
        self.reset_configuration()

    def reset_configuration(self):
        self.macos_version = None
        self.customized_hardware = None
        self.disabled_devices = None
        self.needs_oclp = False
        self.smbios_model = None
        self.configured = False

    @contextlib.contextmanager
    def answering(self, mapping):
        self.preset_hook(mapping)
        try:
            yield
        finally:
            self.preset_hook({})

    def ensure_ocpe(self):
        if self.ocpe is None:
            factory = self.ocpe_factory or (lambda: load_main_module().OCPE())
            self.ocpe = factory()
        return self.ocpe

    def result_dir(self):
        if self.ocpe is not None:
            return self.ocpe.result_dir
        return os.path.join(app_paths.app_dir(), "Results")

    def paths(self):
        result_dir = self.result_dir()
        sysreport = os.path.join(app_paths.app_dir(), "SysReport")
        return {
            "results": result_dir,
            "efi": os.path.join(result_dir, "EFI"),
            "recovery": os.path.join(result_dir, "com.apple.recovery.boot"),
            "postinstall": os.path.join(result_dir, "PostInstall"),
            "summary": os.path.join(result_dir, "build_summary.json"),
            "sysreport": sysreport,
            "usbmap": os.path.join(sysreport, "USBMap.json")
        }

    def preflight(self):
        ocpe = self.ensure_ocpe()
        self.preflight_error = None
        try:
            info, warnings = ocpe.pf.run(self.hardware_report, self.customized_hardware)
        except Exception as e:
            info, warnings = None, []
            self.preflight_error = str(e)
        self.preflight_info = info
        self.preflight_warnings = list(warnings or [])
        return {"info": info, "warnings": self.preflight_warnings, "error": self.preflight_error, "admin": ocpe.pf.is_admin()}

    def scan_report(self):
        ocpe = self.ensure_ocpe()
        report_path, acpi_dir, error_message = ocpe.export_hardware_report()
        if error_message or not report_path:
            raise StageError("Could not export the hardware report. {}".format(error_message or "The hardware scan is only available on Windows."))
        verdict = self.load_report(report_path, acpi_dir if acpi_dir and os.path.isdir(acpi_dir) else None)
        if getattr(ocpe, "acpi_dump_error", None):
            self.validation["warnings"].append("The ACPI tables were not dumped, so DSDT-based ACPI patches are skipped. {}".format(ocpe.acpi_dump_error))
            verdict = self.verdict()
        return verdict

    def load_report(self, report_path, acpi_dir=None, find_acpi=True):
        ocpe = self.ensure_ocpe()
        self.reset_hardware()
        ocpe.ac.dsdt = ocpe.ac.acpi.acpi_tables = None
        ocpe.ac.skip_acpi_tables = False

        is_valid, errors, warnings, data = ocpe.v.validate_report(report_path)
        ocpe.v.show_validation_report(report_path, is_valid, errors, warnings)
        self.validation = {"errors": list(errors or []), "warnings": list(warnings or [])}
        if not is_valid or errors:
            raise StageError("The hardware report {} failed validation. Export it again and retry.".format(report_path), self.validation["errors"])

        if acpi_dir is None and find_acpi:
            candidate = os.path.join(os.path.dirname(os.path.abspath(report_path)), "ACPI")
            if os.path.isdir(candidate):
                acpi_dir = candidate
        if acpi_dir:
            ocpe.ac.read_acpi_tables(acpi_dir)
            if not ocpe.ac._ensure_dsdt():
                print("No DSDT was found in {}; DSDT-based ACPI patches are skipped.".format(acpi_dir))
                acpi_dir = None
        if not acpi_dir:
            ocpe.ac.skip_acpi_tables = True

        try:
            hardware_report, native_macos_version, ocl_patched_macos_version = ocpe.c.check_compatibility(data)
        except (utils.GuiExit, utils.HeadlessExit) as e:
            if getattr(e, "reason", None) == "cancel":
                raise
            raise UnsupportedHardware("This hardware is not compatible with macOS. The log shows which device stopped the check.")

        self.report_path = report_path
        self.acpi_dir = acpi_dir
        self.hardware_report = hardware_report
        self.native_macos_version = native_macos_version
        self.ocl_patched_macos_version = ocl_patched_macos_version
        with self.answering({"Please enter the macOS version": ""}):
            self.suggested_macos_version = ocpe.select_macos_version(hardware_report, native_macos_version, ocl_patched_macos_version)
        return self.verdict()

    def verdict(self):
        checker = self.ocpe.c if self.ocpe is not None else None
        rows = []
        for section, name, props in devices(self.hardware_report or {}):
            compatibility = props.get("Compatibility")
            oclp = props.get("OCLP Compatibility")
            if not compatibility:
                status = "unchecked"
            elif not compatibility[0]:
                status = "unsupported"
            else:
                status = "supported"
            if oclp and status != "supported":
                status = "oclp"
            label = checker.show_macos_compatibility(compatibility) if checker else str(compatibility)
            rows.append({
                "section": section,
                "name": name,
                "status": status,
                "support": utils.ANSI_ESCAPE.sub("", label),
                "max_macos": macos_name(compatibility[0]) if compatibility and compatibility[0] else "-",
                "oclp": "{} to {}".format(macos_name(oclp[-1]), macos_name(oclp[0])) if oclp else ""
            })
        native = self.native_macos_version
        ocl = self.ocl_patched_macos_version
        return {
            "rows": rows,
            "native": (macos_name(native[0]), macos_name(native[-1])) if native else None,
            "oclp": (macos_name(ocl[-1]), macos_name(ocl[0])) if ocl else None,
            "suggested": macos_name(self.suggested_macos_version),
            "warnings": list(self.validation["warnings"]),
            "acpi": self.acpi_dir
        }

    def macos_choices(self):
        native = self.native_macos_version
        if not native:
            return []
        ocl = self.ocl_patched_macos_version
        oclp_min = int(ocl[-1][:2]) if ocl else 99
        oclp_max = int(ocl[0][:2]) if ocl else 0
        lowest = min(int(native[0][:2]), oclp_min)
        highest = max(int(native[-1][:2]), oclp_max)
        native_max = int(native[-1][:2])
        suggested = int(str(self.suggested_macos_version)[:2]) if self.suggested_macos_version else None
        choices = []
        for darwin in range(lowest, highest + 1):
            name = macos_name(str(darwin))
            if not name:
                continue
            choices.append({
                "darwin": darwin,
                "name": name,
                "oclp": oclp_min <= darwin <= oclp_max,
                "suggested": darwin == suggested,
                "highest_native": darwin == native_max
            })
        return choices

    def default_macos(self):
        choices = self.macos_choices()
        for key in ("suggested", "highest_native"):
            for choice in choices:
                if choice[key]:
                    return choice["darwin"]
        return choices[-1]["darwin"] if choices else None

    def option_relevance(self, darwin):
        report = self.hardware_report or {}
        tahoe = darwin is not None and int(darwin) >= 25
        cpu = report.get("CPU", {}) or {}
        audio = tahoe and any(props.get("Device ID") in codec_layouts.data for props in (report.get("Sound") or {}).values() if isinstance(props, dict))
        broadcom = "AMD" not in str(cpu.get("Manufacturer", "")) and any(props.get("Device ID") in pci_data.BroadcomFullMACWiFiIDs for props in (report.get("Network") or {}).values() if isinstance(props, dict))
        whatevergreen = False
        if tahoe and self.ocpe is not None:
            whatevergreen = self.ocpe.k.has_discrete_amd_gpu(report) and not self.ocpe.k.monitor_on_intel_gpu(report)
        return {"audio": bool(audio), "broadcom": bool(broadcom), "whatevergreen": bool(whatevergreen), "debug": True}

    def recommended_options(self, darwin):
        oclp = any(choice["darwin"] == darwin and choice["oclp"] for choice in self.macos_choices())
        return {"audio": "1" if oclp else "2", "broadcom": "1", "whatevergreen": "yes"}

    def option_presets(self, darwin, options=None):
        options = self.options if options is None else options
        # an empty answer takes the prompt's own recommended default
        presets = {"Please enter the macOS version": str(darwin)}
        presets["Select audio kext for your system"] = options.get("audio") if options.get("audio") in ("1", "2") else ""
        presets["Select Broadcom Wi-Fi option"] = options.get("broadcom") if options.get("broadcom") in ("1", "2") else ""
        presets["Disable WhateverGreen for the first boot"] = options.get("whatevergreen") if options.get("whatevergreen") in ("yes", "no") else ""
        return presets

    def configure(self, darwin, options=None):
        if not self.hardware_report:
            raise StageError("Load a hardware report first.")
        options = dict(self.options if options is None else options)
        self.options = options
        ocpe = self.ensure_ocpe()
        self.reset_configuration()
        hardware_report = self.hardware_report
        with self.answering(self.option_presets(darwin, options)):
            macos_version = ocpe.select_macos_version(hardware_report, self.native_macos_version, self.ocl_patched_macos_version)
            customized_hardware, disabled_devices, needs_oclp = ocpe.h.hardware_customization(hardware_report, macos_version)
            if not customized_hardware.get("GPU"):
                raise StageError("The hardware report lists no GPU, so no SMBIOS model can be chosen. Export the report again with the graphics driver installed.")
            smbios_model = ocpe.s.select_smbios_model(customized_hardware, macos_version)
            ocpe.ac.ensure_dsdt()
            ocpe.ac.select_acpi_patches(customized_hardware, disabled_devices)
            needs_oclp = ocpe.k.select_required_kexts(customized_hardware, macos_version, needs_oclp, ocpe.ac.patches)
            ocpe.s.smbios_specific_options(customized_hardware, smbios_model, macos_version, ocpe.ac.patches, ocpe.k)
            if needs_oclp and not ocpe.show_oclp_warning(macos_version):
                raise StageError(ocpe.oclp_declined_message(customized_hardware, macos_version))
        ocpe.debug_build = bool(options.get("debug"))

        self.macos_version = macos_version
        self.customized_hardware = customized_hardware
        self.disabled_devices = disabled_devices
        self.needs_oclp = needs_oclp
        self.smbios_model = smbios_model
        self.configured = True
        return self.configuration()

    def configuration(self):
        if not self.configured:
            return None
        kexts = [kext.name for kext in self.ocpe.k.kexts if kext.checked]
        patches = [patch.name for patch in self.ocpe.ac.patches if patch.checked]
        return {
            "macos_version": self.macos_version,
            "macos_name": macos_name(self.macos_version),
            "smbios_model": self.smbios_model,
            "needs_oclp": self.needs_oclp,
            "disabled_devices": list((self.disabled_devices or {}).keys()),
            "kexts": kexts,
            "acpi_patches": patches,
            "debug": self.ocpe.debug_build
        }

    def build(self):
        if not self.configured:
            raise StageError("Choose the macOS version and options first.")
        ocpe = self.ensure_ocpe()
        if self.usb_map_disabled:
            ocpe.usb_map_override = False
        elif ocpe.usb_map_override is False:
            ocpe.usb_map_override = None
        ocpe.prepare_usb_map(self.customized_hardware, self.macos_version)
        if not ocpe.o.gather_bootloader_kexts(ocpe.k.kexts, self.macos_version, debug=ocpe.debug_build):
            raise StageError("Could not gather OpenCorePkg and kexts.")
        is_valid = ocpe.build_opencore_efi(self.customized_hardware, self.disabled_devices, self.smbios_model, self.macos_version, self.needs_oclp, debug=ocpe.debug_build)
        config_file = os.path.join(ocpe.result_dir, "EFI", "OC", "config.plist")
        if not os.path.exists(config_file):
            raise StageError("The build did not produce {}.".format(config_file))
        self.build_result = self.collect_results(is_valid)
        return self.build_result

    def bios_requirements(self):
        if not self.hardware_report or not self.customized_hardware or self.ocpe is None:
            return None
        return self.ocpe.check_bios_requirements(self.hardware_report, self.customized_hardware, self.ocpe.k.bios_overrides())

    def bios_state(self, bios):
        if not bios or not self.preflight_info or self.ocpe is None:
            return None
        return self.ocpe.bios_checklist.state(bios, self.preflight_info, self.hardware_report)

    def collect_results(self, is_valid):
        ocpe = self.ocpe
        try:
            self.preflight_info, preflight_warnings = ocpe.pf.run(self.hardware_report, self.customized_hardware)
        except Exception as e:
            preflight_warnings = ["Preflight could not run: {}".format(e)]
        bios = self.bios_requirements()
        source = ocpe.usb_map_source
        reason = ocpe.usb_map_reason
        if not source and self.usb_dirty:
            reason = "The USB mapping page has changes that were not saved, so the build used no map. Press Save map there and build again."
        return {
            "ocvalidate": {True: "passed", False: "errors"}.get(is_valid, "unavailable"),
            "result_dir": ocpe.result_dir,
            "usb_map": source["path"] if source else None,
            "usb_map_reason": reason,
            "bios": bios,
            "bios_state": self.bios_state(bios),
            "first_boot": ocpe.first_boot_notes(self.customized_hardware),
            "debug_notes": ocpe.debug_build_notes() if ocpe.debug_build else [],
            "post_install": ocpe.post_install_notes(),
            "summary": copy.deepcopy(ocpe.build_summary),
            "preflight": list(preflight_warnings or []),
            "motherboard": (self.customized_hardware.get("Motherboard") or {}).get("Name")
        }

    def recovery_choices(self):
        return [{"darwin": darwin, "name": board["name"]} for darwin, board in sorted(recovery_boards.data.items())]

    def recovery(self, darwin):
        ocpe = self.ensure_ocpe()
        path = ocpe.rf.download(str(darwin), ocpe.result_dir)
        if not path:
            raise StageError("The macOS recovery download failed. The log shows why.")
        self.recovery_path = path
        return path

    def post_install_kit(self, mode):
        ocpe = self.ensure_ocpe()
        result = ocpe.prepare_post_install_kit(mode)
        if not result:
            raise StageError("Build the OpenCore EFI first; the kit is made from Results\\build_summary.json.")
        checklist = ""
        files = result.get("files") or []
        if files and os.path.isfile(files[0]):
            with open(files[0], "r", encoding="utf-8") as handle:
                checklist = handle.read()
        bios = self.bios_requirements()
        self.kit_result = dict(result, checklist=checklist, bios=bios, bios_state=self.bios_state(bios))
        return self.kit_result

    def express(self, report_path=None, acpi_dir=None, kit_mode="full", recovery=True):
        ocpe = self.ensure_ocpe()
        auto_usb = self.usb_auto and not self.usb_map_disabled
        steps = [step for step in EXPRESS_STEPS if (recovery or step != "macOS recovery") and (kit_mode or step != "Post-install kit") and (auto_usb or step != "USB map")]

        def step(name):
            ocpe.u.progress_bar(EXPRESS_TITLE, steps, steps.index(name))

        self.build_result = None
        utils.Utils.decisions = []
        self.defaults_hook(True)
        try:
            step("Windows preflight")
            warnings = list(self.preflight()["warnings"])

            step("Hardware scan")
            try:
                verdict = self.load_report(report_path, acpi_dir) if report_path else self.scan_report()
            except UnsupportedHardware as e:
                blocked = unsupported_devices(getattr(ocpe.c, "hardware_report", None))
                if blocked:
                    raise UnsupportedHardware("Nothing can be installed: macOS does not support {}.".format(", ".join(blocked)), e.details)
                raise
            darwin = self.default_macos()
            if not self.native_macos_version or darwin is None:
                blocked = unsupported_devices(self.hardware_report)
                raise UnsupportedHardware("Nothing can be installed: macOS does not support {}.".format(", ".join(blocked) or "this hardware"))
            warnings += verdict["warnings"]

            step("Configuration")
            self.configure(darwin, dict(EXPRESS_OPTIONS))

            usb_report = None
            if auto_usb:
                step("USB map")
                usb_report = self.express_usb_map()

            step("EFI build")
            result = self.build()
            warnings += result["preflight"]
            if usb_report and usb_report.get("warning"):
                warnings.append(usb_report["warning"])
            if not result["usb_map"]:
                if usb_report and not usb_report["possible"] and not usb_report.get("warning"):
                    warnings.append("Automatic USB map not possible: {}".format(usb_report["reason"]))
                warnings.append(USB_MAP_NOTE)

            recovery_path = None
            if recovery and result["ocvalidate"] == "errors":
                warnings.append("Recovery download skipped: ocvalidate reported errors, so the EFI needs fixing first.")
            elif recovery:
                step("macOS recovery")
                recovery_path = self.recovery(self.macos_version)

            kit = None
            if kit_mode:
                step("Post-install kit")
                kit = self.post_install_kit(kit_mode)
            ocpe.u.progress_bar(EXPRESS_TITLE, steps, len(steps), done=True)
        finally:
            self.defaults_hook(False)

        return {
            "macos_name": macos_name(self.macos_version),
            "macos_version": self.macos_version,
            "smbios": self.smbios_model,
            "ocvalidate": result["ocvalidate"],
            "result_dir": result["result_dir"],
            "recovery": recovery_path,
            "kit": kit.get("dest_dir") if kit else None,
            "usb_map": result["usb_map"],
            "usb": usb_summary(result["usb_map"], usb_report),
            "bios": result["bios"],
            "bios_state": result.get("bios_state"),
            "decisions": utils.Utils.decision_lines({"codec_layout": ocpe.co.layout_reason}),
            "warnings": list(dict.fromkeys(warnings))
        }

    def usb_disks(self):
        ocpe = self.ensure_ocpe()
        disks = ocpe.usb.list_usb_disks()
        if disks is None:
            raise StageError("Could not list the USB disks. The log shows why.")
        return {"disks": disks, "admin": bool(ocpe.usb.is_admin())}

    def write_installer(self, disk, erase_text, dry_run=False):
        ocpe = self.ensure_ocpe()
        presets = {"Enter the disk number to use": str(disk["number"]), "Type ERASE to continue": erase_text}
        with self.answering(presets):
            ocpe.usb.create_installer(ocpe.result_dir, os.path.join(ocpe.result_dir, "com.apple.recovery.boot"), dry_run=dry_run, expected_disk=disk)
        return True

    def copy_efi(self, letter):
        ocpe = self.ensure_ocpe()
        with self.answering({"Enter the USB drive letter": str(letter)}):
            ocpe.usb.copy_efi_to_existing(ocpe.result_dir)
        return True

    def usb_enumerate(self):
        ocpe = self.ensure_ocpe()
        controllers = ocpe.usb_mapper.enumerate()
        if controllers is None:
            raise StageError("Could not list the USB ports. The log shows why.")
        return controllers

    def load_usb_history(self):
        ocpe = self.ensure_ocpe()
        if self.usb_history is None:
            saved = ocpe.usb_mapper.load_saved()
            self.usb_history = copy.deepcopy(usb_mapper.get_controllers(saved)) if saved else []
        return self.usb_history

    def merge_usb(self, controllers):
        mapper = usb_mapper
        history = self.usb_history or []
        present = set(mapper.controller_key(controller) for controller in controllers)
        history = [item for item in history if mapper.controller_key(item) in present]
        history = mapper.merge_history(history, controllers)
        mapper.preselect(history)
        self.usb_history = history
        return history

    def auto_usb_map(self, controllers):
        return usb_mapper.auto_select(self.merge_usb(controllers))[1]

    def express_usb_map(self):
        ocpe = self.ensure_ocpe()
        if ocpe.usb_map_override:
            return None
        if self.usb_dirty:
            return {"possible": False, "reason": USB_DIRTY_NOTE, "warning": USB_DIRTY_NOTE}
        # a usable saved map, manual or automatic, wins over a new automatic one
        if ocpe.select_usb_map_source(self.hardware_report, announce=False):
            return None
        replaced = os.path.isfile(ocpe.usb_mapper.map_path)
        controllers = ocpe.usb_mapper.enumerate()
        if controllers is None:
            return {"possible": False, "reason": "the USB ports could not be listed."}
        self.load_usb_history()
        report = self.auto_usb_map(controllers)
        if not report["possible"]:
            return report
        matched, _ = usb_mapper.report_matches(self.usb_history, self.hardware_report)
        if not matched:
            return dict(report, possible=False, reason="the USB controllers of this PC are not the ones in the hardware report.")
        _, errors = self.save_usb_map()
        if errors:
            return dict(report, possible=False, reason=" ".join(errors))
        return dict(report, replaced=replaced)

    def save_usb_map(self):
        mapper = usb_mapper
        history = self.usb_history or []
        errors = mapper.validate_selection(history)
        if errors:
            return None, errors
        path = self.ensure_ocpe().usb_mapper.save(history)
        self.usb_dirty = False
        self.set_usb_map_disabled(False)
        return path, []

    def set_usb_map_disabled(self, disabled):
        self.usb_map_disabled = bool(disabled)
        if self.ocpe is not None:
            self.ocpe.usb_map_override = False if self.usb_map_disabled else None

    def analyze_log(self, location):
        ocpe = self.ensure_ocpe()
        report = ocpe.analyze_boot_log(location)
        if not report:
            raise StageError("No OpenCore log (opencore-*.txt or serial.log) was found at {}.".format(location))
        from Scripts import log_analyzer
        report = dict(report, text=log_analyzer.LogAnalyzer().format_report(report))
        self.log_report = report
        return report
