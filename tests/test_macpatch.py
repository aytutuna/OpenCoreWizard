import contextlib
import hashlib
import importlib.util
import io
import json
import logging
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

TOOL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "postinstall", "macpatch")
FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "patches-tahoe.json")
if TOOL_DIR not in sys.path:
    sys.path.insert(0, TOOL_DIR)

import detect  # noqa: E402
import kdk  # noqa: E402
import manifest  # noqa: E402
import patchsets  # noqa: E402
import payload  # noqa: E402
import runner  # noqa: E402
import sysvol  # noqa: E402

MNT = "/System/Volumes/Update/mnt1"
SW_VERS = "ProductName:\t\tmacOS\nProductVersion:\t\t{}\nBuildVersion:\t\t{}\n"
KMUTIL_AMFIPASS = ("/usr/bin/kmutil", "showloaded")
KEXTSTAT_AMFIPASS = ("/usr/sbin/kextstat", "-list-only")


QUIET = logging.NullHandler()
BUILD_SIP = int.from_bytes(bytes.fromhex("030A0000"), "little")


def setUpModule():
    # keeps logging.info() from installing a stderr handler when the tool logs outside main()
    logging.getLogger().addHandler(QUIET)


def tearDownModule():
    logging.getLogger().removeHandler(QUIET)


def load_main():
    spec = importlib.util.spec_from_file_location("macpatch_main", os.path.join(TOOL_DIR, "__main__.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def diskutil(identifier="disk3s1s1", snapshot=True):
    return plistlib.dumps({"DeviceIdentifier": identifier, "APFSSnapshot": snapshot}).decode("utf-8")


def mac(release="25.0.0", version="26.0.1", build="25A362", filevault="FileVault is Off.\n",
        boot_args="-v keepsyms=1 amfi=0x80", amfipass=None, network=True, snapshot=True):
    responses = {
        ("/usr/bin/uname", "-r"): (0, release + "\n"),
        ("/usr/bin/sw_vers",): (0, SW_VERS.format(version, build)),
        ("/usr/sbin/diskutil", "info", "-plist", "/"): (0, diskutil(snapshot=snapshot)),
        ("/usr/bin/fdesetup", "status"): (0, filevault),
        ("/usr/sbin/nvram", "boot-args"): (0, "boot-args\t{}\n".format(boot_args)) if boot_args else (1, "nvram: Error getting variable - 'boot-args': (iokit/common) data was not found\n"),
        ("/usr/bin/curl", "-sI"): (0 if network else 6, ""),
        ("/usr/sbin/pkgutil",): (1, "No receipt\n"),
    }
    loaded = (0, "Index Refs Address Size Wired Name (Version) UUID <Linked Against>\n  210    0 0 0x4000 0x4000 com.dhinakg.AMFIPass ({}) 0C1F <6 3 2 1>\n".format(amfipass)) if amfipass else (0, "No kexts found.\n")
    responses[KMUTIL_AMFIPASS] = loaded
    responses[KEXTSTAT_AMFIPASS] = loaded
    return FakeRunner(responses, build)


REAL_SYSTEM_BUILD = detect.system_build


class FakeRunner:
    def __init__(self, responses=None, build="25A362"):
        self.responses = dict(responses or {})
        self.build = build
        self.effects = {}
        self.calls = []

    def __call__(self, args, capture=True, merge=True):
        args = [str(arg) for arg in args]
        self.calls.append(args)
        for prefix, effect in self.effects.items():
            if tuple(args[:len(prefix)]) == prefix:
                effect(args)
        best = None
        for prefix, response in self.responses.items():
            if tuple(args[:len(prefix)]) == prefix and (best is None or len(prefix) > len(best[0])):
                best = (prefix, response)
        code, output = best[1] if best else (0, "")
        return subprocess.CompletedProcess(args, code, output, "")

    def commands(self, *prefix):
        return [call for call in self.calls if tuple(call[:len(prefix)]) == prefix]


class Patched(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp()
        self.fake = mac()
        self.csr = 0xA03
        patches = [
            mock.patch.object(runner, "run", self.run_fake),
            mock.patch.object(detect, "csr_active_config", lambda: self.csr),
            mock.patch.object(detect, "system_build", lambda: self.fake.build),
            mock.patch.object(detect, "OCLP_PLIST", os.path.join(self.temp, "OpenCore-Legacy-Patcher.plist")),
            mock.patch.object(detect, "RECORD_PLIST", os.path.join(self.temp, "MacPatch.plist")),
            mock.patch.object(kdk, "KDK_INSTALL_PATH", os.path.join(self.temp, "KDKs")),
            mock.patch.object(sysvol, "CACHE_DIR", os.path.join(self.temp, "cache")),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(shutil.rmtree, self.temp, True)

    def run_fake(self, args, capture=True, merge=True):
        return self.fake(args, capture, merge)

    def host(self):
        return detect.Host()


class ManifestTest(unittest.TestCase):
    def test_fixture_parses(self):
        data = manifest.load(FIXTURE)
        self.assertEqual(manifest.patch_sets(data), ["modern_wireless", "modern_audio"])
        self.assertEqual([entry["codename"] for entry in manifest.unsupported(data)], ["Kepler"])
        self.assertEqual(payload.pin_from_manifest(data), payload.PIN)

    def test_duplicates_collapse_and_order_follows_oclp(self):
        data = manifest.validate({"version": 1, "root_patches": [
            {"set": "modern_audio"}, {"set": "modern_wireless", "device": "14E4-43A0"},
            {"set": "modern_wireless", "device": "14E4-43BA"}, {"set": "legacy_wireless"}]})
        self.assertEqual(manifest.patch_sets(data), ["legacy_wireless", "modern_wireless", "modern_audio"])

    def test_missing_payload_falls_back_to_the_pin(self):
        data = manifest.validate({"version": 1, "root_patches": []})
        self.assertEqual(payload.pin_from_manifest(data), payload.PIN)

    def test_invalid_manifests(self):
        good = manifest.load(FIXTURE)
        for broken in (
            [],
            dict(good, version=2),
            dict(good, root_patches={"set": "modern_audio"}),
            dict(good, root_patches=[{"name": "no set"}]),
            dict(good, root_patches=[{"set": "intel_haswell"}]),
            dict(good, payload=dict(good["payload"], sha256="abc")),
            dict(good, payload=dict(good["payload"], url="http://example.invalid/x.dmg")),
            dict(good, payload=dict(good["payload"], size="790749184")),
        ):
            with self.assertRaises(manifest.ManifestError):
                manifest.validate(broken)

    def test_unreadable_file(self):
        temp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp, True)
        path = os.path.join(temp, "patches.json")
        with open(path, "w") as output:
            output.write("{not json")
        with self.assertRaises(manifest.ManifestError):
            manifest.load(path)
        with self.assertRaises(manifest.ManifestError):
            manifest.load(os.path.join(temp, "missing.json"))


class PatchSetTest(unittest.TestCase):
    def test_native_os_yields_nothing(self):
        self.assertEqual(patchsets.needed(["modern_audio", "modern_wireless"], 22, "22H527"), [])
        self.assertEqual(patchsets.needed(["legacy_wireless"], 20, "20G1427"), [])

    def test_tahoe_audio_needs_kdk(self):
        sets = patchsets.needed(["modern_audio"], 25, "25A362")
        self.assertEqual(sets, ["modern_audio"])
        self.assertTrue(patchsets.requires_kdk(sets, 25))
        self.assertEqual(patchsets.merged_patches(sets, 25, 0, "25A362", "26.0.1"), {
            "Modern Audio": {patchsets.OVERWRITE_SYSTEM_VOLUME: {"/System/Library/Extensions": {"AppleHDA.kext": "26.0 Beta 1"}}}
        })

    def test_tahoe_beta_1_is_native(self):
        self.assertEqual(patchsets.needed(["modern_audio"], 25, "25A5279m"), [])
        self.assertEqual(patchsets.needed(["modern_audio"], 25, "25A5295e"), ["modern_audio"])

    def test_sonoma_wireless_versions(self):
        patches = patchsets.merged_patches(["modern_wireless"], 23, 4, "23H124", "14.6")
        self.assertEqual(list(patches), ["Modern Wireless Common", "Modern Wireless Extended"])
        common = patches["Modern Wireless Common"]
        self.assertEqual(common[patchsets.OVERWRITE_SYSTEM_VOLUME], {"/usr/libexec": {"wifip2pd": "13.7.2-23"}})
        self.assertEqual(common[patchsets.MERGE_SYSTEM_VOLUME], {"/System/Library/PrivateFrameworks": {
            "IO80211.framework": "13.7.2-23", "WiFiPeerToPeer.framework": "13.7.2-23"}})
        extended = patches["Modern Wireless Extended"]
        self.assertEqual(extended[patchsets.OVERWRITE_SYSTEM_VOLUME], {"/usr/libexec": {"airportd": "13.7.2-23"}})
        self.assertEqual(extended[patchsets.MERGE_SYSTEM_VOLUME], {
            "/System/Library/Frameworks": {"CoreWLAN.framework": "13.7.2-23"},
            "/System/Library/PrivateFrameworks": {"CoreWiFi.framework": "13.7.2-23"}})
        self.assertFalse(patchsets.requires_kdk(["modern_wireless"], 23))

    def test_sequoia_wireless_drops_extended(self):
        patches = patchsets.merged_patches(["modern_wireless"], 24, 6, "24G84", "15.6")
        self.assertEqual(list(patches), ["Modern Wireless Common"])
        self.assertEqual(patches["Modern Wireless Common"][patchsets.OVERWRITE_SYSTEM_VOLUME]["/usr/libexec"]["wifip2pd"], "13.7.2-24")

    def test_legacy_wireless_versions(self):
        monterey = patchsets.merged_patches(["legacy_wireless"], 21, 6, "21H1015", "12.7.3")
        self.assertEqual(list(monterey), ["Legacy Wireless"])
        self.assertEqual(monterey["Legacy Wireless"][patchsets.OVERWRITE_SYSTEM_VOLUME]["/usr/libexec"]["airportd"], "11.7.10")
        patched = patchsets.merged_patches(["legacy_wireless"], 21, 6, "21H1123", "12.7.4")
        self.assertEqual(patched["Legacy Wireless"][patchsets.OVERWRITE_SYSTEM_VOLUME]["/usr/libexec"]["airportd"], "11.7.10-Sandbox")

        def extended(xnu_major, version):
            entry = patchsets.merged_patches(["legacy_wireless"], xnu_major, 0, "x", version)["Legacy Wireless Extended"]
            return entry[patchsets.OVERWRITE_SYSTEM_VOLUME]["/usr/libexec"]["wps"], entry[patchsets.MERGE_SYSTEM_VOLUME]["/System/Library/Frameworks"]["CoreWLAN.framework"]

        self.assertEqual(extended(23, "14.6"), ("12.7.2", "12.7.2"))
        self.assertEqual(extended(24, "15.6"), ("12.7.2-24", "12.7.2-24"))
        self.assertEqual(extended(25, "26.0"), ("12.7.2", "12.7.2-25"))

    def test_requirements(self):
        self.assertEqual(patchsets.required_sip(["modern_audio"], 25), 0x803)
        self.assertEqual(patchsets.required_sip(["legacy_wireless"], 21), 0x802)
        self.assertEqual(patchsets.required_amfi(["modern_audio", "modern_wireless"]), patchsets.AMFI_ALLOW_ALL)
        self.assertEqual(patchsets.required_amfi([]), patchsets.AMFI_NO_CHECK)

    def test_version_tuple(self):
        self.assertEqual(patchsets.version_tuple("27"), (27, 0, 0))
        self.assertEqual(patchsets.version_tuple("26.7.2"), (26, 7, 2))
        self.assertEqual(patchsets.version_tuple("1.4.1"), (1, 4, 1))


class PreflightTest(Patched):
    def validate(self, sets=("modern_audio",), kdk_missing=False, payload_missing=False):
        return detect.validate(self.host(), list(sets), kdk_missing, payload_missing)

    def test_host_from_sw_vers_and_uname(self):
        host = self.host()
        self.assertEqual((host.xnu_major, host.xnu_minor, host.os_version, host.os_build), (25, 0, "26.0.1", "25A362"))

    def test_ready_machine_passes(self):
        self.assertEqual(self.validate(), [])
        self.assertEqual(self.fake.commands("/usr/bin/curl"), [])

    def test_sip_enabled(self):
        self.csr = 0
        failures = self.validate()
        self.assertIn(detect.SIP_ENABLED, failures)
        self.assertIn("Booted SIP: 0x0 vs expected: 0x803", failures)
        self.csr = 0x803
        self.assertEqual(self.validate(), [])
        self.assertTrue(detect.sip_enabled(0x801, 0x803))

    def test_filevault(self):
        self.fake = mac(filevault="FileVault is On.\n")
        self.assertEqual(self.validate(), [detect.FILEVAULT_ENABLED])

    def test_unsupported_host(self):
        self.fake = mac(release="26.0.0", version="27.0", build="26A428")
        self.assertIn(detect.UNSUPPORTED_HOST_OS, self.validate())
        self.fake = mac(release="19.6.0", version="10.15.7", build="19H2026")
        self.assertIn(detect.UNSUPPORTED_HOST_OS, self.validate())

    def test_network_only_checked_when_something_must_be_downloaded(self):
        self.fake = mac(network=False)
        self.assertEqual(self.validate(), [])
        self.assertEqual(self.validate(kdk_missing=True), [detect.MISSING_NETWORK_CONNECTION])
        self.assertEqual(self.validate(payload_missing=True), [detect.MISSING_NETWORK_CONNECTION])

    def test_amfi(self):
        self.fake = mac(boot_args="-v keepsyms=1")
        self.assertEqual(self.validate(), [detect.AMFI_ENABLED])
        self.fake = mac(boot_args=None)
        self.assertEqual(self.validate(), [detect.AMFI_ENABLED])
        self.fake = mac(boot_args="-v amfi_get_out_of_my_way=1")
        self.assertEqual(self.validate(), [])
        self.fake = mac(boot_args="-amfipassbeta", amfipass="1.4.1")
        self.assertEqual(self.validate(), [])
        self.fake = mac(boot_args="-amfipassbeta", amfipass="1.2.0")
        self.assertEqual(self.validate(), [detect.AMFI_ENABLED])

    def test_amfi_flags(self):
        self.assertEqual(detect.amfi_flags("amfi=0x80"), {"skip_library_validation": True, "allow_invalid_signature": True, "allow_everything": True})
        self.assertEqual(detect.amfi_flags("amfi=3"), {"skip_library_validation": False, "allow_invalid_signature": True, "allow_everything": False})
        self.assertFalse(detect.amfi_level_met(patchsets.AMFI_LIBRARY_VALIDATION_AND_SIG, "amfi=3", ""))
        self.assertTrue(detect.amfi_level_met(patchsets.AMFI_NO_CHECK, "", ""))

    def test_repatching(self):
        self.assertEqual(self.validate(), [])
        with open(detect.RECORD_PLIST, "wb") as record:
            plistlib.dump({"MacPatch": "1", "OS Version": "26.0.1 (25A362)", "Modern Wireless Common": {}}, record)
        self.assertEqual(self.validate(), [])
        self.assertTrue(detect.has_networking_patches())
        with open(detect.RECORD_PLIST, "wb") as record:
            plistlib.dump({"MacPatch": "1", "Modern Audio": {}}, record)
        self.assertEqual(self.validate(), [detect.REPATCHING_NOT_SUPPORTED])
        os.remove(detect.RECORD_PLIST)
        open(detect.OCLP_PLIST, "wb").close()
        self.assertEqual(self.validate(), [detect.REPATCHING_NOT_SUPPORTED])

    def test_root_volume(self):
        self.assertEqual(detect.root_volume(), ("disk3s1", True))
        self.fake = mac(snapshot=False)
        self.assertEqual(self.validate(), [detect.ROOT_NOT_SNAPSHOT])

    def test_revert_needs_sip(self):
        self.assertEqual(detect.validate_revert(self.host()), [])
        self.csr = 0
        self.assertEqual(detect.validate_revert(self.host()), [detect.SIP_ENABLED, "Booted SIP: 0x0 vs expected: 0x803"])


class CommandTest(unittest.TestCase):
    def test_kmutil(self):
        self.assertEqual(sysvol.kmutil_args(25, MNT), ["/usr/bin/kmutil", "create", "--allow-missing-kdk", "--volume-root", MNT, "--update-all", "--variant-suffix", "release"])
        self.assertEqual(sysvol.kmutil_args(21, MNT), ["/usr/bin/kmutil", "install", "--volume-root", MNT, "--update-all", "--variant-suffix", "release"])

    def test_kernel_collection_rebuild_decision(self):
        self.assertTrue(sysvol.rebuilds_kernel_collection(25, True))
        self.assertFalse(sysvol.rebuilds_kernel_collection(24, False))
        self.assertTrue(sysvol.rebuilds_kernel_collection(21, False))

    def test_mount_and_bless(self):
        self.assertEqual(sysvol.mount_args("disk3s1"), ["/sbin/mount", "-o", "nobrowse", "-t", "apfs", "/dev/disk3s1", MNT])
        self.assertEqual(sysvol.snapshot_args(MNT), ["/usr/sbin/bless", "--folder", MNT + "/System/Library/CoreServices", "--bootefi", "--create-snapshot"])
        self.assertEqual(sysvol.revert_args(MNT), ["/usr/sbin/bless", "--mount", MNT, "--bootefi", "--last-sealed-snapshot"])

    def test_hdiutil(self):
        cache = "/Library/Caches/OpenCoreWizard"
        self.assertEqual(payload.attach_args(cache + "/Universal-Binaries.dmg", cache), [
            "/usr/bin/hdiutil", "attach", "-noverify", cache + "/Universal-Binaries.dmg",
            "-mountpoint", os.path.join(cache, "Universal-Binaries"), "-nobrowse",
            "-shadow", os.path.join(cache, "Universal-Binaries_overlay"), "-passphrase", "password"])

    def test_kdk_merge(self):
        self.assertEqual(kdk.merge_args("/Library/Developer/KDKs/KDK_26.0.1_25A362.kdk", MNT), [
            "/usr/bin/rsync", "-r", "-i", "-a", "/Library/Developer/KDKs/KDK_26.0.1_25A362.kdk/System/Library/Extensions/", MNT + "/System/Library/Extensions"])

    def test_pins_payload(self):
        self.assertEqual(payload.PIN["psp_version"], "2.0.3")
        self.assertTrue(payload.PIN["url"].endswith("/2.0.3/Universal-Binaries.dmg"))
        self.assertEqual(payload.PIN["size"], 790749184)

    def test_find_payload_checks_size_and_hash(self):
        temp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp, True)
        with open(os.path.join(temp, payload.NAME), "wb") as dmg:
            dmg.write(b"payload")
        good = {"psp_version": "x", "size": 7, "sha256": payload.sha256_of(os.path.join(temp, payload.NAME))}
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(payload.find(good, [os.path.join(temp, "none"), temp]), os.path.join(temp, payload.NAME))
            self.assertIsNone(payload.find(dict(good, size=8), [temp]))
            self.assertIsNone(payload.find(dict(good, sha256="0" * 64), [temp]))


class FilePlanTest(Patched):
    def test_sources_and_destinations(self):
        audio = patchsets.merged_patches(["modern_audio"], 25, 0, "25A362", "26.0.1")
        self.assertEqual(sysvol.file_plan(audio, "/pay", MNT), [{
            "patch": "Modern Audio", "method": patchsets.OVERWRITE_SYSTEM_VOLUME, "file": "AppleHDA.kext",
            "source": "/pay/26.0 Beta 1/System/Library/Extensions", "destination": MNT + "/System/Library/Extensions"}])
        wireless = sysvol.file_plan(patchsets.merged_patches(["modern_wireless"], 24, 0, "24G84", "15.6"), "/pay", MNT)
        self.assertEqual([(step["method"], step["file"], step["source"]) for step in wireless], [
            (patchsets.OVERWRITE_SYSTEM_VOLUME, "wifip2pd", "/pay/13.7.2-24/usr/libexec"),
            (patchsets.MERGE_SYSTEM_VOLUME, "IO80211.framework", "/pay/13.7.2-24/System/Library/PrivateFrameworks"),
            (patchsets.MERGE_SYSTEM_VOLUME, "WiFiPeerToPeer.framework", "/pay/13.7.2-24/System/Library/PrivateFrameworks"),
        ])

    def make(self, *parts):
        path = os.path.join(self.temp, *parts)
        os.makedirs(path, exist_ok=True)
        return path

    def test_overwrite_replaces_bundle(self):
        source = self.make("pay", "Extensions", "AppleHDA.kext")
        destination = self.make("mnt", "Extensions", "AppleHDA.kext")
        source_folder, destination_folder = os.path.dirname(source), os.path.dirname(destination)
        sysvol.install_new_file(source_folder, destination_folder, "AppleHDA.kext", patchsets.OVERWRITE_SYSTEM_VOLUME)
        target = destination_folder + "/AppleHDA.kext"
        self.assertEqual(self.fake.calls, [
            ["/bin/rm", "-R", target],
            ["/bin/cp", "-R", source_folder + "/AppleHDA.kext", destination_folder],
            ["/bin/chmod", "-Rf", "755", target],
            ["/usr/sbin/chown", "-Rf", "root:wheel", target],
        ])

    def test_overwrite_single_file(self):
        source_folder = self.make("pay", "libexec")
        open(os.path.join(source_folder, "wifip2pd"), "wb").close()
        destination_folder = self.make("mnt", "libexec")
        sysvol.install_new_file(source_folder, destination_folder, "wifip2pd", patchsets.OVERWRITE_SYSTEM_VOLUME)
        target = destination_folder + "/wifip2pd"
        self.assertEqual(self.fake.calls, [
            ["/bin/cp", source_folder + "/wifip2pd", destination_folder],
            ["/bin/chmod", "755", target],
            ["/usr/sbin/chown", "root:wheel", target],
        ])

    def test_merge_uses_rsync(self):
        source_folder = os.path.dirname(self.make("pay", "PrivateFrameworks", "IO80211.framework"))
        destination_folder = os.path.dirname(self.make("mnt", "PrivateFrameworks", "IO80211.framework"))
        sysvol.install_new_file(source_folder, destination_folder, "IO80211.framework", patchsets.MERGE_SYSTEM_VOLUME)
        self.assertEqual(self.fake.calls[0], ["/usr/bin/rsync", "-r", "-i", "-a", source_folder + "/IO80211.framework", destination_folder + "/"])
        self.assertEqual(self.fake.calls[1][:3], ["/bin/chmod", "-Rf", "755"])

    def test_missing_destination_is_skipped(self):
        with contextlib.redirect_stdout(io.StringIO()):
            sysvol.install_new_file(self.make("pay"), os.path.join(self.temp, "nowhere"), "x", patchsets.OVERWRITE_SYSTEM_VOLUME)
        self.assertEqual(self.fake.calls, [])

    def test_remove_and_execute(self):
        self.make("mnt", "Extensions", "Old.kext")
        destination = os.path.join(self.temp, "mnt") + "/Extensions"
        patches = {"Test": {
            patchsets.REMOVE_SYSTEM_VOLUME: {"/Extensions": ["Old.kext", "Absent.kext"]},
            patchsets.EXECUTE: {"/usr/bin/true --flag": True},
        }}
        steps = sysvol.file_plan(patches, "/pay", os.path.join(self.temp, "mnt"))
        sysvol.execute_patchset(patches, steps)
        self.assertEqual(self.fake.calls, [["/bin/rm", "-R", destination + "/Old.kext"], ["/usr/bin/true", "--flag"]])


class KdkTest(Patched):
    LIST = [
        {"build": "26B118", "version": "27.2", "fileSize": 10, "sha256sum": "a" * 64, "url": "https://example.invalid/26B118.dmg"},
        {"build": "25G330", "version": "26.7.2", "fileSize": 10, "sha256sum": "b" * 64, "url": "https://example.invalid/25G330.dmg"},
        {"build": "25G241", "version": "26.7.1", "fileSize": 10, "sha256sum": "c" * 64, "url": "https://example.invalid/25G241.dmg"},
        {"build": "25F84", "version": "26.5.2", "fileSize": 10, "sha256sum": "d" * 64, "url": "https://example.invalid/25F84.dmg"},
        {"build": "25A362", "version": "26.0.1", "fileSize": 10, "sha256sum": "e" * 64, "url": "https://example.invalid/25A362.dmg"},
    ]

    def install(self, name, build):
        root = os.path.join(kdk.KDK_INSTALL_PATH, name)
        os.makedirs(os.path.join(root, "System", "Library", "CoreServices"))
        with open(os.path.join(root, "System", "Library", "CoreServices", "SystemVersion.plist"), "wb") as version:
            plistlib.dump({"ProductBuildVersion": build}, version)
        for kext in kdk.KEXT_CATALOG:
            path = os.path.join(root, "System", "Library", "Extensions", *kext.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "wb").close()
        return root

    def test_exact_build(self):
        self.assertEqual(kdk.pick(self.LIST, "25A362", "26.0.1"), (self.LIST[4], True))

    def test_closest_build(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(kdk.pick(self.LIST, "25G999", "26.7.3"), (self.LIST[1], False))
            self.assertEqual(kdk.pick(self.LIST, "25H5", "26.8"), (self.LIST[1], False))
            self.assertEqual(kdk.resolve("25G999", "26.7.3", self.LIST), {"kdk": self.LIST[1]})

    def test_no_build(self):
        self.assertEqual(kdk.pick(self.LIST, "25D125", "26.3"), (None, False))
        self.assertEqual(kdk.resolve("25D125", "26.3", self.LIST), {"error": "No KDKs found for 25D125 (26.3)"})

    def test_installed_kdk_wins(self):
        path = self.install("KDK_26.0.1_25A362.kdk", "25A362")
        self.assertEqual(kdk.resolve("25A362", "26.0.1", self.LIST), {"path": path})

    def test_installed_closest_match(self):
        path = self.install("KDK_26.7.2_25G330.kdk", "25G330")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(kdk.resolve("25G999", "26.7.3", self.LIST), {"path": path, "kdk": self.LIST[1]})

    def test_offline_falls_back_to_installed_version(self):
        path = self.install("KDK_26.7.2_25G330.kdk", "25G330")
        self.assertEqual(kdk.resolve("25G999", "26.7.3", None), {"path": path})
        self.assertIn("error", kdk.resolve("25F84", "26.5.2", None))

    def test_corrupted_kdk_is_ignored(self):
        path = self.install("KDK_26.0.1_25A362.kdk", "25A362")
        os.remove(os.path.join(path, "System", "Library", "Extensions", "apfs.kext", "Contents", "MacOS", "apfs"))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(kdk.installed("25A362"))

    def test_older_macos_needs_none(self):
        self.assertIn("error", kdk.resolve("21H1123", "12.7.4", self.LIST))


class MainBase(Patched):
    def setUp(self):
        super().setUp()
        self.main = load_main()
        self.log = os.path.join(self.temp, "macpatch.log")
        platform = mock.patch.object(sys, "platform", "darwin")
        platform.start()
        self.addCleanup(platform.stop)

    def run_main(self, *extra, manifest_path=FIXTURE):
        output = io.StringIO()
        argv = ["--log", self.log] + list(extra)
        if manifest_path:
            argv = ["--manifest", manifest_path] + argv
        with contextlib.redirect_stdout(output):
            code = self.main.main(argv)
        return code, output.getvalue()

    def assert_nothing_changed(self):
        for call in self.fake.calls:
            self.assertNotIn(call[0], ("/sbin/mount", "/sbin/umount", "/usr/sbin/bless", "/usr/bin/hdiutil", "/bin/cp", "/bin/rm",
                                       "/usr/bin/rsync", "/usr/sbin/installer", "/bin/chmod", "/usr/sbin/chown"), call)
            self.assertFalse(call[:2] == ["/usr/bin/kmutil", "create"] or call[:2] == ["/usr/bin/kmutil", "install"], call)
            self.assertFalse(call[0] == "/usr/bin/curl" and "-o" in call and "/dev/null" not in call, call)


class MainTest(MainBase):
    def test_dry_run_with_sip_enabled_lists_the_plan(self):
        self.csr = 0
        code, output = self.run_main("--dry-run")
        self.assertEqual(code, 2)
        self.assertIn("Patch sets in patches.json:", output)
        self.assertIn("Miscellaneous: Modern Audio (modern_audio): needed, needs a Kernel Debug Kit", output)
        self.assertIn("Networking: Modern Wireless (modern_wireless): needed [14E4-43A0 Broadcom BCM4360", output)
        self.assertIn("graphics NVIDIA GeForce GTX 680 (Kepler): not covered by MacPatch", output)
        self.assertIn("Modern Audio: overwrite /System/Library/Extensions/AppleHDA.kext <- 26.0 Beta 1/System/Library/Extensions/AppleHDA.kext", output)
        self.assertIn("Modern Wireless Common: merge /System/Library/PrivateFrameworks/IO80211.framework <- 13.7.2-25/System/Library/PrivateFrameworks/IO80211.framework", output)
        self.assertIn("Kernel Debug Kit: none installed for 25A362", output)
        self.assertIn("Payload: Universal-Binaries.dmg not found", output)
        self.assertIn("Kernel collections: rebuilt with /usr/bin/kmutil create --allow-missing-kdk", output)
        self.assertIn("System Integrity Protection is enabled", output)
        self.assertIn("Booted SIP: 0x0 vs expected: 0x803", output)
        self.assert_nothing_changed()
        with open(self.log, "r", encoding="utf-8") as log:
            text = log.read()
        self.assertIn("Command line:", text)
        self.assertIn("System Integrity Protection is enabled", text)

    def test_dry_run_that_would_patch(self):
        code, output = self.run_main("--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("Dry run: preflight passed", output)
        self.assert_nothing_changed()

    def test_nothing_to_do_on_native_macos(self):
        self.fake = mac(release="22.6.0", version="13.7.5", build="22H527")
        code, output = self.run_main("--dry-run")
        self.assertEqual(code, 3)
        self.assertIn("modern_audio): native on this macOS, skipped", output)
        self.assertIn("No root patches are needed", output)

    def test_offline_applies_wifi_first(self):
        self.fake = mac(network=False)
        with mock.patch.object(payload, "find", return_value="/cache/Universal-Binaries.dmg"):
            code, output = self.run_main("--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("applying only the Wi-Fi patches now", output)
        self.assertNotIn("Modern Audio: overwrite", output)
        self.assertIn("(modern_audio): left for the next run", output)
        self.assertIn("Kernel Debug Kit: not needed", output)

    def test_bad_manifest_is_an_error(self):
        path = os.path.join(self.temp, "patches.json")
        with open(path, "w", encoding="utf-8") as output:
            json.dump({"version": 9, "root_patches": []}, output)
        code, output = self.run_main("--dry-run", manifest_path=path)
        self.assertEqual(code, 1)
        self.assertIn("version 9 is not supported", output)

    def test_only_on_macos(self):
        with mock.patch.object(sys, "platform", "win32"):
            code, output = self.run_main("--dry-run")
        self.assertEqual(code, 1)
        self.assertEqual(self.fake.calls, [])

    def test_real_run_reexecutes_with_sudo(self):
        class Exec(Exception):
            pass

        with mock.patch.object(self.main.os, "geteuid", create=True, return_value=501), \
                mock.patch.object(self.main.os, "execv", side_effect=Exec) as execv:
            with self.assertRaises(Exec):
                self.run_main()
        command = execv.call_args[0][1]
        self.assertEqual(command, ["/usr/bin/sudo", sys.executable, "-B", TOOL_DIR, "--log", self.log, "--manifest", FIXTURE])

    def test_revert_dry_run(self):
        code, output = self.run_main("--revert", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("/usr/sbin/bless --mount {} --bootefi --last-sealed-snapshot".format(MNT), output)
        self.assert_nothing_changed()
        self.csr = 0
        self.assertEqual(self.run_main("--revert", "--dry-run")[0], 2)

    def test_full_patch_sequence(self):
        mount = os.path.join(self.temp, "mnt1")
        os.makedirs(os.path.join(mount, "System", "Library", "CoreServices"))
        os.makedirs(os.path.join(mount, "System", "Library", "Extensions"))
        with open(os.path.join(mount, "System", "Library", "CoreServices", "SystemVersion.plist"), "wb") as version:
            plistlib.dump({"ProductVersion": "26.0.1", "ProductBuildVersion": "25A362"}, version)
        cache = sysvol.CACHE_DIR
        os.makedirs(os.path.join(payload.mountpoint(cache), "26.0 Beta 1", "System", "Library", "Extensions", "AppleHDA.kext"))
        data = manifest.validate({"version": 1, "root_patches": [{"set": "modern_audio"}]})
        patcher = sysvol.RootPatcher(data, self.host(), TOOL_DIR)
        patcher.payload_path = os.path.join(cache, payload.NAME)
        self.fake.calls.clear()
        with mock.patch.object(sysvol, "MOUNT_LOCATION", mount), \
                mock.patch.object(kdk, "ensure", return_value="/Library/Developer/KDKs/KDK_26.0.1_25A362.kdk") as ensure, \
                contextlib.redirect_stdout(io.StringIO()):
            patcher.patch()
        ensure.assert_called_once_with("25A362", "26.0.1", cache, mount)
        names = [call[0] for call in self.fake.calls if call[0] not in ("/usr/sbin/diskutil",)]
        self.assertEqual(names, ["/usr/bin/hdiutil", "/bin/cp", "/bin/chmod", "/usr/sbin/chown", "/usr/bin/kmutil", "/usr/sbin/bless", "/sbin/umount"])
        self.assertEqual(self.fake.commands("/usr/bin/kmutil")[0], sysvol.kmutil_args(25, mount))
        self.assertEqual(self.fake.commands("/usr/sbin/bless")[0], sysvol.snapshot_args(mount))
        with open(os.path.join(mount, "System", "Library", "CoreServices", "MacPatch.plist"), "rb") as record:
            record = plistlib.load(record)
        self.assertEqual(record["Kernel Debug Kit Used"], "/Library/Developer/KDKs/KDK_26.0.1_25A362.kdk")
        self.assertIn("Modern Audio", record)

    def test_build_mismatch_stops_before_changes(self):
        mount = os.path.join(self.temp, "mnt1")
        os.makedirs(os.path.join(mount, "System", "Library", "CoreServices"))
        with open(os.path.join(mount, "System", "Library", "CoreServices", "SystemVersion.plist"), "wb") as version:
            plistlib.dump({"ProductVersion": "26.1", "ProductBuildVersion": "25B78"}, version)
        data = manifest.validate({"version": 1, "root_patches": [{"set": "modern_audio"}]})
        patcher = sysvol.RootPatcher(data, self.host(), TOOL_DIR)
        patcher.payload_path = "/cache/Universal-Binaries.dmg"
        self.fake.calls.clear()
        with mock.patch.object(sysvol, "MOUNT_LOCATION", mount), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(RuntimeError):
                patcher.patch()
        self.assertEqual([call[0] for call in self.fake.calls if call[0] != "/usr/sbin/diskutil"], ["/usr/bin/hdiutil", "/sbin/umount"])


class RevertAndBuildTest(MainBase):
    def write_plist(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as output:
            plistlib.dump(data, output)

    def test_build_comes_from_system_version_not_rsr(self):
        plist = os.path.join(self.temp, "SystemVersion.plist")
        self.write_plist(plist, {"ProductVersion": "13.4.1", "ProductBuildVersion": "22F82"})
        self.fake = mac(release="22.5.0", version="13.4.1", build="22F770820d")
        with mock.patch.object(detect, "system_build", REAL_SYSTEM_BUILD), mock.patch.object(detect, "SYSTEM_VERSION_PLIST", plist):
            host = detect.Host()
        self.assertEqual((host.os_version, host.os_build), ("13.4.1", "22F82"))

    def test_revert_needs_neither_snapshot_nor_manifest(self):
        self.fake = mac(snapshot=False)
        self.assertEqual(detect.validate_revert(self.host()), [])
        code, output = self.run_main("--revert", "--dry-run", manifest_path=None)
        self.assertEqual(code, 0)
        self.assertIn("--last-sealed-snapshot", output)
        code, _ = self.run_main("--revert", "--dry-run", manifest_path=os.path.join(self.temp, "lost.json"))
        self.assertEqual(code, 0)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.main.parse_args(["--dry-run"])

    def test_real_run_keeps_the_machine_awake(self):
        with mock.patch.object(self.main.os, "geteuid", create=True, return_value=0), \
                mock.patch.object(self.main.subprocess, "Popen") as popen, \
                mock.patch.object(sysvol.RootPatcher, "patch") as patch:
            code, output = self.run_main()
        self.assertEqual(code, 0)
        popen.assert_called_once_with(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())])
        patch.assert_called_once_with()
        self.assertIn("run MacPatch again after every update", output)
        with mock.patch.object(self.main.os, "geteuid", create=True, return_value=0), \
                mock.patch.object(self.main.subprocess, "Popen") as popen, \
                mock.patch.object(sysvol, "revert") as revert:
            self.assertEqual(self.run_main("--revert", manifest_path=None)[0], 0)
        popen.assert_called_once()
        revert.assert_called_once_with()

    def test_dry_run_accepts_closest_installed_kdk(self):
        root = os.path.join(kdk.KDK_INSTALL_PATH, "KDK_26.0_25A354.kdk")
        self.write_plist(os.path.join(root, "System", "Library", "CoreServices", "SystemVersion.plist"), {"ProductBuildVersion": "25A354"})
        for kext in kdk.KEXT_CATALOG:
            path = os.path.join(root, "System", "Library", "Extensions", *kext.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "wb").close()
        code, output = self.run_main("--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("Kernel Debug Kit: installed at {}".format(root), output)

    def test_wifi_first_then_full_run_is_allowed(self):
        mount = os.path.join(self.temp, "mnt1")
        os.makedirs(os.path.join(mount, "System", "Library", "CoreServices"))
        wifi = patchsets.merged_patches(["legacy_wireless"], 22, 6, "22G621", "13.6.5")
        self.assertEqual(set(wifi), {"Legacy Wireless", "Legacy Wireless Extended"})
        sysvol.write_record(mount, wifi, None, "2.0.3", "13.6.5", "22G621")
        with mock.patch.object(detect, "RECORD_PLIST", os.path.join(mount, detect.RECORD_PATH)):
            self.assertTrue(detect.has_networking_patches())
            self.assertFalse(detect.repatching_not_supported())
            sysvol.write_record(mount, patchsets.merged_patches(["modern_wireless"], 23, 6, "23G93", "14.6"), None, "2.0.3", "14.6", "23G93")
            self.assertFalse(detect.repatching_not_supported())


class DownloadTest(Patched):
    def writer(self, content):
        def effect(args):
            with open(args[args.index("-o") + 1], "wb") as output:
                output.write(content)
        return effect

    def entry(self, content, sha=True):
        return {"build": "25A362", "name": "KDK", "url": "https://example.invalid/kdk.dmg", "fileSize": len(content),
                "sha256sum": hashlib.sha256(content).hexdigest() if sha else None}

    def test_kdk_download_with_hash(self):
        kdk_entry = self.entry(b"kdk image")
        self.fake.effects[("/usr/bin/curl", "-L")] = self.writer(b"kdk image")
        path = kdk.download(kdk_entry, self.temp)
        self.assertEqual(self.fake.calls, [["/usr/bin/curl", "-L", "--fail", "--progress-bar", "-o", path, "https://example.invalid/kdk.dmg"]])
        self.fake.calls.clear()
        self.assertEqual(kdk.download(kdk_entry, self.temp), path)
        self.assertEqual(self.fake.calls, [])

    def test_kdk_download_without_hash_runs_hdiutil_verify(self):
        kdk_entry = self.entry(b"kdk image", sha=False)
        self.fake.effects[("/usr/bin/curl", "-L")] = self.writer(b"kdk image")
        path = kdk.download(kdk_entry, self.temp)
        self.assertEqual(self.fake.calls[-1], ["/usr/bin/hdiutil", "verify", path])
        os.remove(path)
        self.fake.responses[("/usr/bin/hdiutil", "verify")] = (1, "checksum mismatch")
        with self.assertRaises(RuntimeError):
            kdk.download(kdk_entry, self.temp)
        self.assertFalse(os.path.exists(path))

    def test_partial_kdk_is_deleted(self):
        self.fake.effects[("/usr/bin/curl", "-L")] = self.writer(b"kdk")
        with self.assertRaises(RuntimeError):
            kdk.download(self.entry(b"kdk image"), self.temp)
        self.fake.responses[("/usr/bin/curl", "-L")] = (22, "")
        with self.assertRaises(RuntimeError):
            kdk.download(self.entry(b"kdk"), self.temp)
        self.assertEqual(os.listdir(self.temp), [])

    def test_kdk_install_commands(self):
        state = {}

        def attach(args):
            state["mount"] = args[args.index("-mountpoint") + 1]
            open(os.path.join(state["mount"], "KernelDebugKit.pkg"), "wb").close()

        def detach(args):
            os.remove(os.path.join(args[2], "KernelDebugKit.pkg"))

        self.fake.effects[("/usr/bin/hdiutil", "attach")] = attach
        self.fake.effects[("/usr/bin/hdiutil", "detach")] = detach
        kdk.install_dmg("/cache/KDK_25A362.dmg")
        mount = state["mount"]
        self.assertEqual(self.fake.calls, [
            ["/usr/bin/hdiutil", "attach", "/cache/KDK_25A362.dmg", "-mountpoint", mount, "-nobrowse"],
            ["/usr/sbin/installer", "-pkg", os.path.join(mount, "KernelDebugKit.pkg"), "-target", "/"],
            ["/usr/bin/hdiutil", "detach", mount],
        ])
        self.assertFalse(os.path.exists(mount))

    def test_payload_download_and_detach(self):
        content = b"universal binaries"
        pin = {"psp_version": "2.0.3", "url": "https://example.invalid/Universal-Binaries.dmg", "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        self.fake.effects[("/usr/bin/curl", "-L")] = self.writer(content)
        cache = os.path.join(self.temp, "cache")
        path = payload.download(pin, cache)
        self.assertEqual(self.fake.calls, [["/usr/bin/curl", "-L", "--fail", "--progress-bar", "-o", path + ".part", pin["url"]]])
        self.assertEqual(path, os.path.join(cache, payload.NAME))
        self.fake.calls.clear()
        open(os.path.join(cache, "Universal-Binaries_overlay"), "wb").close()
        with mock.patch.object(payload.os.path, "ismount", return_value=True):
            payload.unmount(cache)
        self.assertEqual(self.fake.calls, [["/usr/bin/hdiutil", "detach", payload.mountpoint(cache), "-force"]])
        self.assertFalse(os.path.exists(os.path.join(cache, "Universal-Binaries_overlay")))


class PayloadCheckScriptTest(unittest.TestCase):
    def test_case_check_against_a_synthetic_payload(self):
        spec = importlib.util.spec_from_file_location("check_payload", os.path.join(os.path.dirname(os.path.abspath(__file__)), "ci", "check_payload.py"))
        check_payload = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(check_payload)
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        for case in check_payload.CASES:
            xnu_major, xnu_minor, build, version = case
            patches = patchsets.merged_patches(patchsets.needed(list(patchsets.SETS), xnu_major, build), xnu_major, xnu_minor, build, version)
            for step in sysvol.file_plan(patches, root, ""):
                path = os.path.join(step["source"], step["file"])
                if step["file"].endswith((".kext", ".framework", ".app")):
                    os.makedirs(path, exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    open(path, "wb").close()
        hda = os.path.join(root, "26.0 Beta 1", "System", "Library", "Extensions", "AppleHDA.kext", "Contents", "MacOS")
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertFalse(check_payload.check_case(root, *check_payload.CASES[-1]))
            os.makedirs(hda)
            open(os.path.join(hda, "AppleHDA"), "wb").close()
            self.assertTrue(all(check_payload.check_case(root, *case) for case in check_payload.CASES))
            shutil.rmtree(os.path.join(root, "13.7.2-24"))
            self.assertFalse(check_payload.check_case(root, *check_payload.CASES[6]))
        self.assertIn("has no Contents/MacOS/AppleHDA", output.getvalue())
        self.assertIn("missing", output.getvalue())


class LauncherTest(unittest.TestCase):
    def test_launchers(self):
        for name, tail in (("Root Patch.command", "--manifest ../../patches.json"), ("Revert Root Patch.command", "--revert --log ../../macpatch.log")):
            with open(os.path.join(TOOL_DIR, name), "rb") as launcher:
                text = launcher.read()
            self.assertNotIn(b"\r", text)
            text = text.decode("ascii")
            self.assertTrue(text.startswith("#!/bin/bash\n"))
            self.assertIn("xcode-select --install", text)
            self.assertIn("sys.version_info < (3, 9)", text)
            self.assertIn("/usr/bin/python3 -B . {} \"$@\"".format(tail), text)

    def test_ported_files_carry_the_notice(self):
        for name in ("patchsets.py", "detect.py", "payload.py", "kdk.py", "sysvol.py"):
            with open(os.path.join(TOOL_DIR, name), "r", encoding="utf-8") as source:
                self.assertEqual(source.readline().strip(), "# Adapted from OpenCore Legacy Patcher 3.0.0 (sys_patch), BSD-4-Clause, see LICENSE.")
        with open(os.path.join(TOOL_DIR, "LICENSE"), "r", encoding="utf-8") as licence:
            self.assertIn("This product includes software developed by Dortania and OpenCore Legacy Patcher contributors.", licence.read())


class BuildSipTest(unittest.TestCase):
    # the kit only ships MacPatch with builds that need root patches, and those get csr-active-config 030A0000
    def test_build_value_passes_the_sip_check_for_every_set(self):
        for xnu in range(patchsets.BIG_SUR, patchsets.TAHOE + 1):
            self.assertFalse(detect.sip_enabled(BUILD_SIP, patchsets.required_sip(list(patchsets.SETS), xnu)), xnu)
            self.assertFalse(detect.sip_enabled(BUILD_SIP, patchsets.root_patch_sip(xnu)), xnu)
        self.assertTrue(detect.sip_enabled(0, patchsets.root_patch_sip(patchsets.TAHOE)))


if __name__ == "__main__":
    unittest.main()
