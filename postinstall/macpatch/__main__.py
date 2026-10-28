import argparse
import logging
import os
import subprocess
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
# the modules import each other by name, both for "python3 -m macpatch" and "python3 <this folder>"
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import detect  # noqa: E402
import manifest  # noqa: E402
import sysvol  # noqa: E402

EXIT_PATCHED = 0
EXIT_ERROR = 1
EXIT_PREFLIGHT = 2
EXIT_NOTHING = 3


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="macpatch", description="Applies the root patches listed in an OpenCore Wizard patches.json.")
    parser.add_argument("--manifest", help="path to patches.json (not needed with --revert)")
    parser.add_argument("--dry-run", action="store_true", help="run every check and print the plan without changing anything")
    parser.add_argument("--revert", action="store_true", help="go back to the last sealed system snapshot")
    parser.add_argument("--log", help="log file (default: macpatch.log next to the manifest)")
    args = parser.parse_args(argv)
    if not args.manifest and not args.revert:
        parser.error("--manifest is required unless --revert is given")
    return args


def elevate(args, manifest_path, log_path):
    command = ["/usr/bin/sudo", sys.executable, "-B", HERE, "--log", log_path]
    if manifest_path:
        command += ["--manifest", manifest_path]
    if args.revert:
        command.append("--revert")
    print("Root patching needs administrator rights; sudo asks for your password.")
    sys.stdout.flush()
    os.execv(command[0], command)


def keep_awake():
    # sleeping with the system volume mounted read-write mid-patch is the one state that is hard to recover from
    return subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())])


def setup_logging(path):
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(console)
    handlers = [console]
    try:
        log_file = logging.FileHandler(path, encoding="utf-8")
    except OSError as e:
        logging.warning("Cannot write the log to %s (%s); logging to the screen only.", path, e)
        return handlers
    log_file.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root.addHandler(log_file)
    handlers.append(log_file)
    logging.debug("Command line: %s", " ".join(sys.argv))
    return handlers


def patch(patcher, dry_run):
    if not patcher.sets:
        for line in patcher.describe():
            logging.info(line)
        logging.info("")
        logging.info("No root patches are needed on this macOS.")
        return EXIT_NOTHING

    failures = patcher.validate()
    logging.info("")
    for line in patcher.describe():
        logging.info(line)
    logging.info("")
    if failures:
        logging.error("Cannot continue with patching:")
        for failure in failures:
            logging.error("  - %s", failure)
        return EXIT_PREFLIGHT
    if dry_run:
        logging.info("Dry run: preflight passed and nothing was changed. Run again without --dry-run to patch.")
        return EXIT_PATCHED

    keep_awake()
    patcher.patch()
    logging.info("")
    logging.info("Reboot for the patches to take effect.")
    logging.info("macOS updates remove root patches: run MacPatch again after every update.")
    return EXIT_PATCHED


def revert(host, dry_run):
    failures = detect.validate_revert(host)
    if failures:
        logging.error("Cannot continue with unpatching:")
        for failure in failures:
            logging.error("  - %s", failure)
        return EXIT_PREFLIGHT
    if dry_run:
        logging.info("Dry run: would mount the system volume at %s and run: %s", sysvol.MOUNT_LOCATION, " ".join(sysvol.revert_args(sysvol.MOUNT_LOCATION)))
        return EXIT_PATCHED
    keep_awake()
    sysvol.revert()
    logging.info("")
    logging.info("Reboot to return to the last sealed system snapshot.")
    return EXIT_PATCHED


def run(args, manifest_path):
    logging.info("MacPatch %s (patch sets from OpenCore Legacy Patcher 3.0.0)", sysvol.VERSION)
    try:
        if args.revert:
            # the manifest is not read, so a lost or broken patches.json cannot block a revert
            return revert(detect.Host(), args.dry_run)
        try:
            data = manifest.load(manifest_path)
        except manifest.ManifestError as e:
            logging.error("Error: %s", e)
            return EXIT_ERROR
        return patch(sysvol.RootPatcher(data, detect.Host(), HERE), args.dry_run)
    except Exception as e:
        logging.debug(traceback.format_exc())
        logging.error("Error: %s", e)
        return EXIT_ERROR


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if sys.platform != "darwin":
        print("MacPatch runs on macOS only.")
        return EXIT_ERROR
    manifest_path = os.path.abspath(args.manifest) if args.manifest else None
    log_path = os.path.abspath(args.log or os.path.join(os.path.dirname(manifest_path) if manifest_path else HERE, "macpatch.log"))
    if not args.dry_run and os.geteuid() != 0:
        elevate(args, manifest_path, log_path)

    handlers = setup_logging(log_path)
    try:
        return run(args, manifest_path)
    finally:
        for handler in handlers:
            logging.getLogger().removeHandler(handler)
            handler.close()


if __name__ == "__main__":
    sys.exit(main())
