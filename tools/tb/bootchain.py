"""bootchain: the boot chain for the new system, signed with the AOSP test keys at the device's rollback indexes.

Inputs: the Lenovo stock boot images (pull), the vendor and framework images (vendor, framework), the device
profile (check) and the device's full backup (backup). Output: work/build/bootchain/<tag>/
  boot, init_boot, dtbo         Lenovo stock (boot is re-signed only if its rollback index differs from the device)
  vendor_boot                   Lenovo stock, first-stage fstab with erofs entries, hash footer re-added
  vendor odm vendor_dlkm system_dlkm system system_ext product
                                the images with a dm-verity hashtree footer (no FEC)
  vbmeta                        testkey_rsa4096; the chain layout of the Lenovo vbmeta (boot loc 3, recovery
                                loc 1, vbmeta_system loc 2), hash/hashtree descriptors of the images above
  vbmeta_system                 testkey_rsa2048; system, system_ext, product + the device's own pvmfw
  bootchain.json                rollback indexes used, sha256 of every output, the checks done
recovery and pvmfw are not written: the device keeps its own (their descriptors/keys must match, checked here).
Rollback indexes are exactly the device's current values: never raised, so stock firmware still boots later.

Needs AOSP avbtool + test keys in tools/avb/ and mkbootimg/unpack_bootimg in tools/mkbootimg/, and lz4.
"""
import glob, json, os, re, shutil, subprocess, sys, tempfile, time

from .backup import file_sha256
from .sources import REPO, TOOLS, WORK, SourceError
from .vendor import fstab_add_erofs

AVB_DIR = os.path.join(TOOLS, 'avb')
MKB_DIRS = (os.path.join(TOOLS, 'mkbootimg'), os.path.join(os.path.dirname(REPO), 'tools', 'mkbootimg'))
K4096, K2048 = os.path.join(AVB_DIR, 'testkey_rsa4096.pem'), os.path.join(AVB_DIR, 'testkey_rsa2048.pem')
VBMETA_HASH = ('init_boot', 'dtbo', 'vendor_boot')          # hash descriptors in vbmeta
VBMETA_TREE = ('odm', 'system_dlkm', 'vendor', 'vendor_dlkm')  # hashtree descriptors in vbmeta
SYSTEM_TREE = ('system', 'system_ext', 'product')           # hashtree descriptors in vbmeta_system
CHAINS = {'boot': (3, 'rsa4096'), 'recovery': (1, 'rsa4096'), 'vbmeta_system': (2, 'rsa2048')}
VBMETA_PAD = 4096


def need(path, what):
    if not os.path.exists(path):
        raise SourceError(f'缺 {path}：{what}')
    return path


def avbtool(*args):
    p = subprocess.run([sys.executable, need(os.path.join(AVB_DIR, 'avbtool.py'),
                        'AOSP external/avb 的 avbtool.py'), *map(str, args)], capture_output=True, text=True)
    if p.returncode:
        raise SourceError(f'avbtool {args[0]} 失败：{(p.stdout + p.stderr).strip()[-600:]}')
    return p.stdout


def mkb(script):
    for d in MKB_DIRS:
        if os.path.exists(os.path.join(d, script)):
            return [sys.executable, os.path.join(d, script)]
    raise SourceError(f'缺 tools/mkbootimg/{script}（AOSP system/tools/mkbootimg）')


def run(cmd, **kw):
    p = subprocess.run(cmd, capture_output=True, **kw)
    if p.returncode:
        raise SourceError(f'{os.path.basename(cmd[0])} 失败：{(p.stdout + p.stderr)[-600:]!r}')
    return p.stdout


def info(img):
    t = avbtool('info_image', '--image', img)
    return {'text': t,
            'salt': (re.search(r'Salt:\s+([0-9a-f]+)', t) or [None, None])[1],
            'props': re.findall(r"Prop: (\S+) -> '([^']*)'", t),
            'rollback': int((re.search(r'Rollback Index:\s+(\d+)', t) or [0, 0])[1]),
            'partition_size': int((re.search(r'Image size:\s+(\d+)', t) or [0, 0])[1])}


# ---------------------------------------------------------------- pieces

def vendor_boot(src, dest, work):
    """Lenovo vendor_boot with erofs entries in the first-stage fstab; hash footer with the original salt/props."""
    i = info(src)
    part_size = os.path.getsize(src)
    unpacked = os.path.join(work, 'vb')
    args = run(mkb('unpack_bootimg.py') + ['--boot_img', src, '--out', unpacked, '--format', 'mkbootimg'],
               text=True).strip()
    frags = re.findall(r"--vendor_ramdisk_fragment (\S+)", args)
    if len(frags) != 1:
        raise SourceError(f'vendor_boot 有 {len(frags)} 个 ramdisk 片段，只支持 1 个')
    rd = frags[0]
    cpio = run(['lz4', '-dc', rd])
    entries, trailer = cpio_read(cpio)
    name = 'first_stage_ramdisk/fstab.qcom'
    hit = [e for e in entries if e['name'] == name]
    if not hit:
        raise SourceError(f'vendor_boot ramdisk 里没有 {name}')
    old = hit[0]['data'].decode()
    new, added = fstab_add_erofs(old)
    hit[0]['data'] = new.encode()
    with open(rd, 'wb') as f:
        f.write(run(['lz4', '-l', '-12', '--favor-decSpeed', '-c'], input=cpio_write(entries, trailer)))
    # rebuild with the same header arguments
    cmd = mkb('mkbootimg.py') + split_args(args) + ['--vendor_boot', dest]
    run(cmd)
    if os.path.getsize(dest) > part_size:
        raise SourceError('vendor_boot 重新打包后超过分区大小')
    footer = ['add_hash_footer', '--image', dest, '--partition_name', 'vendor_boot',
              '--partition_size', part_size, '--salt', i['salt']]
    for k, v in i['props']:
        footer += ['--prop', f'{k}:{v}']
    avbtool(*footer)
    return {'fstab': name, 'erofs_added': added}


def split_args(s):
    import shlex
    return shlex.split(s)


def cpio_read(b):
    """newc cpio -> ([{'hdr': fields, 'name', 'data'}], trailer header)"""
    out, i = [], 0
    while True:
        h = b[i:i + 110]
        if h[:6] != b'070701':
            raise SourceError('ramdisk 不是 newc cpio')
        f = [int(h[6 + 8 * k:14 + 8 * k], 16) for k in range(13)]
        namesize, filesize = f[11], f[6]
        name = b[i + 110:i + 110 + namesize - 1].decode()
        p = (i + 110 + namesize + 3) & ~3
        data = b[p:p + filesize]
        i = (p + filesize + 3) & ~3
        if name == 'TRAILER!!!':
            return out, f
        out.append({'f': f, 'name': name, 'data': data})


def cpio_write(entries, trailer):
    o = bytearray()

    def rec(f, name, data):
        f = list(f)
        f[6], f[11] = len(data), len(name) + 1
        o.extend(b'070701' + ''.join(f'{x:08X}' for x in f).encode() + name.encode() + b'\0')
        o.extend(b'\0' * (-len(o) % 4))
        o.extend(data)
        o.extend(b'\0' * (-len(o) % 4))

    for e in entries:
        rec(e['f'], e['name'], e['data'])
    rec(trailer, 'TRAILER!!!', b'')
    return bytes(o)


def min_partition_size(img):
    """Smallest 4 KiB-aligned partition size whose hashtree footer leaves room for the image."""
    size = os.path.getsize(img)

    def fits(p):
        return int(avbtool('add_hashtree_footer', '--partition_size', p, '--do_not_generate_fec',
                           '--calc_max_image_size').strip()) >= size

    lo, hi = size, size + size // 50 + (16 << 20)
    hi = (hi + 4095) // 4096 * 4096
    while not fits(hi):
        hi *= 2
    while hi - lo > 4096:
        mid = (lo + hi) // 2 // 4096 * 4096
        if fits(mid):
            hi = mid
        else:
            lo = mid
    return hi


def hashtree(src, dest, name, props=()):
    shutil.copyfile(src, dest)
    p = min_partition_size(dest)
    cmd = ['add_hashtree_footer', '--image', dest, '--partition_name', name, '--partition_size', p,
           '--hash_algorithm', 'sha256', '--do_not_generate_fec']
    for k, v in props:
        cmd += ['--prop', f'{k}:{v}']
    avbtool(*cmd)
    return p


# ---------------------------------------------------------------- build

def latest(kind):
    c = sorted(glob.glob(os.path.join(WORK, 'build', kind, '*', f'{kind}.json')), key=os.path.getmtime)
    return os.path.dirname(c[-1]) if c else None


def build(lenovo_dir=None, vendor_dir=None, framework_dir=None, backup_dir=None, profile=None, verity=True):
    from . import backup as backup_mod, vendor as vendor_mod
    lenovo_dir = lenovo_dir or vendor_mod.latest_lenovo()
    vendor_dir = vendor_dir or latest('vendor')
    framework_dir = framework_dir or latest('framework')
    backup_dir = backup_dir or backup_mod.latest()
    prof = json.load(open(profile or os.path.join(WORK, 'device', 'latest.json')))
    for d, what in ((lenovo_dir, 'pull'), (vendor_dir, 'vendor'), (framework_dir, 'framework'), (backup_dir, 'backup')):
        if not d or not os.path.isdir(d):
            raise SourceError(f'缺 {what} 的输出：先跑 tb710fu.py {what}')
    for k in (K4096, K2048):
        need(k, 'AOSP external/avb/test/data 的测试密钥')
    slot = prof['slot']
    rb = {int(k): v for k, v in prof['rollback'].items()}
    if prof.get('testkey') != 'trusted':
        raise SourceError('设备档案说 ABL 不信任测试密钥，不能构建')

    fw = json.load(open(os.path.join(framework_dir, 'framework.json')))
    tag = f'{prof["device"].get("chip_serial") or "device"}-{fw["version"]}' + \
          ('' if fw['find_device'] else '-nofind') + ('-debug' if fw['debug'] else '') + ('' if verity else '-noverity')
    out = os.path.join(WORK, 'build', 'bootchain', tag)
    work = os.path.join(out, 'work')
    os.makedirs(work, exist_ok=True)
    res = {'device': prof['device'], 'slot': slot, 'rollback': rb, 'verity': verity, 'inputs': {
        'lenovo': lenovo_dir, 'vendor': vendor_dir, 'framework': framework_dir, 'backup': backup_dir}, 'checks': []}
    print(f'启动链：{out}')

    # 1. the Lenovo vbmeta must have the same chain layout as the device
    stock = info(os.path.join(lenovo_dir, 'vbmeta.img'))['text']
    chains = dict((n, int(l)) for n, l in re.findall(
        r'Chain Partition descriptor:\s+Partition Name:\s+(\S+)\s+Rollback Index Location:\s+(\d+)', stock))
    want = {n: loc for n, (loc, _) in CHAINS.items()}
    devchains = prof['vbmeta']['vbmeta']['chains']
    dev = {c['partition']: c['rollback_location'] for c in devchains}
    devkeys = {c['partition']: (c['key'] or {}).get('name') for c in devchains}
    if chains != want or dev != want:
        raise SourceError(f'链式布局不对：联想包 {chains}，设备 {dev}，预期 {want}')
    if devkeys != {n: f'testkey_{k}' for n, (_, k) in CHAINS.items()}:
        raise SourceError(f'设备链上的公钥不是预期的测试密钥：{devkeys}')
    res['checks'].append(f'链式布局和公钥 {want}（联想包、设备一致）')

    # 2. what the device keeps: recovery and pvmfw
    b = lambda n: need(os.path.join(backup_dir, f'{n}{slot}.bin'), '设备完整备份')  # noqa: E731
    rec = prof['vbmeta']['recovery']
    if (rec.get('key') or {}).get('name') != 'testkey_rsa4096':
        raise SourceError('设备 recovery 不是 testkey_rsa4096 签的，和新 vbmeta 的链对不上')
    res['checks'].append(f'recovery 保留设备上的（testkey_rsa4096，回滚索引 {rec["rollback"]}）')

    # 3. boot images
    for n in ('init_boot', 'dtbo'):
        shutil.copyfile(os.path.join(lenovo_dir, f'{n}.img'), os.path.join(out, f'{n}.img'))
    boot = os.path.join(out, 'boot.img')
    shutil.copyfile(os.path.join(lenovo_dir, 'boot.img'), boot)
    bi = info(boot)
    if bi['rollback'] != rb[3]:
        print(f'  boot 回滚索引 {bi["rollback"]} ≠ 设备 {rb[3]}：重签', flush=True)
        avbtool('erase_footer', '--image', boot)
        cmd = ['add_hash_footer', '--image', boot, '--partition_name', 'boot', '--partition_size',
               os.path.getsize(os.path.join(lenovo_dir, 'boot.img')), '--salt', bi['salt'], '--key', K4096,
               '--algorithm', 'SHA256_RSA4096', '--rollback_index', rb[3], '--rollback_index_location', 3]
        for k, v in bi['props']:
            cmd += ['--prop', f'{k}:{v}']
        avbtool(*cmd)
        res['checks'].append(f'boot 按设备回滚索引 {rb[3]} 重签')
    else:
        res['checks'].append(f'boot 原样（回滚索引 {rb[3]} 和设备一致）')
    print('  vendor_boot：第一阶段 fstab 加 erofs …', flush=True)
    res['vendor_boot'] = vendor_boot(os.path.join(lenovo_dir, 'vendor_boot.img'), os.path.join(out, 'vendor_boot.img'),
                                     work)

    # 4. logical partitions: hashtree footers (props of the Lenovo ones kept from the stock vbmeta)
    stock_props = re.findall(r"Prop: (\S+) -> '([^']*)'", stock)
    res['partition_sizes'] = {}
    for n in VBMETA_TREE + SYSTEM_TREE:
        src = os.path.join(vendor_dir if n in VBMETA_TREE else framework_dir, f'{n}.img')
        props = [(k, v) for k, v in stock_props if k.startswith(f'com.android.build.{n}.')] if n in VBMETA_TREE else []
        print(f'  {n}：hashtree …', flush=True)
        res['partition_sizes'][n] = hashtree(src, os.path.join(out, f'{n}.img'), n, props)

    # 5. vbmeta_system: system, system_ext, product + the device's own pvmfw
    pv = os.path.join(work, 'pvmfw.img')
    shutil.copyfile(b('pvmfw'), pv)
    pvi = info(pv)['text']
    devvs = info(b('vbmeta_system'))['text']
    dig = re.search(r'Partition Name:\s+pvmfw\s+Salt:\s+\S+\s+Digest:\s+(\S+)', pvi)
    devdig = re.search(r'Partition Name:\s+pvmfw\s+Salt:\s+\S+\s+Digest:\s+(\S+)', devvs)
    if not dig or not devdig or dig[1] != devdig[1]:
        raise SourceError('设备 pvmfw 的摘要和设备 vbmeta_system 里记录的不一致')
    res['checks'].append('pvmfw 用设备上的（摘要和设备 vbmeta_system 一致）')
    pk = {}
    for k, path in (('rsa4096', K4096), ('rsa2048', K2048)):
        pk[k] = os.path.join(work, f'pk_{k}.bin')
        avbtool('extract_public_key', '--key', path, '--output', pk[k])
    vs = os.path.join(out, 'vbmeta_system.img')
    cmd = ['make_vbmeta_image', '--output', vs, '--key', K2048, '--algorithm', 'SHA256_RSA2048',
           '--rollback_index', rb[2], '--padding_size', VBMETA_PAD]
    for n in SYSTEM_TREE:
        cmd += ['--include_descriptors_from_image', os.path.join(out, f'{n}.img')]
    cmd += ['--include_descriptors_from_image', pv]
    avbtool(*cmd)

    # 6. vbmeta
    vb = os.path.join(out, 'vbmeta.img')
    cmd = ['make_vbmeta_image', '--output', vb, '--key', K4096, '--algorithm', 'SHA256_RSA4096',
           '--rollback_index', rb[0], '--flags', 0 if verity else 1, '--padding_size', VBMETA_PAD]
    for n, (loc, key) in CHAINS.items():
        cmd += ['--chain_partition', f'{n}:{loc}:{pk[key]}']
    for n in VBMETA_HASH + VBMETA_TREE:
        cmd += ['--include_descriptors_from_image', os.path.join(out, f'{n}.img')]
    avbtool(*cmd)

    # 7. self check: follow the whole chain (recovery / pvmfw = the device's)
    print('  自检 avbtool verify_image --follow_chain_partitions …', flush=True)
    vdir = os.path.join(work, 'verify')
    os.makedirs(vdir, exist_ok=True)
    for f in glob.glob(os.path.join(out, '*.img')):
        dst = os.path.join(vdir, os.path.basename(f))
        if not os.path.exists(dst):
            os.symlink(f, dst)
    for n in ('recovery', 'pvmfw'):
        dst = os.path.join(vdir, f'{n}.img')
        if not os.path.exists(dst):
            os.symlink(os.path.abspath(b(n)), dst)
    # avbtool 1.3.0 passes --key on to the chained vbmeta_system as well, so: follow the chain without --key
    # (every image is checked against its embedded key, the chained ones against --expected_chain_partition),
    # then check that vbmeta's own embedded key is testkey_rsa4096.
    top = info(os.path.join(vdir, 'vbmeta.img'))['text']
    if 'Public key (sha1):        2597c218aae470a130f61162feaae70afd97f011' not in top:
        raise SourceError('vbmeta 内嵌的公钥不是 testkey_rsa4096')
    cmd = ['verify_image', '--image', os.path.join(vdir, 'vbmeta.img'), '--follow_chain_partitions']
    for n, (loc, key) in CHAINS.items():
        cmd += ['--expected_chain_partition', f'{n}:{loc}:{pk[key]}']
    v = avbtool(*cmd)
    bad = [l for l in v.splitlines() if 'ERROR' in l or 'mismatch' in l.lower()]
    if bad:
        raise SourceError('自检失败：' + '; '.join(bad[:5]))
    res['checks'].append(f'avbtool verify_image --follow_chain_partitions 通过（{len(v.splitlines())} 行）')
    res['verify'] = v.splitlines()
    for n, want_rb in ((vb, rb[0]), (vs, rb[2]), (boot, rb[3])):
        got = info(n)['rollback']
        if got != want_rb:
            raise SourceError(f'{os.path.basename(n)} 回滚索引 {got} ≠ 设备 {want_rb}')
    res['checks'].append(f'回滚索引：vbmeta {rb[0]}、vbmeta_system {rb[2]}、boot {rb[3]}（= 设备当前值）')

    shutil.rmtree(work)
    res['images'] = {os.path.basename(f)[:-4]: {'bytes': os.path.getsize(f), 'sha256': file_sha256(f)}
                     for f in sorted(glob.glob(os.path.join(out, '*.img')))}
    res['created'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    with open(os.path.join(out, 'bootchain.json'), 'w') as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    return out, res


def report(out, res):
    print('\n==== 启动链 ====')
    for n, i in res['images'].items():
        print(f'{n:14} {i["bytes"] / 2**20:9.1f} MiB  sha256 {i["sha256"][:16]}…')
    for c in res['checks']:
        print(f'✓ {c}')
    if not res['verity']:
        print('⚠ vbmeta flags=1：dm-verity 关闭（备用方案）')
    print(f'\n✅ {out}')
    return True
