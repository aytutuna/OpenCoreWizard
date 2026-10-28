# Packaging the Windows app

`opencorewizard.spec` builds OpenCore Wizard as a PyInstaller onedir app: an `OpenCoreWizard` folder with `OpenCoreWizard.exe` (windowed, no console) and an `_internal` folder. PyInstaller is a build tool only; the app never imports it and runs on the Python standard library (Tkinter included).

## Build

From the repository root, on Windows with Python 3.8 or newer:

```
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean packaging/opencorewizard.spec
```

The result is `dist\OpenCoreWizard`. Ship the whole folder (zip it); the exe does not run on its own.

The CI job `package` (`.github/workflows/ci.yml`) does the same on `windows-latest`, then runs the packaged app twice: a headless build of the desktop fixture (`OpenCoreWizard.exe --headless tests/fixtures/answers-desktop.json`, exit code 0 and ocvalidate passing are required) and the window self-test (`OpenCoreWizard.exe --self-test`). The folder is uploaded as the `OpenCoreWizard-windows` artifact before those runs, so the artifact holds no downloads or results.

## What goes where

- Bundled, read-only (inside `_internal`): the Python code, `OpenCoreWizard.py`, `Scripts/usb_enum.cs`, `Scripts/sniffer/datasets/*.ids`, `Scripts/datasets/background_picker.icns` and `docs/reference/*.md`.
- Written next to `OpenCoreWizard.exe`: `Results`, `SysReport`, `OCK_Files`, the downloaded tools in `Scripts` (macserial, ocvalidate, iasl, acpidump) and `OpenCoreWizard.log`. `Scripts/app_paths.py` makes this split: `app_dir()` is the exe folder when frozen and the repository root otherwise; `bundle_dir()` is `_internal` when frozen.
- Without a console, standard output goes to `OpenCoreWizard.log` next to the exe. The exe is built from `OpenCoreWizard.py`, so `--headless`, `--analyze-log` and `--self-test` work as they do from source; their output lands in that log.

Put the folder somewhere the user can write to (for example the Desktop or Downloads), not `Program Files`.

## Unsigned executable

The build is not code-signed; CI has no certificate. Expect:

- SmartScreen: "Windows protected your PC" on first start. Users choose More info, then Run anyway. The warning fades as a build gains reputation, and signing with a trusted certificate removes it.
- Antivirus: PyInstaller's bootloader is shared by many programs, including malware, so some scanners flag any unsigned PyInstaller build. The onedir layout, no UPX compression and an `asInvoker` manifest (the default; the exe does not ask for administrator rights) keep false positives down. If a scanner still flags it, report it to the vendor as a false positive.
- Publish the SHA-256 of the zip next to each release so users can check the download (`certutil -hashfile OpenCoreWizard.zip SHA256`).
- Smart App Control (Windows 11 Home and Pro installs that shipped with it on) is stricter than SmartScreen: it refuses unsigned binaries outright, with no "Run anyway". It also blocks some of the tools the app downloads; ocvalidate.exe is one, macserial, iasl and acpidump have enough reputation to pass. The app reports the block instead of pretending the check ran. Users can turn Smart App Control off under Windows Security > App & browser control, but it cannot be turned back on without reinstalling Windows, so the app never suggests that automatically.

Writing a USB drive needs administrator rights. The window asks for them when it starts (it relaunches itself elevated with `--elevated`), so the manifest stays `asInvoker`. If the user declines, it runs without them, and the USB page still offers "Restart as administrator", which starts the wizard again at the Welcome page.
