# CI

Workflow: `.github/workflows/ci.yml`. Triggers: push, pull request, manual dispatch, and a weekly schedule (Monday 04:17 UTC).

## Jobs

- **test** (windows-latest): `python -m unittest discover -s tests -v`. It fails when `docs/reference/choices.md` differs from what
  `tests/ci/render_choices.py` generates; run `python tests/ci/render_choices.py` (it writes the file) after editing
  `Scripts/datasets/explanations.py`.
- **build** (windows-latest, needs test): matrix over the two fixtures. Runs
  `python OpenCoreWizard.py --headless tests/fixtures/answers-<name>.json`.
  Exit codes: 0 = built and ocvalidate passed, 2 = built but ocvalidate reported errors or could not run
  (passes with a warning annotation), 3 = hardware incompatible. The laptop fixture is real hardware with no
  GPU macOS supports, so for it exit 3 is accepted (warning); the desktop fixture accepts 0 and 2 only.
  Any other code fails the job. After exit 0 or 2 the job also runs ocvalidate explicitly on
  `Results/EFI/OC/config.plist` and uploads `Results/EFI` as artifact `efi-<fixture>`.
  `OCK_Files` is cached, keyed on the workflow file hash and the ISO week.
  The desktop entry then runs the express chain with `tests/fixtures/answers-express.json` (exit 0 required).
  That file sets `"express": true`, `"usb_map": false`, a docs-only kit and `"skip_recovery": true` (also the
  default), so CI does not download the recovery image (about 900 MB). Express exits 3 only when nothing can be
  installed and 1 on any other stop.
- **package** (windows-latest, needs test): builds the windowed exe from `OpenCoreWizard.py` with
  `packaging/opencorewizard.spec`, uploads it, then runs it headless and with `--self-test`; output goes to
  `OpenCoreWizard.log` because the exe has no console. See `packaging/README.md`.
- **boot** (ubuntu-latest, needs build): matrix over fixtures that produce an EFI (currently desktop).
  Downloads the artifact, installs `qemu-system-x86 ovmf mtools dosfstools`, runs `tests/boot/run_qemu.sh`.
- **recovery-download** (windows-latest, schedule and manual dispatch only, never on pull requests):
  `tests/ci/download_recovery.py --macos 26 --dest <temp>` downloads the recovery image through
  `Scripts/recovery_fetcher.py` and verifies it against the chunklist. The folder is cached per board id and week.

## Boot harness

`tests/boot/run_qemu.sh <efi-dir>` where `<efi-dir>` holds `BOOT/` and `OC/`:

1. Reads the OpenCore version from `OpenCore.efi` and downloads the matching DEBUG package
   (falls back to the latest release). Replaces `BOOTx64.efi`, `OpenCore.efi`, `OpenRuntime.efi`.
2. Patches `config.plist` (debug target and level, builtin picker shown, 5 s timeout, ScanPolicy 0, watchdog off).
3. Builds a 256 MB FAT32 image with mtools (no root needed).
4. Boots it with `qemu-system-x86_64 -machine q35 -cpu Penryn` and OVMF, serial to `serial.log`, 120 s limit.
5. Prints the whole serial log, then passes only if a success marker is present and no failure marker is.

The markers are a first guess. Tighten them after the first green CI run.

Run locally on Linux:

    sudo apt-get install qemu-system-x86 ovmf mtools dosfstools
    python OpenCoreWizard.py --headless tests/fixtures/answers-desktop.json   # on any OS, produces Results/EFI
    bash tests/boot/run_qemu.sh Results/EFI

`SERIAL_LOG` and `BOOT_TIMEOUT` environment variables override the log path and the time limit.
The script is emulated (no KVM), so expect it to be slow.

## What CI cannot test

- Booting macOS itself (the installer needs Apple firmware behaviour and images that are not in the repo).
- Real firmware behaviour, ACPI quirks and vendor BIOS settings.
- USB enumeration and writing to a real stick (`Scripts/usb_writer.py` uses diskpart).
- Full DSDT patch generation on a supported machine (no such ACPI dump fixture yet).

## Follow-up: VHD job for the USB writer

A windows-latest job can create and attach a VHD with diskpart, but a VHD reports the file-backed bus, not USB,
and the writer refuses non-USB disks. The follow-up needs a test-only switch that lets the writer target a named
VHD disk number, then verifies partitioning, FAT32 formatting, the file copy and the SHA-256 check on it.
