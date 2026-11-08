"""Shared constants and helpers for build_package.py / flash_package.py (TB710FU, HyperOS 4)."""
import hashlib, json, os, re, struct, subprocess, sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
REPO = os.path.dirname(TOOLS)
WORK = os.path.abspath(os.environ.get('TB_WORK', os.path.join(REPO, 'work')))

SECTOR = 4096
SUPER_SIZE = 0x4c0000000           # super partition length in the stock LUN0 GPT
SUPER_START = 9608                 # LUN0 sector of super in the stock GPT

# Never written, never erased (IMEI, RF calibration, keys, DDR training ...)
UNIQUE = ('persist', 'modemst1', 'modemst2', 'fsg', 'fsc', 'devinfo', 'frp', 'keystore',
          'ssd', 'secdata', 'uefivarstore', 'cdt', 'ddr')
# Also left alone by the package (device identity / Lenovo lock state)
KEEP = UNIQUE + ('proinfo', 'lenovolock')

FIREHOSE = 'prog_firehose_ddr-TB710FU.melf'
FIREHOSE_SHA = 'ad4689b1c52458250d46829ee46a89ec974e69f8e82eee8d4950427919e0b9eb'

# The port author's original boot images (inside the HyperOS 3.0.305 port zip). boot/init_boot/dtbo
# are used unchanged by the v3 chain, so their hashes are the v3 output hashes below.
AUTHOR_SHA = {
    'boot.img': 'e35390577fcbfa4a69d87323a54a501392229cff45b08b41ea7bf883439bd90d',
    'init_boot.img': '3ac931533d69156b50cb9a07683021ac53f78166d0a66ae9d77f4f54a7b3f703',
    'dtbo.img': '088063818de6dc7a9852a31a86d5ad7019bdc74157890b0506a0bc3677390f27',
}
V3_SHA = dict(AUTHOR_SHA, **{
    'vendor_boot.img': '988b8aa16c9a6174f8fd349f72901849ce59e643a304401bf1561c51a74b1224',
    'vbmeta.img': 'b641de6c2c42d2fa2b0a3a1d50be913fa4bea2d0988e4a95aecf1e528ba3057c',
})
# vendor side taken from the port's super
PORT_SHA = {
    'vendor_a': 'f4a3674080157b449c41f2f292507a37530c2b6e99a2f62238b51e0a58d22306',
    'odm_a': '7d54d8a11db8de92068943cad7766b5f38980de8cefdc90f823f8f4e502b175b',
    'vendor_dlkm_a': '82c3622403b74ff7b33cb2da31439f49224468ec92b9f51513f02a6906cd474a',
    'system_dlkm_a': 'a99b741bb4f31e57431ca1ef82587388a934062cc06d4aee213053d50c7bb49d',
}
# 1.5.04.495_WITHOUT_UBL supplement: these files replace the port's in the 9008 image set
SUPPLEMENT_FILES = ('abl.img', 'boot.img', 'dtbo.img', 'init_boot.img', 'pvmfw.img', 'recovery.img',
                    'vbmeta.img', 'vbmeta_system.img', 'vendor_boot.img')
# slot A gets the v3 chain; slot B keeps the 495 chain (as on the reference device)
V3_SLOT_A = {'boot_a': 'boot.img', 'init_boot_a': 'init_boot.img', 'dtbo_a': 'dtbo.img',
             'vendor_boot_a': 'vendor_boot.img', 'vbmeta_a': 'vbmeta.img'}
OTA_GLOB = 'yupei-ota_full-OS4.0.5.0.XPZCNXM-*.zip'
PKG_NAME = 'TB710FU-HyperOS4-OS4.0.5.0'


def tested_firmware():
    return json.load(open(os.path.join(HERE, 'tested_firmware.json')))['files']


def log(*a):
    print(*a, flush=True)


def die(msg):
    print('\nERROR: ' + msg, file=sys.stderr, flush=True)
    sys.exit(1)


def sha256(path, cache=None):
    """sha256 of a file; with cache (dict path -> (size, mtime, sha)) big files are hashed once."""
    st = os.stat(path)
    key = (st.st_size, int(st.st_mtime))
    if cache is not None and path in cache and tuple(cache[path][:2]) == key:
        return cache[path][2]
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(16 << 20), b''):
            h.update(b)
    d = h.hexdigest()
    if cache is not None:
        cache[path] = [key[0], key[1], d]
    return d


def run(cmd, **kw):
    log('  $ ' + ' '.join(str(c) for c in cmd))
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def rawprogram_entries(images_dir):
    """[(xml, lun, label, filename, start_sector, num_sectors)] for every <program> of rawprogram0..5."""
    out = []
    for i in range(6):
        x = f'rawprogram{i}.xml'
        for e in ET.parse(os.path.join(images_dir, x)).getroot().iter('program'):
            out.append((x, int(e.get('physical_partition_number')), e.get('label'), e.get('filename', ''),
                        e.get('start_sector'), int(e.get('num_partition_sectors') or 0)))
    return out


def check_rawprogram_safe(images_dir):
    """The package must never program a unique partition (their filename must be empty)."""
    bad = [(x, lab, f) for x, lun, lab, f, *_ in rawprogram_entries(images_dir) if lab in KEEP and f]
    if bad:
        die('rawprogram programs a protected partition: ' + repr(bad))


def parse_gpt(data, sector=SECTOR):
    """{name: (first_lba, sectors)} from a primary GPT image (protective MBR + header + entries)."""
    hdr = data[sector:sector + 92]
    if hdr[:8] != b'EFI PART':
        raise ValueError('no GPT header')
    entries_lba, n, esz = struct.unpack_from('<QII', hdr, 72)
    parts = {}
    for i in range(n):
        e = data[entries_lba * sector + i * esz: entries_lba * sector + (i + 1) * esz]
        if len(e) < 128 or e[:16] == b'\0' * 16:
            continue
        first, last = struct.unpack_from('<QQ', e, 32)
        name = e[56:128].decode('utf-16-le').rstrip('\0')
        parts[name] = (first, last - first + 1)
    return parts


def layout_key(parts):
    """GPT layout for comparison. patch0.xml sets the end of userdata (the last partition) from the real
    disk size, so only its start is compared."""
    return {n: (v[0], None) if n == 'userdata' else v for n, v in parts.items()}


PRINTGPT_RE =re.compile(r'^(\S+):\s+Offset 0x([0-9a-f]+), Length 0x([0-9a-f]+)', re.M)


def parse_printgpt(text):
    """{name: (first_lba, sectors)} from `edl printgpt` output."""
    return {m.group(1): (int(m.group(2), 16) // SECTOR, int(m.group(3), 16) // SECTOR)
            for m in PRINTGPT_RE.finditer(text)}
