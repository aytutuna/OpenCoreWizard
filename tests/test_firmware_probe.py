import struct
import unittest
from unittest import mock

import repo_path  # noqa: F401
from Scripts import firmware_probe


def dmar(*structures):
    return b"DMAR" + bytes(32) + bytes([38, 0x05]) + bytes(10) + b"".join(structures)


def drhd(base):
    return struct.pack("<HHBBHQ", 0, 16, 1, 0, 0, base)


def rmrr():
    return struct.pack("<HHHHQQ", 1, 24, 0, 0, 0x7B800000, 0x7FFFFFFF)


def fake_cpuid(table):
    return lambda leaf, subleaf=0: table.get((leaf, subleaf), (0, 0, 0, 0))


def vendor(name):
    ebx, edx, ecx = struct.unpack("<III", name.encode())
    return (0x20, ebx, ecx, edx)


class FakeKernel:
    def __init__(self):
        self.pinned = []

    def GetCurrentThread(self):
        return -2

    def SetThreadGroupAffinity(self, thread, affinity, previous):
        self.pinned.append(affinity._obj.Mask)
        if previous is not None:
            previous._obj.Mask = 0xFF
        return 1


class ParseDmarTest(unittest.TestCase):
    def test_units_and_reserved_regions(self):
        self.assertEqual(firmware_probe.parse_dmar(dmar(drhd(0xFC800000), drhd(0xFC810000), rmrr())), {"present": True, "drhd": 2, "units": 2, "rmrr": 1})

    def test_zero_base_is_not_a_unit(self):
        self.assertEqual(firmware_probe.parse_dmar(dmar(drhd(0))), {"present": True, "drhd": 1, "units": 0, "rmrr": 0})

    def test_absent_and_unreadable(self):
        self.assertEqual(firmware_probe.parse_dmar(b""), {"present": False})
        self.assertIsNone(firmware_probe.parse_dmar(None))

    def test_bad_length_stops(self):
        self.assertEqual(firmware_probe.parse_dmar(dmar(struct.pack("<HH", 0, 0)))["drhd"], 0)


class CpuidTest(unittest.TestCase):
    def test_smt_width_prefers_leaf_1f(self):
        cpuid = fake_cpuid({(0x1F, 0): (0, 1, 0x100, 0), (0x1F, 1): (0, 24, 0x201, 0)})
        self.assertEqual(firmware_probe.smt_width(cpuid, 0x23), 1)

    def test_smt_width_from_leaf_b(self):
        cpuid = fake_cpuid({(0xB, 0): (0, 2, 0x100, 0), (0xB, 1): (0, 12, 0x201, 0)})
        self.assertEqual(firmware_probe.smt_width(cpuid, 0x16), 2)

    def test_smt_width_missing(self):
        self.assertIsNone(firmware_probe.smt_width(fake_cpuid({}), 0x0A))
        self.assertIsNone(firmware_probe.smt_width(fake_cpuid({(0xB, 0): (0, 0, 0x100, 0)}), 0x16))

    def test_hyper_v_root_partition(self):
        table = {
            (0, 0): vendor("GenuineIntel"), (1, 0): (0, 0, 1 << 31 | 1 << 5, 0), (0x80000000, 0): (0x80000008, 0, 0, 0),
            (0x40000000, 0): (0x4000000B,) + struct.unpack("<III", b"Microsoft Hv"), (0x40000001, 0): (0x31237648, 0, 0, 0),
            (0x40000003, 0): (0, 1, 0, 0)
        }
        facts = firmware_probe.read_cpu(fake_cpuid(table), None, None)
        self.assertEqual((facts["vendor"], facts["hypervisor"], facts["vmx"], facts["hv_root"]), ("GenuineIntel", True, True, True))
        table[(0x40000003, 0)] = (0, 0, 0, 0)
        self.assertFalse(firmware_probe.read_cpu(fake_cpuid(table), None, None)["hv_root"])
        table[(0x40000000, 0)] = (0x40000001,) + struct.unpack("<III", b"KVMKVMKVM\0\0\0")
        self.assertFalse(firmware_probe.read_cpu(fake_cpuid(table), None, None)["hv_root"])

    def test_amd_svm(self):
        table = {(0, 0): vendor("AuthenticAMD"), (1, 0): (0, 0, 0, 0), (0x80000000, 0): (0x80000020, 0, 0, 0), (0x80000001, 0): (0, 0, 1 << 2, 0)}
        facts = firmware_probe.read_cpu(fake_cpuid(table), None, {0: 0xF})
        self.assertTrue(facts["svm"])
        self.assertIsNone(facts["smt_width"])

    def test_widest_cpu_wins_and_affinity_is_restored(self):
        kernel = FakeKernel()

        def cpuid(leaf, subleaf=0):
            if (leaf, subleaf) == (0, 0):
                return vendor("GenuineIntel")
            if leaf == 0xB:
                width = 2 if kernel.pinned[-1] == 1 else 1
                return (0, width, 0x100, 0) if subleaf == 0 else (0, 0, 0, 0)
            return (0, 0, 0, 0)

        facts = firmware_probe.read_cpu(cpuid, kernel, {0: 0b101})
        self.assertEqual(facts["smt_width"], 2)
        self.assertEqual(kernel.pinned, [1, 4, 0xFF])


class CollectTest(unittest.TestCase):
    def test_everything_is_unknown_without_the_windows_api(self):
        with mock.patch.object(firmware_probe.ctypes, "WinDLL", side_effect=OSError("no such library"), create=True):
            facts = firmware_probe.collect()
        self.assertEqual(set(facts), {"smt_cores", "dmar", "ivrs", "dma_guard", "pf_virt", "sgx", "firmware_type", "cpu", "tpm", "bars"})
        self.assertEqual(set(facts.values()), {None})

    def test_cpuid_needs_64_bit_x86(self):
        with mock.patch.object(firmware_probe.platform, "machine", return_value="ARM64"):
            self.assertIsNone(firmware_probe.cpu())


if __name__ == "__main__":
    unittest.main()
