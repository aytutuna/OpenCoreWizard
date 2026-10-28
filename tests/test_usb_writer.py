import contextlib
import io
import json
import unittest
from unittest import mock

import repo_path  # noqa: F401
from Scripts import usb_writer

GB = 1000 ** 3
IS_WINDOWS = usb_writer.os.name == "nt"


def disk(number, size=16 * GB, **overrides):
    item = {"Number": number, "FriendlyName": "Disk {}".format(number), "Size": size, "BusType": "USB",
            "PartitionStyle": "RAW", "IsBoot": False, "IsSystem": False, "IsOffline": False,
            "IsReadOnly": False, "UniqueId": "ID{}".format(number), "SerialNumber": "SN{}".format(number)}
    item.update(overrides)
    return item


class DiskpartScriptTest(unittest.TestCase):
    def setUp(self):
        self.writer = usb_writer.USBWriter()

    def test_small_disk_has_no_size(self):
        script = self.writer.build_diskpart_script(2, 16 * 1024)
        self.assertNotIn("size=", script)
        self.assertIn("select disk 2", script)

    def test_large_disk_capped(self):
        script = self.writer.build_diskpart_script(2, 64 * 1024)
        self.assertIn("size=32000", script)

    def test_script_commands(self):
        script = self.writer.build_diskpart_script(3, 16 * 1024)
        for line in ("clean", "convert gpt", "create partition primary"):
            self.assertIn(line, script)
        # the format runs separately, once Windows lists the new volume
        self.assertNotIn("format", script)

    def test_mbr_and_raw_disks_converted(self):
        for style in ("MBR", "RAW"):
            script = self.writer.build_diskpart_script(3, 16 * 1024, partition_style=style)
            self.assertIn("convert gpt", script)

    def test_gpt_disk_not_converted(self):
        script = self.writer.build_diskpart_script(3, 16 * 1024, partition_style="GPT")
        self.assertNotIn("convert", script)
        self.assertLess(script.index("clean"), script.index("create partition primary"))

    def test_format_selects_the_partition_and_dismounts_it(self):
        script = self.writer.build_format_script(3, 2)
        self.assertLess(script.index("select disk 3"), script.index("select partition 2"))
        self.assertLess(script.index("select partition 2"), script.index("format fs=fat32 quick label=OPENCORE override"))
        self.assertIn("assign", script)


CREATED = ("DiskPart succeeded in creating the specified partition.\n", "", 0)
FORMATTED = ("DiskPart successfully formatted the volume.\n", "", 0)
NO_VOLUME = ("There is no volume selected.\nPlease select a volume and try again.\n", "", 2147942487)
DENIED = ("DiskPart has encountered an error: Access is denied.\n", "", 5)


class PrepareDiskRetryTest(unittest.TestCase):
    def prepare(self, results, changed=None, style="GPT", volume=1):
        writer = usb_writer.USBWriter()
        scripts = []

        def run(command):
            with open(command["args"][2]) as f:
                scripts.append(f.read())
            return results.pop(0)
        writer.r.run = run
        stick = {"number": 3, "size_bytes": 16 * GB, "partition_style": "MBR", "identity": "A"}
        with mock.patch.object(writer, "_supported", return_value=True), \
                mock.patch.object(writer, "check_disk_unchanged", return_value=changed), \
                mock.patch.object(writer, "get_disk", return_value={"PartitionStyle": style}), \
                mock.patch.object(writer, "wait_for_volume", return_value=volume) as wait, \
                mock.patch.object(usb_writer.time, "sleep"), \
                contextlib.redirect_stdout(io.StringIO()):
            return writer.prepare_disk(stick), scripts, wait

    def test_formats_in_a_second_run_once_the_volume_is_there(self):
        result, scripts, wait = self.prepare([CREATED, FORMATTED], volume=2)
        self.assertEqual(result[0], 0)
        self.assertEqual(len(scripts), 2)
        self.assertIn("create partition primary", scripts[0])
        self.assertIn("select partition 2", scripts[1])
        self.assertIn("format fs=fat32", scripts[1])
        wait.assert_called_once_with(3)

    def test_failed_format_is_tried_again_without_erasing_again(self):
        # the run that failed on the stick: the volume was not there yet when the format started
        result, scripts, wait = self.prepare([CREATED, NO_VOLUME, FORMATTED])
        self.assertEqual(result[0], 0)
        self.assertEqual(len(scripts), 3)
        self.assertNotIn("clean", scripts[2])
        self.assertIn("format fs=fat32", scripts[2])

    def test_failed_set_up_is_tried_again_with_the_current_layout(self):
        result, scripts, wait = self.prepare([DENIED, CREATED, FORMATTED])
        self.assertEqual(result[0], 0)
        self.assertIn("convert gpt", scripts[0])
        self.assertNotIn("convert", scripts[1])

    def test_gives_up_after_three_tries(self):
        result, scripts, wait = self.prepare([CREATED] + [NO_VOLUME] * 3)
        self.assertEqual(len(scripts), 4)
        self.assertNotEqual(result[0], 0)
        self.assertIn("no volume selected", result[1])

    def test_no_retry_on_a_different_disk(self):
        result, scripts, wait = self.prepare([DENIED], changed="The identity of disk 3 changed.")
        self.assertEqual(len(scripts), 1)
        self.assertNotEqual(result[0], 0)
        self.assertIn("identity of disk 3 changed", result[1])

    def test_volume_number_comes_from_windows(self):
        writer = usb_writer.USBWriter()
        with mock.patch.object(writer, "_powershell", return_value=("WARNING: noise\n2\n", "", 0)):
            self.assertEqual(writer.wait_for_volume(3, timeout_s=0), 2)
        with mock.patch.object(writer, "_powershell", return_value=("", "", 0)):
            self.assertIsNone(writer.wait_for_volume(3, timeout_s=0))


@unittest.skipUnless(IS_WINDOWS, "Windows only")
class CopyPayloadTest(unittest.TestCase):
    def setUp(self):
        self.writer = usb_writer.USBWriter()

    def run_copy(self, drive):
        with mock.patch.object(usb_writer.shutil, "copytree") as copytree, \
                mock.patch.object(usb_writer.shutil, "rmtree") as rmtree, \
                contextlib.redirect_stdout(io.StringIO()):
            result = self.writer.copy_payload(drive, "no-such-efi")
        copytree.assert_not_called()
        rmtree.assert_not_called()
        return result

    def test_empty_drive_rejected(self):
        ok, problems = self.run_copy("")
        self.assertFalse(ok)
        self.assertTrue(problems)

    def test_root_path_rejected(self):
        ok, problems = self.run_copy("C:\\")
        self.assertFalse(ok)
        self.assertTrue(problems)


@unittest.skipUnless(IS_WINDOWS, "Windows only")
class PowerShellParsingTest(unittest.TestCase):
    def setUp(self):
        self.writer = usb_writer.USBWriter()

    def test_drive_letter_ignores_noise(self):
        output = ("WARNING: something happened\n", "", 0)
        with mock.patch.object(self.writer, "_powershell", return_value=output):
            self.assertIsNone(self.writer.wait_for_drive_letter(2, timeout_s=0))

    def test_drive_letter_accepts_letter(self):
        output = ("WARNING: something happened\nE\n", "", 0)
        with mock.patch.object(self.writer, "_powershell", return_value=output):
            self.assertEqual(self.writer.wait_for_drive_letter(2, timeout_s=0), "E:")

    def list_disks(self, payload):
        with mock.patch.object(self.writer, "_powershell", return_value=(json.dumps(payload), "", 0)), \
                contextlib.redirect_stdout(io.StringIO()):
            return self.writer.list_usb_disks()

    def test_filter_drops_unsafe_disks(self):
        payload = [
            disk(0, BusType="NVMe", IsBoot=True),
            disk(1, BusType="NVMe"),
            disk(2, IsBoot=True),
            disk(3, size=GB // 2),
            disk(4, IsOffline=True),
            disk(5),
        ]
        result = self.list_disks(payload)
        self.assertEqual([d["number"] for d in result], [5])

    def test_single_object_output(self):
        result = self.list_disks(disk(7))
        self.assertEqual([d["number"] for d in result], [7])
        self.assertEqual(self.list_disks(disk(8, BusType="NVMe")), [])


class ExpectedDiskTest(unittest.TestCase):
    def run_installer(self, listed, expected):
        writer = usb_writer.USBWriter()
        prompts = []
        writer.utils.request_input = lambda text="": prompts.append(text) or "3"
        buffer = io.StringIO()
        with mock.patch.object(writer, "_supported", return_value=True),                 mock.patch.object(writer, "is_admin", return_value=True),                 mock.patch.object(writer, "list_usb_disks", return_value=[listed]),                 mock.patch.object(writer, "_confirm", return_value=True),                 mock.patch.object(usb_writer.os.path, "isfile", return_value=True),                 contextlib.redirect_stdout(buffer):
            writer.create_installer("efi", "recovery", dry_run=True, expected_disk=expected)
        return prompts, buffer.getvalue()

    def entry(self, **overrides):
        item = {"number": 3, "friendly_name": "Stick", "size_bytes": 16 * GB, "partition_style": "RAW", "identity": "A"}
        item.update(overrides)
        return item

    def test_swapped_disk_aborts_before_erase_prompt(self):
        prompts, output = self.run_installer(self.entry(identity="B"), self.entry())
        self.assertIn("not the drive that was selected", output)
        self.assertFalse(any("ERASE" in text for text in prompts))

    def test_size_change_aborts(self):
        prompts, output = self.run_installer(self.entry(size_bytes=8 * GB), self.entry())
        self.assertIn("not the drive that was selected", output)

    def test_matching_disk_reaches_erase_prompt(self):
        prompts, output = self.run_installer(self.entry(), self.entry())
        self.assertNotIn("not the drive that was selected", output)
        self.assertTrue(any("ERASE" in text for text in prompts))

    def write(self, copied):
        writer = usb_writer.USBWriter()
        answers = iter(["3", "ERASE"])
        writer.utils.request_input = lambda text="": next(answers)
        with mock.patch.object(writer, "_supported", return_value=True), \
                mock.patch.object(writer, "is_admin", return_value=True), \
                mock.patch.object(writer, "list_usb_disks", return_value=[self.entry()]), \
                mock.patch.object(writer, "check_disk_unchanged", return_value=None), \
                mock.patch.object(writer, "prepare_disk", return_value=(0, "")), \
                mock.patch.object(writer, "wait_for_drive_letter", return_value="E:"), \
                mock.patch.object(writer, "copy_payload", return_value=copied), \
                mock.patch.object(usb_writer.os.path, "isfile", return_value=True), \
                contextlib.redirect_stdout(io.StringIO()):
            return writer.create_installer("efi", "recovery", expected_disk=self.entry())

    def test_reports_whether_the_stick_was_written(self):
        self.assertIs(self.write((True, [])), True)
        self.assertIs(self.write((False, ["SHA-256 differs: E:\\EFI\\OC\\config.plist"])), False)


if __name__ == "__main__":
    unittest.main()
