import ctypes
import platform
import struct

ACPI = 0x41435049
ERROR_NOT_FOUND = 1168
TBS_E_TPM_NOT_FOUND = 0x8028400F
# rcx = leaf, edx = subleaf, r8 = uint32[4] out (Windows x64 calling convention)
CPUID_CODE = bytes.fromhex("5389c889d10fa241890041895804418948084189500c5bc3")
FOUR_GB = 0x100000000


def kernel32():
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetSystemFirmwareTable.restype = wintypes.UINT
    k32.GetSystemFirmwareTable.argtypes = [wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    k32.VirtualAlloc.restype = ctypes.c_void_p
    k32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
    k32.VirtualProtect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    k32.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD]
    k32.GetCurrentThread.restype = ctypes.c_void_p
    k32.SetThreadGroupAffinity.argtypes = [ctypes.c_void_p, ctypes.POINTER(GroupAffinity), ctypes.POINTER(GroupAffinity)]
    return k32


class GroupAffinity(ctypes.Structure):
    _fields_ = [("Mask", ctypes.c_size_t), ("Group", ctypes.c_ushort), ("Reserved", ctypes.c_ushort * 3)]


def acpi_table(signature):
    k32 = kernel32()
    table = struct.unpack("<I", signature)[0]
    size = k32.GetSystemFirmwareTable(ACPI, table, None, 0)
    if not size:
        return b"" if ctypes.get_last_error() == ERROR_NOT_FOUND else None
    buffer = ctypes.create_string_buffer(size)
    if k32.GetSystemFirmwareTable(ACPI, table, buffer, size) != size:
        return None
    return buffer.raw


def parse_dmar(data):
    if data is None:
        return None
    if not data:
        return {"present": False}
    units = drhd = rmrr = 0
    offset = 48
    while offset + 4 <= len(data):
        kind, length = struct.unpack_from("<HH", data, offset)
        if length < 4:
            break
        if kind == 0 and offset + 16 <= len(data):
            drhd += 1
            if struct.unpack_from("<Q", data, offset + 8)[0]:
                units += 1
        elif kind == 1:
            rmrr += 1
        offset += length
    return {"present": True, "drhd": drhd, "units": units, "rmrr": rmrr}


def dma_guard():
    value = ctypes.c_ubyte()
    length = ctypes.c_ulong()
    # a 4-byte buffer fails with STATUS_INFO_LENGTH_MISMATCH; the value is one byte
    status = ctypes.WinDLL("ntdll").NtQuerySystemInformation(202, ctypes.byref(value), 1, ctypes.byref(length))
    return value.value if status == 0 else None


def virtualization_enabled():
    return bool(kernel32().IsProcessorFeaturePresent(21))


def sgx_active():
    return bool(kernel32().IsEnclaveTypeSupported(1))


def firmware_type():
    value = ctypes.c_uint32()
    if not kernel32().GetFirmwareType(ctypes.byref(value)):
        return None
    return value.value


def core_groups():
    k32 = kernel32()
    size = ctypes.c_uint32()
    k32.GetLogicalProcessorInformationEx(0, None, ctypes.byref(size))
    buffer = ctypes.create_string_buffer(size.value)
    if not k32.GetLogicalProcessorInformationEx(0, buffer, ctypes.byref(size)):
        return None
    mask_format, stride = ("<QH", 16) if struct.calcsize("P") == 8 else ("<IH", 12)
    smt_cores = 0
    groups = {}
    offset = 0
    while offset < size.value:
        record = struct.unpack_from("<I", buffer, offset + 4)[0]
        smt_cores += struct.unpack_from("<B", buffer, offset + 8)[0] & 1
        for index in range(struct.unpack_from("<H", buffer, offset + 30)[0]):
            mask, group = struct.unpack_from(mask_format, buffer, offset + 32 + index * stride)
            groups[group] = groups.get(group, 0) | mask
        offset += record
    return smt_cores, groups


def smt_width(cpuid, top):
    leaf = 0x1F if top >= 0x1F and cpuid(0x1F)[1] else 0xB
    if top < leaf:
        return None
    for subleaf in range(8):
        _, ebx, ecx, _ = cpuid(leaf, subleaf)
        level = ecx >> 8 & 0xFF
        if level == 0:
            return None
        if level == 1:
            return (ebx & 0xFFFF) or None
    return None


def read_cpu(cpuid, k32, groups):
    top, ebx, ecx, edx = cpuid(0)
    vendor = struct.pack("<III", ebx, edx, ecx).decode("ascii", "replace")
    ecx1 = cpuid(1)[2]
    facts = {"vendor": vendor, "hypervisor": bool(ecx1 >> 31 & 1), "vmx": bool(ecx1 >> 5 & 1), "svm": None, "hv_root": None, "smt_width": None}
    if cpuid(0x80000000)[0] >= 0x80000001:
        facts["svm"] = bool(cpuid(0x80000001)[2] >> 2 & 1)
    if facts["hypervisor"]:
        _, ebx, ecx, edx = cpuid(0x40000000)
        hyper_v = struct.pack("<III", ebx, ecx, edx) == b"Microsoft Hv" and cpuid(0x40000001)[0] == 0x31237648
        # CreatePartitions (bit 0 of EBX) is only granted to the root partition, never to a guest
        facts["hv_root"] = hyper_v and bool(cpuid(0x40000003)[1] & 1)
    if vendor != "GenuineIntel" or not groups:
        return facts

    # hybrid CPUs report 2 threads on P-cores and 1 on E-cores, so every CPU has to be asked
    thread = k32.GetCurrentThread()
    previous = None
    widths = []
    try:
        for group, mask in sorted(groups.items()):
            for bit in range(mask.bit_length()):
                if not mask >> bit & 1:
                    continue
                old = GroupAffinity()
                if not k32.SetThreadGroupAffinity(thread, ctypes.byref(GroupAffinity(1 << bit, group)), ctypes.byref(old)):
                    return facts
                if previous is None:
                    previous = old
                widths.append(smt_width(cpuid, top))
    finally:
        if previous is not None:
            k32.SetThreadGroupAffinity(thread, ctypes.byref(previous), None)
    if widths and None not in widths:
        facts["smt_width"] = max(widths)
    return facts


def cpu(groups=None):
    if struct.calcsize("P") != 8 or platform.machine().lower() not in ("amd64", "x86_64"):
        return None
    from ctypes import wintypes
    k32 = kernel32()
    memory = k32.VirtualAlloc(None, 4096, 0x3000, 0x04)
    if not memory:
        return None
    try:
        ctypes.memmove(memory, CPUID_CODE, len(CPUID_CODE))
        old = wintypes.DWORD()
        # written read-write, then made read-execute: antivirus heuristics dislike RWX pages
        if not k32.VirtualProtect(memory, 4096, 0x20, ctypes.byref(old)):
            return None
        function = ctypes.CFUNCTYPE(None, ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32 * 4))(memory)

        def cpuid(leaf, subleaf=0):
            out = (ctypes.c_uint32 * 4)()
            function(leaf, subleaf, ctypes.byref(out))
            return tuple(out)

        return read_cpu(cpuid, k32, groups)
    finally:
        k32.VirtualFree(memory, 0, 0x8000)


def tpm():
    tbs = ctypes.WinDLL("tbs")
    tbs.Tbsip_Submit_Command.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    tbs.Tbsip_Context_Close.argtypes = [ctypes.c_void_p]
    info = (ctypes.c_uint32 * 4)()
    status = tbs.Tbsi_GetDeviceInfo(ctypes.sizeof(info), ctypes.byref(info)) & 0xFFFFFFFF
    if status == TBS_E_TPM_NOT_FOUND:
        return {"present": False}
    if status:
        return None
    facts = {"present": True, "version": info[1], "maker": None}
    if info[1] != 2:
        return facts
    params = (ctypes.c_uint32 * 2)(2, 4)
    handle = ctypes.c_void_p()
    if tbs.Tbsi_Context_Create(ctypes.byref(params), ctypes.byref(handle)):
        return facts
    try:
        # TPM2_GetCapability(TPM_CAP_TPM_PROPERTIES, TPM_PT_MANUFACTURER, 1)
        command = struct.pack(">HIIIII", 0x8001, 22, 0x17A, 6, 0x105, 1)
        out = ctypes.create_string_buffer(64)
        size = ctypes.c_uint32(64)
        if tbs.Tbsip_Submit_Command(handle, 0, 200, command, len(command), out, ctypes.byref(size)) == 0:
            raw = out.raw[:size.value]
            if len(raw) >= 14 and struct.unpack_from(">I", raw, 6)[0] == 0:
                facts["maker"] = raw[-4:].decode("ascii", "replace").strip("\0 ")
    finally:
        tbs.Tbsip_Context_Close(handle)
    return facts


def pci_bars():
    from ctypes import wintypes
    cm = ctypes.WinDLL("cfgmgr32")
    handle = ctypes.POINTER(ctypes.c_size_t)
    cm.CM_Get_First_Log_Conf.argtypes = [handle, wintypes.DWORD, wintypes.ULONG]
    cm.CM_Free_Log_Conf_Handle.argtypes = [ctypes.c_size_t]
    cm.CM_Get_Next_Res_Des.argtypes = [handle, ctypes.c_size_t, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG), wintypes.ULONG]
    cm.CM_Get_Res_Des_Data_Size.argtypes = [ctypes.POINTER(wintypes.ULONG), ctypes.c_size_t, wintypes.ULONG]
    cm.CM_Get_Res_Des_Data.argtypes = [ctypes.c_size_t, ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG]
    cm.CM_Free_Res_Des_Handle.argtypes = [ctypes.c_size_t]

    size = wintypes.ULONG()
    # CM_GETIDLIST_FILTER_ENUMERATOR | CM_GETIDLIST_FILTER_PRESENT
    if cm.CM_Get_Device_ID_List_SizeW(ctypes.byref(size), "PCI", 0x101):
        return None
    names = ctypes.create_unicode_buffer(size.value)
    if cm.CM_Get_Device_ID_ListW("PCI", names, size, 0x101):
        return None

    def prop(node, key):
        kind = wintypes.ULONG()
        length = wintypes.ULONG(0)
        cm.CM_Get_DevNode_Registry_PropertyW(node, key, ctypes.byref(kind), None, ctypes.byref(length), 0)
        if not length.value:
            return None
        data = ctypes.create_string_buffer(length.value)
        if cm.CM_Get_DevNode_Registry_PropertyW(node, key, ctypes.byref(kind), data, ctypes.byref(length), 0):
            return None
        return data.raw

    devices = []
    for device_id in [name for name in names[:size.value].split("\0") if name]:
        node = wintypes.DWORD()
        if cm.CM_Locate_DevNodeW(ctypes.byref(node), device_id, 0):
            continue
        conf = ctypes.c_size_t()
        # ALLOC_LOG_CONF: what the device actually got, not what it asked for
        if cm.CM_Get_First_Log_Conf(ctypes.byref(conf), node, 2):
            continue
        ranges = []
        try:
            for res_type in (1, 7):
                current = ctypes.c_size_t()
                res_id = wintypes.ULONG()
                status = cm.CM_Get_Next_Res_Des(ctypes.byref(current), conf.value, res_type, ctypes.byref(res_id), 0)
                while status == 0:
                    length = wintypes.ULONG()
                    cm.CM_Get_Res_Des_Data_Size(ctypes.byref(length), current.value, 0)
                    data = ctypes.create_string_buffer(length.value)
                    if length.value >= 28 and cm.CM_Get_Res_Des_Data(current.value, data, length, 0) == 0:
                        ranges.append(struct.unpack_from("<QQI", data.raw, 8))
                    following = ctypes.c_size_t()
                    status = cm.CM_Get_Next_Res_Des(ctypes.byref(following), current.value, res_type, ctypes.byref(res_id), 0)
                    cm.CM_Free_Res_Des_Handle(current.value)
                    current = following
        finally:
            cm.CM_Free_Log_Conf_Handle(conf.value)
        if not ranges:
            continue
        # the bus number is no use here: Meteor Lake and newer put SoC devices on a second root bus
        parent = wintypes.DWORD()
        parent_id = ctypes.create_unicode_buffer(512)
        bridge = None
        if cm.CM_Get_Parent(ctypes.byref(parent), node, 0) == 0 and cm.CM_Get_Device_IDW(parent, parent_id, 512, 0) == 0:
            bridge = parent_id.value.upper().startswith("PCI\\")
        compatible = prop(node, 0x03)
        devices.append({
            "id": device_id,
            "bridge": bridge,
            "compatible": [item for item in compatible.decode("utf-16-le", "replace").split("\0") if item] if compatible else [],
            "ranges": ranges
        })
    return devices


def collect():
    facts = {}
    groups = None
    try:
        smt_cores, groups = core_groups()
        facts["smt_cores"] = smt_cores
    except Exception:
        facts["smt_cores"] = None
    readers = (
        ("dmar", lambda: parse_dmar(acpi_table(b"DMAR"))),
        ("ivrs", lambda: parse_dmar(acpi_table(b"IVRS"))),
        ("dma_guard", dma_guard),
        ("pf_virt", virtualization_enabled),
        ("sgx", sgx_active),
        ("firmware_type", firmware_type),
        ("cpu", lambda: cpu(groups)),
        ("tpm", tpm),
        ("bars", pci_bars)
    )
    for name, reader in readers:
        try:
            facts[name] = reader()
        except Exception:
            facts[name] = None
    return facts
