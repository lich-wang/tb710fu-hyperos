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


def parse_lun_sectors(text):
    """{lun: total sectors} from the 'Total disk size:..., sectors:0x...' line after each LUN in `edl printgpt`."""
    out, lun = {}, None
    for line in text.splitlines():
        m = re.match(r'\s*Parsing Lun (\d+):', line)
        if m:
            lun = int(m.group(1))
            continue
        m = re.search(r'Total disk size:\s*0x[0-9a-f]+,\s*sectors:\s*0x([0-9a-f]+)', line, re.I)
        if m and lun is not None:
            out[lun] = int(m.group(1), 16)
    return out


def gpt_header(blob, sector=SECTOR):
    """Primary GPT header fields from the first sectors of a LUN (MBR + header + entries)."""
    h = blob[sector:sector + 92]
    if h[:8] != b'EFI PART':
        raise ValueError('没有 GPT 头（EFI PART）')
    alt, first_use, last_use = struct.unpack_from('<QQQ', h, 32)
    entries_lba, n, size = struct.unpack_from('<QII', h, 72)
    return {'alternate_lba': alt, 'entries_lba': entries_lba, 'entries': n, 'entry_size': size,
            'entries_sectors': (n * size + sector - 1) // sector, 'last_usable': last_use}


def gpt_partitions(blob, sector=SECTOR):
    """{name: (first_lba, sectors)} from a primary GPT dump (gpt_main*.bin of a backup or a 9008 package)."""
    h = gpt_header(blob, sector)
    out = {}
    for i in range(h['entries']):
        e = blob[h['entries_lba'] * sector + i * h['entry_size']:][:h['entry_size']]
        if len(e) < 128 or e[:16] == b'\0' * 16:
            continue
        first, last = struct.unpack_from('<QQ', e, 32)
        name = e[56:128].decode('utf-16-le', errors='replace').split('\0')[0]
        out[name] = (first, last - first + 1)
    return out


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
    for key, rx in (('hwid', r'HW_ID\s*:\s*([0-9a-fA-F]+)'), ('pk_hash', r'OEM PKHASH\s*:\s*([0-9a-fA-F]+)'),
                    ('chip_serial', r'Chip Serial Number\s*:\s*([0-9a-fA-F]+)')):
        m = re.search(rx, text)
        if m:
            out[key] = m.group(1).lower()
    return out


# ---------------------------------------------------------------- super (liblp)

LP_GEOM_MAGIC, LP_HDR_MAGIC = 0x616C4467, 0x414C5030
LP_HEAD_BYTES = 4096 * 3 + 65536 * 2   # reserved + 2 geometry copies + room for the first metadata slot


def parse_lp(head):
    """Partitions of a super image from its first LP_HEAD_BYTES -> {name: [(byte_offset, byte_len), ...]}."""
    magic = struct.unpack_from('<I', head, 4096)[0]
    if magic != LP_GEOM_MAGIC:
        raise ValueError('super 开头没有 liblp geometry')
    off = 4096 * 3
    magic, _, _, hsize = struct.unpack_from('<IHHI', head, off)
    if magic != LP_HDR_MAGIC:
        raise ValueError('super 里没有 liblp metadata header')
    tsize = struct.unpack_from('<I', head, off + 44)[0]
    pt_off, pt_n, pt_sz, ex_off, ex_n, ex_sz = struct.unpack_from('<6I', head, off + 80)
    tables = head[off + hsize:off + hsize + tsize]
    if len(tables) < tsize:
        raise ValueError('super metadata 读取不完整')
    extents = [struct.unpack_from('<QIQI', tables, ex_off + i * ex_sz) for i in range(ex_n)]
    out = {}
    for i in range(pt_n):
        e = tables[pt_off + i * pt_sz:pt_off + (i + 1) * pt_sz]
        name = e[:36].rstrip(b'\0').decode(errors='replace')
        _, first, n_ext, _ = struct.unpack_from('<IIII', e, 36)
        out[name] = [(tdata * 512, nsec * 512) for nsec, ttype, tdata, _ in extents[first:first + n_ext] if ttype == 0]
    return out


def fs_type(block):
    """Filesystem of a partition from its first 4 KiB: ('ext4', volume name) / ('erofs', '') / (None, '')."""
    if len(block) >= 2048 and struct.unpack_from('<H', block, 1024 + 56)[0] == 0xEF53:
        return 'ext4', block[1024 + 120:1024 + 136].split(b'\0')[0].decode(errors='replace')
    if len(block) >= 1028 and struct.unpack_from('<I', block, 1024)[0] == 0xE0F5E1E2:
        return 'erofs', ''
    return None, ''
