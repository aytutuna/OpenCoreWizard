import math
import os
import queue
import sys
import tkinter as tk
from tkinter import filedialog
from tkinter import messagebox
from tkinter import ttk

from Scripts import app_paths
from Scripts import utils
from Scripts.datasets.explanations import decisions
from Scripts.datasets.explanations import glossary
from Scripts.gui import broker as broker_module
from Scripts.gui import easy
from Scripts.gui import pages
from Scripts.gui import widgets
from Scripts.gui import winsupport
from Scripts.gui.session import EXPRESS_STEPS
from Scripts.gui.session import EXPRESS_TITLE
from Scripts.gui.session import Session
from Scripts.gui.session import StageError

APP_TITLE = "OpenCore Wizard"
POLL_MS = 50
STAGE_OUTPUT_LIMIT = 20000
LOG_LINE_LIMIT = 5000
DETAILS_NAME = "OpenCoreWizard-details.log"
EXPRESS_PAGES = dict(zip(EXPRESS_STEPS, ("Welcome", "Hardware", "Options", "USB mapping", "Build", "Recovery", "USB")))
# the simple window is what opens; Advanced mode has every page and choice
MODES = {"easy": easy.PAGE_CLASSES, "advanced": pages.PAGE_CLASSES}


class PromptDialog(tk.Toplevel):
    def __init__(self, app, request):
        super().__init__(app)
        self.app = app
        self.request = request
        self.answered = False
        self.title("{}: answer needed".format(APP_TITLE))
        self.transient(app)
        self.resizable(True, True)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        ttk.Label(self, text=request.prompt.strip(), font=widgets.bold_font(self), wraplength=760, justify="left").grid(row=0, column=0, sticky="w", padx=12, pady=(12, 6))
        decision = decisions.get(request.decision) if request.decision else None
        self.explanation = None
        if decision:
            frame, self.explanation = widgets.scrolled_text(self, height=8, width=96)
            frame.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 6))
            self.explain(decision)
        frame, text = widgets.scrolled_text(self, height=8 if decision else 16, width=96, mono=True, wrap="none")
        frame.grid(row=2, column=0, sticky="nsew", padx=12)
        widgets.set_text(text, broker_module.strip_ansi(request.captured).strip("\n"))
        text.see("end")
        # with buttons to choose from, the tool output behind the question is only noise
        self.output = frame
        if request.choices:
            frame.grid_remove()

        if request.choices:
            buttons = ttk.Frame(self)
            buttons.grid(row=3, column=0, sticky="ew", padx=12, pady=(8, 0))
            # at most four rows of buttons, so the answer field stays on screen
            columns = 1 if len(request.choices) <= 2 else max(3, math.ceil(len(request.choices) / 4))
            for index, (label, value) in enumerate(request.choices):
                ttk.Button(buttons, text=label, command=lambda answer=value: self.submit(answer)).grid(row=index // columns, column=index % columns, sticky="ew", padx=(0, 6), pady=2)
            for column in range(columns):
                buttons.columnconfigure(column, weight=1)

        entry_row = ttk.Frame(self)
        entry_row.grid(row=4, column=0, sticky="ew", padx=12, pady=(8, 12))
        entry_row.columnconfigure(1, weight=1)
        ttk.Label(entry_row, text="Answer:").grid(row=0, column=0, padx=(0, 6))
        self.answer = tk.StringVar()
        entry = ttk.Entry(entry_row, textvariable=self.answer)
        entry.grid(row=0, column=1, sticky="ew")
        ttk.Button(entry_row, text="Submit", command=lambda: self.submit(self.answer.get())).grid(row=0, column=2, padx=(6, 0))
        ttk.Button(entry_row, text="Cancel stage", command=self.cancel).grid(row=0, column=3, padx=(6, 0))
        if request.choices:
            ttk.Button(entry_row, text="Details", command=lambda: self.output.grid()).grid(row=0, column=4, padx=(6, 0))
        hint = "Press Enter to submit."
        if request.default:
            hint += " An empty answer uses the default ({}).".format(request.default)
        ttk.Label(entry_row, text=hint, foreground=widgets.COLORS["grey"]).grid(row=1, column=0, columnspan=5, sticky="w", pady=(4, 0))

        entry.bind("<Return>", lambda _: self.submit(self.answer.get()))
        self.bind("<Escape>", lambda _: self.cancel())
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        entry.focus_set()
        self.update_idletasks()
        self.geometry("+{}+{}".format(app.winfo_rootx() + 60, app.winfo_rooty() + 60))
        try:
            self.grab_set()
        except tk.TclError:
            pass

    def explain(self, decision):
        text = self.explanation
        text.tag_configure("option", lmargin1=16, lmargin2=32)
        text.configure(state="normal")
        text.insert("end", decision["title"] + "\n", ("bold",))
        text.insert("end", "What: {}\nWhy: {}\n".format(decision["what"], decision["why"]))
        for value, label, when in decision["options"]:
            text.insert("end", "{}: {}. {}\n".format(value, label, when), ("option",))
        text.insert("end", "Default: " + decision["default"])
        text.configure(state="disabled")

    def submit(self, value):
        if self.answered:
            return
        self.answered = True
        self.request.reply(value)
        self.close()

    def cancel(self):
        if self.answered:
            return
        self.answered = True
        self.app.cancel_stage()
        self.request.cancel()
        self.close()

    def close(self):
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
        if self in self.app.dialogs:
            self.app.dialogs.remove(self)


class GlossaryWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.title("{}: what the words mean".format(APP_TITLE))
        self.transient(app)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        frame, self.text = widgets.scrolled_text(self, height=24, width=90)
        frame.grid(row=0, column=0, sticky="nsew", padx=12, pady=(12, 6))
        self.text.configure(state="normal")
        for term, meaning in glossary.items():
            self.text.insert("end", term + "\n", ("bold",))
            self.text.insert("end", meaning + "\n\n")
        self.text.configure(state="disabled")
        ttk.Button(self, text="Close", command=self.destroy).grid(row=1, column=0, sticky="e", padx=12, pady=(0, 12))
        self.bind("<Escape>", lambda _: self.destroy())
        self.geometry("+{}+{}".format(app.winfo_rootx() + 80, app.winfo_rooty() + 40))


class OpenCoreWizardApp(tk.Tk):
    def __init__(self, autostart=True, install_hooks=True, mode="easy", details_path=None):
        super().__init__()
        self.title(APP_TITLE)
        self.fit_window(1180, 820, 960, 680)
        self.autostart = autostart
        self.queue = queue.Queue()
        self.broker = broker_module.PromptBroker(self.queue.put)
        self.runner = broker_module.StageRunner(self.broker, self.queue.put)
        self.session = Session(preset_hook=self.broker.set_presets, defaults_hook=self.broker.set_accept_defaults)
        self.callbacks = {}
        self.dialogs = []
        self.generation = 0
        self.closing = False
        self.percent_progress = False
        self.stage_label = ""
        self.ansi = broker_module.AnsiStream()
        self.stage_output = ""
        self._pending_cr = False
        self._poll_id = None
        self._restore_output = None
        self.hooks_installed = False
        self.glossary = None

        self.mode = None
        self.page_sets = {}
        self.pages = []
        self.step_labels = []
        self.index = 0
        self.log_visible = False
        # what Details shows, also kept in a file, so a failure can be looked at after the window is closed
        self.details_file = None
        if details_path:
            try:
                self.details_file = open(details_path, "w", encoding="utf-8")
            except OSError:
                self.details_file = None

        self.setup_style()
        self.build_layout()
        if install_hooks:
            self.install_hooks()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.set_mode(mode)
        self._poll_id = self.after(POLL_MS, self.poll)

    def fit_window(self, width, height, min_width, min_height):
        try:
            factor = max(1.0, float(self.winfo_fpixels("1i")) / 96.0)
        except tk.TclError:
            factor = 1.0
        width = min(int(width * factor), max(640, self.winfo_screenwidth() - 40))
        height = min(int(height * factor), max(480, self.winfo_screenheight() - 80))
        self.geometry("{}x{}".format(width, height))
        self.minsize(min(int(min_width * factor), width), min(int(min_height * factor), height))

    def setup_style(self):
        self.style = ttk.Style(self)
        names = self.style.theme_names()
        for theme in ("vista", "clam"):
            if theme in names:
                self.style.theme_use(theme)
                break
        self.style.configure("Step.TLabel", padding=(14, 6))
        self.style.configure("CurrentStep.TLabel", padding=(14, 6), font=widgets.bold_font(self))
        self.style.configure("LockedStep.TLabel", padding=(14, 6), foreground=widgets.COLORS["grey"])
        self.style.configure("Express.TButton", padding=(16, 8), font=widgets.bold_font(self))

    def build_layout(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        main = ttk.Frame(self)
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)

        self.steps_frame = ttk.Frame(main, padding=(0, 12, 0, 0), relief="groove")
        self.steps_frame.grid(row=0, column=0, sticky="ns")

        panes = ttk.Panedwindow(main, orient="vertical")
        panes.grid(row=0, column=1, sticky="nsew")
        self.page_host = ttk.Frame(panes)
        self.page_host.columnconfigure(0, weight=1)
        self.page_host.rowconfigure(0, weight=1)
        panes.add(self.page_host, weight=4)

        # the output of the tools behind each step; hidden until Details is pressed
        self.panes = panes
        self.log_frame = ttk.Frame(panes, padding=(8, 4, 8, 0))
        self.log_frame.columnconfigure(0, weight=1)
        self.log_frame.rowconfigure(1, weight=1)
        header = ttk.Frame(self.log_frame)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Details", font=widgets.bold_font(self)).grid(row=0, column=0, sticky="w")
        ttk.Button(header, text="Save...", command=self.save_log).grid(row=0, column=1, padx=(0, 4))
        ttk.Button(header, text="Clear", command=self.clear_log).grid(row=0, column=2)
        frame, self.log = widgets.scrolled_text(self.log_frame, height=9, mono=True, wrap="char")
        frame.grid(row=1, column=0, sticky="nsew")

        nav = ttk.Frame(self, padding=(8, 6))
        nav.grid(row=1, column=0, sticky="ew")
        nav.columnconfigure(3, weight=1)
        self.cancel_button = ttk.Button(nav, text="Cancel", command=self.cancel_stage)
        self.cancel_button.grid(row=0, column=0)
        self.mode_button = ttk.Button(nav, text="Advanced mode", command=self.toggle_mode)
        self.mode_button.grid(row=0, column=1, padx=(6, 0))
        self.glossary_button = ttk.Button(nav, text="What do these words mean?", command=self.show_glossary)
        self.glossary_button.grid(row=0, column=2, sticky="w", padx=(6, 0))
        self.next_hint = ttk.Label(nav, text="", foreground=widgets.COLORS["orange"], wraplength=520, justify="right")
        self.next_hint.grid(row=0, column=3, sticky="e", padx=(6, 12))
        self.back_button = ttk.Button(nav, text="< Back", command=self.back)
        self.back_button.grid(row=0, column=4, padx=(0, 6))
        self.next_button = ttk.Button(nav, text="Next >", command=self.next)
        self.next_button.grid(row=0, column=5)

        status = ttk.Frame(self, padding=(8, 2, 8, 6))
        status.grid(row=2, column=0, sticky="ew")
        status.columnconfigure(0, weight=1)
        self.status = ttk.Label(status, text="Ready.")
        self.status.grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(status, length=260, mode="determinate", maximum=100)
        self.progress.grid(row=0, column=1, sticky="e")
        self.details_button = ttk.Button(status, text="Details", command=self.toggle_log)
        self.details_button.grid(row=0, column=2, padx=(8, 0))

    def toggle_log(self, show=None):
        show = not self.log_visible if show is None else bool(show)
        if show and not self.log_visible:
            self.panes.add(self.log_frame, weight=2)
        elif not show and self.log_visible:
            self.panes.forget(self.log_frame)
        self.log_visible = show
        self.details_button.configure(text="Hide details" if show else "Details")

    def set_mode(self, mode):
        if self.runner.busy() or mode == self.mode:
            return
        if mode not in self.page_sets:
            created = [page_class(self) for page_class in MODES[mode]]
            for page in created:
                page.grid(row=0, column=0, sticky="nsew")
            self.page_sets[mode] = created
        previous = self.mode
        self.mode = mode
        self.pages = self.page_sets[mode]
        self.index = 0
        self.build_steps()
        if previous and mode == "advanced":
            pages.sync_pages(self)
        self.mode_button.configure(text="Simple mode" if mode == "advanced" else "Advanced mode")
        if mode == "advanced":
            self.glossary_button.grid()
        else:
            self.glossary_button.grid_remove()
        # carry on where the work stands: the first step that is not done yet
        self.show_page(self.reachable() if previous else 0)

    def toggle_mode(self):
        self.set_mode("easy" if self.mode == "advanced" else "advanced")

    def build_steps(self):
        for label in self.step_labels:
            label.destroy()
        self.step_labels = []
        # a fixed width, so the list does not jump when the bold current step moves
        width = max(len(page.title) for page in self.pages) + 8
        for index, page in enumerate(self.pages):
            label = ttk.Label(self.steps_frame, text="{:>2}  {}".format(index + 1, page.title), style="Step.TLabel", cursor="hand2", width=width)
            label.grid(row=index, column=0, sticky="ew")
            label.bind("<Button-1>", lambda _, target=index: self.jump(target))
            self.step_labels.append(label)

    def install_hooks(self):
        if self.hooks_installed:
            return
        self._restore_output = utils.Utils.install_output_proxy()
        utils.Utils.set_output_sink(self.broker.sink)
        utils.Utils.set_prompt_handler(self.broker)
        utils.Utils.set_progress_handler(self.broker.progress)
        winsupport.hide_child_consoles()
        self.hooks_installed = True

    def uninstall_hooks(self):
        if not self.hooks_installed:
            return
        utils.Utils.set_prompt_handler(None)
        utils.Utils.set_progress_handler(None)
        utils.Utils.set_output_sink(None)
        if self._restore_output:
            self._restore_output()
            self._restore_output = None
        self.hooks_installed = False

    def page_by_class(self, page_class):
        return next(page for page in self.pages if isinstance(page, page_class))

    def show_page(self, index):
        index = max(0, min(index, len(self.pages) - 1))
        self.index = index
        page = self.pages[index]
        page.tkraise()
        self.render_steps()
        page.on_show()
        self.update_nav()

    def render_steps(self, running=None):
        reachable = self.reachable()
        for position, label in enumerate(self.step_labels):
            title = self.pages[position].title
            if running:
                marker, current = ("*", True) if title == running else (" ", False)
            else:
                marker, current = (">", True) if position == self.index else (" ", False)
            style = "CurrentStep.TLabel" if current else "Step.TLabel" if position <= reachable else "LockedStep.TLabel"
            label.configure(text="{} {:>2}  {}".format(marker, position + 1, title), style=style)

    def reachable(self):
        # the furthest step that may be opened: every step before it is done
        for position, page in enumerate(self.pages):
            if not page.complete():
                return position
        return len(self.pages) - 1

    def jump(self, index):
        if self.runner.busy() or index > max(self.index, self.reachable()):
            return
        self.show_page(index)

    def back(self):
        if not self.runner.busy() and self.index > 0:
            self.show_page(self.index - 1)

    def next(self):
        if self.runner.busy():
            return
        if self.index >= len(self.pages) - 1:
            self.on_close()
            return
        page = self.pages[self.index]
        if not page.can_next():
            return
        if page.go_next():
            self.advance()

    def advance(self):
        if self.index < len(self.pages) - 1:
            self.show_page(self.index + 1)

    def pages_changed(self, configuration_only=False):
        if not configuration_only:
            self.generation += 1
        self.update_nav()

    def update_nav(self):
        busy = self.runner.busy()
        page = self.pages[self.index]
        self.back_button.state(["!disabled"] if self.index > 0 and not busy else ["disabled"])
        last = self.index >= len(self.pages) - 1
        self.next_button.configure(text="Close" if last else "Next >")
        allowed = last or page.can_next()
        self.next_button.state(["!disabled"] if not busy and allowed else ["disabled"])
        self.next_hint.configure(text="" if busy or allowed else page.hint())
        if self.step_labels and not busy:
            self.render_steps()
        self.cancel_button.state(["!disabled"] if busy else ["disabled"])
        self.mode_button.state(["disabled"] if busy else ["!disabled"])
        if hasattr(page, "update_state"):
            page.update_state()

    def show_glossary(self):
        if self.glossary is not None and self.glossary.winfo_exists():
            self.glossary.lift()
        else:
            self.glossary = GlossaryWindow(self)
        return self.glossary

    def set_status(self, text):
        self.status.configure(text=text)

    def set_progress(self, mode, value=0):
        if mode == "indeterminate":
            if str(self.progress.cget("mode")) != "indeterminate":
                self.progress.configure(mode="indeterminate")
                self.progress.start(12)
        else:
            if str(self.progress.cget("mode")) == "indeterminate":
                self.progress.stop()
                self.progress.configure(mode="determinate")
            self.progress.configure(value=value)

    def run_stage(self, label, func, on_done=None, on_error=None, on_stopped=None, percent=False):
        if self.runner.busy():
            messagebox.showinfo(APP_TITLE, "Wait for the running step to finish, or cancel it.", parent=self)
            return None
        self.stage_output = ""
        self._pending_cr = False
        self.stage_label = label
        token = self.runner.start(label, func)
        self.callbacks[token] = (on_done, on_error, on_stopped)
        self.percent_progress = percent
        self.set_status("{}...".format(label))
        self.set_progress("indeterminate")
        self.update_nav()
        return token

    def stage_text(self):
        return broker_module.strip_ansi(self.stage_output)

    def cancel_stage(self):
        if self.runner.busy():
            if self.stage_label.startswith("Writing the USB") and not messagebox.askyesno(APP_TITLE, "Cancelling now can leave the USB drive blank or partly written. Cancel anyway?", parent=self, icon="warning"):
                return
            self.broker.cancel()
            self.set_status("Cancelling: the step stops at its next output or question...")

    def poll(self):
        self._poll_id = None
        chunks = []
        try:
            while True:
                try:
                    message = self.queue.get_nowait()
                except queue.Empty:
                    break
                if message[0] == "log":
                    chunks.append(message[1])
                    continue
                if chunks:
                    self.append_log("".join(chunks))
                    chunks = []
                self.dispatch(message)
            if chunks:
                self.append_log("".join(chunks))
        finally:
            if not self.closing:
                try:
                    self._poll_id = self.after(POLL_MS, self.poll)
                except tk.TclError:
                    pass

    def dispatch(self, message):
        kind = message[0]
        if kind == "progress":
            _, title, steps, index, done = message
            value = 100 if done or not steps else max(0, index) * 100.0 / len(steps)
            self.set_progress("determinate", value)
            if steps and 0 <= index < len(steps) and not done:
                self.set_status("{}: {}...".format(title, easy.PLAIN_STEPS.get(steps[index], steps[index])))
            self.pages[self.index].on_progress(title, steps, index, done)
            if title == EXPRESS_TITLE and not done and 0 <= index < len(steps):
                self.render_steps(EXPRESS_PAGES.get(steps[index]))
        elif kind == "prompt":
            request = message[1]
            if self.closing:
                request.cancel()
                return
            self.set_status("Waiting for your answer...")
            self.dialogs.append(PromptDialog(self, request))
        elif kind in ("done", "error", "stopped"):
            self.finish_stage(message)

    def finish_stage(self, message):
        kind, token, label = message[0], message[1], message[2]
        self.runner.finish(token)
        on_done, on_error, on_stopped = self.callbacks.pop(token, (None, None, None))
        for dialog in list(self.dialogs):
            dialog.answered = True
            dialog.close()
        self.set_progress("determinate", 100 if kind == "done" else 0)
        self.percent_progress = False
        if self.closing:
            return
        self.render_steps()
        self.after_idle(self.update_nav)
        if kind == "done":
            self.set_status("{}: done.".format(label))
            if on_done:
                on_done(message[3])
        elif kind == "stopped":
            reason, text = message[3], message[4]
            self.set_status("{}: {}.".format(label, "cancelled" if reason == "cancel" else "stopped"))
            if on_stopped:
                on_stopped(reason, text)
            elif reason != "cancel":
                messagebox.showwarning(APP_TITLE, "{} stopped: {}".format(label, text), parent=self)
        else:
            error, details = message[3], message[4]
            self.set_status("{}: failed.".format(label))
            if not isinstance(error, StageError):
                self.append_log("\n" + details)
            handled = on_error(error, details) if on_error else False
            if not handled:
                text = str(error) or error.__class__.__name__
                extra = getattr(error, "details", None) or []
                if extra:
                    text += "\n\n" + "\n".join("- {}".format(line) for line in extra[:15])
                if not isinstance(error, StageError):
                    text += "\n\nDetails, at the bottom right of the window, shows what happened."
                messagebox.showerror(APP_TITLE, "{} failed.\n\n{}".format(label, text), parent=self)

    def append_log(self, text):
        if not text:
            return
        self.stage_output = (self.stage_output + text)[-STAGE_OUTPUT_LIMIT:]
        if self.details_file:
            try:
                self.details_file.write(broker_module.strip_ansi(text))
                self.details_file.flush()
            except (OSError, ValueError):
                self.details_file = None
        self.log.configure(state="normal")
        for segment, tag in self.ansi.feed(text):
            parts = segment.split("\r")
            for position, part in enumerate(parts):
                if position > 0:
                    self._pending_cr = True
                if not part:
                    continue
                if self._pending_cr:
                    if not part.startswith("\n"):
                        self.log.delete("end-1c linestart", "end-1c")
                    self._pending_cr = False
                self.log.insert("end", part, (tag,) if tag else ())
        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > LOG_LINE_LIMIT:
            self.log.delete("1.0", "{}.0".format(lines - LOG_LINE_LIMIT))
        self.log.see("end")
        self.log.configure(state="disabled")
        if self.percent_progress:
            value = broker_module.last_percent(self.log.get("end-1c linestart", "end-1c"))
            if value is not None:
                self.set_progress("determinate", value)

    def clear_log(self):
        widgets.set_text(self.log, "")

    def save_log(self):
        path = filedialog.asksaveasfilename(parent=self, title="Save log", defaultextension=".txt", initialfile="OpenCoreWizard-log.txt", filetypes=[("Text", "*.txt")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(self.log.get("1.0", "end-1c"))

    def on_close(self, force=False):
        if self.runner.busy() and not force:
            if not messagebox.askyesno(APP_TITLE, "A step is still running. Cancel it and quit?", parent=self):
                return
        if self.runner.busy():
            self.broker.cancel()
        self.closing = True
        self.destroy()

    def destroy(self):
        self.closing = True
        if self._poll_id is not None:
            try:
                self.after_cancel(self._poll_id)
            except tk.TclError:
                pass
            self._poll_id = None
        self.uninstall_hooks()
        if self.details_file:
            self.details_file.close()
            self.details_file = None
        try:
            self.progress.stop()
        except tk.TclError:
            pass
        super().destroy()

    def self_test(self, duration_ms=2000):
        # every page of both modes, the current mode first
        visited = []
        modes = [self.mode] + [mode for mode in MODES if mode != self.mode]
        order = [(mode, index) for mode in modes for index in range(len(MODES[mode]))]
        step = max(50, duration_ms // (len(order) + 1))

        def visit(position):
            if self.closing:
                return
            mode, index = order[position]
            self.set_mode(mode)
            self.show_page(index)
            visited.append(self.pages[index].title)
            if position + 1 < len(order):
                self.after(step, lambda: visit(position + 1))
            else:
                self.after(step, self.destroy)

        self.after(10, lambda: visit(0))
        # safety net only; a slow page used to hit this before the last visit
        self.after(duration_ms * 5, self.destroy)
        self.mainloop()
        return visited


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # writing the USB drive needs administrator rights, and restarting later would lose the steps already done
    if "--self-test" not in argv and "--elevated" not in argv and os.name == "nt" and not winsupport.is_admin():
        if winsupport.relaunch_as_admin(["--elevated"]):
            return 0
    winsupport.enable_dpi_awareness()
    test = "--self-test" in argv
    app = OpenCoreWizardApp(autostart=not test, mode="advanced" if "--advanced" in argv else "easy",
                            details_path=None if test else os.path.join(app_paths.app_dir(), DETAILS_NAME))
    if "--self-test" in argv:
        visited = app.self_test()
        print("Self-test: visited {} pages: {}".format(len(visited), ", ".join(visited)))
        return 0 if len(visited) == sum(len(classes) for classes in MODES.values()) else 1
    app.mainloop()
    return 0
