"""Device check: does this tablet's ABL trust the AOSP test key, and what does the rest of the tool chain need
to know about it (slot, rollback indexes, super layout, firmware). Produces the device profile (JSON).

Read only. See tools/tb710fu.py check.
"""
import json, os, re, shutil, subprocess, tempfile, time, zipfile

from . import avb, layout
from .sources import WORK, SourceError, sha256

SCHEMA = 1

# Reference TB710FU (the one this project was developed on).
REF_HWID = '003050e100400000'
REF_PK_HASH = '8d0634170130a3bdb563558f7b9f6b031f3ee0f184eda385e0750028412a2a4e2605ac19544f7015ec54d5c45edffc4c'

# Non-unique firmware partitions (per slot when the device has _a/_b). Fingerprint only: never written.
FIRMWARE = ('xbl', 'xbl_config', 'xbl_ramdump', 'shrm', 'aop', 'aop_config', 'tz', 'hyp', 'devcfg', 'cpucp',
            'cpucp_dtb', 'qupfw', 'uefi', 'uefisecapp', 'imagefv', 'toolsfv', 'keymaster', 'featenabler',
            'multiimgoem', 'multiimgqti', 'abl', 'modem', 'dsp', 'bluetooth')
LOGICAL = ('system', 'system_ext', 'product', 'vendor', 'odm', 'vendor_dlkm', 'system_dlkm')

# Known firmware sets: image sha256 of the 9008 package that was flashed on the reference device
TESTED_FIRMWARE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tested_firmware.json')
KNOWN_SETS = {'tested_firmware.json': 'ZUXOS 1.5.04.495（HyperOS 3.0.305 移植包 + 495 免解锁补充包，参考设备）'}


def trim_sha(data):
    return sha256(data.rstrip(b'\0'))


def known_firmware():
    """{partition: sha256} of the reference firmware set, or None when the list cannot be read."""
    try:
        with open(TESTED_FIRMWARE) as f:
            files = json.load(f)['files']
    except (OSError, ValueError, KeyError):
        return None
    return {os.path.splitext(k)[0]: v for k, v in files.items()}


# ---------------------------------------------------------------- vbmeta chain

def partition_vbmeta(src, name):
    """vbmeta blob of a partition: a vbmeta* partition is read whole, others via their AVB footer."""
    if name.startswith('vbmeta'):
        data = src.read([name]).get(name, b'')
        return avb.find_vbmeta(data) if data else None
    size = src.size(name)
    foot = avb.parse_footer(src.read_range(name, size - layout.SECTOR, layout.SECTOR))
    if not foot:
        return None
    return src.read_range(name, foot[0], foot[1])


def check_chain(src, slot, prof):
    top = partition_vbmeta(src, f'vbmeta{slot}')
    if top is None:
        prof['errors'].append(f'读不到 vbmeta{slot}')
        return
    v = avb.parse_vbmeta(top)
    prof['vbmeta'] = {'vbmeta': v}
    prof['rollback'] = {str(v['rollback_location']): v['rollback']}
    for c in v['chains']:
        name = c['partition'] + slot
        if name not in src.parts:
            prof['warnings'].append(f'vbmeta 链到 {c["partition"]}，但设备上没有 {name}')
            continue
        blob = partition_vbmeta(src, name)
        if blob is None:
            prof['warnings'].append(f'{name} 没有 AVB 签名（vbmeta 要求它链式校验）')
            continue
        cv = avb.parse_vbmeta(blob)
        cv['key_matches_chain'] = bool(cv['key'] and c['key'] and cv['key']['sha1'] == c['key']['sha1'])
        prof['vbmeta'][c['partition']] = cv
        prof['rollback'][str(c['rollback_location'])] = cv['rollback']
        if not (cv['key'] and c['key']):
            prof['errors'].append(f'{name} 的链式公钥核对不了（{"vbmeta 链式描述" if not c["key"] else name}里缺少公钥）')
        elif not cv['key_matches_chain']:
            prof['errors'].append(f'{name} 的签名公钥和 vbmeta 链式描述里的不一致（按 AVB 规则这样启动不了）')


# ---------------------------------------------------------------- super

def check_super(src, slot, prof):
    if 'super' not in src.parts:
        prof['errors'].append('设备上没有 super 分区')
        return
    idx = layout.lp_slot_index(slot)
    head = src.read_range('super', 0, layout.LP_HEAD_BYTES)
    need = layout.lp_bytes_needed(head, idx)
    if need > len(head):
        head = src.read_range('super', 0, need)
    lp = layout.parse_lp(head, idx)
    sup = {'bytes': src.size('super'), 'metadata_slot': idx, 'partitions': {}}
    if src.kind == 'edl':
        p = src.parts['super']
        sup.update(lun=p['lun'], start=p['start'])
    for base in LOGICAL:
        name = base + slot
        ext = lp.get(name)
        if not ext:
            continue
        fs, label = layout.fs_type(src.read_range('super', ext[0][0], layout.SECTOR))
        sup['partitions'][name] = {'bytes': sum(e[1] for e in ext), 'fs': fs, 'label': label}
    fss = {sup['partitions'].get(n + slot, {}).get('fs') for n in ('system', 'vendor')}
    # Guess only: ZUXOS 1.5.04.495 ships ext4 (its vendor_boot fstab has no erofs entries) and every port seen so
    # far uses EROFS. A newer Lenovo build may switch to EROFS; `pull` checks the build.prop before using it.
    if fss == {'ext4'}:
        sup['content'] = 'stock'
    elif 'erofs' in fss:
        sup['content'] = 'modified'
    else:
        sup['content'] = 'unknown'
    prof['super'] = sup


# ---------------------------------------------------------------- firmware

def check_firmware(src, slot, prof):
    names = []
    for base in FIRMWARE:
        n = base + slot if base + slot in src.parts else base
        if n in src.parts:
            names.append(n)
    data = src.read(names)
    known = known_firmware()
    fw, hits, total = {}, 0, 0
    for n, d in data.items():
        base = n[:-2] if n.endswith(('_a', '_b')) else n
        full = sha256(d)
        fw[base] = {'partition': n, 'bytes': len(d), 'sha256': full, 'sha256_trim': trim_sha(d),
                    'version': avb.image_version(d)}
        if known and base in known:
            total += 1
            hits += known[base] in (full, fw[base]['sha256_trim'])
    prof['firmware'] = fw
    prof['firmware_match'] = {'set': KNOWN_SETS['tested_firmware.json'], 'matched': hits, 'compared': total}
    if known is None:
        prof['firmware_match']['skipped'] = True
        prof['warnings'].append(f'读不到参考固件清单 {TESTED_FIRMWARE}，没和参考设备比对底层固件')
    abl = 'abl' + slot if 'abl' + slot in data else 'abl'
    prof['abl'] = avb.check_abl(data.get(abl, b''))


def check_abl_only(src, slot, prof):
    n = 'abl' + slot if 'abl' + slot in src.parts else 'abl'
    d = src.read([n]).get(n, b'')
    prof['abl'] = avb.check_abl(d)
    prof['firmware'] = {'abl': {'partition': n, 'bytes': len(d), 'sha256': sha256(d), 'sha256_trim': trim_sha(d),
                                'version': avb.image_version(d)}}


# ---------------------------------------------------------------- device

def identity(src, prof):
    p = src.props
    dev = {'source': src.kind}
    if src.kind == 'edl':
        dev.update(src.sahara)
        lun0 = [v for v in src.parts.values() if v['lun'] == 0]
        dev['lun0_bytes'] = max((v['start'] + v['sectors']) * layout.SECTOR for v in lun0) if lun0 else None
        if src.sahara.get('hwid') and src.sahara['hwid'] != REF_HWID:
            prof['errors'].append(f'芯片 HWID {src.sahara["hwid"]} 与 TB710FU（{REF_HWID}）不同')
        if src.sahara.get('pk_hash') and src.sahara['pk_hash'] != REF_PK_HASH:
            prof['warnings'].append('OEM 签名公钥（PK hash）与参考 TB710FU 不同，可能是别的型号')
        if not src.sahara:
            prof['warnings'].append('这次 edl 没打印芯片信息（firehose 已在运行），HWID 未核对')
    else:
        for k in ('ro.product.model', 'ro.product.device', 'ro.product.vendor.model', 'ro.build.display.id',
                  'ro.build.version.incremental', 'ro.build.version.release', 'ro.vendor.build.fingerprint',
                  'ro.boot.verifiedbootstate', 'ro.boot.vbmeta.device_state', 'ro.boot.flash.locked',
                  'ro.boot.vbmeta.public_key_digest', 'ro.serialno'):
            if p.get(k):
                dev[k] = p[k]
        dev['root'] = src.root
        text = ' '.join(p.get(k, '') for k in ('ro.product.model', 'ro.product.vendor.model', 'ro.product.device',
                                                'ro.vendor.build.fingerprint', 'ro.build.display.id'))
        if 'TB710FU' not in text.upper():
            prof['warnings'].append('属性里没看到 TB710FU（第三方系统会改型号，以 9008 检查为准）')
        dev['advisory_hint'] = avb.advisory_hint('TB710FU', ' '.join(
            p.get(k, '') for k in ('ro.build.display.id', 'ro.build.version.incremental', 'ro.vendor.build.fingerprint')))
        pkd = p.get('ro.boot.vbmeta.public_key_digest', '').lower()
        if pkd:
            dev['bootloader_vbmeta_key'] = avb.TESTKEYS_SHA256.get(pkd, 'other')
    prof['device'] = dev
    for n in ('super', 'boot_a', 'boot_b', 'vbmeta_a', 'vbmeta_b', 'abl_a', 'abl_b'):
        if src.parts and n not in src.parts:
            prof['errors'].append(f'设备上没有 {n} 分区，不像是 TB710FU')
            break


def verdict(prof):
    abl = prof.get('abl') or {}
    if abl.get('trusts_testkey') is True:
        prof['testkey'] = 'trusted'
    elif abl.get('trusts_testkey') is False:
        prof['testkey'] = 'not_trusted'
        prof['errors'].append('ABL 只信任非测试密钥（多半是联想私钥）：锁着 BL 时 testkey 签名的镜像不会被接受')
    else:
        prof['testkey'] = 'unknown'
        prof['errors'].append('没能从 abl 判断是否信任测试密钥' + (f'：{abl["error"]}' if abl.get('error') else ''))
    sup = prof.get('super') or {}
    if sup.get('content') == 'modified':
        prof['warnings'].append('super 里是 EROFS，多半已不是联想原厂系统（推测）：pull 时可能需要另给联想官方包')
    elif sup.get('content') == 'unknown':
        prof['warnings'].append('认不出 super 里的系统是不是联想原厂')
    prof['ok'] = not prof['errors']


def run_check(src, firmware=True):
    prof = {'schema': SCHEMA, 'created': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'errors': [], 'warnings': []}
    identity(src, prof)
    slot = src.slot
    prof['slot'] = slot
    if src.kind == 'edl':
        prof['slots'] = {s: layout.ab_state(src.parts[f'boot{s}']['flags']) for s in ('_a', '_b')
                         if f'boot{s}' in src.parts}
    if not slot:
        prof['errors'].append('认不出当前槽（boot_a/boot_b 没有恰好一个 active）')
        verdict(prof)
        return prof
    (check_firmware if firmware else check_abl_only)(src, slot, prof)
    for step in (check_chain, check_super):
        try:
            step(src, slot, prof)
        except (ValueError, SourceError) as e:
            prof['errors'].append(f'{step.__name__}: {e}')
    verdict(prof)
    return prof


def save(prof):
    d = os.path.join(WORK, 'device')
    os.makedirs(d, exist_ok=True)
    dev = prof.get('device', {})
    ident = str(dev.get('chip_serial') or dev.get('ro.serialno') or 'unknown')
    ident = re.sub(r'[^0-9A-Za-z_.-]', '_', ident).strip('.')[:64] or 'unknown'   # the serial comes from the device
    path = os.path.join(d, f'{ident}-{time.strftime("%Y%m%d-%H%M%S")}.json')
    if os.path.dirname(os.path.abspath(path)) != os.path.abspath(d):
        raise ValueError(f'设备档案路径不在 {d} 里：{path}')
    for p in (path, os.path.join(d, 'latest.json')):
        with open(p, 'w') as f:
            json.dump(prof, f, ensure_ascii=False, indent=1)
    return path


# ---------------------------------------------------------------- report

def report(prof):
    dev = prof.get('device', {})
    print('\n==== 设备 ====')
    if dev.get('source') == 'edl':
        print(f'9008：HWID {dev.get("hwid", "?")}  芯片序列号 {dev.get("chip_serial", "?")}'
              f'  LUN0 {(dev.get("lun0_bytes") or 0) / 1e9:.0f} GB')
    else:
        for k in ('ro.product.model', 'ro.build.display.id', 'ro.boot.verifiedbootstate', 'ro.boot.vbmeta.device_state'):
            if dev.get(k):
                print(f'{k} = {dev[k]}')
        if dev.get('advisory_hint'):
            print(f'按联想公告推断：{dev["advisory_hint"]}')
    print(f'当前槽：{prof.get("slot") or "?"}')
    abl = prof.get('abl') or {}
    for k in abl.get('keys', []):
        print(f'ABL 内置 AVB 根公钥：RSA{k["bits"]}  {avb.key_str(k)}')
    vb = prof.get('vbmeta') or {}
    for part, v in vb.items():
        print(f'{part:14s} 内嵌公钥 {avb.key_str(v["key"])}（签名本身未验证）  回滚索引 {v["rollback"]}')
    if prof.get('rollback'):
        print('回滚位置：' + '  '.join(f'{k}={v}' for k, v in sorted(prof['rollback'].items())))
    sup = prof.get('super')
    if sup:
        fs = ', '.join(f'{n}:{v["fs"] or "?"}' for n, v in sup['partitions'].items())
        print(f'super {sup["bytes"] / 2**30:.1f} GiB  [{fs}]  → '
              + {'stock': '像联想原厂（ext4，推测）', 'modified': '多半已不是原厂（EROFS，推测）', 'unknown': '不确定'}[sup['content']])
    fm = prof.get('firmware_match')
    if fm and fm.get('skipped'):
        print('底层固件：没有参考清单，跳过比对')
    elif fm and fm['compared']:
        print(f'底层固件与 {fm["set"]}：{fm["matched"]}/{fm["compared"]} 个分区一致')
    print('\n==== 结论 ====')
    if prof['ok']:
        print('✅ 可以继续：ABL 信任 AOSP 测试密钥。构建时启动链会按上面的回滚索引用 testkey 重签。')
    else:
        print('❌ 不能继续：')
        for e in prof['errors']:
            print(f'   - {e}')
    for w in prof['warnings']:
        print(f'⚠ {w}')


# ---------------------------------------------------------------- offline files

MAX_ENTRY = 64 << 20   # abl / vbmeta images are a few MB at most
MAX_TOTAL = 512 << 20

ABL_RE = re.compile(r'^abl(_[ab])?\.(img|elf|mbn|bin)$', re.I)
VBMETA_RE = re.compile(r'^vbmeta(_system|_vendor)?(_[ab])?\.(img|bin)$', re.I)


def _wanted(name):
    b = os.path.basename(name)
    return ABL_RE.match(b) or VBMETA_RE.match(b)


def collect(path):
    """{name: bytes} of abl / vbmeta files in a folder, .zip, .7z or a single image."""
    if os.path.isdir(path):
        out = {}
        for root, _, names in os.walk(path):
            for n in names:
                if _wanted(n):
                    p = os.path.join(root, n)
                    out[os.path.relpath(p, path)] = open(p, 'rb').read()
        return out
    low = path.lower()
    if low.endswith('.zip'):
        out, total = {}, 0
        with zipfile.ZipFile(path) as z:
            for i in z.infolist():
                if not _wanted(i.filename):
                    continue
                if i.file_size > MAX_ENTRY or total + i.file_size > MAX_TOTAL:
                    raise SourceError(f'{path}: {i.filename} 解压后 {i.file_size} 字节，不像 abl / vbmeta，拒绝读取')
                with z.open(i) as f:
                    data = f.read(MAX_ENTRY + 1)
                if len(data) > MAX_ENTRY:
                    raise SourceError(f'{path}: {i.filename} 实际解压超过 {MAX_ENTRY} 字节，拒绝读取')
                total += len(data)
                out[i.filename] = data
        return out
    if low.endswith('.7z'):
        exe = shutil.which('7zz') or shutil.which('7z')
        if not exe:
            raise SourceError(f'{path}: 需要 7zz 或 7z 才能读 .7z（或者先解压，再传文件夹）')
        lst = subprocess.run([exe, 'l', '-slt', '-ba', path], capture_output=True, text=True).stdout
        want = [l[7:] for l in lst.splitlines() if l.startswith('Path = ') and _wanted(l[7:])]
        if not want:
            return {}
        with tempfile.TemporaryDirectory() as t:
            print(f'  从 7z 解出 {len(want)} 个文件（固实压缩包可能要等几分钟）…', flush=True)
            subprocess.run([exe, 'x', '-y', f'-o{t}', path] + want, check=True, stdout=subprocess.DEVNULL)
            return {n: open(os.path.join(t, n), 'rb').read() for n in want if os.path.exists(os.path.join(t, n))}
    return {os.path.basename(path): open(path, 'rb').read()}


def check_files(paths):
    """Offline check of firmware files (official package, or images dumped from a device). Returns ok."""
    files = {}
    for p in paths:
        got = collect(p)
        if not got:
            print(f'{p}: 没找到 abl / vbmeta 文件')
        pre = os.path.basename(p.rstrip('/')) if os.path.isdir(p) or p.lower().endswith(('.zip', '.7z')) else ''
        files.update({os.path.join(pre, n) if pre else n: d for n, d in got.items()})
    trusts = []
    for name in sorted(files):
        data = files[name]
        print(f'\n[{name}]  {len(data)} 字节  sha256 {sha256(data)[:16]}…')
        if data[:4] == b'\x7fELF' or ABL_RE.match(os.path.basename(name)):
            r = avb.check_abl(data)
            if r['signer']:
                print(f'  abl 镜像签名证书：{", ".join(r["signer"])}')
            if r['error']:
                print(f'  ⚠ {r["error"]}')
            for k in r['keys']:
                print(f'  ABL 内置 AVB 根公钥：RSA{k["bits"]}  {avb.key_str(k)}')
            trusts.append((name, r['trusts_testkey']))
            continue
        vb = avb.find_vbmeta(data)
        if vb is None:
            print('  （不认识的文件，跳过）')
            continue
        try:
            v = avb.parse_vbmeta(vb)
        except ValueError as e:
            print(f'  ⚠ {e}')
            continue
        print(f'  算法 {v["alg"]}  内嵌公钥 {avb.key_str(v["key"])}（签名本身未验证）')
        print(f'  回滚索引 {v["rollback"]}（位置 {v["rollback_location"]}）  flags {v["flags"]}  {v["release"]}')
        for c in v['chains']:
            print(f'  链 → {c["partition"]}：{avb.key_str(c["key"])}，回滚位置 {c["rollback_location"]}')
        if v['hashed']:
            print(f'  直接校验：{" ".join(v["hashed"])}')
    print('\n==== 结论 ====')
    word = {True: '✅ 信任 AOSP 测试密钥', False: '❌ 只信任非测试密钥（多半是联想私钥）', None: '❓ 判断不了'}
    for name, tr in trusts:
        print(f'{name}：{word[tr]}')
    if not trusts:
        print('❓ 没有可判断的 abl。')
        return False
    if all(tr is True for _, tr in trusts):
        print('✅ 所有 abl 都信任 AOSP 测试密钥。')
        return True
    print('❌ 不是所有 abl 都信任 AOSP 测试密钥（只看要刷的那个槽时，单独检查那个槽的 abl）。')
    return False
