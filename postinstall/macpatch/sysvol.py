# Adapted from OpenCore Legacy Patcher 3.0.0 (sys_patch), BSD-4-Clause, see LICENSE.
import datetime
import logging
import os
import plistlib
from pathlib import Path

import detect
import kdk
import manifest
import patchsets
import payload
import runner

MOUNT_LOCATION = "/System/Volumes/Update/mnt1"
CACHE_DIR = "/Library/Caches/OpenCoreWizard"
VERSION = "1"


def mount_args(identifier):
    return ["/sbin/mount", "-o", "nobrowse", "-t", "apfs", "/dev/{}".format(identifier), MOUNT_LOCATION]


def mount_root(identifier):
    if Path(MOUNT_LOCATION, "System/Library/CoreServices/SystemVersion.plist").exists():
        return MOUNT_LOCATION
    runner.check(mount_args(identifier))
    if not Path(MOUNT_LOCATION).exists():
        raise RuntimeError("Attempted to mount root volume, but failed: {}".format(MOUNT_LOCATION))
    return MOUNT_LOCATION


def unmount_root():
    logging.info("- Unmounting root volume")
    runner.run(["/sbin/umount", MOUNT_LOCATION])


def snapshot_args(mount_location):
    # Intel form only; Apple Silicon (bless --mount ... --create-snapshot) is not a target
    return ["/usr/sbin/bless", "--folder", "{}/System/Library/CoreServices".format(mount_location), "--bootefi", "--create-snapshot"]


def revert_args(mount_location):
    return ["/usr/sbin/bless", "--mount", mount_location, "--bootefi", "--last-sealed-snapshot"]


def kmutil_args(xnu_major, mount_location):
    args = ["/usr/bin/kmutil"]
    if xnu_major >= patchsets.VENTURA:
        args += ["create", "--allow-missing-kdk"]
    else:
        args.append("install")
    return args + ["--volume-root", mount_location, "--update-all", "--variant-suffix", "release"]


def rebuilds_kernel_collection(xnu_major, needs_kdk):
    # On Ventura and newer OCLP only builds the auxiliary collection when no KDK is involved, and
    # these sets put nothing in /Library/Extensions, so there is nothing to rebuild
    return xnu_major < patchsets.VENTURA or needs_kdk


def sanity_check(mount_location, os_build):
    version_plist = Path(mount_location) / "System/Library/CoreServices/SystemVersion.plist"
    if not version_plist.exists():
        logging.error("- Failed to find SystemVersion.plist on mounted root volume")
        return False
    try:
        with open(version_plist, "rb") as source:
            data = plistlib.load(source)
    except Exception:
        logging.error("- Failed to parse SystemVersion.plist")
        return False
    if data.get("ProductBuildVersion") != os_build:
        logging.error("- SystemVersion.plist build version mismatch: found %s (%s), expected build %s",
                      data.get("ProductVersion"), data.get("ProductBuildVersion"), os_build)
        logging.error("An update is in progress on your machine and patching cannot continue until it is cancelled or finished")
        return False
    return True


def file_plan(patches, payload_root, mount_location):
    steps = []
    for patch, methods in patches.items():
        for directory, files in methods.get(patchsets.REMOVE_SYSTEM_VOLUME, {}).items():
            for name in files:
                steps.append({"patch": patch, "method": patchsets.REMOVE_SYSTEM_VOLUME, "file": name,
                              "source": None, "destination": mount_location + directory})
        for method in patchsets.INSTALL_TYPES:
            for directory, files in methods.get(method, {}).items():
                for name, version in files.items():
                    source = version + directory
                    if not version.startswith("/"):
                        source = payload_root + "/" + source
                    steps.append({"patch": patch, "method": method, "file": name,
                                  "source": source, "destination": mount_location + directory})
    return steps


def fix_permissions(path):
    chmod_args = ["/bin/chmod", "-Rf", "755", path]
    chown_args = ["/usr/sbin/chown", "-Rf", "root:wheel", path]
    if not Path(path).is_dir():
        chmod_args.pop(1)
        chown_args.pop(1)
    runner.check(chmod_args)
    runner.check(chown_args)


def copy_args(source, destination):
    if Path(source).is_dir():
        return ["/bin/cp", "-R", source, destination]
    return ["/bin/cp", source, destination]


def install_new_file(source_folder, destination_folder, file_name, method):
    if not Path(destination_folder).exists():
        logging.info("  - Skipping %s, cannot locate %s", file_name, destination_folder)
        return
    source = "{}/{}".format(source_folder, file_name)
    destination = "{}/{}".format(destination_folder, file_name)
    if method == patchsets.MERGE_SYSTEM_VOLUME:
        logging.info("  - Installing: %s", file_name)
        runner.run(["/usr/bin/rsync", "-r", "-i", "-a", source, "{}/".format(destination_folder)])
    else:
        if Path(destination).exists():
            logging.info("  - Found existing %s, overwriting...", file_name)
            runner.check(["/bin/rm", "-R", destination] if Path(destination).is_dir() else ["/bin/rm", destination])
        else:
            logging.info("  - Installing: %s", file_name)
        runner.check(copy_args(source, destination_folder))
    fix_permissions(destination)


def remove_file(destination_folder, file_name):
    path = "{}/{}".format(destination_folder, file_name)
    if Path(path).exists():
        logging.info("  - Removing: %s", file_name)
        runner.check(["/bin/rm", "-R", path] if Path(path).is_dir() else ["/bin/rm", path])


def execute_patchset(patches, steps):
    for patch in patches:
        logging.info("- Installing Patchset: %s", patch)
        for step in steps:
            if step["patch"] != patch:
                continue
            if step["source"] is None:
                remove_file(step["destination"], step["file"])
            else:
                install_new_file(step["source"], step["destination"], step["file"], step["method"])
        for process, as_root in patches[patch].get(patchsets.EXECUTE, {}).items():
            logging.info("- Running Process:\n%s", process)
            runner.check(process.split(" ") if as_root else ["/bin/sh", "-c", process])


def write_record(mount_location, patches, kdk_path, psp_version, os_version, os_build):
    record = {
        "MacPatch": VERSION,
        "PatcherSupportPkg": psp_version,
        "Time Patched": datetime.datetime.now().strftime("%B %d, %Y @ %H:%M:%S"),
        "Kernel Debug Kit Used": str(kdk_path) if kdk_path else "Not applicable",
        "OS Version": "{} ({})".format(os_version, os_build),
    }
    record.update(patches)
    logging.info("- Writing patchset information to Root Volume")
    with open(os.path.join(mount_location, detect.RECORD_PATH), "wb") as output:
        plistlib.dump(record, output, sort_keys=False)


class RootPatcher:
    def __init__(self, data, host, tool_dir, cache_dir=None):
        self.data = data
        self.host = host
        self.tool_dir = tool_dir
        self.cache_dir = cache_dir or CACHE_DIR
        self.pin = payload.pin_from_manifest(data)
        self.listed = manifest.patch_sets(data)
        self.set_sets(patchsets.needed(self.listed, host.xnu_major, host.os_build))
        self.payload_path = None
        self.kdk_path = None

    def set_sets(self, set_ids):
        self.sets = set_ids
        self.patches = patchsets.merged_patches(set_ids, self.host.xnu_major, self.host.xnu_minor, self.host.os_build, self.host.os_version)
        self.needs_kdk = patchsets.requires_kdk(set_ids, self.host.xnu_major)

    def describe(self):
        host = self.host
        lines = [
            "Host: macOS {} ({}), Darwin {}.{}".format(host.os_version, host.os_build, host.xnu_major, host.xnu_minor),
            "Built for: {} (Darwin {}), {}".format(self.data.get("macos_name") or "unknown", self.data.get("macos_version") or "unknown", self.data.get("smbios_model") or "unknown model"),
            "",
            "Patch sets in patches.json:",
        ]
        devices = {}
        for entry in self.data.get("root_patches", []):
            if entry.get("device"):
                devices.setdefault(entry["set"], []).append("{} {}".format(entry["device"], entry.get("name") or "").strip())
        for set_id in self.listed:
            if set_id in self.sets:
                state = "needed"
            elif patchsets.SETS[set_id]["native_os"](host.xnu_major, host.os_build):
                state = "native on this macOS, skipped"
            else:
                state = "left for the next run (no network connection)"
            if set_id in self.sets and patchsets.SETS[set_id]["requires_kdk"](host.xnu_major):
                state += ", needs a Kernel Debug Kit"
            suffix = " [{}]".format("; ".join(devices[set_id])) if devices.get(set_id) else ""
            lines.append("  - {} ({}): {}{}".format(patchsets.SETS[set_id]["name"], set_id, state, suffix))
        for entry in manifest.unsupported(self.data):
            lines.append("  - {} {} ({}): not covered by MacPatch; use mainline OpenCore Legacy Patcher".format(
                entry.get("set"), entry.get("name") or entry.get("device") or "", entry.get("codename") or "unknown"))
        if not self.sets:
            return lines

        lines.extend(["", "Files (source inside Universal-Binaries.dmg -> destination on the system volume):"])
        for step in file_plan(self.patches, "", ""):
            if step["source"] is None:
                lines.append("  - {}: remove {}/{}".format(step["patch"], step["destination"], step["file"]))
            else:
                action = "merge" if step["method"] == patchsets.MERGE_SYSTEM_VOLUME else "overwrite"
                lines.append("  - {}: {} {}/{} <- {}/{}".format(step["patch"], action, step["destination"], step["file"], step["source"].lstrip("/"), step["file"]))
        lines.append("")
        if self.needs_kdk:
            if self.kdk_path:
                lines.append("Kernel Debug Kit: installed at {} (if KdkSupportPkg lists a closer build, that one is downloaded instead)".format(self.kdk_path))
            else:
                lines.append("Kernel Debug Kit: none installed for {}; it will be resolved from KdkSupportPkg and downloaded (about 1.2 GB)".format(host.os_build))
        else:
            lines.append("Kernel Debug Kit: not needed")
        if self.payload_path:
            lines.append("Payload: {} (PatcherSupportPkg {}, SHA-256 matches)".format(self.payload_path, self.pin["psp_version"]))
        else:
            lines.append("Payload: Universal-Binaries.dmg not found; it will be downloaded to {} ({} MB)".format(self.cache_dir, self.pin["size"] // (1024 * 1024)))
        if rebuilds_kernel_collection(host.xnu_major, self.needs_kdk):
            lines.append("Kernel collections: rebuilt with {}".format(" ".join(kmutil_args(host.xnu_major, MOUNT_LOCATION))))
        else:
            lines.append("Kernel collections: not rebuilt (no kext changes)")
        lines.append("Snapshot: {}".format(" ".join(snapshot_args(MOUNT_LOCATION))))
        return lines

    def validate(self):
        logging.info("- Verifying whether root patching is possible")
        self.payload_path = payload.find(self.pin, [self.tool_dir, self.cache_dir])
        if self.needs_kdk:
            # offline lookup: exact build, else an installed KDK of the same or previous minor release
            self.kdk_path = kdk.resolve(self.host.os_build, self.host.os_version).get("path")
        failures = detect.validate(self.host, self.sets, self.needs_kdk and not self.kdk_path, not self.payload_path)
        if failures == [detect.MISSING_NETWORK_CONNECTION] and self.payload_path and not detect.has_networking_patches():
            wireless = [set_id for set_id in self.sets if patchsets.SETS[set_id]["networking"]]
            if wireless:
                logging.info("- Network connection missing; applying only the Wi-Fi patches now. Reboot, connect and run MacPatch again for the rest.")
                self.set_sets(wireless)
                failures = []
        return failures

    def patch(self):
        if not self.payload_path:
            self.payload_path = payload.download(self.pin, self.cache_dir)
        payload_root = payload.mount(self.payload_path, self.cache_dir)
        try:
            identifier, _ = detect.root_volume()
            mount_location = mount_root(identifier)
            if not sanity_check(mount_location, self.host.os_build):
                unmount_root()
                raise RuntimeError("Failed sanity checks; make sure no macOS update is pending")

            steps = file_plan(self.patches, payload_root, mount_location)
            for step in steps:
                if step["source"] is not None and not Path(step["source"], step["file"]).exists():
                    raise RuntimeError("Failed to find {}/{}".format(step["source"], step["file"]))
            if self.needs_kdk:
                self.kdk_path = kdk.ensure(self.host.os_build, self.host.os_version, self.cache_dir, mount_location)

            execute_patchset(self.patches, steps)
            write_record(mount_location, self.patches, self.kdk_path, self.pin["psp_version"], self.host.os_version, self.host.os_build)

            if rebuilds_kernel_collection(self.host.xnu_major, self.needs_kdk):
                logging.info("- Rebuilding Boot and System Kernel Collections")
                runner.check(kmutil_args(self.host.xnu_major, mount_location))
            logging.info("- Creating new APFS snapshot")
            result = runner.run(snapshot_args(mount_location))
            if result.returncode != 0:
                logging.error(result.stdout.rstrip())
                if "Can't use last-sealed-snapshot or create-snapshot on non system volume" in result.stdout:
                    logging.info("- This is an APFS bug with Monterey and newer! Perform a clean installation to ensure your APFS volume is built correctly")
                raise RuntimeError("Failed to create APFS snapshot")
            unmount_root()
        finally:
            payload.unmount(self.cache_dir)
        logging.info("- Patching complete")


def revert():
    identifier, _ = detect.root_volume()
    mount_location = mount_root(identifier)
    result = runner.run(revert_args(mount_location))
    if result.returncode != 0:
        logging.error(result.stdout.rstrip())
        raise RuntimeError("Failed to revert APFS snapshot")
    unmount_root()
    logging.info("- Unpatching complete")
