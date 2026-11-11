"""import: the Xiaomi Pad 8 (yupei) parts from an official Xiaomi full OTA package (payload.bin).

Output: work/xiaomi/<version>/
  system.img system_ext.img product.img mi_ext.img   (go into the HyperOS super)
  vendor.img odm.img                                 (only as a source of Xiaomi HALs, see #5)
  xiaomi.json   OTA metadata, sha256 of every image, filesystems
Standard library only (ZSTD operations, if any, need the `zstd` command). Nothing touches the device.
"""
import base64, bz2, concurrent.futures, hashlib, json, lzma, os, re, shutil, struct, subprocess, tempfile, threading, time, zipfile

from . import layout
from .backup import file_sha256
from .sources import WORK, SourceError

DEVICE = 'yupei'
WANT = ('system', 'system_ext', 'product', 'mi_ext', 'vendor', 'odm')
# Versions this project has been verified with, by the OTA's post-build fingerprint (the OS4.x version name only
# appears in the file name). Official download: docs/flash-guide.md.
VERIFIED = {
    'Xiaomi/yupei/yupei:17/CP2A.260605.016/17OS4.0.260913.205532236.QCPDCN.S:user/release-keys': {
        'version': 'OS4.0.5.0.XPZCNXM', 'zip': 'yupei-ota_full-OS4.0.5.0.XPZCNXM-user-17.0-4fe489f542.zip',
        'bytes': 9922261403},
}

# update_engine InstallOperation types used by full payloads
REPLACE, REPLACE_BZ, ZERO, DISCARD, REPLACE_XZ, ZSTD = 0, 1, 6, 7, 8, 14


# ---------------------------------------------------------------- minimal protobuf reader

def _varint(b, i):
    v = s = 0
    while True:
        c = b[i]
        i += 1
        v |= (c & 0x7F) << s
        if c < 0x80:
            return v, i
        s += 7


def pb(b):
    """{field number: [values]} for one protobuf message (varints as int, length-delimited as bytes)."""
    out, i = {}, 0
    while i < len(b):
        key, i = _varint(b, i)
        f, wt = key >> 3, key & 7
        if wt == 0:
            v, i = _varint(b, i)
        elif wt == 2:
            n, i = _varint(b, i)
            v, i = b[i:i + n], i + n
        elif wt == 1:
            v, i = struct.unpack_from('<Q', b, i)[0], i + 8
        elif wt == 5:
            v, i = struct.unpack_from('<I', b, i)[0], i + 4
        else:
            raise ValueError(f'protobuf wire type {wt}')
        out.setdefault(f, []).append(v)
    return out


def _one(m, f, default=None):
    return m.get(f, [default])[0]


# ---------------------------------------------------------------- payload.bin

class Payload:
    """payload.bin inside a zip (stored, not compressed) or as a plain file."""

    def __init__(self, path):
        self.path = path
        self.base = 0
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as z:
                info = z.getinfo('payload.bin')
                if info.compress_type != zipfile.ZIP_STORED:
                    raise SourceError('zip 里的 payload.bin 被压缩过，不是官方 OTA 包的格式')
                with open(path, 'rb') as f:        # data starts after the local header
                    f.seek(info.header_offset)
                    h = f.read(30)
                    n, e = struct.unpack_from('<HH', h, 26)
                    self.base = info.header_offset + 30 + n + e
        with open(path, 'rb') as f:
            f.seek(self.base)
            h = f.read(24)
            magic, ver, msize, ssize = struct.unpack('>4sQQI', h)
            if magic != b'CrAU' or ver != 2:
                raise SourceError('不是 payload.bin（CrAU v2）')
            man = pb(f.read(msize))
        self.data = self.base + 24 + msize + ssize
        self.block = _one(man, 3, 4096)
        self.parts = {}
        for raw in man.get(13, []):
            p = pb(raw)
            name = _one(p, 1).decode()
            info = pb(_one(p, 7, b''))
            ops = []
            for o in p.get(8, []):
                o = pb(o)
                ext = [pb(e) for e in o.get(6, [])]
                ops.append({'type': _one(o, 1, 0), 'off': _one(o, 2, 0), 'len': _one(o, 3, 0),
                            'dst': [(_one(e, 1, 0), _one(e, 2, 0)) for e in ext], 'sha256': _one(o, 8)})
            self.parts[name] = {'size': _one(info, 1, 0), 'sha256': _one(info, 2), 'ops': ops}

    def extract(self, name, dest, threads=os.cpu_count() or 4):
        p = self.parts[name]
        fd = os.open(dest, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            os.ftruncate(fd, p['size'])
            with open(self.path, 'rb') as src:
                lock = threading.Lock()

                def one(op):
                    if op['type'] in (ZERO, DISCARD):
                        return                         # the file is already zero there
                    with lock:
                        src.seek(self.data + op['off'])
                        raw = src.read(op['len'])
                    if op['sha256'] and hashlib.sha256(raw).digest() != op['sha256']:
                        raise SourceError(f'{name}: payload 数据块校验失败（包损坏或没下载完）')
                    t = op['type']
                    if t == REPLACE:
                        out = raw
                    elif t == REPLACE_XZ:
                        out = lzma.decompress(raw)
                    elif t == REPLACE_BZ:
                        out = bz2.decompress(raw)
                    elif t == ZSTD:
                        if not shutil.which('zstd'):
                            raise SourceError('payload 里有 zstd 压缩块，需要 zstd 命令（brew/apt install zstd）')
                        out = subprocess.run(['zstd', '-dc'], input=raw, capture_output=True, check=True).stdout
                    else:
                        raise SourceError(f'{name}: 不支持的操作类型 {t}（增量包？要用全量包）')
                    pos = 0
                    for start, n in op['dst']:
                        ln = n * self.block
                        os.pwrite(fd, out[pos:pos + ln], start * self.block)
                        pos += ln

                with concurrent.futures.ThreadPoolExecutor(threads) as ex:
                    for _ in ex.map(one, p['ops']):
                        pass
        finally:
            os.close(fd)
        got = file_sha256(dest)
        if p['sha256'] and bytes.fromhex(got) != p['sha256']:
            raise SourceError(f'{name}: 解出的镜像和 payload 里记录的 sha256 不一致')
        return got


# ---------------------------------------------------------------- OTA package

def metadata(path):
    """key=value pairs of META-INF/com/android/metadata (pre-device, post-build, ota-type, ...)."""
    if not zipfile.is_zipfile(path):
        return {}
    with zipfile.ZipFile(path) as z:
        try:
            t = z.read('META-INF/com/android/metadata').decode(errors='replace')
        except KeyError:
            return {}
    return dict(l.split('=', 1) for l in t.splitlines() if '=' in l)


def payload_hash_check(path, pay):
    """sha256 of payload.bin against FILE_HASH / FILE_SIZE in payload_properties.txt."""
    with zipfile.ZipFile(path) as z:
        props = dict(l.split('=', 1) for l in z.read('payload_properties.txt').decode().splitlines() if '=' in l)
    want, size = base64.b64decode(props['FILE_HASH']), int(props['FILE_SIZE'])
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        f.seek(pay.base)
        left = size
        while left:
            b = f.read(min(left, 1 << 22))
            if not b:
                break
            h.update(b)
            left -= len(b)
    return h.digest() == want and left == 0


def import_ota(path, force=False):
    if not os.path.isfile(path):
        raise SourceError(f'{path}: 不存在')
    if path.lower().endswith(('.tgz', '.tar.gz')):
        raise SourceError('线刷包（.tgz）还不支持，先用卡刷全量包（*-ota_full-*.zip，里面是 payload.bin）')
    meta = metadata(path)
    dev = meta.get('pre-device', '')
    post = meta.get('post-build', '')
    m = re.search(r'OS\d+\.\d+\.\d+\.\d+\.[A-Z]{7}', os.path.basename(path))
    version = VERIFIED.get(post, {}).get('version') or (m.group(0) if m else meta.get('post-build-incremental', ''))
    print(f'小米官方包：{os.path.basename(path)}')
    print(f'  机型 {dev or "?"}  版本 {version or "?"}  安全补丁 {meta.get("post-security-patch-level", "?")}')
    print(f'  {post}')
    if not meta:
        raise SourceError('zip 里没有 META-INF/com/android/metadata，不像是小米官方 OTA 包')
    if dev != DEVICE and f'/{DEVICE}/' not in post:
        raise SourceError(f'这是 {dev or "未知机型"} 的包，要小米平板 8（{DEVICE}）的包')
    if meta.get('ota-type') not in (None, 'AB'):
        raise SourceError(f'OTA 类型 {meta.get("ota-type")}，要 A/B 全量包')
    if meta.get('pre-build'):
        raise SourceError('这是增量包（有 pre-build），要全量包（文件名里有 ota_full）')
    if post not in VERIFIED:
        msg = f'{version} 还没验证过（验证过的：{", ".join(v["version"] for v in VERIFIED.values())}）'
        if not force:
            raise SourceError(msg + '。确实要用就加 --force。')
        print(f'  ⚠ {msg}')
    pay = Payload(path)
    print('  校验 payload.bin 的 sha256（payload_properties.txt）…', flush=True)
    if not payload_hash_check(path, pay):
        raise SourceError('payload.bin 的 sha256 和包里记录的不一致：包损坏或没下载完')
    missing = [n for n in WANT if n not in pay.parts]
    if missing:
        raise SourceError(f'payload 里没有 {missing}')
    # version can come from inside the zip: one plain directory name below work/xiaomi, --force or not
    name = version or 'unknown'
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9._-]{0,63}', name):
        raise SourceError(f'版本号 {name!r} 不能当目录名，拒绝导入')
    root = os.path.join(WORK, 'xiaomi')
    out = os.path.join(root, name)
    if os.path.dirname(os.path.abspath(out)) != os.path.abspath(root):
        raise SourceError(f'输出目录 {out} 不在 {root} 里，拒绝导入')
    os.makedirs(root, exist_ok=True)
    # extract into a temporary folder and swap it in at the end: a failed import never damages a previous one
    tmp = tempfile.mkdtemp(prefix=f'.{name}-', dir=root)
    try:
        info = _extract_all(path, pay, meta, version, post, tmp)
        old = out + '.old'
        if os.path.exists(out):
            shutil.rmtree(old, ignore_errors=True)
            os.rename(out, old)
        os.rename(tmp, out)
        shutil.rmtree(old, ignore_errors=True)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        if not os.path.exists(out) and os.path.exists(out + '.old'):
            os.rename(out + '.old', out)              # put the previous import back
        raise
    return out, info


def _extract_all(path, pay, meta, version, post, out):
    info = {'source': os.path.abspath(path), 'zip_bytes': os.path.getsize(path), 'metadata': meta,
            'version': version, 'verified': post in VERIFIED, 'partitions': {}}
    t0 = time.time()
    for n in WANT:
        p = pay.parts[n]
        print(f'  解出 {n}（{p["size"] / 2**30:.2f} GiB）…', flush=True)
        dest = os.path.join(out, f'{n}.img')
        sha = pay.extract(n, dest)
        with open(dest, 'rb') as f:
            fs, label = layout.fs_type(f.read(4096))
        info['partitions'][n] = {'bytes': p['size'], 'sha256': sha, 'fs': fs}
    info['created'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    with open(os.path.join(out, 'xiaomi.json'), 'w') as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    print(f'  用时 {(time.time() - t0) / 60:.1f} 分钟')
    return info


def report(out, info):
    print('\n==== 导入结果 ====')
    for n, p in info['partitions'].items():
        print(f'{n:11} {p["fs"] or "?":6} {p["bytes"] / 2**30:6.2f} GiB  sha256 {p["sha256"][:16]}…')
    print(f'\n✅ 小米官方组件已就绪：{out}' + ('' if info['verified'] else '（未验证版本）'))
    return True
