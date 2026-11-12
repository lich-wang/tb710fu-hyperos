"""package: the flash package for one tablet, from two bootchain builds (with and without Find Device).

Output: work/out/<Xiaomi version>-<chip serial>/
  boot.img init_boot.img dtbo.img vendor_boot.img vbmeta.img      written to the device's current slot
  vbmeta_system_nofind.img + super_nofind.img                     first boot / setup wizard (no Find Device app)
  vbmeta_system_find.img   + super_find.img                       after the setup wizard
  manifest.json   sha256 of every file, the inputs (Lenovo / Xiaomi versions), the device it is bound to
                  (chip serial, HWID, OEM PK hash, slot, rollback indexes, super size) and the write plan
super holds the current slot's logical partitions (the other slot's are empty); it is written from the start
of the super partition and only as far as its contents go. Each super is read back: every partition's bytes
must match the image it came from.
"""
import glob, hashlib, json, os, re, shutil, sys, tempfile, time

from . import bootchain, layout
from .lenovo import fingerprints
from .backup import file_sha256
from .sources import TOOLS, WORK, SourceError

sys.path.insert(0, TOOLS)
import make_super  # noqa: E402

COMMON = ('boot', 'init_boot', 'dtbo', 'vendor_boot', 'vbmeta')
LOGICAL = ('system', 'system_ext', 'product', 'vendor', 'odm', 'vendor_dlkm', 'system_dlkm')
UNIQUE = ('persist', 'modemst1', 'modemst2', 'fsg', 'fsc', 'devinfo', 'frp', 'keystore', 'ssd', 'secdata',
          'uefivarstore', 'cdt', 'ddr', 'proinfo', 'lenovolock')


def range_sha256(path, off, length):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        f.seek(off)
        left = length
        while left:
            b = f.read(min(left, 1 << 22))
            if not b:
                raise SourceError(f'{path}: 读回时文件太短')
            h.update(b)
            left -= len(b)
    return h.hexdigest()


def pick(find):
    """Latest bootchain build of the given variant."""
    c = []
    for j in glob.glob(os.path.join(WORK, 'build', 'bootchain', '*', 'bootchain.json')):
        d = os.path.dirname(j)
        fw = json.load(open(os.path.join(json.load(open(j))['inputs']['framework'], 'framework.json')))
        if fw['find_device'] == find and not fw['debug']:
            c.append((os.path.getmtime(j), d))
    return sorted(c)[-1][1] if c else None


def build(find_dir=None, nofind_dir=None):
    find_dir, nofind_dir = find_dir or pick(True), nofind_dir or pick(False)
    if not find_dir or not nofind_dir:
        raise SourceError('要两份启动链：tb710fu.py framework 和 framework --no-finddevice，各自再跑 bootchain')
    bc = {k: json.load(open(os.path.join(d, 'bootchain.json'))) for k, d in (('find', find_dir), ('nofind', nofind_dir))}
    fwv = {k: json.load(open(os.path.join(bc[k]['inputs']['framework'], 'framework.json'))) for k in bc}
    if bc['find']['device'] != bc['nofind']['device'] or bc['find']['rollback'] != bc['nofind']['rollback']:
        raise SourceError('两份启动链不是同一台设备的')
    if fwv['find']['version'] != fwv['nofind']['version']:
        raise SourceError('两份启动链要同一个小米版本')
    if fwv['find']['find_device'] is not True or fwv['nofind']['find_device'] is not False:
        raise SourceError(f'--find 要带查找设备的启动链、--nofind 要不带的（现在 find={fwv["find"]["find_device"]}，'
                          f'nofind={fwv["nofind"]["find_device"]}）：两个目录是不是传反了')
    for n in COMMON + ('vendor', 'odm', 'vendor_dlkm', 'system_dlkm', 'system', 'system_ext'):
        a, b = bc['find']['images'][n]['sha256'], bc['nofind']['images'][n]['sha256']
        if a != b:
            raise SourceError(f'两份启动链的 {n} 不同（应该只有 product 和 vbmeta_system 不同）')
    prof = bootchain.load_profile(os.path.join(WORK, 'device', 'latest.json'))
    dev = bc['find']['device']
    if prof['device'].get('chip_serial') != dev.get('chip_serial'):
        raise SourceError('设备档案和启动链不是同一台设备（芯片序列号不同）')
    slots = {bc['find']['slot'], bc['nofind']['slot'], prof['slot']}
    if len(slots) != 1:
        raise SourceError(f'槽位不一致：find {bc["find"]["slot"]}、nofind {bc["nofind"]["slot"]}、设备档案 {prof["slot"]}'
                          '：按设备当前槽重建启动链')
    norm = lambda r: {int(k): int(v) for k, v in r.items()}       # noqa: E731
    if norm(prof['rollback']) != norm(bc['find']['rollback']):
        raise SourceError(f'设备现在的回滚索引 {norm(prof["rollback"])} 和启动链里的 {norm(bc["find"]["rollback"])} 不同'
                          '：启动链已过期，重跑 bootchain')
    if not (prof.get('super') or {}).get('bytes'):
        raise SourceError('设备档案里没有 super 大小：重跑 tb710fu.py check')
    slot = prof['slot']
    super_size = prof['super']['bytes']
    root = os.path.join(WORK, 'out')
    tag = re.sub(r'[^\w.-]', '_', f'{fwv["find"]["version"]}-{dev.get("chip_serial") or "device"}').lstrip('.')
    final = os.path.join(root, tag)
    os.makedirs(root, exist_ok=True)
    print(f'刷机包：{final}（槽 {slot}，super {super_size / 2**30:.1f} GiB）')
    # assemble and check in a temporary folder, swap it in at the end: a failed run never damages a previous package
    tmp = tempfile.mkdtemp(prefix=f'.{tag}-new-', dir=root)
    old = None
    try:
        manifest = _assemble(tmp, bc, fwv, prof, dev, slot, super_size, find_dir, nofind_dir)
        if os.path.exists(final):
            old = tempfile.mkdtemp(prefix=f'.{tag}-old-', dir=root)
            os.rmdir(old)
            os.rename(final, old)
        os.rename(tmp, final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        if old and not os.path.exists(final):
            os.rename(old, final)
        raise
    if old:
        shutil.rmtree(old, ignore_errors=True)
    return final, manifest


def copy_checked(src, dest, want):
    """Copy a bootchain image and check it against what bootchain.json recorded for it."""
    shutil.copyfile(src, dest)
    if os.path.getsize(dest) != want['bytes'] or file_sha256(dest) != want['sha256']:
        raise SourceError(f'{src} 和 bootchain.json 的记录不一致（文件被改过或损坏）：重跑 bootchain')


def _assemble(out, bc, fwv, prof, dev, slot, super_size, find_dir, nofind_dir):
    files = {}
    for n in COMMON:
        copy_checked(os.path.join(find_dir, f'{n}.img'), os.path.join(out, f'{n}.img'), bc['find']['images'][n])
        files[f'{n}.img'] = None
    supers = {}
    for k, d in (('nofind', nofind_dir), ('find', find_dir)):
        copy_checked(os.path.join(d, 'vbmeta_system.img'), os.path.join(out, f'vbmeta_system_{k}.img'),
                     bc[k]['images']['vbmeta_system'])
        files[f'vbmeta_system_{k}.img'] = None
        sp = os.path.join(out, f'super_{k}.img')
        print(f'  super_{k} …', flush=True)
        make_super.build(sp, super_size, {n: os.path.join(d, f'{n}.img') for n in LOGICAL}, slot)
        # read back: every partition's bytes must be the image it came from
        with open(sp, 'rb') as f:
            lp = layout.parse_lp(f.read(layout.LP_HEAD_BYTES))
        for n in LOGICAL:
            ext = lp.get(n + slot)
            want = bc[k]['images'][n]
            if not ext or len(ext) != 1 or ext[0][1] < want['bytes']:
                raise SourceError(f'super_{k}: {n}{slot} 的位置不对 {ext}')
            other = lp.get(n + ('_b' if slot == '_a' else '_a'))
            if other:
                raise SourceError(f'super_{k}: 另一个槽的 {n} 不是空的')
            if range_sha256(sp, ext[0][0], want['bytes']) != want['sha256']:
                raise SourceError(f'super_{k}: 读回的 {n} 和镜像不一致')
        supers[k] = {'bytes_written': os.path.getsize(sp),
                     'partitions': {n + slot: {'offset': lp[n + slot][0][0], 'bytes': lp[n + slot][0][1]} for n in LOGICAL}}
        files[f'super_{k}.img'] = None
        print(f'    读回核对通过（{len(LOGICAL)} 个分区）', flush=True)
    for f in files:
        p = os.path.join(out, f)
        files[f] = {'bytes': os.path.getsize(p), 'sha256': file_sha256(p)}
    lenovo = json.load(open(os.path.join(bc['find']['inputs']['lenovo'], 'lenovo.json')))
    manifest = {
        'schema': 1,
        'created': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'device': {'chip_serial': dev.get('chip_serial'), 'hwid': dev.get('hwid'), 'pk_hash': dev.get('pk_hash'),
                   'slot': slot, 'rollback': bc['find']['rollback'], 'super_bytes': super_size,
                   'profile_created': prof.get('created')},
        'inputs': {
            'xiaomi': {'version': fwv['find']['version'], 'fingerprint': None},
            'lenovo': {'source': lenovo.get('source'), 'package': lenovo.get('package'),
                       'system': next(iter(fingerprints(lenovo.get('partitions', {}).get('system', {})
                                                        .get('props', {}))), None)},
            'bootchain': {'find': find_dir, 'nofind': nofind_dir}, 'verity': bc['find']['verity']},
        'files': files,
        'supers': supers,
        # what flash writes (the device's current slot only); anything else, the unique partitions above all,
        # is never written
        'plan': {'first': {f'{n}{slot}': f'{n}.img' for n in COMMON} |
                          {f'vbmeta_system{slot}': 'vbmeta_system_nofind.img', 'super': 'super_nofind.img'},
                 'after_setup': {f'vbmeta_system{slot}': 'vbmeta_system_find.img', 'super': 'super_find.img'},
                 'never': list(UNIQUE)},
        'checks': bc['find']['checks'],
    }
    xj = os.path.join(fwv['find']['xiaomi'], 'xiaomi.json')
    xm = json.load(open(xj)) if os.path.exists(xj) else {}
    manifest['inputs']['xiaomi']['fingerprint'] = xm.get('metadata', {}).get('post-build')
    with open(os.path.join(out, 'manifest.json'), 'w') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    return manifest


def report(out, m):
    print('\n==== 刷机包 ====')
    for f, i in m['files'].items():
        print(f'{f:26} {i["bytes"] / 2**20:9.1f} MiB  sha256 {i["sha256"][:16]}…')
    d = m['device']
    print(f'绑定设备：芯片序列号 {d["chip_serial"]}  HWID {d["hwid"]}  槽 {d["slot"]}  回滚 {d["rollback"]}')
    print(f'小米 {m["inputs"]["xiaomi"]["version"]}；联想 {m["inputs"]["lenovo"]["system"]}')
    print(f'\n✅ {out}')
    return True
