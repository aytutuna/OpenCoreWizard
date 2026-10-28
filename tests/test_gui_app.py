import contextlib
import importlib.util
import io
import os
import re
import subprocess
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from repo_path import FIXTURES
from repo_path import REPO_ROOT

from Scripts import utils

try:
    import tkinter
    TK_IMPORT_ERROR = None
except ImportError as error:
    tkinter = None
    TK_IMPORT_ERROR = error

TK_SKIP_REASON = None


def tk_skip_reason():
    global TK_SKIP_REASON
    if TK_SKIP_REASON is not None:
        return TK_SKIP_REASON or None
    if tkinter is None:
        TK_SKIP_REASON = "tkinter is not available: {}".format(TK_IMPORT_ERROR)
        return TK_SKIP_REASON
    try:
        root = tkinter.Tk()
        root.withdraw()
        root.destroy()
        TK_SKIP_REASON = ""
    except tkinter.TclError as error:
        TK_SKIP_REASON = "Tk cannot open a display: {}".format(error)
    return TK_SKIP_REASON or None


class TkTestCase(unittest.TestCase):
    def setUp(self):
        reason = tk_skip_reason()
        if reason:
            self.skipTest(reason)
        from Scripts.gui import app as app_module
        self.app_module = app_module

    def make_app(self, **kwargs):
        kwargs.setdefault("autostart", False)
        kwargs.setdefault("mode", "advanced")
        app = self.app_module.OpenCoreWizardApp(**kwargs)
        app.withdraw()
        self.addCleanup(self.destroy, app)
        return app

    def destroy(self, app):
        try:
            if not app.closing:
                app.destroy()
        except tkinter.TclError:
            pass

    def pump(self, app, condition, timeout=20):
        deadline = time.time() + timeout
        while time.time() < deadline:
            app.update()
            if condition():
                return True
            time.sleep(0.02)
        return False


class AppSmokeTest(TkTestCase):
    def test_builds_and_visits_every_page(self):
        from Scripts.gui import pages
        app = self.make_app(install_hooks=False)
        self.assertEqual(len(app.pages), len(pages.PAGE_CLASSES))
        self.assertEqual([page.title for page in app.pages],
                         ["Welcome", "Hardware", "macOS version", "Options", "USB mapping", "Build", "Recovery", "USB", "Finish"])
        for index in range(len(app.pages)):
            app.show_page(index)
            app.update_idletasks()
            self.assertEqual(app.index, index)
            self.assertTrue(str(app.step_labels[index].cget("text")).startswith(">"))
        self.assertEqual(str(app.next_button.cget("text")), "Close")
        self.assertFalse(app.runner.busy())
        app.destroy()
        self.assertTrue(app.closing)

    def test_next_is_gated_until_a_report_is_loaded(self):
        app = self.make_app(install_hooks=False)
        app.show_page(1)
        self.assertTrue(app.next_button.instate(["disabled"]))
        self.assertEqual(app.next_hint.cget("text"), "Scan this PC or open a hardware report.")
        app.session.preflight_ran = True
        app.show_page(0)
        self.assertFalse(app.next_button.instate(["disabled"]))
        self.assertEqual(app.next_hint.cget("text"), "")

    def test_steps_cannot_be_skipped(self):
        from Scripts.gui import pages
        app = self.make_app(install_hooks=False)
        self.assertEqual(app.reachable(), 0)
        app.jump(3)
        self.assertEqual(app.index, 0)
        self.assertTrue(app.next_button.instate(["disabled"]))
        self.assertIn("preflight", app.next_hint.cget("text"))
        self.assertEqual(str(app.step_labels[3].cget("style")), "LockedStep.TLabel")
        app.session.preflight_ran = True
        app.session.hardware_report = {"CPU": {}}
        app.update_nav()
        self.assertEqual(app.reachable(), 2)
        app.jump(1)
        self.assertEqual(app.index, 1)
        app.jump(5)
        self.assertEqual(app.index, 1)

    def test_build_with_ocvalidate_errors_blocks_next(self):
        from Scripts.gui import pages
        app = self.make_app(install_hooks=False)
        app.show_page(app.pages.index(app.page_by_class(pages.BuildPage)))
        self.assertTrue(app.next_button.instate(["disabled"]))
        self.assertEqual(app.next_hint.cget("text"), "Press Build EFI.")
        app.session.build_result = {"ocvalidate": "errors"}
        app.update_nav()
        self.assertTrue(app.next_button.instate(["disabled"]))
        self.assertIn("ocvalidate found errors", app.next_hint.cget("text"))
        app.session.build_result = {"ocvalidate": "passed"}
        app.update_nav()
        self.assertFalse(app.next_button.instate(["disabled"]))

    def test_usb_page_needs_a_real_write(self):
        from Scripts.gui import pages
        app = self.make_app(install_hooks=False)
        app.show_page(app.pages.index(app.page_by_class(pages.UsbWriterPage)))
        self.assertTrue(app.next_button.instate(["disabled"]))
        self.assertIn("a dry run does not count", app.next_hint.cget("text"))
        app.session.build_result = {"ocvalidate": "passed"}
        app.session.build_serial = 1
        app.session.usb_written_for = 1
        app.update_nav()
        self.assertFalse(app.next_button.instate(["disabled"]))

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_window_asks_for_administrator_rights_at_start(self):
        module = self.app_module
        with mock.patch.object(module.winsupport, "is_admin", return_value=False), \
                mock.patch.object(module.winsupport, "relaunch_as_admin", return_value=True) as relaunch, \
                mock.patch.object(module, "OpenCoreWizardApp") as window:
            self.assertEqual(module.main([]), 0)
            relaunch.assert_called_once_with(["--elevated"])
            window.assert_not_called()
        with mock.patch.object(module.winsupport, "is_admin", return_value=False), \
                mock.patch.object(module.winsupport, "relaunch_as_admin") as relaunch, \
                mock.patch.object(module, "OpenCoreWizardApp") as window:
            module.main(["--elevated"])
            relaunch.assert_not_called()
            window.assert_called_once()

    def test_theme_is_vista_or_clam_when_available(self):
        app = self.make_app(install_hooks=False)
        names = app.style.theme_names()
        expected = "vista" if "vista" in names else "clam" if "clam" in names else None
        if expected:
            self.assertEqual(app.style.theme_use(), expected)

    def test_hooks_are_installed_and_removed(self):
        app = self.make_app()
        self.assertIs(utils.Utils.prompt_handler, app.broker)
        self.assertIsInstance(sys.stdout, utils.OutputProxy)
        app.destroy()
        self.assertIsNone(utils.Utils.prompt_handler)
        self.assertIsNone(utils.Utils.output_sink)
        self.assertIsNone(utils.Utils.progress_handler)

    def test_stage_prompt_opens_a_dialog_and_returns_the_answer(self):
        app = self.make_app()
        results = []
        app.run_stage("Asking", lambda: (print("1. First\n2. Second"), utils.Utils().request_input("Select an option (default: 1): "))[1], on_done=results.append)
        self.assertTrue(self.pump(app, lambda: bool(app.dialogs)), "the prompt dialog did not open")
        dialog = app.dialogs[0]
        self.assertEqual(dialog.request.choices, [("1. First", "1"), ("2. Second", "2")])
        # the buttons say it all, so the tool output stays hidden
        self.assertEqual(dialog.output.grid_info(), {})
        dialog.submit("2")
        self.assertTrue(self.pump(app, lambda: bool(results)), "the stage did not finish")
        self.assertEqual(results, ["2"])
        self.assertIn("Select an option (default: 1): 2", app.log.get("1.0", "end"))
        self.assertFalse(app.runner.busy())

    def test_cancel_stops_the_stage_with_gui_exit(self):
        app = self.make_app()
        stopped = []
        app.run_stage("Asking", lambda: utils.Utils().request_input("Type ERASE to continue: "), on_stopped=lambda reason, text: stopped.append(reason))
        self.assertTrue(self.pump(app, lambda: bool(app.dialogs)))
        app.cancel_stage()
        self.assertTrue(self.pump(app, lambda: bool(stopped)))
        self.assertEqual(stopped, ["cancel"])
        self.assertFalse(app.dialogs)

    def test_log_pane_handles_colour_and_carriage_returns(self):
        app = self.make_app(install_hooks=False)
        app.append_log("start\n\033[92mok\033[0m\n10%\r20%\r30%\n")
        content = app.log.get("1.0", "end-1c")
        self.assertEqual(content, "start\nok\n30%\n")
        self.assertIn("green", app.log.tag_names("2.0"))

    def test_hardware_page_loads_a_report_through_the_worker(self):
        app = self.make_app()
        hardware = app.pages[1]
        app.show_page(1)
        done = []
        app.run_stage("Loading", lambda: app.session.load_report(os.path.join(FIXTURES, "desktop", "Report.json")),
                      on_done=lambda verdict: (hardware.show_verdict(verdict), done.append(True)))
        self.assertTrue(self.pump(app, lambda: bool(done), timeout=120), "the report did not load")
        self.assertTrue(hardware.table.get_children())
        self.assertFalse(app.next_button.instate(["disabled"]))
        app.show_page(2)
        self.assertEqual(app.pages[2].selected(), app.session.default_macos())


class ExpressPageTest(TkTestCase):
    def test_express_done_fills_the_pages_and_opens_the_usb_page(self):
        from Scripts.gui import pages as pages_module
        app = self.make_app(install_hooks=False)
        session = app.session
        session.configuration = lambda: {"macos_name": "macOS Tahoe 26", "macos_version": "25.99.99", "needs_oclp": False, "smbios_model": "iMac20,1",
                                         "debug": False, "kexts": ["Lilu"], "acpi_patches": [], "disabled_devices": []}
        session.build_result = {"ocvalidate": "passed", "result_dir": "Results", "usb_map": None, "usb_map_reason": "No USB map has been saved yet.",
                                "preflight": [], "first_boot": [], "debug_notes": [], "post_install": [],
                                "bios": {"disable": ["Secure Boot", "CSM"], "enable": ["Above 4G Decoding"], "notes": ["n"]}}
        session.kit_result = {"dest_dir": "Results/PostInstall", "checklist": "steps", "tools": {}, "bios": None}
        session.options = {"audio": None, "broadcom": "1", "whatevergreen": "yes", "debug": False}
        session.native_macos_version = ("17.0.0", "25.99.99")
        session.suggested_macos_version = "24.99.99"
        session.macos_version = "25.99.99"
        summary = {"macos_name": "macOS Tahoe 26", "smbios": "iMac20,1", "ocvalidate": "passed", "result_dir": "Results",
                   "recovery": None, "kit": "Results/PostInstall", "warnings": [], "bios": {"disable": ["Secure Boot", "VT-d", "Fast Boot"], "enable": [], "notes": []},
                   "bios_state": self.BIOS_STATE}
        with mock.patch.object(pages_module.messagebox, "showinfo") as info:
            app.pages[0].express_done(summary)
        self.assertIsInstance(app.pages[app.index], pages_module.UsbWriterPage)
        self.assertIn("SMBIOS: iMac20,1", info.call_args[0][1])
        self.assertIn("BIOS settings before you boot from the stick:\n  - Change in the BIOS: Secure Boot (should be Off).\n  - Nothing to do: VT-d.\n  - Check yourself in the BIOS: Fast Boot (should be Off).\n\nNext:", info.call_args[0][1])
        self.assertEqual(app.page_by_class(pages_module.OptionsPage).vars["whatevergreen"].get(), "yes")
        self.assertEqual(app.page_by_class(pages_module.MacOSPage).selected(), 25)
        self.assertIn("ocvalidate passed", app.pages[0].express_result.cget("text"))
        notes = app.page_by_class(pages_module.BuildPage).notes.get("1.0", "end")
        self.assertIn("No USB map has been saved yet.", notes)
        self.assertIn("press Save map, then build again", notes)
        self.assertIn("BIOS settings before you boot from the stick", notes)
        self.assertIn("Turn off: Secure Boot, CSM.", notes)
        self.assertLess(notes.index("Turn off: Secure Boot"), notes.index("No USB map"))

    BIOS_STATE = {"other_pc": False, "items": [
        {"setting": "Secure Boot", "want": "off", "status": "change", "evidence": "Windows reports it is on. OpenCore will not start while it is on."},
        {"setting": "VT-d", "want": "off", "status": "ok", "evidence": "The firmware publishes no VT-d table (DMAR), so VT-d is off."},
        {"setting": "Fast Boot", "want": "off", "status": "unknown", "evidence": "Windows cannot see this setting."}
    ]}

    def test_build_page_groups_the_bios_settings(self):
        from Scripts.gui import pages as pages_module
        app = self.make_app(install_hooks=False)
        page = app.page_by_class(pages_module.BuildPage)
        page.show_result({"ocvalidate": "passed", "result_dir": "Results", "usb_map": None, "usb_map_reason": "No USB map has been saved yet.",
                          "preflight": [], "first_boot": [], "debug_notes": [], "post_install": [],
                          "bios": {"disable": ["Secure Boot", "VT-d", "Fast Boot"], "enable": [], "notes": []}, "bios_state": self.BIOS_STATE})
        notes = page.notes.get("1.0", "end")
        expected = ("BIOS settings before you boot from the stick\n"
                    "  Change these in the BIOS before you boot from the stick:\n"
                    "    - Secure Boot: should be Off. Windows reports it is on. OpenCore will not start while it is on.\n"
                    "  Nothing to do:\n"
                    "    - VT-d: The firmware publishes no VT-d table (DMAR), so VT-d is off.\n"
                    "  Check these yourself in the BIOS (the app could not tell from Windows):\n"
                    "    - Fast Boot: should be Off. Windows cannot see this setting.\n"
                    "  - The app cannot read every BIOS setting from Windows, so look at the 'check yourself' items in the BIOS too. If a setting you were asked to change is still wrong, OpenCore may not start. The BIOS settings tab on the Finish page and POST-INSTALL.md in the kit explain each setting.")
        self.assertIn(expected, notes)
        self.assertNotIn("Turn off:", notes)
        self.assertLess(notes.index("Fast Boot"), notes.index("No USB map"))

        page.show_result({"ocvalidate": "passed", "result_dir": "Results", "usb_map": None, "usb_map_reason": None, "preflight": [], "first_boot": [],
                          "debug_notes": [], "post_install": [], "bios": {"disable": ["Secure Boot"], "enable": [], "notes": []}, "bios_state": {"other_pc": True, "items": []}})
        notes = page.notes.get("1.0", "end")
        self.assertIn("  - This hardware report is from another PC, so the app cannot check that PC's BIOS from here. Run the app on that PC to check it, or go through the list below in its BIOS yourself.\n  - Turn off: Secure Boot.", notes)

    def test_bios_lines_for_the_express_message(self):
        from Scripts.gui import pages as pages_module
        bios = {"disable": ["Secure Boot", "VT-d", "Fast Boot"], "enable": [], "notes": []}
        self.assertEqual(pages_module.bios_lines(bios, self.BIOS_STATE)[:-1], ["Change in the BIOS: Secure Boot (should be Off).", "Nothing to do: VT-d.", "Check yourself in the BIOS: Fast Boot (should be Off)."])
        self.assertEqual(pages_module.bios_lines(bios)[0], "Turn off: Secure Boot, VT-d, Fast Boot.")

    def test_finish_page_marks_each_bios_setting(self):
        from Scripts.gui import pages as pages_module
        app = self.make_app(install_hooks=False)
        page = app.page_by_class(pages_module.FinishPage)
        page.show_bios({"disable": ["Secure Boot", "VT-d", "Fast Boot"], "enable": [], "notes": ["n"]}, self.BIOS_STATE)
        text = page.bios["disable"].get("1.0", "end")
        self.assertIn("- Change in the BIOS - Secure Boot: should be Off. Windows reports it is on.", text)
        self.assertIn("- Nothing to do - VT-d: The firmware publishes no VT-d table", text)
        self.assertIn("- Check yourself in the BIOS - Fast Boot: should be Off. Windows cannot see this setting.", text)
        self.assertIn("red", page.bios["disable"].tag_names("1.0"))
        self.assertEqual(page.bios["enable"].get("1.0", "end").strip(), "- (none)")
        page.show_bios({"disable": ["Secure Boot"], "enable": [], "notes": ["n"]}, {"other_pc": True, "items": []})
        self.assertTrue(page.bios["disable"].get("1.0", "end").startswith("This hardware report is from another PC"))
        self.assertEqual(page.bios["notes"].get("1.0", "end").strip(), "- n")

    def test_welcome_page_lists_the_firmware_readings(self):
        app = self.make_app(install_hooks=False)
        info = {"bitlocker": "off", "secure_boot": "on", "partition_style": "GPT", "esp_mb": 260,
                "firmware": {"machine": None, "pf_virt": False, "dmar": {"present": False},
                             "cpu": {"vendor": "GenuineIntel", "hypervisor": False, "vmx": True, "svm": False, "hv_root": None, "smt_width": 1}}}
        app.pages[0].show_checks({"info": info, "warnings": [], "error": None, "admin": False})
        rows = {app.pages[0].checks.item(row)["values"][0]: app.pages[0].checks.item(row)["values"][1] for row in app.pages[0].checks.get_children()}
        self.assertEqual(rows["Secure Boot"], "on")
        self.assertEqual(rows["VT-x (virtualization)"], "Windows reports it is off in the BIOS.")
        self.assertEqual(rows["VT-d (device memory protection)"], "The BIOS gives Windows no VT-d table (DMAR), so VT-d is off.")
        self.assertIn("Thunderbolt / USB4", rows)

    def test_build_page_says_whether_the_usb_map_is_used(self):
        from Scripts.gui import pages as pages_module
        app = self.make_app(install_hooks=False)
        session = app.session
        session.configuration = lambda: {"macos_name": "macOS Sequoia 15", "smbios_model": "iMac20,1", "debug": False}

        class Ocpe:
            usb_map_reason = None
            source = None

            def select_usb_map_source(self, hardware_report, announce=True):
                self.usb_map_reason = None if self.source else "No USB map has been saved yet."
                return self.source

        session.ocpe = Ocpe()
        page = app.page_by_class(pages_module.BuildPage)
        page.on_show()
        self.assertIn("USB map: none. No USB map has been saved yet.", page.summary.cget("text"))
        session.ocpe.source = {"kind": "json", "path": "SysReport/USBMap.json"}
        session.usb_dirty = True
        page.on_show()
        self.assertIn("USB map: SysReport/USBMap.json will be used.", page.summary.cget("text"))
        self.assertIn("press Save map first", page.summary.cget("text"))

    def test_express_failure_still_shows_the_verdict(self):
        from Scripts.gui import pages as pages_module
        from Scripts.gui.session import UnsupportedHardware
        app = self.make_app(install_hooks=False)
        app.session.hardware_report = {"GPU": {"RTX 5070 Ti": {"Compatibility": (None, None)}}}
        self.assertFalse(app.pages[0].express_failed(UnsupportedHardware("Nothing can be installed"), ""))
        table = app.page_by_class(pages_module.HardwarePage).table
        self.assertEqual([table.item(row)["values"][1] for row in table.get_children()], ["RTX 5070 Ti"])

    def test_express_stop_warns_unless_cancelled(self):
        from Scripts.gui import pages as pages_module
        app = self.make_app(install_hooks=False)
        with mock.patch.object(pages_module.messagebox, "showwarning") as warning:
            app.pages[0].express_stopped("cancel", "Cancelled by the user")
            warning.assert_not_called()
            app.pages[0].express_stopped("exit", "Program exit requested")
            warning.assert_called_once()

    def test_express_progress_marks_the_sidebar(self):
        app = self.make_app(install_hooks=False)
        from Scripts.gui import session as session_module
        app.dispatch(("progress", session_module.EXPRESS_TITLE, list(session_module.EXPRESS_STEPS), session_module.EXPRESS_STEPS.index("EFI build"), False))
        build = next(label for label in app.step_labels if label.cget("text").endswith("Build"))
        self.assertTrue(build.cget("text").startswith("*"))
        app.render_steps()
        self.assertTrue(app.step_labels[0].cget("text").startswith(">"))


class SimpleModeTest(TkTestCase):
    LAPTOP = {"Motherboard": {"Name": "ASUSTEK ZENBOOK UX534FT_UX534FT", "Platform": "Laptop"},
              "GPU": {"NVIDIA GeForce GTX 1650": {"Device Type": "Discrete GPU"}, "Intel(R) UHD Graphics 620": {"Device Type": "Integrated GPU"}}}
    ROWS = [
        {"section": "CPU", "name": "Intel Core i7-10510U", "status": "supported"},
        {"section": "GPU", "name": "NVIDIA GeForce GTX 1650", "status": "unsupported"},
        {"section": "GPU", "name": "Intel(R) UHD Graphics 620", "status": "supported"},
        {"section": "Network", "name": "Intel(R) Wireless-AC 9560", "status": "unchecked"}
    ]

    def simple(self):
        app = self.make_app(install_hooks=False, mode="easy")
        session = app.session
        session.native_macos_version = ("17.0.0", "24.99.99")
        session.suggested_macos_version = "24.99.99"
        return app

    def test_opens_with_five_plain_steps_and_no_log(self):
        app = self.make_app(install_hooks=False, mode="easy")
        self.assertEqual([page.title for page in app.pages], ["Check this PC", "Get the files", "USB stick", "BIOS", "Install macOS"])
        self.assertFalse(app.log_visible)
        self.assertNotIn(str(app.log_frame), [str(pane) for pane in app.panes.panes()])
        self.assertEqual(app.glossary_button.grid_info(), {})
        self.assertTrue(app.next_button.instate(["disabled"]))
        self.assertEqual(app.next_hint.cget("text"), "Press Check this PC.")
        app.toggle_log()
        self.assertIn(str(app.log_frame), [str(pane) for pane in app.panes.panes()])
        self.assertEqual(app.details_button.cget("text"), "Hide details")

    def test_check_page_says_it_in_plain_words(self):
        from Scripts.gui import easy
        app = self.simple()
        app.session.hardware_report = self.LAPTOP
        page = app.page_by_class(easy.CheckPage)
        page.show({"rows": self.ROWS, "preflight": ["Suspend BitLocker first."], "warnings": []})
        text = page.result.get("1.0", "end")
        self.assertIn("Yes. This PC can run macOS Sequoia 15.", text)
        self.assertIn("Graphics: Intel(R) UHD Graphics 620", text)
        self.assertIn("macOS switches it off and uses the Intel(R) UHD Graphics 620 for the screen", text)
        self.assertNotIn("Wireless", text)
        self.assertIn("Suspend BitLocker first.", text)
        self.assertFalse(app.next_button.instate(["disabled"]))

    def test_check_page_explains_why_not(self):
        from Scripts.gui import easy
        from Scripts.gui.session import UnsupportedHardware
        app = self.simple()
        page = app.page_by_class(easy.CheckPage)
        self.assertTrue(page.failed(UnsupportedHardware("Nothing can be installed: macOS does not support RTX 5070 Ti."), ""))
        text = page.result.get("1.0", "end")
        self.assertIn("This PC cannot run macOS.", text)
        self.assertIn("MacOS does not support RTX 5070 Ti.", text)
        app.session.hardware_report = None
        app.update_nav()
        self.assertEqual(app.next_hint.cget("text"), "macOS cannot run on this PC.")

    def test_usb_page_needs_a_stick_the_tick_and_a_real_write(self):
        from Scripts.gui import easy
        app = self.simple()
        page = app.page_by_class(easy.UsbPage)
        with mock.patch.object(easy.winsupport, "is_admin", return_value=True):
            app.show_page(app.pages.index(page))
            page.show_disks({"disks": [{"number": 2, "friendly_name": "Lexar USB Flash Drive", "size_bytes": 16 * 1000 ** 3, "partition_style": "MBR"}]})
            self.assertEqual(page.choice.get(), 2)
            self.assertTrue(page.write_button.instate(["disabled"]))
            self.assertEqual(page.hint(), "Tick the box, then press Make the USB stick.")
            page.agree.set(True)
            page.update_state()
            self.assertFalse(page.write_button.instate(["disabled"]))
            self.assertFalse(page.complete())
            app.session.build_result = {"ocvalidate": "passed"}
            app.session.build_serial = app.session.usb_written_for = 1
            page.finished({"written": True})
            self.assertIn("The USB stick is ready.", page.result.get("1.0", "end"))
            self.assertTrue(page.complete())
        with mock.patch.object(easy.winsupport, "is_admin", return_value=False):
            page.update_state()
            self.assertTrue(page.write_button.instate(["disabled"]))
            self.assertEqual(page.hint(), "Restart with administrator rights first.")

    def test_failed_write_keeps_the_tick_and_shows_why(self):
        from Scripts.gui import easy
        app = self.simple()
        page = app.page_by_class(easy.UsbPage)
        stages = []
        output = "Running diskpart...\nDiskPart has encountered an error: The parameter is incorrect.\nAborted.\n"
        with mock.patch.object(easy.winsupport, "is_admin", return_value=True), \
                mock.patch.object(easy.messagebox, "askyesno", return_value=True), \
                mock.patch.object(app, "run_stage", side_effect=lambda label, func, **kwargs: stages.append((label, kwargs))), \
                mock.patch.object(app, "stage_text", return_value=output):
            app.show_page(app.pages.index(page))
            page.show_disks({"disks": [{"number": 2, "friendly_name": "Lexar USB Flash Drive", "size_bytes": 16 * 1000 ** 3, "partition_style": "MBR"}]})
            page.agree.set(True)
            page.update_state()
            page.write_button.invoke()
            self.assertEqual([label for label, _ in stages], ["Making the USB stick"])
            stages[0][1]["on_done"]({"written": False, "dry_run": False, "kit": None})
            self.assertTrue(page.agree.get())
            self.assertFalse(page.write_button.instate(["disabled"]))
            self.assertEqual(app.next_hint.cget("text"), "The last try did not work. Press Make the USB stick to try again.")
            text = page.result.get("1.0", "end")
            self.assertIn("The USB stick could not be made.", text)
            self.assertIn("DiskPart has encountered an error: The parameter is incorrect.", text)
            stages[0][1]["on_error"](RuntimeError("Could not list the USB disks."), "")
            self.assertIn("Could not list the USB disks.", page.result.get("1.0", "end"))

    def test_details_are_kept_in_a_file(self):
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "details.log")
        app = self.make_app(install_hooks=False, mode="easy", details_path=path)
        app.append_log("diskpart said \033[91mno\033[0m\n")
        app.destroy()
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "diskpart said no\n")

    def test_bios_page_lists_what_to_change_and_needs_the_tick(self):
        from Scripts.gui import easy
        app = self.simple()
        app.session.hardware_report = self.LAPTOP
        app.session.preflight_warnings = ["Suspend BitLocker or save your recovery key before changing TPM or Secure Boot settings."]
        app.session.build_result = {"ocvalidate": "passed", "bios": {"disable": ["Secure Boot", "Fast Boot", "VT-d"], "enable": []}, "bios_state": ExpressPageTest.BIOS_STATE}
        page = app.page_by_class(easy.BiosPage)
        app.show_page(app.pages.index(page))
        text = page.text.get("1.0", "end")
        self.assertIn("First: save your BitLocker recovery key.", text)
        self.assertIn("press F2 again and again", text)
        self.assertLess(text.index("Change these"), text.index("Secure Boot: Off"))
        self.assertLess(text.index("Check these too"), text.index("Fast Boot: Off"))
        self.assertIn("Already right", text)
        self.assertFalse(page.complete())
        page.confirmed.set(True)
        page.confirm()
        self.assertTrue(page.complete())

    def test_install_steps_name_the_boot_key_and_the_stick(self):
        from Scripts.gui import easy
        app = self.simple()
        app.session.hardware_report = self.LAPTOP
        app.session.macos_version = "24.0.0"
        app.session.usb_disk = {"friendly_name": "Lexar USB Flash Drive"}
        steps, problems = easy.install_steps(app.session)
        text = " ".join(line for _, paragraphs in steps for line in paragraphs)
        self.assertIn("press Esc again and again", text)
        self.assertIn("\"UEFI: Lexar USB Flash Drive\"", text)
        self.assertIn("Reinstall macOS Sequoia 15", text)
        self.assertIn("START HERE.html", text)

    def test_switching_modes_keeps_the_work(self):
        from Scripts.gui import pages as pages_module
        app = self.simple()
        app.session.preflight_ran = True
        app.session.hardware_report = self.LAPTOP
        app.update_nav()
        app.set_mode("advanced")
        self.assertEqual(app.mode, "advanced")
        self.assertEqual(len(app.pages), len(pages_module.PAGE_CLASSES))
        # Welcome, Hardware and the suggested macOS version are done, so it opens at Options
        self.assertIsInstance(app.pages[app.index], pages_module.OptionsPage)
        self.assertEqual(app.mode_button.cget("text"), "Simple mode")
        app.set_mode("easy")
        self.assertEqual(app.index, 1)
        self.assertEqual(app.pages[0].title, "Check this PC")


class UsbMapPageTest(TkTestCase):
    def test_automatic_map_fills_the_table(self):
        import json
        from Scripts import usb_mapper
        from Scripts.gui import pages as pages_module
        from test_usb_mapper import CANNED_DUMP
        app = self.make_app(install_hooks=False)
        page = app.page_by_class(pages_module.UsbMapPage)
        report = app.session.auto_usb_map(usb_mapper.parse_controllers(json.dumps(CANNED_DUMP)))
        page.auto_done(report)
        text = page.message.cget("text")
        self.assertIn("Confidence: guess", text)
        self.assertIn("press Save map", text)
        self.assertEqual(len(page.table.get_children()), 2)
        self.assertIn("8 ports selected", page.counter.cget("text"))
        self.assertFalse(app.session.usb_dirty)
        page.apply_type(1, "3: USB 3 Type A")
        self.assertTrue(app.session.usb_dirty)


class SelfTestFlagTest(TkTestCase):
    def test_launcher_self_test_opens_and_closes(self):
        script = os.path.join(REPO_ROOT, "OpenCoreWizard.py")
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        result = subprocess.run([sys.executable, script, "--self-test"], capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Self-test: visited 14 pages", result.stdout)


class LauncherFilesTest(unittest.TestCase):
    def read(self, name):
        with open(os.path.join(REPO_ROOT, name), "r", encoding="utf-8") as handle:
            return handle.read()

    def test_batch_file_is_well_formed(self):
        text = self.read("OpenCoreWizard.bat")
        lines = text.splitlines()
        self.assertEqual(lines[0].strip().lower(), "@echo off")
        self.assertIn('set "script=!thisDir!OpenCoreWizard.py"', text)
        self.assertIn('start "" "!pydir!pythonw.exe" "!script!"', text)
        self.assertIn('"!py3path!" "!script!" %*', text)
        self.assertEqual(text.count("("), text.count(")"))
        labels = {line.strip()[1:].split()[0].lower() for line in lines if line.strip().startswith(":") and not line.strip().startswith("::")}
        targets = {target.lower() for target in re.findall(r"(?:call|goto)\s+:(\w+)", text, re.IGNORECASE)}
        targets.discard("eof")
        self.assertTrue(targets)
        self.assertTrue(targets <= labels, targets - labels)
        for line in lines:
            if line.strip().lower().startswith("rem"):
                continue
            self.assertEqual(line.count('"') % 2, 0, line)

    def test_old_entry_points_are_gone(self):
        for name in ("OpCore-Simplify.py", "OpCore-Simplify.bat", "OpenCoreWizard.pyw", "OpenCoreWizard-GUI.bat", "updater.py"):
            self.assertFalse(os.path.exists(os.path.join(REPO_ROOT, name)), name)

    def test_entry_routes_headless_and_never_imports_pyinstaller(self):
        text = self.read("OpenCoreWizard.py")
        self.assertIn("--headless", text)
        self.assertIn("--self-test", self.read(os.path.join("Scripts", "gui", "app.py")))
        for root, _, files in os.walk(os.path.join(REPO_ROOT, "Scripts")):
            for name in files:
                if name.endswith(".py"):
                    with open(os.path.join(root, name), "r", encoding="utf-8") as handle:
                        self.assertNotIn("import PyInstaller", handle.read(), name)
        self.assertNotIn("PyInstaller", text)

    def test_entry_analyzes_a_log(self):
        script = os.path.join(REPO_ROOT, "OpenCoreWizard.py")
        sample = os.path.join(FIXTURES, "boot_logs", "ci-serial-sample.txt")
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        result = subprocess.run([sys.executable, script, "--analyze-log", sample], capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Reached the boot picker", result.stdout)

    def test_spec_bundles_the_read_only_data(self):
        text = self.read(os.path.join("packaging", "opencorewizard.spec"))
        self.assertNotIn("OpCore", text)
        self.assertNotIn("updater", text)
        self.assertIn('[os.path.join(ROOT, "OpenCoreWizard.py")]', text)
        for needle in ('data("OpenCoreWizard.py", ".")', "usb_enum.cs", "background_picker.icns", "docs", "console=False", 'name="OpenCoreWizard"', "COLLECT("):
            self.assertIn(needle, text)


class EntryPointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("opencorewizard_entry_test", os.path.join(REPO_ROOT, "OpenCoreWizard.py"))
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def route(self, argv):
        with mock.patch.object(self.module, "run_terminal", return_value="menu"):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                return self.module.main(argv), output.getvalue()

    def test_terminal_and_dry_run_flags_open_the_menu(self):
        self.assertEqual(self.route(["--terminal"])[0], "menu")
        self.assertEqual(self.route(["--dry-run-usb"])[0], "menu")

    def test_missing_tkinter_falls_back_to_the_menu(self):
        with mock.patch.dict(sys.modules, {"tkinter": None}):
            result, output = self.route([])
        self.assertEqual(result, "menu")
        self.assertIn("tkinter is not available", output)

    def test_terminal_menu_exits_when_stdin_closes(self):
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        result = subprocess.run([sys.executable, os.path.join(REPO_ROOT, "OpenCoreWizard.py"), "--terminal"], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(" OpenCore Wizard ", result.stdout)
        self.assertIn("Q. Quit", result.stdout)
        self.assertNotIn("OpCore", result.stdout)


if __name__ == "__main__":
    unittest.main()
