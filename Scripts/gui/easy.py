# The simple window: five steps in plain words for someone who has never heard of OpenCore.
# Every step does one thing with one button; the technical pages stay in Advanced mode.
import os
import tkinter as tk
from tkinter import messagebox
from tkinter import ttk

from Scripts import bios_checklist
from Scripts import postinstall_kit
from Scripts.gui import pages
from Scripts.gui import widgets
from Scripts.gui import winsupport
from Scripts.gui.session import PREPARE_STEPS
from Scripts.gui.session import PREPARE_TITLE
from Scripts.gui.session import UnsupportedHardware
from Scripts.gui.session import darwin_major
from Scripts.gui.session import macos_name

PLAIN_STEPS = {
    "Windows preflight": "Check Windows",
    "Hardware scan": "Look at the parts inside this PC",
    "Configuration": "Choose the right settings for this PC",
    "USB map": "Find the USB ports",
    "EFI build": "Download OpenCore and the drivers, and put the start-up files together",
    "macOS recovery": "Download the macOS installer from Apple (about 1 GB)",
    "Post-install kit": "Add the after-install guide and helper"
}
SECTIONS = {
    "CPU": "Processor",
    "GPU": "Graphics",
    "Sound": "Sound",
    "Biometric": "Face or fingerprint sign-in",
    "Network": "Network",
    "Storage Controllers": "Disk controller",
    "Bluetooth": "Bluetooth",
    "SD Controller": "Card reader"
}
MISSING = {
    "Network": "macOS has no driver for it. Use another network port, or a USB network adapter that works with macOS.",
    "Sound": "No sound from it in macOS. Sound over HDMI or DisplayPort, or a USB headset, can still work.",
    "Storage Controllers": "Disks connected to it do not show up in macOS. Install macOS on a disk connected elsewhere.",
    "Bluetooth": "Bluetooth does not work in macOS.",
    "Biometric": "Not available in macOS.",
    "SD Controller": "The card reader does not work in macOS."
}
# where a setting usually is in the BIOS, matched by the first word that appears in the setting
BIOS_PLACES = (
    ("OS type", "On ASUS boards, under Boot, Secure Boot."),
    ("Secure Boot", "Usually under Boot or Security. On ASUS boards, set OS Type to Other OS."),
    ("CSM", "Usually under Boot. Also called Legacy boot."),
    ("UEFI mode", "Usually under Boot. Choose UEFI only, not Legacy."),
    ("Fast Boot", "Usually under Boot."),
    ("VT-d", "Usually under Advanced, CPU or Chipset."),
    ("IOMMU", "Usually under Advanced, AMD CBS."),
    ("VT-x", "Usually under Advanced, CPU. Also called Intel Virtualization Technology."),
    ("SVM", "Usually under Advanced, CPU."),
    ("Above 4G", "Usually under Advanced or PCI settings."),
    ("Resizable BAR", "Next to Above 4G Decoding. Also called Smart Access Memory."),
    ("SATA Mode", "Usually under Advanced, Storage."),
    ("CFG Lock", "Often hidden. Skip it if you cannot find it."),
    ("DVMT", "Usually under Advanced, Graphics. Skip it if your BIOS does not have it."),
    ("Execute Disable", "Usually under Advanced, CPU."),
    ("Hyper-Threading", "Usually under Advanced, CPU."),
    ("SGX", "Usually under Advanced, CPU."),
    ("Serial", "Usually under Advanced, Super IO or Onboard Devices."),
    ("Parallel", "Usually under Advanced, Super IO or Onboard Devices."),
    ("Thunderbolt", "Usually under Advanced, Thunderbolt."),
    ("Hand-off","Usually under Advanced, USB. Also called XHCI Hand-off."),
    ("Platform Trust", "Usually under Advanced, PCH-FW or Trusted Computing.")
)
DONE = "✓"


def rich_text(parent, height=14):
    frame, text = widgets.scrolled_text(parent, height=height)
    background = ttk.Style(parent).lookup("TFrame", "background") or "SystemButtonFace"
    text.configure(relief="flat", borderwidth=0, padx=2, pady=2, background=background, font=widgets.sized_font(parent, 1), spacing1=2, spacing3=2)
    text.tag_configure("good", foreground=widgets.COLORS["green"], font=widgets.sized_font(parent, 4, bold=True), spacing3=8)
    text.tag_configure("bad", foreground=widgets.COLORS["red"], font=widgets.sized_font(parent, 4, bold=True), spacing3=8)
    text.tag_configure("warn", foreground=widgets.COLORS["orange"], font=widgets.sized_font(parent, 1, bold=True))
    text.tag_configure("heading", font=widgets.sized_font(parent, 2, bold=True), spacing1=12, spacing3=4)
    text.tag_configure("muted", foreground=widgets.COLORS["grey"])
    text.tag_configure("bullet", lmargin1=14, lmargin2=32)
    text.tag_configure("step", lmargin1=6, lmargin2=34, spacing1=6)
    text.tag_configure("note", lmargin1=34, lmargin2=34, foreground=widgets.COLORS["grey"])
    # the scroll bar only shows when the text is longer than the box
    scrollbar = next(child for child in frame.winfo_children() if isinstance(child, ttk.Scrollbar))

    def scrolled(first, last):
        if float(first) <= 0.0 and float(last) >= 1.0:
            scrollbar.grid_remove()
        else:
            scrollbar.grid()
        scrollbar.set(first, last)

    text.configure(yscrollcommand=scrolled)
    return frame, text


def show_blocks(text, blocks):
    # blocks: (kind, value); kind is good, bad, warn, heading, text, muted, bullets, steps or notes
    widgets.set_text(text, "")
    text.configure(state="normal")
    for kind, value in blocks:
        if kind == "bullets":
            for item in value:
                text.insert("end", "•  {}\n".format(item), ("bullet",))
        elif kind == "notes":
            for item in value:
                text.insert("end", "{}\n".format(item), ("note",))
        elif kind == "steps":
            for number, item in enumerate(value, 1):
                text.insert("end", "{}.  {}\n".format(number, item), ("step",))
        else:
            text.insert("end", value + "\n", () if kind == "text" else (kind,))
    text.configure(state="disabled")


def open_file(path):
    if path and os.path.exists(path):
        winsupport.open_path(path)


class EasyPage(pages.Page):
    intro = ""

    def __init__(self, app):
        ttk.Frame.__init__(self, app.page_host, padding=(24, 18))
        self.app = app
        self.session = app.session
        self.columnconfigure(0, weight=1)
        ttk.Label(self, text=self.heading or self.title, font=widgets.heading_font(self, 8)).grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.intro_label = ttk.Label(self, text=self.intro, font=widgets.sized_font(self, 1), wraplength=820, justify="left")
        self.intro_label.grid(row=1, column=0, sticky="w", pady=(0, 14))
        self.body = ttk.Frame(self)
        self.body.grid(row=2, column=0, sticky="nsew")
        self.body.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.bind("<Configure>", lambda event: self.intro_label.configure(wraplength=max(320, event.width - 60)), add="+")
        self.build()

    def button(self, parent, text, command, row, column=0):
        button = ttk.Button(parent, text=text, style="Express.TButton", command=command)
        button.grid(row=row, column=column, sticky="w")
        return button


class CheckPage(EasyPage):
    title = "Check this PC"
    heading = "Can this PC run macOS?"
    intro = "The app looks at the parts inside this PC and tells you which macOS it can run. Nothing on this PC is changed."

    def build(self):
        self.ran = False
        self.blocked = None
        self.run_button = self.button(self.body, "Check this PC", self.run, 0)
        if os.name != "nt":
            self.run_button.state(["disabled"])
        frame, self.result = rich_text(self.body)
        frame.grid(row=1, column=0, sticky="nsew", pady=(14, 0))
        self.body.rowconfigure(1, weight=1)

    def on_show(self):
        if not self.ran and self.app.autostart and os.name == "nt":
            self.run()

    def run(self):
        self.ran = True
        self.blocked = None
        show_blocks(self.result, [("muted", "Checking... this takes up to a minute.")])
        self.app.run_stage("Checking this PC", self.session.check_pc, on_done=self.show, on_error=self.failed)

    def failed(self, error, details):
        self.app.pages_changed()
        if isinstance(error, UnsupportedHardware):
            self.blocked = str(error)
            reason = str(error).replace("Nothing can be installed: ", "")
            show_blocks(self.result, [("bad", "This PC cannot run macOS."), ("text", reason[:1].upper() + reason[1:]),
                                      ("muted", "Windows on this PC is not affected. The Hardware page in Advanced mode lists every part.")])
        else:
            show_blocks(self.result, [("bad", "The check did not finish."), ("text", str(error)),
                                      ("muted", "Press Check this PC to try again. Details at the bottom right shows what happened.")])
        return True

    def show(self, result):
        self.app.pages_changed()
        show_blocks(self.result, verdict_blocks(self.session, result))

    def complete(self):
        return bool(self.session.hardware_report) and self.session.default_macos() is not None

    def hint(self):
        return "macOS cannot run on this PC." if self.blocked else "Press Check this PC."


def verdict_blocks(session, verdict):
    darwin = session.default_macos()
    choice = next((item for item in session.macos_choices() if item["darwin"] == darwin), None)
    blocks = [("good", "Yes. This PC can run {}.".format(macos_name(str(darwin))))]
    if choice and choice["oclp"]:
        blocks.append(("text", "Some parts need an extra step after the install. The guide on the USB stick explains it."))
    rows = verdict.get("rows") or []
    laptop = ((session.hardware_report or {}).get("Motherboard") or {}).get("Platform") == "Laptop"
    works = ["{}: {}".format(SECTIONS.get(row["section"], row["section"]), row["name"]) for row in rows if row["status"] == "supported"]
    extra = ["{}: {}".format(SECTIONS.get(row["section"], row["section"]), row["name"]) for row in rows if row["status"] == "oclp"]
    missing = [(row, missing_text(row, rows, session.hardware_report or {}, laptop)) for row in rows if row["status"] == "unsupported"]
    if works:
        blocks += [("heading", "Works in macOS"), ("bullets", works)]
    if extra:
        blocks += [("heading", "Works after an extra step once macOS is installed"), ("bullets", extra)]
    if missing:
        blocks.append(("heading", "Does not work in macOS"))
        for row, text in missing:
            blocks += [("bullets", ["{}: {}".format(SECTIONS.get(row["section"], row["section"]), row["name"])]), ("notes", [text])]
    warnings = verdict.get("preflight") or []
    if warnings:
        blocks += [("heading", "Good to know before you start"), ("bullets", warnings)]
    blocks += [("muted", ""), ("text", "Press Next to get the files ready.")]
    return blocks


def missing_text(row, rows, report, laptop):
    if row["section"] != "GPU":
        return MISSING.get(row["section"], "It does not work in macOS. Windows is not affected.")
    others = [item for item in rows if item["section"] == "GPU" and item["status"] in ("supported", "oclp")]
    if not others:
        return "macOS cannot use it."
    other = others[0]["name"]
    integrated = ((report.get("GPU") or {}).get(other) or {}).get("Device Type") == "Integrated GPU"
    if laptop:
        return "macOS switches it off and uses the {} for the screen. That is normal on laptops.".format(other)
    if integrated:
        return "macOS cannot use it. Connect your monitor to the video port on the motherboard instead; that port uses the {}.".format(other)
    return "macOS cannot use it. Connect your monitor to the {}.".format(other)


class PreparePage(EasyPage):
    title = "Get the files"
    heading = "Get the files ready"
    intro = ("The app picks the right settings for this PC and downloads what the USB stick needs: OpenCore, the program that starts macOS on a PC, "
             "and the macOS installer from Apple. This takes about 5 to 20 minutes, depending on your internet.")

    def build(self):
        self.generation = None
        self.choices = []
        row = ttk.Frame(self.body)
        row.grid(row=0, column=0, sticky="w", pady=(0, 12))
        ttk.Label(row, text="macOS version:", font=widgets.sized_font(self, 1)).grid(row=0, column=0, padx=(0, 8))
        self.combo = ttk.Combobox(row, state="readonly", width=52, font=widgets.sized_font(self, 1))
        self.combo.grid(row=0, column=1)
        self.combo.bind("<<ComboboxSelected>>", lambda _: self.version_changed())
        self.run_button = self.button(self.body, "Get the files ready", self.run, 1)
        self.steps_frame = ttk.Frame(self.body)
        self.steps_frame.grid(row=2, column=0, sticky="w", pady=(14, 0))
        self.step_labels = []
        frame, self.result = rich_text(self.body, height=8)
        frame.grid(row=3, column=0, sticky="nsew", pady=(10, 0))
        self.body.rowconfigure(3, weight=1)

    def on_show(self):
        if self.generation != self.app.generation:
            self.generation = self.app.generation
            self.fill_versions()
        if self.session.build_result:
            self.show_done()
        elif not self.app.runner.busy():
            self.on_progress(PREPARE_TITLE, self.session.chain_steps(PREPARE_STEPS, "full", True), -1, False)

    def fill_versions(self):
        self.choices = self.session.macos_choices()
        default = self.session.default_macos()
        names = []
        for item in self.choices:
            name = item["name"]
            if item["darwin"] == default:
                name += "  (recommended)"
            elif item["oclp"]:
                name += "  (needs an extra step after the install)"
            names.append(name)
        self.combo.configure(values=names)
        current = darwin_major(self.session.macos_version) or default
        positions = [item["darwin"] for item in self.choices]
        if current in positions:
            self.combo.current(positions.index(current))
        else:
            self.combo.set("")
        show_blocks(self.result, [])

    def selected(self):
        index = self.combo.current()
        return self.choices[index]["darwin"] if 0 <= index < len(self.choices) else None

    def version_changed(self):
        if self.session.configured and darwin_major(self.session.macos_version) != self.selected():
            self.session.reset_configuration()
            show_blocks(self.result, [("text", "You picked another version. Press Get the files ready again.")])
            self.app.pages_changed(configuration_only=True)

    def run(self):
        darwin = self.selected()
        show_blocks(self.result, [])
        self.app.run_stage(PREPARE_TITLE, lambda: self.session.prepare(darwin), on_done=lambda _: self.show_done(), on_error=self.failed)

    def on_progress(self, title, steps, index, done):
        if title != PREPARE_TITLE:
            return
        for label in self.step_labels:
            label.destroy()
        self.step_labels = []
        for position, step in enumerate(steps):
            if done or position < index:
                text, color = "{}  {}".format(DONE, PLAIN_STEPS.get(step, step)), "green"
            elif position == index:
                text, color = "…  {}".format(PLAIN_STEPS.get(step, step)), None
            else:
                text, color = "    {}".format(PLAIN_STEPS.get(step, step)), "grey"
            label = ttk.Label(self.steps_frame, text=text, font=widgets.sized_font(self, 1))
            if color:
                label.configure(foreground=widgets.COLORS[color])
            label.grid(row=position, column=0, sticky="w", pady=1)
            self.step_labels.append(label)

    def show_done(self):
        self.app.update_nav()
        if self.complete():
            name = macos_name(self.session.macos_version)
            blocks = [("good", "Everything is ready."), ("text", "The files for {} on this PC are made. Next: copy them to a USB stick.".format(name))]
            if self.session.needs_oclp:
                blocks.append(("text", "Some parts of this PC need an extra step after the install. The guide on the USB stick explains it."))
            if self.session.kit_result:
                failed = [name for name, state in (self.session.kit_result.get("tools") or {}).items() if state == "failed"]
                if failed:
                    blocks.append(("muted", "Not downloaded (the guide links to them instead): {}.".format(", ".join(failed))))
        elif self.session.build_result and self.session.build_result["ocvalidate"] == "errors":
            blocks = [("bad", "The start-up files did not pass their check."),
                      ("text", "Try another macOS version above, or open Advanced mode to see the problem on the Build page.")]
        else:
            blocks = [("bad", "Not finished."), ("text", "Press Get the files ready again.")]
        show_blocks(self.result, blocks)

    def failed(self, error, details):
        self.app.pages_changed(configuration_only=True)
        show_blocks(self.result, [("bad", "Something went wrong."), ("text", str(error)),
                                  ("muted", "Check the internet connection and press Get the files ready again. Details at the bottom right shows what happened.")])
        return True

    def complete(self):
        session = self.session
        return session.build_ready() and session.recovery_ready() and bool(session.kit_for) and session.kit_for[0] == session.build_serial

    def hint(self):
        return "Press Get the files ready."


class UsbPage(EasyPage):
    title = "USB stick"
    heading = "Make the USB stick"
    intro = "Plug in a USB stick of 4 GB or more. Everything on it is deleted, so use one with nothing on it you want to keep."

    def build(self):
        self.disks = []
        self.listed = False
        self.write_failed = False
        self.admin_frame = ttk.Frame(self.body)
        self.admin_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(self.admin_frame, text="Making the stick needs administrator rights. Restarting starts the app again from the first step.",
                  foreground=widgets.COLORS["orange"], font=widgets.sized_font(self, 1), wraplength=600, justify="left").grid(row=0, column=0, sticky="w")
        ttk.Button(self.admin_frame, text="Restart with administrator rights", command=self.restart_admin).grid(row=0, column=1, padx=(12, 0))

        sticks = ttk.Frame(self.body)
        sticks.grid(row=1, column=0, sticky="ew")
        sticks.columnconfigure(0, weight=1)
        self.stick_list = ttk.Frame(sticks)
        self.stick_list.grid(row=0, column=0, sticky="w")
        ttk.Button(sticks, text="Look again", command=self.refresh).grid(row=0, column=1, sticky="ne")
        self.choice = tk.IntVar(value=-1)

        self.agree = tk.BooleanVar(value=False)
        ttk.Checkbutton(self.body, text="I understand that everything on this USB stick will be deleted.", variable=self.agree,
                        command=self.update_state).grid(row=2, column=0, sticky="w", pady=(14, 10))
        self.write_button = self.button(self.body, "Make the USB stick", self.write, 3)
        frame, self.result = rich_text(self.body, height=8)
        frame.grid(row=4, column=0, sticky="nsew", pady=(12, 0))
        self.body.rowconfigure(4, weight=1)

    def on_show(self):
        if winsupport.is_admin():
            self.admin_frame.grid_remove()
        else:
            self.admin_frame.grid()
        if self.session.usb_ready():
            show_blocks(self.result, [("good", "The USB stick is ready."), ("text", "Press Next.")])
        if not self.listed and os.name == "nt" and self.app.autostart:
            self.refresh()
        self.update_state()

    def refresh(self):
        self.listed = True
        self.app.run_stage("Looking for USB sticks", self.session.usb_disks, on_done=self.show_disks)

    def show_disks(self, result):
        self.disks = result["disks"]
        for child in self.stick_list.winfo_children():
            child.destroy()
        for disk in self.disks:
            ttk.Radiobutton(self.stick_list, text="{}   ({:.0f} GB)".format(disk["friendly_name"], disk["size_bytes"] / 1e9), value=disk["number"],
                            variable=self.choice, command=self.update_state).grid(sticky="w", pady=2)
        numbers = [disk["number"] for disk in self.disks]
        if len(numbers) == 1:
            self.choice.set(numbers[0])
        elif self.choice.get() not in numbers:
            self.choice.set(-1)
        if not self.disks:
            ttk.Label(self.stick_list, text="No USB stick found. Plug one in, then press Look again.", foreground=widgets.COLORS["orange"],
                      font=widgets.sized_font(self, 1)).grid(sticky="w")
        self.update_state()

    def selected_disk(self):
        return next((disk for disk in self.disks if disk["number"] == self.choice.get()), None)

    def update_state(self):
        ready = self.selected_disk() is not None and self.agree.get() and not self.app.runner.busy() and winsupport.is_admin()
        self.write_button.state(["!disabled"] if ready else ["disabled"])

    def write(self):
        disk = self.selected_disk()
        if not disk or not self.agree.get():
            return
        name = "{} ({:.0f} GB)".format(disk["friendly_name"], disk["size_bytes"] / 1e9)
        if not messagebox.askyesno("Make the USB stick", "Delete everything on {} and make the macOS USB stick?".format(name), parent=self, icon="warning"):
            return
        self.write_failed = False
        show_blocks(self.result, [("muted", "Making the USB stick. This takes about 5 to 10 minutes. Do not unplug it.")])
        self.app.run_stage("Making the USB stick", lambda: self.session.write_installer(disk, "ERASE", False, "full"), on_done=self.finished, on_error=self.write_error)

    def finished(self, result):
        self.write_failed = not result.get("written")
        if result.get("written"):
            show_blocks(self.result, [("good", "The USB stick is ready."), ("text", "Every file on it was checked against the originals. Press Next.")])
        else:
            self.show_failure()
        self.update_state()
        self.app.update_nav()

    def write_error(self, error, details):
        self.write_failed = True
        self.show_failure(str(error))
        self.update_state()
        return True

    def show_failure(self, error=None):
        blocks = [("bad", "The USB stick could not be made."),
                  ("text", "Press Make the USB stick to try again. If it fails again, try another USB port or another stick.")]
        reason = [error] if error else writer_reason(self.app.stage_text())
        if reason:
            blocks += [("heading", "What the stick writer said"), ("notes", reason)]
        show_blocks(self.result, blocks)

    def restart_admin(self):
        if winsupport.relaunch_as_admin(["--elevated"]):
            self.app.on_close(force=True)

    def complete(self):
        return self.session.usb_ready()

    def hint(self):
        if not winsupport.is_admin():
            return "Restart with administrator rights first."
        if self.selected_disk() is None:
            return "Plug in a USB stick and choose it."
        if not self.agree.get():
            return "Tick the box, then press Make the USB stick."
        if self.write_failed:
            return "The last try did not work. Press Make the USB stick to try again."
        return "Press Make the USB stick."


def writer_reason(text, count=8):
    # the last lines the writer printed: where it stopped, and diskpart's own message
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip() and not set(line.strip()) <= set("-=#*. ")]
    return lines[-count:]


class BiosPage(EasyPage):
    title = "BIOS"
    heading = "Change a few BIOS settings"
    intro = "The BIOS holds this PC's start-up settings. A few of them have to be set once so the PC can start macOS. Windows keeps starting with these settings."

    def build(self):
        frame, self.text = rich_text(self.body)
        frame.grid(row=0, column=0, sticky="nsew")
        self.body.rowconfigure(0, weight=1)
        self.confirmed = tk.BooleanVar(value=False)
        ttk.Checkbutton(self.body, text="I checked these settings in the BIOS (or they were already right).", variable=self.confirmed,
                        command=self.confirm).grid(row=1, column=0, sticky="w", pady=(12, 0))

    def on_show(self):
        self.confirmed.set(self.session.bios_confirmed)
        show_blocks(self.text, bios_blocks(self.session))

    def confirm(self):
        self.session.bios_confirmed = bool(self.confirmed.get())
        self.app.update_nav()

    def complete(self):
        return self.session.bios_confirmed

    def hint(self):
        return "Change the settings, then tick the box."


def bios_blocks(session):
    keys = session.firmware_keys()
    result = session.build_result or {}
    bios = result.get("bios") or session.bios_requirements() or {}
    state = result.get("bios_state")
    blocks = []
    if any("BitLocker" in warning for warning in session.preflight_warnings):
        blocks += [("warn", "First: save your BitLocker recovery key."),
                   ("text", "BitLocker protects the Windows disk. After BIOS changes, Windows can ask for its recovery key. Save the key now: "
                            "Settings, Privacy & security, Device encryption (or BitLocker), Back up your recovery key.")]
    blocks += [("heading", "How to open the BIOS"),
               ("steps", ["Restart the PC.",
                          "As soon as the screen lights up, press {} again and again until the BIOS opens.".format(keys["setup"]),
                          "Change the settings below. If a setting is not there, skip it; every BIOS looks a little different.",
                          "Choose Save and Exit (often the F10 key). The PC restarts into Windows as usual."])]
    if state and not state.get("other_pc"):
        change = [item for item in state["items"] if item["status"] == "change"]
        unknown = [item for item in state["items"] if item["status"] == "unknown"]
        fine = [item for item in state["items"] if item["status"] == "ok"]
        if change:
            blocks.append(("heading", "Change these"))
            for item in change:
                blocks += [("bullets", [setting_line(item["setting"], item["want"])]), ("notes", [place(item["setting"])])]
        if unknown:
            blocks.append(("heading", "Check these too (Windows cannot see them)"))
            for item in unknown:
                blocks += [("bullets", [setting_line(item["setting"], item["want"])]), ("notes", [place(item["setting"])])]
        if fine:
            blocks += [("heading", "Already right"), ("muted", ", ".join(bios_checklist.split_setting(item["setting"], item["want"])[0] for item in fine) + ".")]
        if not change and not unknown:
            blocks.append(("good", "Nothing to change."))
    else:
        items = [(setting, "off") for setting in bios.get("disable") or []] + [(setting, "on") for setting in bios.get("enable") or []]
        if items:
            blocks.append(("heading", "Set these"))
            for setting, want in items:
                blocks += [("bullets", [setting_line(setting, want)]), ("notes", [place(setting)])]
    return blocks


def setting_line(setting, want):
    name, value = bios_checklist.split_setting(setting, want)
    return "{}: {}".format(name, value)


def place(setting):
    return next((text for word, text in BIOS_PLACES if word.lower() in setting.lower()), "Look through the tabs of the BIOS.")


class InstallPage(EasyPage):
    title = "Install macOS"
    heading = "Install macOS"
    intro = "Everything is ready. The PC restarts during the install and this window closes, so take a photo of these steps first, or open them as a page to print."

    def build(self):
        bar = ttk.Frame(self.body)
        bar.grid(row=0, column=0, sticky="w", pady=(0, 10))
        ttk.Button(bar, text="Open these steps as a page", command=self.open_steps).grid(row=0, column=0, padx=(0, 8))
        self.guide_button = ttk.Button(bar, text="Open the after-install guide", command=self.open_guide)
        self.guide_button.grid(row=0, column=1)
        frame, self.text = rich_text(self.body)
        frame.grid(row=1, column=0, sticky="nsew")
        self.body.rowconfigure(1, weight=1)

    def on_show(self):
        steps, problems = install_steps(self.session)
        blocks = []
        for number, (title, paragraphs) in enumerate(steps, 1):
            blocks.append(("heading", "{}. {}".format(number, title)))
            for paragraph in paragraphs:
                blocks.append(("bullets", paragraph) if isinstance(paragraph, list) else ("text", paragraph))
        blocks.append(("heading", "If something goes wrong"))
        for question, answer in problems:
            blocks += [("bullets", [question]), ("notes", [answer])]
        show_blocks(self.text, blocks)
        self.guide_button.state(["!disabled"] if self.guide_path() else ["disabled"])

    def guide_path(self):
        path = (self.session.kit_result or {}).get("guide")
        return path if path and os.path.isfile(path) else None

    def open_guide(self):
        open_file(self.guide_path())

    def open_steps(self):
        steps, problems = install_steps(self.session)
        path = os.path.join(self.session.paths()["results"], "Install macOS.html")
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(postinstall_kit.html_page("Install macOS", "The steps for this PC, made by OpenCore Wizard.", steps, problems))
        except OSError as e:
            messagebox.showerror("Install macOS", "Could not save the page: {}".format(e), parent=self)
            return
        open_file(path)


def install_steps(session):
    keys = session.firmware_keys()
    stick = (session.usb_disk or {}).get("friendly_name") or "your USB stick"
    name = macos_name(session.macos_version) or "macOS"
    steps = [
        ("Start from the USB stick", [
            "Shut down this PC. Leave the USB stick plugged in; on a desktop, a USB port on the back is the safest.",
            "Turn the PC on and press {} again and again until a list of drives appears.".format(keys["boot_menu"]),
            "Choose the entry that starts with UEFI and has the stick's name, for example \"UEFI: {}\".".format(stick)
        ]),
        ("Open the installer", [
            "A menu with icons appears: that is OpenCore. It waits for you. Choose the entry with \"(dmg)\" in its name, such as \"Install {}\" or \"macOS Base System\", with the arrow keys and press Return.".format(name),
            "Lots of white text is normal. The Apple logo can take several minutes.",
            "Plug in a network cable if you can. The stick only holds a small installer; the rest of macOS is downloaded from Apple during the install."
        ]),
        ("Prepare the disk", [
            "Choose Disk Utility and press Continue. Click View at the top left, then Show All Devices.",
            "On the left, select the disk for macOS. Check its name and size: everything on it is deleted.",
            "Click Erase. Name: Macintosh HD. Format: APFS. Scheme: GUID Partition Map. Click Erase, then Done, and close Disk Utility.",
            "Do not choose the disk Windows is on, unless you want to delete Windows. Sharing one disk with Windows needs preparing in Windows first (Advanced mode, Finish page)."
        ]),
        ("Install", [
            "Choose Reinstall {} and pick Macintosh HD. It takes 30 to 60 minutes.".format(name),
            "The PC restarts several times. Each time, press {} again, choose the USB stick, and in the OpenCore menu choose \"macOS Installer\" (at the end: \"Macintosh HD\").".format(keys["boot_menu"])
        ]),
        ("Finish", [
            "Go through the setup screens. Skip signing in with your Apple Account for now.",
            "On the desktop, open the USB stick (OPENCORE) in Finder, then the PostInstall folder, and open START HERE.html. It walks you through the last steps."
        ])
    ]
    problems = [
        ("Windows starts instead", "The PC did not start from the stick. Restart, press {} right after turning it on, and choose the UEFI entry with the stick's name. "
                                   "If the stick is not in that list, see below.".format(keys["boot_menu"])),
        ("The screen stays black, or the PC restarts by itself", "Take a photo of the last lines on the screen. Unplug the stick: Windows starts as before, nothing on this PC has changed."),
        ("The USB stick is not in the list", "Try another USB port, on the back of the PC if it has them. Check that Fast Boot is off in the BIOS."),
        ("Disk Utility shows no disk for macOS", "In the BIOS, set SATA Mode to AHCI and turn Intel VMD off, if your BIOS has them.")
    ]
    if session.needs_oclp:
        problems.append(("A part does not work after the install", "That is expected here: START HERE.html on the stick has the extra step for it."))
    return steps, problems


PAGE_CLASSES = (CheckPage, PreparePage, UsbPage, BiosPage, InstallPage)
