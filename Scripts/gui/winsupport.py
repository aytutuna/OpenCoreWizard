import os
import subprocess
import sys

from Scripts import app_paths

CREATE_NO_WINDOW = 0x08000000


def enable_dpi_awareness():
    if os.name != "nt":
        return False
    try:
        import ctypes
    except ImportError:
        return False
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        return True
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return True
    except Exception:
        return False


def has_console():
    if os.name != "nt":
        return True
    try:
        import ctypes
        return bool(ctypes.windll.kernel32.GetConsoleWindow())
    except Exception:
        return True


class _HiddenConsolePopen(subprocess.Popen):
    def __init__(self, *args, **kwargs):
        if not kwargs.get("creationflags"):
            kwargs["creationflags"] = CREATE_NO_WINDOW
        if kwargs.get("stdin") is None and sys.stdin is None:
            kwargs["stdin"] = subprocess.DEVNULL
        super().__init__(*args, **kwargs)


def hide_child_consoles():
    if os.name != "nt" or has_console():
        return False
    if not issubclass(subprocess.Popen, _HiddenConsolePopen):
        subprocess.Popen = _HiddenConsolePopen
    return True


def is_admin():
    if os.name != "nt":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_arguments():
    if app_paths.is_frozen():
        return sys.executable, []
    launcher = os.path.join(app_paths.source_root(), "OpenCoreWizard.py")
    interpreter = sys.executable
    folder, name = os.path.split(interpreter)
    if name.lower() == "python.exe" and os.path.isfile(os.path.join(folder, "pythonw.exe")):
        interpreter = os.path.join(folder, "pythonw.exe")
    return interpreter, [launcher]


def relaunch_as_admin(extra=()):
    if os.name != "nt":
        return False
    import ctypes
    executable, arguments = relaunch_arguments()
    parameters = subprocess.list2cmdline(arguments + list(extra))
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", executable, parameters, app_paths.app_dir(), 1)
    return int(result) > 32


def open_path(path):
    if not path or not os.path.exists(path):
        return False
    if os.name == "nt":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])
    return True
