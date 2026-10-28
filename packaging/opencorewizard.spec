# PyInstaller spec for the OpenCore Wizard Windows build (onedir, windowed).
# Build from the repository root:  python -m PyInstaller --noconfirm --clean packaging/opencorewizard.spec
import glob
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.dirname(SPECPATH)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def data(relative, target):
    return (os.path.join(ROOT, *relative.split("/")), target)


datas = [
    data("OpenCoreWizard.py", "."),
    data("Scripts/usb_enum.cs", "Scripts"),
    data("Scripts/sniffer/datasets/pci.ids", "Scripts/sniffer/datasets"),
    data("Scripts/sniffer/datasets/usb.ids", "Scripts/sniffer/datasets"),
    data("Scripts/sniffer/LICENSE", "Scripts/sniffer"),
    data("LICENSE", "."),
    data("Scripts/datasets/background_picker.icns", "Scripts/datasets"),
]
datas += [(path, "docs/reference") for path in sorted(glob.glob(os.path.join(ROOT, "docs", "reference", "*.md")))]
# MacPatch runs on the Mac, so it ships as plain files for the post-install kit
datas += [(path, "postinstall/macpatch") for path in sorted(glob.glob(os.path.join(ROOT, "postinstall", "macpatch", "*"))) if os.path.isfile(path)]

# collect_submodules skips folders without __init__.py, so the scanner is listed by hand
hiddenimports = collect_submodules("Scripts") + ["Scripts.sniffer." + name for name in (
    "hardware_sniffer", "wmi", "cpuid", "cpu_identifier", "gpu_identifier", "device_locator", "platforms.windows",
    "datasets.cpu_data", "datasets.gpu_data", "datasets.chipset_data", "datasets.pci_data",
)]

a = Analysis(
    [os.path.join(ROOT, "OpenCoreWizard.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tests", "PyInstaller"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OpenCoreWizard",
    console=False,
    debug=False,
    strip=False,
    upx=False,
    uac_admin=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="OpenCoreWizard",
)
