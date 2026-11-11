"""Partition layout: `edl printgpt` output, A/B slot flags, liblp (super) metadata, filesystem magic."""
import re, struct

SECTOR = 4096

# Qualcomm A/B attribute byte = bits 48-55 of the GPT entry flags
AB_ACTIVE, AB_SUCCESSFUL, AB_UNBOOTABLE = 0x04, 0x40, 0x80

PRINTGPT_LINE = re.compile(r'^(\S+):\s+Offset 0x([0-9a-f]+), Length 0x([0-9a-f]+), Flags 0x([0-9a-f]+)', re.M | re.I)


def parse_printgpt(text, sector=SECTOR):
    """{name: {'lun', 'start', 'sectors', 'bytes', 'flags'}} from the full `edl printgpt` output (all LUNs)."""
    parts, lun = {}, None
    for line in text.splitlines():
        m = re.match(r'\s*Parsing Lun (\d+):', line)
        if m:
            lun = int(m.group(1))
            continue
        m = PRINTGPT_LINE.match(line.strip())
        if m and lun is not None:
            off, ln, fl = (int(m.group(i), 16) for i in (2, 3, 4))
            parts[m.group(1)] = {'lun': lun, 'start': off // sector, 'sectors': ln // sector, 'bytes': ln, 'flags': fl}
    return parts


def ab_state(flags):
    b = (flags >> 48) & 0xFF
    return {'active': bool(b & AB_ACTIVE), 'successful': bool(b & AB_SUCCESSFUL),
            'unbootable': bool(b & AB_UNBOOTABLE), 'priority': b & 0x3, 'retry': (b >> 3) & 0x7}


def active_slot(parts):
    """'_a' / '_b' from the boot_a/boot_b attributes, None if not exactly one is active."""
    act = [s for s in ('_a', '_b') if f'boot{s}' in parts and ab_state(parts[f'boot{s}']['flags'])['active']]
    return act[0] if len(act) == 1 else None


def sahara_info(text):
    """HWID / PK hash / chip serial printed by edl while connecting (only on the first connect after 9008)."""
    out = {}
    # newer edl: "HW_ID:", "OEM PKHASH:", "Chip Serial Number:"; older edl: "HWID:", "PK_HASH:", "Serial:" (0x...)
    for key, rx in (('hwid', r'\bHW_?ID\s*:\s*(?:0x)?([0-9a-fA-F]+)'),
                    ('pk_hash', r'(?:OEM PKHASH|\bPK_HASH)\s*:\s*(?:0x)?([0-9a-fA-F]+)'),
                    ('chip_serial', r'(?:Chip Serial Number|\bSerial)\s*:\s*(?:0x)?([0-9a-fA-F]+)')):
        m = re.search(rx, text)
        if m:
            out[key] = m.group(1).lower()
    return out


# ---------------------------------------------------------------- super (liblp)

LP_GEOM_MAGIC, LP_HDR_MAGIC = 0x616C4467, 0x414C5030
LP_HEAD_BYTES = 4096 * 3 + 65536 * 2   # reserved + 2 geometry copies + room for the first metadata slot
LP_METADATA_START = 4096 * 3           # first metadata slot, after the reserved area and the two geometry copies


def lp_slot_index(slot):
    """liblp metadata slot of a boot slot: '_a' / None -> 0, '_b' -> 1."""
    return 1 if slot == '_b' else 0


def lp_bytes_needed(head, slot_index=0):
    """How many bytes from the start of super hold geometry + metadata slot `slot_index`."""
    if len(head) < 4096 * 2 or struct.unpack_from('<I', head, 4096)[0] != LP_GEOM_MAGIC:
        raise ValueError('super 开头没有 liblp geometry')
    max_size, slot_count = struct.unpack_from('<II', head, 4096 + 40)
    if slot_index >= slot_count:
        raise ValueError(f'super 只有 {slot_count} 个 metadata 槽')
    return LP_METADATA_START + (slot_index + 1) * max_size


def parse_lp(head, slot_index=0):
    """Partitions in metadata slot `slot_index` of a super image -> {name: [(byte_offset, byte_len), ...]}.

    head = the first lp_bytes_needed(head, slot_index) bytes (LP_HEAD_BYTES is enough for slot 0)."""
    need = lp_bytes_needed(head, slot_index)
    max_size = struct.unpack_from('<I', head, 4096 + 40)[0]
    off = LP_METADATA_START + slot_index * max_size
    if len(head) < min(need, off + 128):
        raise ValueError('super metadata 读取不完整')
    magic, _, _, hsize = struct.unpack_from('<IHHI', head, off)
    if magic != LP_HDR_MAGIC:
        raise ValueError(f'super 的 metadata 槽 {slot_index} 里没有 liblp header')
    tsize = struct.unpack_from('<I', head, off + 44)[0]
    if hsize < 128 or hsize + tsize > max_size:   # 128 = header v10.0; one metadata copy holds at most max_size
        raise ValueError(f'super metadata 损坏（header {hsize} + 表 {tsize} 字节超出单槽 {max_size}）')
    pt_off, pt_n, pt_sz, ex_off, ex_n, ex_sz = struct.unpack_from('<6I', head, off + 80)
    tables = head[off + hsize:off + hsize + tsize]
    if len(tables) < tsize:
        raise ValueError('super metadata 读取不完整')
    # table descriptors: entries must fit the tables blob and be at least as large as the fields read
    if pt_sz < 52 or ex_sz < 24 or pt_off + pt_n * pt_sz > tsize or ex_off + ex_n * ex_sz > tsize:
        raise ValueError('super metadata 损坏（分区表或 extent 表越界）')
    extents = [struct.unpack_from('<QIQI', tables, ex_off + i * ex_sz) for i in range(ex_n)]
    out = {}
    for i in range(pt_n):
        e = tables[pt_off + i * pt_sz:pt_off + (i + 1) * pt_sz]
        name = e[:36].rstrip(b'\0').decode(errors='replace')
        _, first, n_ext, _ = struct.unpack_from('<IIII', e, 36)
        if first + n_ext > ex_n:
            raise ValueError(f'super metadata 损坏（{name} 引用的 extent {first}+{n_ext} 超出 {ex_n} 个）')
        out[name] = [(tdata * 512, nsec * 512) for nsec, ttype, tdata, _ in extents[first:first + n_ext]
                     if ttype == 0]
    return out


def fs_type(block):
    """Filesystem of a partition from its first 4 KiB: ('ext4', volume name) / ('erofs', '') / (None, '')."""
    if len(block) >= 2048 and struct.unpack_from('<H', block, 1024 + 56)[0] == 0xEF53:
        return 'ext4', block[1024 + 120:1024 + 136].split(b'\0')[0].decode(errors='replace')
    if len(block) >= 1028 and struct.unpack_from('<I', block, 1024)[0] == 0xE0F5E1E2:
        return 'erofs', ''
    return None, ''
