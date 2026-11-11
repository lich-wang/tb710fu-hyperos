"""pull: the Lenovo parts the build needs, from a full backup of the tablet or from an official Lenovo package.

Output: work/lenovo/<id>/
  vendor.img odm.img vendor_dlkm.img system_dlkm.img   (Lenovo stock, used by the build)
  system.img system_ext.img product.img                (Lenovo stock, reference only)
  boot.img init_boot.img vendor_boot.img dtbo.img recovery.img vbmeta.img vbmeta_system.img
  lenovo.json   source, build properties found, sha256 of every file, firmware comparison (package only)
Read only: nothing here talks to the device.
"""
import glob, hashlib, json, os, re, shutil, subprocess, tempfile, time, zipfile
import xml.etree.ElementTree as ET

from . import avb, layout, sparse
from .backup import file_sha256
from .check import FIRMWARE, LOGICAL
from .sources import WORK, SourceError

BOOT_CHAIN = ('boot', 'init_boot', 'vendor_boot', 'dtbo', 'recovery', 'vbmeta', 'vbmeta_system')
PROP = re.compile(rb'(ro\.[\w.]*(?:build\.fingerprint|build\.display\.id|build\.version\.incremental|'
                  rb'build\.version\.release|product\.[\w.]*(?:brand|manufacturer)|zui[\w.]*|lenovo[\w.]*))=([\x20-\x7e]{1,200})')


# ---------------------------------------------------------------- shared

FINGERPRINT = re.compile(r'^[^/\s]+/[^/\s]+/[^/\s:]+:[^/\s]+/')    # brand/product/device:release/...


def scan_props(path):
    """Build properties found as plain text in an image (works for ext4, and for uncompressed EROFS files).

    {key: [values]}: a key can appear more than once (build.prop, but also templates such as "...fingerprint=*").
    """
    found = {}
    with open(path, 'rb') as f:
        prev = b''
        for b in iter(lambda: f.read(64 << 20), b''):
            for k, v in PROP.findall(prev[-512:] + b):
                vals = found.setdefault(k.decode(), [])
                v = v.decode().strip()
                if v not in vals and len(vals) < 8:
                    vals.append(v)
            prev = b
    return found


def fingerprints(props):
    return [v for k, vs in props.items() if k.endswith('fingerprint') for v in vs if FINGERPRINT.match(v)]


def is_lenovo(props):
    """A Lenovo/ fingerprint; without any real fingerprint, a vendor brand/manufacturer of Lenovo."""
    fps = fingerprints(props)
    if fps:
        return any(v.lower().startswith('lenovo/') for v in fps)
    return any(v.lower() == 'lenovo' for k, vs in props.items() if k.startswith('ro.product.vendor.')
               and k.endswith(('brand', 'manufacturer')) for v in vs)


def extract_super(super_path, slot, out):
    """Copy the slot's logical partitions out of a raw super image -> {base name: info}.

    vendor goes first: when it is not Lenovo stock nothing else is extracted and its image is removed again
    (the rest would only be gigabytes of unusable files).
    """
    with open(super_path, 'rb') as f:
        lp = layout.parse_lp(f.read(layout.LP_HEAD_BYTES))
        res = {}
        for base in ('vendor',) + tuple(b for b in LOGICAL if b != 'vendor'):
            ext = lp.get(base + slot) or (lp.get(base) if not slot else None)
            if not ext:
                continue
            path = os.path.join(out, f'{base}.img')
            with open(path, 'wb') as o:
                for off, ln in ext:
                    f.seek(off)
                    left = ln
                    while left:
                        b = f.read(min(left, 1 << 22))
                        if not b:
                            raise SourceError(f'super 镜像被截断（{base}）')
                        o.write(b)
                        left -= len(b)
            with open(path, 'rb') as o:
                fs, label = layout.fs_type(o.read(4096))
            res[base] = {'fs': fs, 'props': scan_props(path)}
            if base == 'vendor' and not is_lenovo(res[base]['props']):
                os.remove(path)
                break
    return res


def finish(out, info):
    files = {}
    for p in sorted(glob.glob(os.path.join(out, '*.img'))):
        files[os.path.basename(p)] = {'bytes': os.path.getsize(p), 'sha256': file_sha256(p)}
    info['files'] = files
    info['created'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    for base in ('vbmeta', 'boot', 'vendor_boot'):
        p = os.path.join(out, f'{base}.img')
        if os.path.exists(p):
            data = open(p, 'rb').read()
            vb = avb.find_vbmeta(data) if base == 'vbmeta' else _footer_vbmeta(data)
            if vb:
                v = avb.parse_vbmeta(vb)
                key = avb.key_str(v['key']) if v['key'] or base == 'vbmeta' else '无单独签名（由 vbmeta 里的哈希校验）'
                info.setdefault('avb', {})[base] = {'key': key, 'rollback': v['rollback'],
                                                    'release': v['release']}
    with open(os.path.join(out, 'lenovo.json'), 'w') as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    return info


def _footer_vbmeta(data):
    foot = avb.parse_footer(data[-avb.FOOTER_SIZE:])
    return data[foot[0]:foot[0] + foot[1]] if foot else None


def verdict(info):
    """Errors that make this source unusable for the build."""
    errs = []
    parts = info.get('partitions', {})
    if 'vendor' in parts and not is_lenovo(parts['vendor'].get('props', {})):
        return ['vendor 不是联想原厂（没找到 Lenovo/ 开头的 vendor 指纹）']
    for base in ('vendor', 'odm', 'vendor_dlkm', 'system_dlkm'):
        if base not in parts:
            errs.append(f'没有 {base}')
    for base in BOOT_CHAIN:
        if f'{base}.img' not in info.get('files', {}):
            errs.append(f'没有 {base}.img')
    return errs


def fresh_dir(path):
    """Empty output directory, so files of an earlier extraction never count as this one's."""
    if os.path.isdir(path):
        shutil.rmtree(path)
    os.makedirs(path)
    return path


# ---------------------------------------------------------------- from a backup

def from_backup(bdir, st, force=False):
    slot = st.get('slot')
    if not slot:
        raise SourceError('备份里认不出当前槽')
    ident = os.path.basename(bdir.rstrip('/'))
    out = fresh_dir(os.path.join(WORK, 'lenovo', f'device-{ident}'))
    print(f'从备份 {bdir} 取件（当前槽 {slot}）→ {out}')
    for base in BOOT_CHAIN:
        src = os.path.join(bdir, f'{base}{slot}.bin')
        if not os.path.exists(src):
            src = os.path.join(bdir, f'{base}.bin')
        if os.path.exists(src):
            shutil.copyfile(src, os.path.join(out, f'{base}.img'))
    print('  拆 super …', flush=True)
    parts = extract_super(os.path.join(bdir, 'super.bin'), slot, out)
    info = finish(out, {'source': 'device', 'backup': bdir, 'device': st.get('device', {}), 'slot': slot,
                        'partitions': parts})
    return out, info


# ---------------------------------------------------------------- from an official package

class Package:
    """An official 9008 package: folder, .zip or .7z (7z is extracted to a temporary folder first)."""

    def __init__(self, path):
        self.path, self.tmp, self.zip = path, None, None
        low = path.lower()
        if os.path.isdir(path):
            self.root = path
        elif low.endswith('.zip'):
            self.zip = zipfile.ZipFile(path)
        elif low.endswith('.7z'):
            exe = shutil.which('7zz') or shutil.which('7z')
            if not exe:
                raise SourceError('需要 7zz 或 7z 才能读 .7z（或者先解压，再传文件夹）')
            os.makedirs(WORK, exist_ok=True)
            self.tmp = tempfile.mkdtemp(prefix='lenovo-pkg-', dir=WORK)
            print(f'  解压 {path}（十几 GB，要等几分钟）…', flush=True)
            # everything pull needs is in the images folder; leave out what it never reads (user data, tools)
            skip = ['userdata_*', 'metadata*.img', 'persist.img', 'Driver.tar.gz', '*.exe', '*.dll', '*.hex', '*.HEX']
            try:
                subprocess.run([exe, 'x', '-y', f'-o{self.tmp}', path] + [f'-xr!{x}' for x in skip], check=True,
                               stdout=subprocess.DEVNULL)
            except (subprocess.CalledProcessError, KeyboardInterrupt):
                shutil.rmtree(self.tmp, ignore_errors=True)
                raise SourceError(f'{path}: 解压失败（包损坏或没下载完？）')
            self.root = self.tmp
        else:
            raise SourceError(f'{path}: 要一个文件夹、.zip 或 .7z')
        self.names = ([i.filename for i in self.zip.infolist() if not i.is_dir()] if self.zip else
                      [os.path.relpath(os.path.join(r, n), self.root) for r, _, ns in os.walk(self.root) for n in ns])

    def find(self, filename):
        """Member whose basename is filename (packages keep everything in one images folder)."""
        hits = [n for n in self.names if os.path.basename(n) == filename]
        return hits[0] if hits else None

    def open(self, name):
        return self.zip.open(name) if self.zip else open(os.path.join(self.root, name), 'rb')

    def copy(self, name, dest):
        with self.open(name) as f, open(dest, 'wb') as o:
            shutil.copyfileobj(f, o, 1 << 22)

    def close(self):
        if self.tmp:
            shutil.rmtree(self.tmp, ignore_errors=True)


def rawprogram(pkg):
    """<program> entries of every rawprogram*.xml: [{label, filename, start, sectors, sector_size, lun, sparse}]

    Some packages ship the files as *.x (truncated extension), and several rawprogram files (e.g. *_unsparse0.xml and
    *_unsparse0-half.xml) repeat the same entries: duplicates are dropped.
    """
    out, seen = [], set()
    for n in sorted(pkg.names):
        b = os.path.basename(n).lower()
        if not (b.startswith('rawprogram') and (b.endswith('.xml') or b.endswith('.x'))):
            continue
        with pkg.open(n) as f:
            raw = f.read()
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            if not raw.lstrip()[:1] == b'<':
                raise SourceError(f'{os.path.basename(n)} 是加密的（联想海外包的 .x 文件只有联想的刷机工具能解开），'
                                  '没法知道 super 各段的位置。换一个 rawprogram 是明文 xml 的包（例如国行包）。')
            raise
        for e in root.iter('program'):
            a = e.attrib
            if not a.get('filename'):
                continue
            try:
                start = int(a.get('start_sector', ''))
            except ValueError:
                continue                       # e.g. "NUM_DISK_SECTORS-5." (backup GPT)
            key = (a.get('label', ''), a['filename'], start, a.get('physical_partition_number', '0'))
            if key in seen:
                continue
            seen.add(key)
            out.append({'label': a.get('label', ''), 'filename': a['filename'], 'start': start,
                        'sectors': int(a.get('num_partition_sectors', '0') or 0),
                        'sector_size': int(a.get('SECTOR_SIZE_IN_BYTES', '4096')),
                        'lun': a.get('physical_partition_number', '0'), 'sparse': a.get('sparse') == 'true'})
    return out


def build_super(pkg, prog, dest):
    """Assemble the raw super image from the package's super entries (sparse or raw chunks)."""
    ents = [e for e in prog if e['label'] == 'super']
    if not ents:
        raise SourceError('包里的 rawprogram 没有 super')
    ss = ents[0]['sector_size']
    base = None
    gpt = pkg.find(f'gpt_main{ents[0]["lun"]}.bin')
    if gpt:
        with pkg.open(gpt) as f:
            base = layout.gpt_partitions(f.read(), ss).get('super', (None,))[0]
    if base is None:
        base = min(e['start'] for e in ents)
    size = max(e['sectors'] for e in ents) * ss if len(ents) == 1 else 0
    print(f'  组装 super（{len(ents)} 段）…', flush=True)
    with open(dest, 'wb') as out:
        for e in sorted(ents, key=lambda e: e['start']):
            name = pkg.find(e['filename'])
            if not name:
                raise SourceError(f'包里缺 {e["filename"]}')
            off = (e['start'] - base) * ss
            with pkg.open(name) as f:
                head = f.read(4)
            with pkg.open(name) as f:
                if sparse.is_sparse(head):
                    if pkg.zip:                # zip members are not seekable: unsparse needs only forward reads
                        n = sparse.unsparse(_Forward(f), out, off)
                    else:
                        n = sparse.unsparse(f, out, off)
                else:
                    out.seek(off)
                    shutil.copyfileobj(f, out, 1 << 22)
                    n = out.tell() - off
            if pkg.tmp:                        # our own temporary extraction: free the space as we go
                os.remove(os.path.join(pkg.root, name))
            size = max(size, off + n)
        out.truncate(size)


class _Forward:
    """Wrap a forward-only stream for sparse.unsparse (which seeks only to the current or a later position)."""

    def __init__(self, f):
        self.f, self.pos = f, 0

    def seek(self, pos):
        if pos < self.pos:
            raise ValueError('不能回退')
        while self.pos < pos:
            if not self.read(min(pos - self.pos, 1 << 22)):
                raise ValueError('sparse 镜像被截断（跳过时到了文件结尾）')

    def read(self, n):
        b = self.f.read(n)
        self.pos += len(b)
        return b


def firmware_compare(pkg, prog, profile):
    """Package firmware vs the device's (from the check profile): {base: 'same' / 'different' / 'no device data'}."""
    dev = (profile or {}).get('firmware', {})
    res = {}
    for base in FIRMWARE:
        e = next((x for x in prog if x['label'] in (base + '_a', base)), None)
        name = e and pkg.find(e['filename'])
        if not name:
            continue
        with pkg.open(name) as f:
            data = f.read()
        mine = {'sha256': hashlib.sha256(data).hexdigest(),
                'sha256_trim': hashlib.sha256(data.rstrip(b'\0')).hexdigest(),
                'version': avb.image_version(data)}
        d = dev.get(base)
        if not d:
            mine['match'] = 'no device data'
        else:
            mine['match'] = 'same' if mine['sha256_trim'] == d['sha256_trim'] else 'different'
            mine['device_version'] = d.get('version')
        res[base] = mine
    return res


def from_package(path, profile=None):
    pkg = Package(path)
    try:
        prog = rawprogram(pkg)
        if not prog:
            raise SourceError(f'{path}: 没找到 rawprogram*.xml，不像是 9008 线刷包')
        stem = re.sub(r'\.(zip|7z)$', '', os.path.basename(path.rstrip('/')), flags=re.I)
        out = fresh_dir(os.path.join(WORK, 'lenovo', f'package-{stem}'))
        print(f'从联想官方包 {path} 取件 → {out}')
        for base in BOOT_CHAIN:
            e = next((x for x in prog if x['label'] in (base + '_a', base)), None)
            name = e and pkg.find(e['filename'])
            if name:
                pkg.copy(name, os.path.join(out, f'{base}.img'))
        sup = os.path.join(out, 'super.raw')
        build_super(pkg, prog, sup)
        print('  拆 super …', flush=True)
        parts = extract_super(sup, '_a', out)
        os.remove(sup)
        fw = firmware_compare(pkg, prog, profile)
        info = finish(out, {'source': 'package', 'package': os.path.abspath(path),
                            'package_sha256': file_sha256(path) if os.path.isfile(path) else None,
                            'partitions': parts, 'firmware': fw})
        return out, info
    finally:
        pkg.close()


# ---------------------------------------------------------------- report

def report(out, info):
    print('\n==== 取件结果 ====')
    for base, p in info.get('partitions', {}).items():
        fps = fingerprints(p['props'])
        fp = next((v for v in fps if v.lower().startswith('lenovo/')), fps[0] if fps else '（没找到指纹）')
        print(f'{base:12} {p["fs"] or "?":6} {fp}')
    for base, a in info.get('avb', {}).items():
        print(f'{base:12} 签名 {a["key"]}' + (f'  回滚索引 {a["rollback"]}' if a['rollback'] else ''))
    fw = info.get('firmware')
    if fw:
        diff = [b for b, v in fw.items() if v['match'] == 'different']
        same = [b for b, v in fw.items() if v['match'] == 'same']
        print(f'包里的底层固件与平板：{len(same)} 个一致，{len(diff)} 个不同'
              + (f'（{", ".join(diff)}）' if diff else ''))
        if diff:
            print('  ⚠ 包的版本和平板上的固件不一致：本项目不写底层固件，但联想 vendor 和固件版本差得多时可能不兼容。')
        if not same and not diff:
            print('  （没有设备档案可比，先跑 check）')
    errs = verdict(info)
    print()
    if errs:
        print('❌ 不能用来构建：')
        for e in errs:
            print(f'  - {e}')
    else:
        print(f'✅ 联想原厂组件已就绪：{out}')
    return not errs
