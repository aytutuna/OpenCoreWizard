import os
import platform
import sys
import tkinter as tk
from tkinter import filedialog
from tkinter import messagebox
from tkinter import ttk

from Scripts import usb_mapper
from Scripts.datasets.explanations import decisions, install_steps
from Scripts.gui import widgets
from Scripts.gui import winsupport
from Scripts.gui.session import StageError
from Scripts.gui.session import macos_name
from Scripts.gui.session import summary_lines

CHECKED = "[x]"
UNCHECKED = "[ ]"
STATUS_TAGS = {"supported": "green", "unsupported": "red", "oclp": "orange", "unchecked": "grey"}
OCVALIDATE_TEXT = {
    "passed": ("config.plist passed ocvalidate.", "green"),
    "errors": ("ocvalidate reported errors in config.plist. The EFI may not boot; the log lists them.", "red"),
    "unavailable": ("config.plist was not validated (ocvalidate is unavailable).", "orange")
}


class Page(ttk.Frame):
    title = ""
    heading = ""
    explains = ()

    def __init__(self, app):
        super().__init__(app.page_host, padding=(16, 12))
        self.app = app
        self.session = app.session
        self.columnconfigure(0, weight=1)
        ttk.Label(self, text=self.heading or self.title, font=widgets.heading_font(self)).grid(row=0, column=0, sticky="w", pady=(0, 8))
        if self.explains:
            self.note(self, "\n".join("{} {}".format(decisions[key]["what"], decisions[key]["why"]) for key in self.explains), 1, tag="grey")
        self.body = ttk.Frame(self)
        self.body.grid(row=2, column=0, sticky="nsew")
        self.body.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.build()

    def build(self):
        pass

    def on_show(self):
        pass

    def can_next(self):
        return True

    def go_next(self):
        return True

    def on_progress(self, title, steps, index, done):
        pass

    def note(self, parent, text, row, column=0, columnspan=1, tag=None, wrap=760):
        label = ttk.Label(parent, text=text, wraplength=wrap, justify="left")
        if tag:
            label.configure(foreground=widgets.COLORS[tag])
        label.grid(row=row, column=column, columnspan=columnspan, sticky="w", pady=(0, 6))
        return label

    def set_label(self, label, text, tag=None):
        label.configure(text=text, foreground=widgets.COLORS[tag] if tag else "")


class WelcomePage(Page):
    title = "Welcome"
    heading = "Welcome to MacBootsmith"

    def build(self):
        self.ran = False
        self.note(self.body, "This wizard runs the same stages as the terminal menu: hardware scan, compatibility verdict, macOS choice, USB port map, "
                             "EFI build with ocvalidate, recovery download, USB installer and post-install kit. Every step can be repeated.", 0)
        express = ttk.Frame(self.body)
        express.grid(row=1, column=0, sticky="ew", pady=(4, 4))
        self.express_button = ttk.Button(express, text="Build everything with recommended choices", style="Express.TButton", command=self.express)
        self.express_button.grid(row=0, column=0, sticky="w")
        if os.name != "nt":
            self.express_button.state(["disabled"])
        self.note(express, "Scans this PC, builds the EFI, downloads the recovery image and prepares the post-install kit. "
                           "Nothing is written to a USB drive until you choose it on the USB page.", 1, tag="grey")
        self.express_result = self.note(express, "", 2)
        ttk.Label(self.body, text="Requirements", font=widgets.bold_font(self)).grid(row=2, column=0, sticky="w", pady=(8, 2))
        frame, self.requirements = widgets.tree(self.body, ("item", "status"), ("Requirement", "Status"), {"item": 340, "status": 420}, height=6)
        frame.grid(row=3, column=0, sticky="nsew")
        header = ttk.Frame(self.body)
        header.grid(row=4, column=0, sticky="ew", pady=(12, 2))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Windows preflight (this PC)", font=widgets.bold_font(self)).grid(row=0, column=0, sticky="w")
        self.check_button = ttk.Button(header, text="Run checks", command=self.run_checks)
        self.check_button.grid(row=0, column=1, sticky="e")
        frame, self.checks = widgets.tree(self.body, ("check", "result"), ("Check", "Result"), {"check": 340, "result": 420}, height=4)
        frame.grid(row=5, column=0, sticky="nsew")
        ttk.Label(self.body, text="Warnings", font=widgets.bold_font(self)).grid(row=6, column=0, sticky="w", pady=(10, 2))
        frame, self.warnings = widgets.scrolled_text(self.body, height=5)
        frame.grid(row=7, column=0, sticky="nsew")
        self.body.rowconfigure(7, weight=1)
        self.fill_requirements()

    def fill_requirements(self, admin=None):
        widgets.clear_tree(self.requirements)
        windows = os.name == "nt"
        admin = winsupport.is_admin() if admin is None else admin
        rows = [
            ("Windows 10 or 11", "{} {}".format(platform.system(), platform.release()), "green" if windows else "red"),
            ("Python 3.8 or newer", "{}.{}.{}".format(*sys.version_info[:3]), "green" if sys.version_info >= (3, 8) else "red"),
            ("Administrator rights (USB writing only)", "yes" if admin else "no: restart as administrator on the USB page", "green" if admin else "orange"),
            ("Internet connection", "needed for OpenCore, kexts and the recovery image", None),
            ("USB drive of 4 GB or more", "its contents will be erased", None)
        ]
        for item, status, tag in rows:
            self.requirements.insert("", "end", values=(item, status), tags=(tag,) if tag else ())

    def on_show(self):
        if not self.ran and self.app.autostart:
            self.run_checks()

    def run_checks(self):
        self.ran = True
        self.app.run_stage("Checking this PC", self.session.preflight, on_done=self.show_checks)

    def express(self):
        self.ran = True
        self.set_label(self.express_result, "")
        self.app.run_stage("Building everything", self.session.express, on_done=self.express_done, on_error=self.express_failed, on_stopped=self.express_stopped)

    def express_failed(self, error, details):
        self.app.pages_changed()
        if self.session.hardware_report:
            self.app.page_by_class(HardwarePage).show_verdict(self.session.verdict())
        if self.session.build_result:
            self.app.page_by_class(BuildPage).show_result(self.session.build_result)
        return False

    def express_stopped(self, reason, text):
        self.app.pages_changed()
        if reason != "cancel":
            messagebox.showwarning("Build everything", "Building everything stopped: {}".format(text), parent=self)

    def express_done(self, summary):
        app = self.app
        session = self.session
        self.show_checks({"info": session.preflight_info, "warnings": session.preflight_warnings, "error": session.preflight_error, "admin": None})
        app.page_by_class(HardwarePage).show_verdict(session.verdict())
        macos = app.page_by_class(MacOSPage)
        macos.on_show()
        macos.choice.set(int(str(session.macos_version).split(".")[0]))
        options = app.page_by_class(OptionsPage)
        for key, var in options.vars.items():
            var.set(session.options.get(key) or "")
        options.debug.set(bool(session.options.get("debug")))
        options.show_configuration(session.configuration(), advance=False)
        app.page_by_class(BuildPage).show_result(session.build_result)
        if summary["recovery"]:
            app.page_by_class(RecoveryPage).done(summary["recovery"])
        app.page_by_class(PostInstallPage).show_kit(session.kit_result)
        lines = summary_lines(summary)
        self.set_label(self.express_result, "Last run: {}, {}, ocvalidate {}.".format(summary["macos_name"], summary["smbios"], summary["ocvalidate"]), "green")
        messagebox.showinfo("Build everything", "\n".join(lines + ["", "Next: write the USB drive on the USB page."]), parent=self)
        app.show_page(app.pages.index(app.page_by_class(UsbWriterPage)))

    def show_checks(self, result):
        widgets.clear_tree(self.checks)
        info = result.get("info")
        if info:
            esp = info.get("esp_mb")
            for check, value in (("BitLocker on the system drive", info.get("bitlocker")),
                                 ("System disk partition style", info.get("partition_style") or "unknown"),
                                 ("Windows EFI partition", "{} MB".format(esp) if esp is not None else "not found")):
                self.checks.insert("", "end", values=(check, value))
        elif result.get("error"):
            self.checks.insert("", "end", values=("Preflight", "could not run: {}".format(result["error"])), tags=("orange",))
        else:
            self.checks.insert("", "end", values=("Preflight", "nothing to check on this system"))
        warnings = result.get("warnings") or []
        widgets.set_text(self.warnings, "")
        if warnings:
            widgets.append_lines(self.warnings, warnings, "red", "- ")
        else:
            widgets.append_lines(self.warnings, ["No warnings."], "green")
        self.fill_requirements(result.get("admin"))


class HardwarePage(Page):
    title = "Hardware"
    heading = "Hardware report and compatibility"
    LEGEND = (
        ("supported", "Supported", "macOS has its own driver for it in the versions shown."),
        ("oclp", "Needs root patches (OCLP)", "Works only after a root patch, which copies driver files Apple removed back into the installed macOS; lower security, redone after every update."),
        ("unsupported", "Unsupported", "macOS has no driver for it; for a graphics card that means no acceleration, other devices simply do not work."),
        ("unchecked", "Unchecked", "The compatibility check did not rate it, so it does not limit the macOS version.")
    )

    def build(self):
        self.acpi_dir = None
        bar = ttk.Frame(self.body)
        bar.grid(row=0, column=0, sticky="ew")
        self.scan_button = ttk.Button(bar, text="Scan this PC", command=self.scan)
        self.scan_button.grid(row=0, column=0, padx=(0, 6))
        if os.name != "nt":
            self.scan_button.state(["disabled"])
        ttk.Button(bar, text="Open report...", command=self.open_report).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(bar, text="ACPI folder...", command=self.choose_acpi).grid(row=0, column=2, padx=(0, 6))
        self.source = ttk.Label(bar, text="No hardware report loaded.")
        self.source.grid(row=0, column=3, sticky="w", padx=(8, 0))
        self.acpi_label = self.note(self.body, "ACPI tables: taken from an ACPI folder next to the report when present; otherwise DSDT-based patches are skipped.", 1)
        frame, self.table = widgets.tree(self.body, ("section", "device", "support", "max", "oclp"),
                                         ("Type", "Device", "Support", "Max macOS", "With OCLP"),
                                         {"section": 150, "device": 300, "support": 240, "max": 160, "oclp": 190}, height=10)
        frame.grid(row=2, column=0, sticky="nsew", pady=(4, 6))
        self.body.rowconfigure(2, weight=1)
        legend = ttk.Frame(self.body)
        legend.grid(row=3, column=0, sticky="ew", pady=(0, 6))
        legend.columnconfigure(1, weight=1)
        for row, (status, name, text) in enumerate(self.LEGEND):
            ttk.Label(legend, text=name, foreground=widgets.COLORS[STATUS_TAGS[status]]).grid(row=row, column=0, sticky="nw", padx=(0, 8))
            ttk.Label(legend, text=text, foreground=widgets.COLORS["grey"], wraplength=760, justify="left").grid(row=row, column=1, sticky="w")
        self.summary = self.note(self.body, "", 4)
        frame, self.messages = widgets.scrolled_text(self.body, height=4)
        frame.grid(row=5, column=0, sticky="nsew")

    def can_next(self):
        return self.session.hardware_report is not None

    def scan(self):
        self.app.run_stage("Scanning this PC", self.session.scan_report, on_done=self.show_verdict, on_error=self.show_failure)

    def open_report(self):
        path = filedialog.askopenfilename(parent=self, title="Open hardware report", filetypes=[("Hardware report", "*.json"), ("All files", "*.*")])
        if not path:
            return
        acpi_dir = self.acpi_dir
        self.app.run_stage("Loading the hardware report", lambda: self.session.load_report(path, acpi_dir), on_done=self.show_verdict, on_error=self.show_failure)

    def choose_acpi(self):
        path = filedialog.askdirectory(parent=self, title="Choose the ACPI tables folder")
        if not path:
            return
        self.acpi_dir = path
        self.set_label(self.acpi_label, "ACPI tables: {} (used for the next report you load or scan)".format(path))

    def show_failure(self, error, details):
        widgets.clear_tree(self.table)
        self.set_label(self.summary, str(error), "red")
        widgets.set_text(self.messages, "")
        lines = getattr(error, "details", None) or []
        if lines:
            widgets.append_lines(self.messages, lines, "red", "- ")
        self.app.pages_changed()
        return isinstance(error, StageError)

    def show_verdict(self, verdict):
        self.app.pages_changed()
        self.set_label(self.source, self.session.report_path or "")
        self.set_label(self.acpi_label, "ACPI tables: {}".format(verdict.get("acpi") or "none loaded; DSDT-based patches are skipped"))
        widgets.clear_tree(self.table)
        for row in verdict["rows"]:
            tag = STATUS_TAGS.get(row["status"])
            self.table.insert("", "end", values=(row["section"], row["name"], row["support"], row["max_macos"], row["oclp"]), tags=(tag,) if tag else ())
        parts = []
        if verdict.get("native"):
            parts.append("Native support: {} to {}.".format(*verdict["native"]))
        if verdict.get("oclp"):
            parts.append("With OpenCore Legacy Patcher: {} to {}.".format(*verdict["oclp"]))
        if verdict.get("suggested"):
            parts.append("Suggested: {}.".format(verdict["suggested"]))
        self.set_label(self.summary, " ".join(parts), "green")
        widgets.set_text(self.messages, "")
        if verdict.get("warnings"):
            widgets.append_lines(self.messages, verdict["warnings"], "yellow", "- ")
        self.app.update_nav()


class MacOSPage(Page):
    title = "macOS version"
    heading = "Choose the macOS version"
    explains = ("macos_version",)

    def build(self):
        self.choice = tk.IntVar(value=0)
        self.generation = None
        self.info = self.note(self.body, "", 0)
        self.list_frame = ttk.Frame(self.body)
        self.list_frame.grid(row=1, column=0, sticky="nw")
        self.note(self.body, "Versions marked as needing OpenCore Legacy Patcher lower macOS security (SIP, AMFI) and need root patches after every update.", 2, tag="grey")

    def on_show(self):
        if self.generation == self.app.generation:
            return
        self.generation = self.app.generation
        for child in self.list_frame.winfo_children():
            child.destroy()
        choices = self.session.macos_choices()
        if not choices:
            self.set_label(self.info, "Load a hardware report on the Hardware page first.", "orange")
            self.choice.set(0)
            return
        self.set_label(self.info, "Supported versions for this hardware:")
        default = self.session.default_macos()
        self.choice.set(default or 0)
        for row, item in enumerate(choices):
            label = item["name"]
            extras = []
            if item["suggested"]:
                extras.append("suggested")
            if item["oclp"]:
                extras.append("requires OpenCore Legacy Patcher")
            if extras:
                label = "{} ({})".format(label, ", ".join(extras))
            ttk.Radiobutton(self.list_frame, text=label, value=item["darwin"], variable=self.choice, command=self.choice_changed).grid(row=row, column=0, sticky="w", pady=2)

    def choice_changed(self):
        configured = self.session.macos_version
        if self.session.configured and configured and int(str(configured)[:2]) != self.choice.get():
            self.session.reset_configuration()
            self.app.pages_changed(configuration_only=True)
        else:
            self.app.update_nav()

    def can_next(self):
        return bool(self.session.hardware_report) and self.choice.get() > 0

    def selected(self):
        return self.choice.get() or None


class OptionsPage(Page):
    title = "Options"
    heading = "Build options"

    GROUPS = (
        ("audio", "Audio on macOS 26 (AppleHDA was removed)", (
            ("", "Use the recommended choice"),
            ("1", "AppleALC + OCLP root patch (sound works after you run OpenCore Legacy Patcher once in macOS)"),
            ("2", "AppleALC + SimpleHDA (sound works without any extra patching in macOS)"))),
        ("broadcom", "Broadcom Wi-Fi on macOS 14 and newer", (
            ("", "Use the recommended choice"),
            ("1", "OCLP root patches (Wi-Fi works after you run OpenCore Legacy Patcher once in macOS)"),
            ("2", "AppleBCMWLANCompanion (needs VT-d, unstable)"))),
        ("whatevergreen", "WhateverGreen on first boot (AMD GPU, macOS 26)", (
            ("", "Use the recommended choice"),
            ("yes", "Disable it for the first boot"),
            ("no", "Keep it enabled")))
    )

    def build(self):
        self.vars = {}
        self.frames = {}
        self.hints = {}
        self.wrapped = []
        outer, canvas, grid = widgets.scrolled_frame(self.body)
        outer.grid(row=0, column=0, sticky="nsew")
        self.body.rowconfigure(0, weight=3)
        grid.columnconfigure(0, weight=1)
        canvas.bind("<Configure>", self.rewrap, add="+")
        for position, (key, title, options) in enumerate(self.GROUPS):
            frame = ttk.LabelFrame(grid, text=title, padding=(10, 4))
            frame.grid(row=position, column=0, sticky="ew", padx=(0, 6), pady=(0, 6))
            frame.columnconfigure(0, weight=1)
            var = tk.StringVar(value=self.session.options.get(key) or "")
            self.vars[key] = var
            hint = ttk.Label(frame, text="")
            hint.grid(row=0, column=0, sticky="w")
            row = self.explain(frame, key, 1)
            buttons = []
            for value, text in options:
                button = ttk.Radiobutton(frame, text=text, value=value, variable=var)
                button.grid(row=row, column=0, sticky="w")
                row = self.when(frame, key, value, row + 1)
                buttons.append(button)
            self.frames[key] = buttons
            self.hints[key] = hint
        frame = ttk.LabelFrame(grid, text="OpenCore build type", padding=(10, 4))
        frame.grid(row=len(self.GROUPS), column=0, sticky="ew", padx=(0, 6), pady=(0, 6))
        frame.columnconfigure(0, weight=1)
        self.debug = tk.BooleanVar(value=bool(self.session.options.get("debug")))
        row = self.explain(frame, "debug", 0)
        ttk.Radiobutton(frame, text="RELEASE (normal)", value=False, variable=self.debug).grid(row=row, column=0, sticky="w")
        row = self.when(frame, "debug", "no", row + 1)
        ttk.Radiobutton(frame, text="DEBUG: writes opencore-*.txt to the EFI partition", value=True, variable=self.debug).grid(row=row, column=0, sticky="w")
        self.when(frame, "debug", "yes", row + 1)
        self.note(self.body, "Other choices the stages need (for example a GPU or Wi-Fi card to keep) appear as questions while the build is configured.", 1, tag="grey")
        frame, self.summary = widgets.scrolled_text(self.body, height=6)
        frame.grid(row=2, column=0, sticky="nsew")
        self.body.rowconfigure(2, weight=1)

    def grey(self, parent, text, row, indent=0):
        label = ttk.Label(parent, text=text, foreground=widgets.COLORS["grey"], wraplength=700 - indent, justify="left")
        label.grid(row=row, column=0, sticky="w", padx=(indent, 0), pady=(0, 2))
        self.wrapped.append((label, indent))
        return label

    def explain(self, parent, key, row):
        self.grey(parent, decisions[key]["what"], row)
        self.grey(parent, decisions[key]["why"], row + 1)
        return row + 2

    def when(self, parent, key, value, row):
        for option in decisions[key]["options"]:
            if option[0] == value:
                self.grey(parent, option[2], row, 24)
                return row + 1
        return row

    def rewrap(self, event):
        for label, indent in self.wrapped:
            label.configure(wraplength=max(240, event.width - 60 - indent))

    def darwin(self):
        return self.app.page_by_class(MacOSPage).selected()

    def on_show(self):
        relevance = self.session.option_relevance(self.darwin())
        recommended = self.session.recommended_options(self.darwin())
        for key, buttons in self.frames.items():
            applies = relevance.get(key)
            if applies:
                labels = dict(next(options for group, _, options in self.GROUPS if group == key))
                buttons[0].configure(text="Use the recommended choice: " + labels[recommended[key]])
            else:
                buttons[0].configure(text="Use the recommended choice (not needed for this hardware)")
            for button in buttons:
                button.state(["!disabled"] if applies else ["disabled"])
            self.set_label(self.hints[key], "applies to this build" if applies else "not used for this hardware", "green" if applies else "grey")
        config = self.session.configuration()
        if config:
            self.show_configuration(config, advance=False)

    def can_next(self):
        return bool(self.session.hardware_report) and bool(self.darwin())

    def options(self):
        options = {key: (var.get() or None) for key, var in self.vars.items()}
        options["debug"] = bool(self.debug.get())
        return options

    def go_next(self):
        darwin = self.darwin()
        options = self.options()
        self.app.run_stage("Configuring the build", lambda: self.session.configure(darwin, options), on_done=self.show_configuration)
        return False

    def show_configuration(self, config, advance=True):
        lines = [
            "macOS: {} ({}){}".format(config["macos_name"], config["macos_version"], ", needs OpenCore Legacy Patcher" if config["needs_oclp"] else ""),
            "SMBIOS: {}".format(config["smbios_model"]),
            "Build type: {}".format("DEBUG" if config["debug"] else "RELEASE"),
            "Kexts: {}".format(", ".join(config["kexts"])),
            "ACPI patches: {}".format(", ".join(config["acpi_patches"]) or "none")
        ]
        if config["disabled_devices"]:
            lines.append("Disabled devices: {}".format(", ".join(config["disabled_devices"])))
        widgets.set_text(self.summary, "\n".join(lines))
        self.app.pages_changed(configuration_only=True)
        if advance:
            self.app.advance()


class UsbMapPage(Page):
    title = "USB mapping"
    heading = "USB port mapping"
    explains = ("usb_map",)

    COLUMNS = ("use", "port", "name", "speed", "type", "devices", "seen")

    def build(self):
        self.loaded = False
        self.editor = None
        self.note(self.body, "Automatic map selects the ports from what Windows reports, without plugging anything in. "
                             "To map by hand, plug a USB 2.0 and a USB 3.0 device into every port you use (front panel and Type-C too), pressing Refresh after each. "
                             "Ports that saw a device are selected. macOS allows at most {} ports per controller.".format(usb_mapper.MAX_PORTS_PER_CONTROLLER), 0)
        bar = ttk.Frame(self.body)
        bar.grid(row=1, column=0, sticky="ew", pady=(0, 4))
        self.auto_button = ttk.Button(bar, text="Automatic map", command=self.auto_map)
        self.auto_button.grid(row=0, column=0, padx=(0, 6))
        self.refresh_button = ttk.Button(bar, text="Refresh", command=self.refresh)
        self.refresh_button.grid(row=0, column=1, padx=(0, 6))
        ttk.Button(bar, text="Save map", command=self.save).grid(row=0, column=2, padx=(0, 6))
        self.no_map = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Build without a USB map (every port turned on; some may not work)", variable=self.no_map, command=self.toggle_no_map).grid(row=0, column=3, padx=(12, 0))
        self.counter = ttk.Label(bar, text="")
        self.counter.grid(row=0, column=4, padx=(12, 0))
        if os.name != "nt":
            self.auto_button.state(["disabled"])
            self.refresh_button.state(["disabled"])
        frame, self.table = widgets.tree(self.body, self.COLUMNS, ("Use", "Port", "Name", "Speed", "Connector type (double-click)", "Connected now", "Seen"),
                                         {"use": 50, "port": 50, "name": 70, "speed": 70, "type": 220, "devices": 220, "seen": 200}, height=12, show="tree headings")
        self.table.column("#0", width=260, minwidth=120, stretch=True)
        self.table.heading("#0", text="Controller / port", anchor="w")
        frame.grid(row=2, column=0, sticky="nsew")
        self.body.rowconfigure(2, weight=1)
        self.table.bind("<Button-1>", self.on_click, add="+")
        self.table.bind("<Double-1>", self.on_double_click, add="+")
        self.message = self.note(self.body, "", 3)

    def on_show(self):
        if self.session.usb_history is not None:
            self.redraw()
        elif not self.loaded and self.app.autostart:
            self.loaded = True
            self.app.run_stage("Reading the saved USB map", self.session.load_usb_history, on_done=lambda _: self.redraw())

    def refresh(self):
        def work():
            self.session.load_usb_history()
            return self.session.usb_enumerate()
        self.app.run_stage("Listing USB ports", work, on_done=self.merged)

    def merged(self, controllers):
        self.session.merge_usb(controllers)
        self.redraw()

    def auto_map(self):
        def work():
            self.session.load_usb_history()
            return self.session.auto_usb_map(self.session.usb_enumerate())
        self.app.run_stage("Automatic USB map", work, on_done=self.auto_done)

    def auto_done(self, report):
        self.redraw()
        color = "red" if not report["possible"] else "green" if report["confidence"] == "good" else "orange"
        text = "\n".join(usb_mapper.report_lines(report))
        if report["possible"]:
            text += "\nCheck the table, then press Save map."
        self.set_label(self.message, text, color)

    def controllers(self):
        return self.session.usb_history or []

    def redraw(self):
        self.close_editor()
        widgets.clear_tree(self.table)
        number = 0
        total = 0
        over = False
        for ci, controller in enumerate(self.controllers()):
            count = usb_mapper.selected_count(controller)
            total += count
            over = over or count > usb_mapper.MAX_PORTS_PER_CONTROLLER
            names = usb_mapper.port_names(controller)
            parent = self.table.insert("", "end", iid="c{}".format(ci), open=True,
                                       text="{} [{}]  {} of {} selected".format(controller.get("name"), controller.get("class", "?"), count, usb_mapper.MAX_PORTS_PER_CONTROLLER),
                                       tags=("red",) if count > usb_mapper.MAX_PORTS_PER_CONTROLLER else ())
            if controller.get("error"):
                self.table.insert(parent, "end", text=controller["error"], tags=("orange",))
            for pi, port in enumerate(controller.get("ports", [])):
                number += 1
                devices = "; ".join(device.get("name", "") for device in port.get("devices", [])) or "-"
                self.table.insert(parent, "end", iid="p{}_{}".format(ci, pi), text=port.get("name") or "Port {}".format(port.get("index")),
                                  values=(CHECKED if port.get("selected") else UNCHECKED, port.get("index"), names.get(port.get("index"), ""),
                                          usb_mapper.PORT_CLASS_LABELS.get(port.get("class"), "?"), self.type_label(port), devices,
                                          ", ".join(port.get("seen") or []) or "-"),
                                  tags=("green",) if port.get("selected") else ())
        self.set_label(self.counter, "{} ports selected".format(total), "red" if over else None)
        if not self.controllers():
            self.set_label(self.message, "No ports listed yet. Press Refresh to list the USB controllers of this PC." if os.name == "nt" else "USB port mapping is only supported on Windows.", "grey")

    def type_label(self, port):
        value = usb_mapper.connector_type(port)
        label = usb_mapper.PORT_TYPES.get(value, "Unknown") if value is not None else "Unknown"
        if port.get("type") is None and value is not None:
            label += " (guess)"
        return label

    def port_number(self, iid):
        if not iid or not iid.startswith("p"):
            return None
        ci, pi = (int(part) for part in iid[1:].split("_"))
        controllers = self.controllers()
        return sum(len(controller.get("ports", [])) for controller in controllers[:ci]) + pi + 1

    def on_click(self, event):
        if self.app.runner.busy():
            return
        if self.table.identify_region(event.x, event.y) != "cell" or self.table.identify_column(event.x) != "#1":
            return
        number = self.port_number(self.table.identify_row(event.y))
        if number is None:
            return
        ok, message = usb_mapper.toggle_port(self.controllers(), number)
        self.session.usb_dirty = self.session.usb_dirty or ok
        self.redraw()
        self.set_label(self.message, message, None if ok else "red")
        return "break"

    def on_double_click(self, event):
        if self.app.runner.busy() or self.table.identify_column(event.x) != "#5":
            return
        iid = self.table.identify_row(event.y)
        number = self.port_number(iid)
        if number is None:
            return
        self.close_editor()
        box = self.table.bbox(iid, "type")
        if not box:
            return
        x, y, width, height = box
        values = ["{}: {}".format(code, label) for code, label in sorted(usb_mapper.PORT_TYPES.items())]
        editor = ttk.Combobox(self.table, values=values, state="readonly")
        editor.place(x=x, y=y, width=max(width, 200), height=height)
        editor.bind("<<ComboboxSelected>>", lambda _: self.apply_type(number, editor.get()))
        editor.bind("<Escape>", lambda _: self.close_editor())
        editor.bind("<FocusOut>", lambda _: self.after(150, self.close_editor))
        editor.focus_set()
        self.editor = editor
        return "break"

    def apply_type(self, number, text):
        code = int(text.split(":", 1)[0])
        ok, message = usb_mapper.set_port_type(self.controllers(), number, code)
        self.session.usb_dirty = self.session.usb_dirty or ok
        self.redraw()
        self.set_label(self.message, message, None if ok else "red")

    def close_editor(self):
        if self.editor is not None:
            try:
                self.editor.destroy()
            except tk.TclError:
                pass
            self.editor = None

    def save(self):
        if self.session.usb_history is None:
            self.set_label(self.message, "Press Refresh first to list the ports.", "orange")
            return
        path, errors = self.session.save_usb_map()
        if errors:
            self.set_label(self.message, "\n".join(errors), "red")
            return
        self.no_map.set(False)
        self.set_label(self.message, "USB map saved to {}. The next build turns it into UTBMap.kext.".format(path), "green")

    def toggle_no_map(self):
        self.session.set_usb_map_disabled(bool(self.no_map.get()))


class BuildPage(Page):
    title = "Build"
    heading = "Build the OpenCore EFI"

    def build(self):
        self.summary = self.note(self.body, "", 0)
        bar = ttk.Frame(self.body)
        bar.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        self.run_button = ttk.Button(bar, text="Build EFI", command=self.run)
        self.run_button.grid(row=0, column=0)
        self.steps_frame = ttk.Frame(self.body)
        self.steps_frame.grid(row=2, column=0, sticky="w")
        self.step_labels = []
        self.result = ttk.Label(self.body, text="", font=widgets.bold_font(self))
        self.result.grid(row=3, column=0, sticky="w", pady=(8, 6))
        frame, self.notes = widgets.scrolled_text(self.body, height=10)
        frame.grid(row=4, column=0, sticky="nsew")
        self.body.rowconfigure(4, weight=1)

    def on_show(self):
        config = self.session.configuration()
        if not config:
            self.set_label(self.summary, "Choose the macOS version and options first (Options page, Next).", "orange")
            self.run_button.state(["disabled"])
            return
        self.run_button.state(["!disabled"])
        ocpe = self.session.ocpe
        try:
            source = ocpe.select_usb_map_source(self.session.customized_hardware or self.session.hardware_report, announce=False)
            usb = "USB map: {} will be used.".format(source["path"]) if source else "USB map: none. {}".format(ocpe.usb_map_reason)
        except Exception as e:
            usb = "USB map: {}".format(e)
        tag = None
        if self.session.usb_dirty:
            usb += " The USB mapping page has changes that are not saved yet; they will not be in this build. Go back and press Save map first if you want them."
            tag = "orange"
        self.set_label(self.summary, "{} with {}, {} build. {}".format(config["macos_name"], config["smbios_model"], "DEBUG" if config["debug"] else "RELEASE", usb), tag)

    def run(self):
        self.set_label(self.result, "")
        widgets.set_text(self.notes, "")
        self.on_progress("", [], -1, False)
        self.app.run_stage("Building the EFI", self.session.build, on_done=self.show_result)

    def on_progress(self, title, steps, index, done):
        for label in self.step_labels:
            label.destroy()
        self.step_labels = []
        for position, step in enumerate(steps):
            if done or position < index:
                text, tag = "[done] {}".format(step), "green"
            elif position == index:
                text, tag = "[....] {}".format(step), None
            else:
                text, tag = "[    ] {}".format(step), "grey"
            label = ttk.Label(self.steps_frame, text=text, font=widgets.mono_font(self))
            if tag:
                label.configure(foreground=widgets.COLORS[tag])
            label.grid(row=position, column=0, sticky="w")
            self.step_labels.append(label)

    def show_result(self, result):
        text, tag = OCVALIDATE_TEXT.get(result["ocvalidate"], OCVALIDATE_TEXT["unavailable"])
        self.set_label(self.result, "EFI built in {}. {}".format(os.path.join(result["result_dir"], "EFI"), text), tag)
        widgets.set_text(self.notes, "")
        if result["usb_map"]:
            name = os.path.basename(os.path.normpath(result["usb_map"]))
            kext = name if name.lower().endswith(".kext") else usb_mapper.KEXT_NAME
            usb_lines = ["Your USB map was used: the port file {} is inside the EFI folder (EFI\\OC\\Kexts), so macOS will see only the ports you selected. "
                         "After installing, check that each of those ports works.".format(kext)]
        else:
            usb_lines = [result["usb_map_reason"] or "No USB map was used.",
                         "Without a map, the build falls back to a catch-all file (UTBDefault.kext) that turns on every USB port. "
                         "macOS accepts at most 15 ports per USB chip, so with every port on some may stay dead and sleep may fail. "
                         "To fix this, pick the ports on the USB mapping page, press Save map, then build again."]
        sections = [
            ("USB map", usb_lines),
            ("Windows preflight", result["preflight"] or ["No warnings."]),
            ("First boot", result["first_boot"]),
            ("DEBUG build", result["debug_notes"]),
            ("After installing macOS", result["post_install"])
        ]
        for title, lines in sections:
            if not lines:
                continue
            widgets.append_lines(self.notes, [title], "bold")
            widgets.append_lines(self.notes, lines, "red" if title == "Windows preflight" and result["preflight"] else None, "  - ")
        self.app.update_nav()


class RecoveryPage(Page):
    title = "Recovery"
    heading = "Download the macOS recovery image"
    explains = ("recovery",)

    def build(self):
        self.version = self.note(self.body, "", 0)
        row = ttk.Frame(self.body)
        row.grid(row=1, column=0, sticky="w", pady=(0, 6))
        ttk.Label(row, text="Version:").grid(row=0, column=0, padx=(0, 6))
        self.choices = self.session.recovery_choices()
        self.combo = ttk.Combobox(row, state="readonly", width=32, values=[item["name"] for item in self.choices])
        self.combo.grid(row=0, column=1, padx=(0, 6))
        self.download_button = ttk.Button(row, text="Download", command=self.download)
        self.download_button.grid(row=0, column=2)
        self.state_label = self.note(self.body, "", 2)
        self.note(self.body, "The image comes from Apple's servers and is verified with its chunklist. Copy com.apple.recovery.boot next to the EFI folder on the USB drive; the USB page does this for you.", 3, tag="grey")

    def on_show(self):
        darwin = None
        if self.session.macos_version:
            darwin = int(str(self.session.macos_version).split(".")[0])
        elif self.app.page_by_class(MacOSPage).selected():
            darwin = self.app.page_by_class(MacOSPage).selected()
        names = [item["darwin"] for item in self.choices]
        if darwin in names:
            self.combo.current(names.index(darwin))
            self.set_label(self.version, "Recovery for the chosen version: {}.".format(macos_name(str(darwin))))
        else:
            if self.combo.current() < 0 and self.choices:
                self.combo.current(len(self.choices) - 1)
            self.set_label(self.version, "No macOS version chosen in this session; pick one below.", "orange")
        dmg = os.path.join(self.session.paths()["recovery"], "BaseSystem.dmg")
        if os.path.isfile(dmg):
            self.set_label(self.state_label, "A recovery image is already in {}.".format(self.session.paths()["recovery"]), "green")

    def download(self):
        index = self.combo.current()
        if index < 0:
            return
        darwin = self.choices[index]["darwin"]
        self.app.run_stage("Downloading macOS recovery", lambda: self.session.recovery(darwin), on_done=self.done, percent=True)

    def done(self, path):
        self.set_label(self.state_label, "macOS recovery files are ready at {}.".format(path), "green")


class UsbWriterPage(Page):
    title = "USB"
    heading = "Write the USB installer"
    explains = ("usb_disk", "usb_erase")

    def build(self):
        self.disks = []
        admin_row = ttk.Frame(self.body)
        admin_row.grid(row=0, column=0, sticky="ew")
        self.admin_label = ttk.Label(admin_row, text="")
        self.admin_label.grid(row=0, column=0, sticky="w")
        self.admin_button = ttk.Button(admin_row, text="Restart as administrator", command=self.restart_admin)
        self.admin_button.grid(row=0, column=1, padx=(12, 0))
        self.mode = tk.StringVar(value="format")
        modes = ttk.Frame(self.body)
        modes.grid(row=1, column=0, sticky="w", pady=(6, 4))
        ttk.Radiobutton(modes, text="Format a USB drive and write the installer", value="format", variable=self.mode, command=self.update_mode).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(modes, text="Copy the EFI to an existing USB drive", value="copy", variable=self.mode, command=self.update_mode).grid(row=1, column=0, sticky="w")

        self.format_frame = ttk.LabelFrame(self.body, text="Format and write", padding=(10, 6))
        self.format_frame.grid(row=2, column=0, sticky="nsew")
        self.format_frame.columnconfigure(0, weight=1)
        bar = ttk.Frame(self.format_frame)
        bar.grid(row=0, column=0, sticky="ew")
        ttk.Button(bar, text="Refresh", command=self.refresh).grid(row=0, column=0)
        self.payload = ttk.Label(bar, text="")
        self.payload.grid(row=0, column=1, padx=(12, 0))
        frame, self.table = widgets.tree(self.format_frame, ("disk", "name", "size", "style"), ("Disk", "Name", "Size", "Partition style"),
                                         {"disk": 60, "name": 360, "size": 100, "style": 120}, height=4)
        frame.grid(row=1, column=0, sticky="nsew", pady=(4, 4))
        self.format_frame.rowconfigure(1, weight=1)
        self.table.bind("<<TreeviewSelect>>", lambda _: self.update_state())
        self.details = ttk.Label(self.format_frame, text="Select a disk.", wraplength=760, justify="left")
        self.details.grid(row=2, column=0, sticky="w")
        confirm = ttk.Frame(self.format_frame)
        confirm.grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Label(confirm, text="Type ERASE to confirm:").grid(row=0, column=0, padx=(0, 6))
        self.erase = tk.StringVar()
        self.erase.trace_add("write", lambda *_: self.update_state())
        ttk.Entry(confirm, textvariable=self.erase, width=12).grid(row=0, column=1, padx=(0, 12))
        self.dry_run = tk.BooleanVar(value=False)
        ttk.Checkbutton(confirm, text="Dry run (change nothing)", variable=self.dry_run).grid(row=0, column=2, padx=(0, 12))
        self.write_button = ttk.Button(confirm, text="Write", command=self.write)
        self.write_button.grid(row=0, column=3)

        self.copy_frame = ttk.LabelFrame(self.body, text="Copy EFI", padding=(10, 6))
        self.copy_frame.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        ttk.Label(self.copy_frame, text="USB drive letter:").grid(row=0, column=0, padx=(0, 6))
        self.letter = tk.StringVar()
        self.letter.trace_add("write", lambda *_: self.update_state())
        ttk.Entry(self.copy_frame, textvariable=self.letter, width=4).grid(row=0, column=1, padx=(0, 12))
        self.copy_button = ttk.Button(self.copy_frame, text="Copy EFI", command=self.copy)
        self.copy_button.grid(row=0, column=2)
        self.result = self.note(self.body, "", 4)
        self.body.rowconfigure(2, weight=1)
        self.update_mode()

    def on_show(self):
        admin = winsupport.is_admin()
        if admin:
            self.set_label(self.admin_label, "Running as administrator.", "green")
            self.admin_button.grid_remove()
        else:
            self.set_label(self.admin_label, "Writing a USB drive needs administrator rights (a dry run does not).", "orange")
            self.admin_button.grid()
        paths = self.session.paths()
        efi = os.path.isfile(os.path.join(paths["efi"], "OC", "config.plist"))
        recovery = os.path.isfile(os.path.join(paths["recovery"], "BaseSystem.dmg"))
        kit = os.path.isfile(os.path.join(paths["postinstall"], "POST-INSTALL.md"))
        self.set_label(self.payload, "EFI: {}   Recovery: {}   Post-install kit: {}".format("yes" if efi else "missing", "yes" if recovery else "missing", "yes" if kit else "no"),
                       None if efi and recovery else "orange")
        self.update_state()

    def update_mode(self):
        copy = self.mode.get() == "copy"
        for child in self.format_frame.winfo_children():
            self._set_state(child, not copy)
        for child in self.copy_frame.winfo_children():
            self._set_state(child, copy)
        self.update_state()

    def _set_state(self, widget, enabled):
        try:
            widget.state(["!disabled"] if enabled else ["disabled"])
        except (AttributeError, tk.TclError):
            pass
        for child in widget.winfo_children():
            self._set_state(child, enabled)

    def selected_disk(self):
        selection = self.table.selection()
        if not selection:
            return None
        number = int(selection[0])
        return next((disk for disk in self.disks if disk["number"] == number), None)

    def update_state(self):
        disk = self.selected_disk()
        if disk:
            text = "Disk {}: {}, {:.1f} GB, {}. ALL DATA on this disk will be erased.".format(disk["number"], disk["friendly_name"], disk["size_bytes"] / 1e9, disk["partition_style"])
            self.details.configure(text=text, foreground=widgets.COLORS["red"])
        else:
            self.details.configure(text="Select a disk.", foreground="")
        busy = self.app.runner.busy()
        can_write = self.mode.get() == "format" and disk is not None and self.erase.get().strip() == "ERASE" and not busy
        self.write_button.state(["!disabled"] if can_write else ["disabled"])
        letter = self.letter.get().strip().rstrip(":\\/")
        can_copy = self.mode.get() == "copy" and len(letter) == 1 and letter.isalpha() and not busy
        self.copy_button.state(["!disabled"] if can_copy else ["disabled"])

    def refresh(self):
        self.app.run_stage("Listing USB disks", self.session.usb_disks, on_done=self.show_disks)

    def show_disks(self, result):
        self.disks = result["disks"]
        widgets.clear_tree(self.table)
        for disk in self.disks:
            self.table.insert("", "end", iid=str(disk["number"]), values=(disk["number"], disk["friendly_name"], "{:.1f} GB".format(disk["size_bytes"] / 1e9), disk["partition_style"]))
        if not self.disks:
            self.set_label(self.result, "No USB disks found. Insert a USB drive of 4 GB or more and press Refresh.", "orange")
        self.update_state()

    def write(self):
        disk = self.selected_disk()
        if not disk or self.erase.get().strip() != "ERASE":
            return
        dry_run = bool(self.dry_run.get())
        if not dry_run and not messagebox.askyesno("Erase disk", "Erase disk {} ({}) and write the installer?".format(disk["number"], disk["friendly_name"]), parent=self, icon="warning"):
            return
        erase = self.erase.get().strip()
        self.erase.set("")
        self.app.run_stage("Writing the USB installer", lambda: self.session.write_installer(disk, erase, dry_run), on_done=lambda _: self.finished("write"))

    def copy(self):
        letter = self.letter.get().strip().rstrip(":\\/").upper()
        self.app.run_stage("Copying the EFI", lambda: self.session.copy_efi(letter), on_done=lambda _: self.finished("copy"))

    def finished(self, kind):
        output = self.app.stage_text()
        if "Verification passed" in output:
            self.set_label(self.result, "Done: the files on the USB drive match the build.", "green")
        elif "Dry run finished" in output:
            self.set_label(self.result, "Dry run finished, nothing was changed.", "green")
        else:
            self.set_label(self.result, "Not completed. The log shows why.", "red")
        self.update_state()

    def restart_admin(self):
        if winsupport.relaunch_as_admin():
            self.app.on_close(force=True)


class PostInstallPage(Page):
    title = "Post-install"
    heading = "Post-install kit and BIOS checklist"
    explains = ("postinstall_kit",)

    def build(self):
        bar = ttk.Frame(self.body)
        bar.grid(row=0, column=0, sticky="ew")
        ttk.Button(bar, text="Prepare full kit (downloads tools)", command=lambda: self.prepare("full")).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(bar, text="Prepare docs-only kit", command=lambda: self.prepare("docs-only")).grid(row=0, column=1, padx=(0, 6))
        self.state_label = ttk.Label(bar, text="")
        self.state_label.grid(row=0, column=2, padx=(12, 0))
        panes = ttk.Panedwindow(self.body, orient="vertical")
        panes.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        self.body.rowconfigure(1, weight=1)
        frame, self.checklist = widgets.scrolled_text(panes, height=10, mono=True)
        panes.add(frame, weight=3)
        lists = ttk.Frame(panes)
        panes.add(lists, weight=2)
        self.bios = {}
        for column, (key, title) in enumerate((("disable", "BIOS: Disable"), ("enable", "BIOS: Enable"), ("notes", "BIOS: Notes"))):
            lists.columnconfigure(column, weight=1)
            ttk.Label(lists, text=title, font=widgets.bold_font(self)).grid(row=0, column=column, sticky="w", pady=(4, 2))
            frame, text = widgets.scrolled_text(lists, height=7, width=30)
            frame.grid(row=1, column=column, sticky="nsew", padx=(0, 6))
            self.bios[key] = text
        lists.rowconfigure(1, weight=1)

    def on_show(self):
        path = os.path.join(self.session.paths()["postinstall"], "POST-INSTALL.md")
        if os.path.isfile(path) and not self.session.kit_result:
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    widgets.set_text(self.checklist, handle.read())
                self.set_label(self.state_label, "Kit in {}".format(os.path.dirname(path)), "green")
            except OSError:
                pass
        self.show_bios(self.session.bios_requirements())

    def show_bios(self, bios):
        for key, text in self.bios.items():
            widgets.set_text(text, "")
            if bios is None:
                widgets.append_lines(text, ["Load a hardware report and configure the build to see this list."], "grey")
                continue
            widgets.append_lines(text, bios.get(key) or ["(none)"], None, "- ")

    def prepare(self, mode):
        self.app.run_stage("Preparing the post-install kit", lambda: self.session.post_install_kit(mode), on_done=self.show_kit, percent=(mode == "full"))

    def show_kit(self, result):
        widgets.set_text(self.checklist, result.get("checklist") or "")
        failed = [name for name, state in (result.get("tools") or {}).items() if state == "failed"]
        text = "Kit ready in {}".format(result.get("dest_dir"))
        if failed:
            text += "; not downloaded: {}".format(", ".join(failed))
        self.set_label(self.state_label, text, "orange" if failed else "green")
        if result.get("bios") is not None:
            self.show_bios(result["bios"])


class FinishPage(Page):
    title = "Finish"
    heading = "Finished: where everything is"

    def build(self):
        self.rows = ttk.Frame(self.body)
        self.rows.grid(row=0, column=0, sticky="ew")
        self.rows.columnconfigure(1, weight=1)
        ttk.Label(self.body, text="What happens next", font=widgets.bold_font(self)).grid(row=1, column=0, sticky="w", pady=(12, 2))
        frame, self.steps = widgets.scrolled_text(self.body, height=9)
        frame.grid(row=2, column=0, sticky="nsew")
        widgets.append_lines(self.steps, ["{}. {}".format(number, step) for number, step in enumerate(install_steps, 1)])
        ttk.Label(self.body, text="Analyze a boot log", font=widgets.bold_font(self)).grid(row=3, column=0, sticky="w", pady=(12, 2))
        self.note(self.body, "A DEBUG EFI writes opencore-YYYY-MM-DD-hhmmss.txt to the root of the EFI partition it booted from. Pick that file, or the USB drive to use its newest log.", 4)
        bar = ttk.Frame(self.body)
        bar.grid(row=5, column=0, sticky="w")
        ttk.Button(bar, text="Open log file...", command=self.open_log).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(bar, text="Choose drive or folder...", command=self.open_folder_log).grid(row=0, column=1)
        self.verdict = ttk.Label(self.body, text="", font=widgets.bold_font(self), wraplength=760, justify="left")
        self.verdict.grid(row=6, column=0, sticky="w", pady=(6, 4))
        frame, self.findings = widgets.tree(self.body, ("severity", "line", "cause", "fix"), ("Severity", "Line", "Cause", "Fix"),
                                            {"severity": 80, "line": 60, "cause": 340, "fix": 380}, height=5)
        frame.grid(row=7, column=0, sticky="nsew")
        frame, self.report = widgets.scrolled_text(self.body, height=8, mono=True)
        frame.grid(row=8, column=0, sticky="nsew", pady=(6, 0))
        self.body.rowconfigure(2, weight=1)
        self.body.rowconfigure(8, weight=1)

    def on_show(self):
        for child in self.rows.winfo_children():
            child.destroy()
        paths = self.session.paths()
        entries = (
            ("Results folder", paths["results"]),
            ("OpenCore EFI", paths["efi"]),
            ("macOS recovery", paths["recovery"]),
            ("Post-install kit", paths["postinstall"]),
            ("Hardware report and USB map", paths["sysreport"])
        )
        for row, (title, path) in enumerate(entries):
            exists = os.path.exists(path)
            ttk.Label(self.rows, text=title, font=widgets.bold_font(self)).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=1)
            label = ttk.Label(self.rows, text=path if exists else "{} (not created yet)".format(path))
            if not exists:
                label.configure(foreground=widgets.COLORS["grey"])
            label.grid(row=row, column=1, sticky="w")
            button = ttk.Button(self.rows, text="Open", command=lambda target=path: winsupport.open_path(target))
            button.grid(row=row, column=2, sticky="e", pady=1)
            if not exists:
                button.state(["disabled"])

    def open_log(self):
        path = filedialog.askopenfilename(parent=self, title="Open an OpenCore log", filetypes=[("OpenCore log", "*.txt *.log"), ("All files", "*.*")])
        if path:
            self.analyze(path)

    def open_folder_log(self):
        path = filedialog.askdirectory(parent=self, title="Choose the USB drive or a folder with opencore-*.txt")
        if path:
            self.analyze(path)

    def analyze(self, location):
        self.app.run_stage("Analyzing the boot log", lambda: self.session.analyze_log(location), on_done=self.show_report)

    def show_report(self, report):
        fatal = any(finding.get("severity") == "fatal" for finding in report.get("findings") or [])
        good = report.get("picker_reached") and not fatal
        self.set_label(self.verdict, "Verdict: {}".format(report.get("verdict")), "green" if good else "orange")
        widgets.clear_tree(self.findings)
        for finding in report.get("findings") or []:
            self.findings.insert("", "end", values=(finding.get("severity", "").upper(), finding.get("line_number"), finding.get("cause"), finding.get("fix")),
                                 tags=("red",) if finding.get("severity") == "fatal" else ())
        widgets.set_text(self.report, report.get("text") or "")


PAGE_CLASSES = (WelcomePage, HardwarePage, MacOSPage, OptionsPage, UsbMapPage, BuildPage, RecoveryPage, UsbWriterPage, PostInstallPage, FinishPage)
