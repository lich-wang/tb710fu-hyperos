"""vendor: the vendor side of the new super, from Lenovo's stock images (see docs/porting.md).

  vendor       Lenovo stock + one change: an erofs entry next to every ext4 logical partition in
               /etc/fstab.qcom (the Xiaomi system, system_ext and product are EROFS); avb= flags are kept
  odm, vendor_dlkm, system_dlkm   Lenovo stock, unchanged

Output: work/build/vendor/<Lenovo source>/<name>.img, plain filesystems with the AVB footer and hashtree removed
(the build signs them again with the device's rollback indexes), plus vendor.json (sources, changes, sha256).

The vendor is edited in place with e2fsprogs (no root): the ext4 image is grown, its deduplicated blocks are
unshared (`e2fsck -E unshare_blocks`, Android builds use the shared_blocks feature, which e2fsprogs cannot write),
the file is replaced keeping mode, owner and SELinux label, and the image is shrunk back to its minimum.
Every other file keeps its content and metadata.
"""
import glob, hashlib, json, os, re, shutil, subprocess, tempfile, time

from . import avb, layout, lenovo
from .backup import file_sha256
from .sources import WORK, SourceError

LOGICAL = ('system', 'system_ext', 'product', 'vendor', 'vendor_dlkm', 'system_dlkm', 'odm')
FSTAB = '/etc/fstab.qcom'
PASS = ('odm', 'vendor_dlkm', 'system_dlkm')


# ---------------------------------------------------------------- fstab

def fstab_add_erofs(text):
    """Add an erofs line after every ext4 line of a logical partition (same fs_mgr flags). Idempotent."""
    out, added = [], []
    lines = text.splitlines()
    have = {(l.split()[1], l.split()[2]) for l in lines if l.strip() and not l.lstrip().startswith('#')
            and len(l.split()) >= 5}
    for l in lines:
        out.append(l)
        f = l.split()
        if (len(f) >= 5 and not l.lstrip().startswith('#') and f[2] == 'ext4' and f[0] in LOGICAL
                and 'logical' in f[4].split(',') and (f[1], 'erofs') not in have):
            # same column layout as the ext4 line: replace the type and the mount options
            m = re.match(r'(\s*\S+\s+\S+\s+)ext4(\s+)(\S+)(\s+)(.*)', l)
            out.append(f'{m.group(1)}erofs{m.group(2)[:-1] or " "}{"ro".ljust(len(m.group(3)))}{m.group(4)}{m.group(5)}')
            added.append(f[0])
    return '\n'.join(out) + ('\n' if text.endswith('\n') else ''), added


# ---------------------------------------------------------------- e2fsprogs

def e2tool(name):
    for d in ('', '/opt/homebrew/opt/e2fsprogs/sbin', '/usr/local/opt/e2fsprogs/sbin', '/sbin', '/usr/sbin'):
        p = os.path.join(d, name) if d else shutil.which(name)
        if p and os.access(p, os.X_OK):
            return p
    raise SourceError(f'找不到 {name}：要 e2fsprogs（macOS: brew install e2fsprogs；Linux: apt install e2fsprogs）')


def run(*cmd, ok=(0,)):
    p = subprocess.run(cmd, capture_output=True, text=True, errors='replace')
    if p.returncode not in ok:
        raise SourceError(f'{os.path.basename(cmd[0])} 失败（{p.returncode}）：{(p.stdout + p.stderr).strip()[-600:]}')
    return p.stdout + p.stderr


def debugfs(img, *cmds, write=False):
    with tempfile.NamedTemporaryFile('w', suffix='.cmd', delete=False) as f:
        f.write('\n'.join(cmds) + '\n')
    try:
        out = run(e2tool('debugfs'), *(['-w'] if write else []), '-f', f.name, img)
    finally:
        os.remove(f.name)
    bad = [l for l in out.splitlines() if re.search(r'(?i)error|not found|could not|invalid|failed', l)]
    if bad:
        raise SourceError('debugfs: ' + '; '.join(bad[:5]))
    return out


def fsck_clean(img):
    """e2fsck -fn exits 0 only when the filesystem is clean."""
    return subprocess.run([e2tool('e2fsck'), '-fn', img], capture_output=True).returncode == 0


def strip_footer(src, dest):
    """Copy the filesystem part of a partition image (drops hashtree, FEC and AVB footer)."""
    with open(src, 'rb') as f:
        f.seek(-avb.FOOTER_SIZE, 2)
        info = avb.footer_info(f.read(avb.FOOTER_SIZE))
        size = info['original_size'] if info else os.path.getsize(src)
        f.seek(0)
        with open(dest, 'wb') as o:
            left = size
            while left:
                b = f.read(min(left, 1 << 22))
                if not b:
                    raise SourceError(f'{src}: 比 footer 记录的大小短')
                o.write(b)
                left -= len(b)
    return bool(info)


def replace_file(img, path, data):
    """Replace a file inside an ext4 image, keeping mode, owner and security.selinux."""
    st = debugfs(img, f'stat {path}')
    mode = int(re.search(r'Mode:\s+0?([0-7]+)', st).group(1), 8)
    uid = int(re.search(r'User:\s+(\d+)', st).group(1))
    gid = int(re.search(r'Group:\s+(\d+)', st).group(1))
    with tempfile.TemporaryDirectory() as t:
        ea = os.path.join(t, 'ea')
        debugfs(img, f'ea_get -f {ea} {path} security.selinux')
        selinux = open(ea, 'rb').read()
        new = os.path.join(t, 'new')
        open(new, 'wb').write(data)
        debugfs(img, f'rm {path}', f'write {new} {path}', f'sif {path} mode 0{0o100000 | mode:o}',
                f'sif {path} uid {uid}', f'sif {path} gid {gid}', f'ea_set -f {ea} {path} security.selinux',
                write=True)
    st2 = debugfs(img, f'stat {path}')
    with tempfile.TemporaryDirectory() as t:
        ea2 = os.path.join(t, 'ea')
        debugfs(img, f'ea_get -f {ea2} {path} security.selinux')
        label = open(ea2, 'rb').read()
    got = (int(re.search(r'Mode:\s+0?([0-7]+)', st2).group(1), 8), int(re.search(r'User:\s+(\d+)', st2).group(1)),
           int(re.search(r'Group:\s+(\d+)', st2).group(1)), label)
    if got != (mode, uid, gid, selinux):
        raise SourceError(f'{path}: 替换后元数据不对 {got}')


def edit_vendor(src, dest):
    """Lenovo stock vendor -> vendor with the erofs fstab entries."""
    strip_footer(src, dest)
    e2fsck, resize = e2tool('e2fsck'), e2tool('resize2fs')
    with open(dest, 'r+b') as f:                    # room to unshare the deduplicated blocks
        f.truncate(os.path.getsize(dest) + (1 << 30))
    run(resize, dest)
    run(e2fsck, '-fy', '-E', 'unshare_blocks', dest, ok=(0, 1))
    if 'shared_blocks' in run(e2tool('dumpe2fs'), '-h', dest):
        raise SourceError('vendor: unshare_blocks 没成功')
    with tempfile.TemporaryDirectory() as t:
        old = os.path.join(t, 'fstab')
        debugfs(dest, f'dump {FSTAB} {old}')
        text = open(old).read()
    new, added = fstab_add_erofs(text)
    if added:
        replace_file(dest, FSTAB, new.encode())
    if not fsck_clean(dest):
        raise SourceError('vendor: 改完后 e2fsck 检查不干净')
    run(resize, '-M', dest)
    if not fsck_clean(dest):
        raise SourceError('vendor: 收缩后 e2fsck 检查不干净')
    return {'file': FSTAB, 'erofs_added': added, 'before_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'after_sha256': hashlib.sha256(new.encode()).hexdigest()}


def latest_lenovo():
    c = sorted(glob.glob(os.path.join(WORK, 'lenovo', '*', 'lenovo.json')), key=os.path.getmtime)
    return os.path.dirname(c[-1]) if c else None


def build(lenovo_dir=None):
    lenovo_dir = lenovo_dir or latest_lenovo()
    if not lenovo_dir or not os.path.exists(os.path.join(lenovo_dir, 'lenovo.json')):
        raise SourceError('没有联想原厂组件：先跑 tb710fu.py pull')
    with open(os.path.join(lenovo_dir, 'lenovo.json')) as f:
        info = json.load(f)
    errs = lenovo.verdict(info)
    if errs:
        raise SourceError(f'{lenovo_dir} 不能用来构建：' + '；'.join(errs))
    root = os.path.join(WORK, 'build', 'vendor')
    src = os.path.realpath(lenovo_dir)
    # output name from the source folder name: keep letters (any script), digits, . _ -, replace the rest
    name = re.sub(r'[^\w.-]', '_', os.path.basename(src)).lstrip('.') or 'lenovo'
    out = os.path.join(root, name)
    o = os.path.realpath(out)
    if src == o or src.startswith(o + os.sep):
        raise SourceError(f'来源 {lenovo_dir} 就在输出目录 {out} 里，替换输出会删掉来源：把来源放到别处')
    print(f'vendor 侧：{lenovo_dir} → {out}')
    os.makedirs(root, exist_ok=True)
    # build in a temporary folder and swap it in at the end: a failed rebuild never damages the previous result
    tmp = tempfile.mkdtemp(prefix=f'.{name}-new-', dir=root)
    old = None
    try:
        res = _build(lenovo_dir, info, tmp)
        if os.path.exists(out):
            old = tempfile.mkdtemp(prefix=f'.{name}-old-', dir=root)
            os.rmdir(old)
            os.rename(out, old)
        os.rename(tmp, out)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        if old and not os.path.exists(out):
            os.rename(old, out)
        raise
    if old:
        shutil.rmtree(old, ignore_errors=True)
    return out, res


def _build(lenovo_dir, info, out):
    res = {'lenovo': lenovo_dir, 'lenovo_files': info.get('files', {}), 'images': {}}
    for n in PASS:
        src = os.path.join(lenovo_dir, f'{n}.img')
        if not os.path.exists(src):
            raise SourceError(f'联想原厂组件里没有 {n}.img')
        print(f'  {n}：原样（去掉 AVB footer）', flush=True)
        strip_footer(src, os.path.join(out, f'{n}.img'))
    print('  vendor：fstab 加 erofs …', flush=True)
    res['changes'] = {'vendor': [edit_vendor(os.path.join(lenovo_dir, 'vendor.img'), os.path.join(out, 'vendor.img'))]}
    for n in ('vendor',) + PASS:
        p = os.path.join(out, f'{n}.img')
        with open(p, 'rb') as f:
            fs, _ = layout.fs_type(f.read(4096))
        res['images'][n] = {'bytes': os.path.getsize(p), 'sha256': file_sha256(p), 'fs': fs}
    res['created'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    with open(os.path.join(out, 'vendor.json'), 'w') as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    return res


def report(out, res):
    print('\n==== vendor 侧 ====')
    for n, i in res['images'].items():
        print(f'{n:12} {i["fs"]:5} {i["bytes"] / 2**20:8.1f} MiB  sha256 {i["sha256"][:16]}…')
    for c in res['changes']['vendor']:
        print(f'vendor {c["file"]}：加了 erofs 条目 {", ".join(c["erofs_added"]) or "（已有，没改）"}')
    print(f'\n✅ {out}')
    return True
