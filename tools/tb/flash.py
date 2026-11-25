"""flash: write a package made by `package` to this tablet over 9008, and put the backup back (`restore`).

Stages (manifest.json "plan"):
  first        boot, init_boot, dtbo, vendor_boot, vbmeta, vbmeta_system of the current slot + super without Find
               Device; then, separately confirmed, metadata and the start of userdata are zeroed (factory reset).
               When the package replaces the stock 495 ABL (manifest device.abl), also abl of the current slot: only
               the pinned CN 279 ABL (abl.py), only over the exact ABL check saw (or over itself on a re-run).
  after-setup  vbmeta_system + super with Find Device; data kept. Only allowed when the tablet is in stage first.
  upgrade      a newer (or the same) Xiaomi version of the same region, or a rebuild with more of this project's fixes,
               onto a tablet this computer has fully flashed (after-setup or an earlier upgrade) and that still runs it
               (vbmeta_system and the start of super match the record's fingerprint): the Find Device super +
               vbmeta_system, plus every boot chain image that differs from what the tablet has. Data kept; never a
               downgrade; the ABL must already be the one stage first wrote. restore / restore-stock drop the record.
  system       ArmadaOS Freedom is installed next to Android (its installer replaced boot with the kShimC menu and
               re-signed vbmeta / vbmeta_system / recovery): only super + vbmeta_system of the current slot, the
               latter re-signed with the key the tablet's vbmeta chains to it. Nothing of the boot chain is written,
               so the menu, Freedom and its signatures stay. first / after-setup refuse while Freedom is installed.
restore        boot chain of the current slot (with recovery and abl) + super from the device's own full backup.

What is never done: writing the GPT, the other slot, firmware below the boot chain (abl only as above), or any
partition whose sectors
overlap a unique-data partition (persist, modemst1/2, fsg, fsc, devinfo, frp, keystore, ssd, secdata,
uefivarstore, cdt, ddr, proinfo, lenovolock). Every write is read back; a mismatch stops at once and leaves the
tablet in 9008 (no reset). The partition table is compared again after the writes.
"""
import hashlib, json, os, re, shutil, subprocess, sys, tempfile, time

from . import abl as abl_mod, avb, backup as backup_mod, check, layout
from .sources import WORK, SourceError

UNIQUE = ('persist', 'modemst1', 'modemst2', 'fsg', 'fsc', 'devinfo', 'frp', 'keystore', 'ssd', 'secdata',
          'uefivarstore', 'cdt', 'ddr', 'proinfo', 'lenovolock')
BOOT_CHAIN = ('boot', 'init_boot', 'dtbo', 'vendor_boot', 'vbmeta', 'vbmeta_system')
RESTORE_CHAIN = BOOT_CHAIN + ('recovery', 'abl')
# partitions the ArmadaOS Freedom installer adds to LUN0: their presence means the boot chain is no longer ours
FREEDOM_PARTS = ('ARMADA_BOOT', 'ARMADA_ROOT', 'ARMADA_BACKUP')
# vbmeta / vbmeta_system hold a bare AVB image, no footer: a shorter image written over a longer one leaves the old tail
# behind it, and tools that later re-sign in place (the ArmadaOS Freedom installer) refuse to grow over non-zero bytes.
# These are always written zero-padded to the whole partition.
RAW_VBMETA = ('vbmeta', 'vbmeta_system')
WIPE = {'metadata': None, 'userdata': 64 << 20}     # bytes zeroed from the start (None = the whole partition)
CHUNK = 1 << 22


def file_sha256(path, length=None):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        left = os.path.getsize(path) if length is None else length
        while left:
            b = f.read(min(left, CHUNK))
            if not b:
                raise SourceError(f'{path}: 比预期短')
            h.update(b)
            left -= len(b)
    return h.hexdigest()


def confirm(prompt, word, assume_yes=False):
    """The user types `word` to go on. assume_yes is for tests calling run()/restore() directly; the command
    line has no way to set it."""
    if assume_yes:
        print(f'{prompt}\n（测试：自动确认 "{word}"）')
        return
    got = input(f'{prompt}\n要继续就输入 {word}：').strip()
    if got != word:
        raise SourceError('没有确认，什么都没写')


# ---------------------------------------------------------------- package and backup

def load_package(pkg):
    """manifest.json of a package directory, with every listed file checked (size + sha256)."""
    try:
        with open(os.path.join(pkg, 'manifest.json')) as f:
            m = json.load(f)
    except (OSError, ValueError):
        raise SourceError(f'{pkg} 不是 package 生成的刷机包（没有 manifest.json）')
    if m.get('schema') != 1:
        raise SourceError(f'{pkg}/manifest.json 的格式版本 {m.get("schema")} 不认识')
    print(f'核对刷机包 {pkg} …', flush=True)
    for name, v in sorted(m.get('files', {}).items()):
        p = os.path.join(pkg, name)
        if os.path.basename(name) != name or not os.path.isfile(p):
            raise SourceError(f'刷机包里没有 {name}')
        if os.path.getsize(p) != v['bytes'] or file_sha256(p) != v['sha256']:
            raise SourceError(f'刷机包里的 {name} 和 manifest 的记录不一致（文件损坏或被改过）')
    if m.get('inputs', {}).get('debug'):
        print('⚠ 这是调试版刷机包（adb 免授权）：只用于排查启动问题')
    return m


def device_backup(serial, table, backup_dir=None):
    """The latest complete backup of this tablet whose partition table equals the current one, fully verified."""
    root = os.path.join(WORK, 'backup')
    cands = [backup_dir] if backup_dir else sorted(
        (os.path.join(root, d) for d in os.listdir(root)) if os.path.isdir(root) else [], reverse=True)
    for d in cands:
        try:
            with open(os.path.join(d, 'backup.json')) as f:
                st = json.load(f)
        except (OSError, ValueError):
            if backup_dir:
                raise SourceError(f'{d} 不是本工具做的备份')
            continue
        why = None
        if (st.get('device') or {}).get('chip_serial') != serial:
            why = f'不是这台平板（芯片序列号 {serial}）的备份'
        elif not st.get('complete'):
            why = '还没备份完'
        elif st.get('table') != table:
            why = '备份之后分区表变了'
        if why:
            if backup_dir:
                raise SourceError(f'{d} {why}')
            continue
        missing = [n for n in UNIQUE if n in table and f'{n}.bin' not in st.get('items', {})]
        if missing:
            raise SourceError(f'{d} 里缺唯一数据分区 {missing}')
        backup_mod.load(d)                  # every recorded file present with its recorded sha256
        return d, st
    raise SourceError(f'没有这台平板（芯片序列号 {serial}）、和现在分区表一致的完整备份：先跑 tb710fu.py backup')


# ---------------------------------------------------------------- device

def table_of(parts):
    return {n: {k: p[k] for k in ('lun', 'start', 'sectors')} for n, p in sorted(parts.items())}


def overlaps(a, b):
    return a['lun'] == b['lun'] and a['start'] < b['start'] + b['sectors'] and b['start'] < a['start'] + a['sectors']


def target(src, name, nbytes):
    """GPT entry of partition `name`, checked: present once, not unique data, no overlap with unique data, fits."""
    if name in UNIQUE:
        raise SourceError(f'{name} 是唯一数据分区，永远不写')
    p = src.parts.get(name)
    if not p:
        raise SourceError(f'设备上没有 {name} 分区')
    if src.names_count.get(name, 0) != 1:
        raise SourceError(f'分区名 {name} 在 GPT 里出现了 {src.names_count.get(name, 0)} 次，不能按名字定位')
    dup = [u for u in UNIQUE if src.names_count.get(u, 0) > 1]
    if dup:
        raise SourceError(f'唯一数据分区 {dup} 在 GPT 里出现了不止一次，核对不了重叠，拒绝写')
    for u in UNIQUE:
        if u in src.parts and overlaps(p, src.parts[u]):
            raise SourceError(f'{name} 的扇区和唯一数据分区 {u} 重叠，拒绝写')
    if nbytes > p['bytes']:
        raise SourceError(f'{name}：要写 {nbytes} 字节，分区只有 {p["bytes"]}')
    if nbytes % layout.SECTOR:
        raise SourceError(f'{name}：要写的长度 {nbytes} 不是 {layout.SECTOR} 的整数倍')
    return p


def rollback_now(src, slot):
    """Rollback indexes of the images on the tablet now: {location: index} for vbmeta, vbmeta_system, boot."""
    out = {}
    top = check.partition_vbmeta(src, f'vbmeta{slot}')
    if top is None:
        raise SourceError(f'读不到设备上的 vbmeta{slot}')
    v = avb.parse_vbmeta(top)
    out[v['rollback_location']] = v['rollback']
    for c in v['chains']:
        if c['partition'] in ('boot', 'vbmeta_system'):
            blob = check.partition_vbmeta(src, c['partition'] + slot)
            if blob is None:
                raise SourceError(f'读不到设备上 {c["partition"]}{slot} 的 AVB 签名')
            out[c['rollback_location']] = avb.parse_vbmeta(blob)['rollback']
    return out


def precheck(src, serial_want, slot_want):
    """Same tablet (chip serial from Sahara), active slot as expected, partition names unique."""
    got = (src.sahara or {}).get('chip_serial')
    if not got:
        raise SourceError('这次 edl 没打印芯片序列号（firehose 已经在跑），认不出是不是同一台平板：'
                          '拔线，长按电源 15–20 秒关机，重新进 9008 再试')
    if got != serial_want:
        raise SourceError(f'这台平板的芯片序列号是 {got}，刷机包是给 {serial_want} 做的：不能刷')
    if src.slot != slot_want:
        raise SourceError(f'平板现在的当前槽是 {src.slot}，刷机包按 {slot_want} 做的：重新 check、重建刷机包')


# ---------------------------------------------------------------- writes

def raw_vbmeta(name):
    """True for vbmeta / vbmeta_system of either slot (or without a slot suffix)."""
    return (name[:-2] if name.endswith(('_a', '_b')) else name) in RAW_VBMETA


def write_length(src, name, nbytes):
    """Bytes `write_file` really writes for an `nbytes` image of `name`: a short vbmeta / vbmeta_system fills the
    partition. The checks before the first write (no_overlap) must use the same length."""
    p = src.parts.get(name)
    if raw_vbmeta(name) and p and p.get('bytes') and nbytes < p['bytes']:
        return p['bytes']
    return nbytes


def zero_padded(path, nbytes, workdir):
    """`path` followed by zeros up to `nbytes`, as a new file in `workdir`."""
    out = os.path.join(workdir, 'padded-' + os.path.basename(path))
    with open(path, 'rb') as f, open(out, 'wb') as o:
        shutil.copyfileobj(f, o, CHUNK)
        o.truncate(nbytes)
    return out


class Writer:
    """Writes files / zeros to GPT-located partitions and reads every write back."""

    def __init__(self, src, workdir, before_first_write=None):
        self.src, self.work, self.first = src, workdir, before_first_write

    def _ws(self, p, path):
        if self.first:                      # the tablet is about to change: run the hook exactly once
            self.first, hook = None, self.first
            hook()
        self.src.edl('ws', p['start'], path, f'--lun={p["lun"]}')

    def write_file(self, name, path, expect):
        """expect = {'bytes', 'sha256'} recorded for the file (manifest.json / backup.json)."""
        n = os.path.getsize(path)
        p = target(self.src, name, n)
        want = file_sha256(path)
        if n != expect['bytes'] or want != expect['sha256']:
            raise SourceError(f'{path} 和核对时的记录不一致（核对之后被改过？）：没有写 {name}')
        note = ''
        full = write_length(self.src, name, n)
        if full != n:                                   # the rest of the partition becomes zeros, not what was there
            path, note = zero_padded(path, full, self.work), f'，补零到整个分区 {full} 字节'
            n, want = full, file_sha256(path)
        print(f'  写 {name}（LUN {p["lun"]}，扇区 {p["start"]}，{n / 2**20:.1f} MiB{note}）…', flush=True)
        self._ws(p, path)
        self.verify(name, p, n, want)
        if note:
            os.remove(path)

    def zero(self, name):
        n = wipe_bytes(self.src, name)
        p = target(self.src, name, n)
        z = os.path.join(self.work, f'zero-{name}.bin')
        with open(z, 'wb') as f:
            f.truncate(n)
        print(f'  清零 {name} 前 {n / 2**20:.1f} MiB …', flush=True)
        self._ws(p, z)
        self.verify(name, p, n, file_sha256(z))
        os.remove(z)

    def verify(self, name, p, n, want):
        back = os.path.join(self.work, f'readback-{name}.bin')
        self.src.read_sectors(p['lun'], p['start'], n // layout.SECTOR, back)
        got = file_sha256(back, n) if os.path.getsize(back) >= n else None
        os.remove(back)
        if got != want:
            raise SourceError(f'{name} 读回和写入的不一致：已停止，平板留在 9008（没有复位）。'
                              '先把这段输出原样保存、发出来，不要急着重试写入或开机。'
                              '写回备份的办法：拔线 → 长按电源 15–20 秒关机 → 按住音量+和音量−插线重新进 9008 → tb710fu.py restore --dry-run，再 tb710fu.py restore（restore 要重新进 9008 才能读到芯片序列号）')
        print(f'    读回一致（sha256 {want[:16]}…）', flush=True)


def wipe_bytes(src, name):
    p = src.parts.get(name)
    if not p:
        raise SourceError(f'设备上没有 {name} 分区')
    return min(WIPE[name] or p['bytes'], p['bytes'])


def same_table_after(src, table, flags):
    """Partition table read again after writing: same entries and same attribute flags (they pick the slot)."""
    parts = src.reread_parts()
    if table_of(parts) != table:
        raise SourceError('写完后分区表和写之前不一样：停在这里，平板留在 9008。把输出发出来')
    changed = sorted(n for n, p in parts.items() if flags.get(n) != p.get('flags'))
    if changed:
        raise SourceError(f'写完后这些分区的属性（flags，决定启动哪个槽）变了：{changed}。停在这里，平板留在 9008')


def check_space(nbytes):
    need = nbytes + (1 << 30)
    free = shutil.disk_usage(WORK).free
    if free < need:
        raise SourceError(f'{WORK} 剩余 {free / 2**30:.1f} GiB，读回需要 {need / 2**30:.1f} GiB')


def no_overlap(src, writes):
    """writes = [(partition, bytes)]: refuse when any two of the ranges written overlap on the disk."""
    rng = []
    for name, nbytes in writes:
        p = src.parts[name]
        rng.append((name, {'lun': p['lun'], 'start': p['start'], 'sectors': max(nbytes // layout.SECTOR, 1)}))
    for i, (a, ra) in enumerate(rng):
        for b, rb in rng[i + 1:]:
            if overlaps(ra, rb):
                raise SourceError(f'{a} 和 {b} 要写的扇区重叠（GPT 不对？），拒绝写')


def rollback_of_backup(bdir, slot):
    """Rollback indexes of the images in the backup: {location: index} for vbmeta, vbmeta_system, boot."""
    def blob(part):
        with open(os.path.join(bdir, f'{part}.bin'), 'rb') as f:
            vb = avb.find_vbmeta(f.read())
        if vb is None:
            raise SourceError(f'备份里的 {part} 没有 AVB 签名')
        return vb
    v = avb.parse_vbmeta(blob(f'vbmeta{slot}'))
    out = {v['rollback_location']: v['rollback']}
    for c in v['chains']:
        if c['partition'] in ('boot', 'vbmeta_system'):
            out[c['rollback_location']] = avb.parse_vbmeta(blob(c['partition'] + slot))['rollback']
    return out


# stage first leaves a record here; after-setup requires it (same tablet, same package)
def state_path(serial):
    return os.path.join(WORK, 'flash', f'{serial}.json')


HEAD = 1 << 20      # start of super compared before after-setup (holds the liblp metadata of the variant)


def plan_lines(rows):
    return '\n'.join(f'  {dst:18} ← {src_desc}' for dst, src_desc in rows)


def file_desc(name, info):
    """How a source file shows in a plan: name, size and the full sha256, so the user can check it."""
    return f'{name}（{info["bytes"]} 字节，sha256 {info["sha256"]}）'


def short_serial(s):
    """The chip serial as plans print it: output gets pasted into public issues."""
    return f'{s[:3]}…{s[-3:]}' if s and len(s) > 6 else (s or '?')


VERSION_RE = r'OS(\d+)\.(\d+)\.(\d+)\.(\d+)(?:\.(\w+))?$'


def version_key(v):
    """OS4.0.5.0.XPZCNXM -> (4, 0, 5, 0); None when it does not look like a HyperOS version.

    >>> version_key('OS4.0.5.0.XPZCNXM') < version_key('OS4.0.10.0.XPZCNXM')
    True
    >>> version_key('V816') is None
    True
    """
    import re
    m = re.match(VERSION_RE, v or '')
    return tuple(int(x) for x in m.groups()[:4]) if m else None


def version_region(v):
    """OS4.0.5.0.XPZCNXM -> 'XPZCNXM' (device / region build); None when absent.

    >>> version_region('OS4.0.5.0.XPZCNXM'), version_region('OS4.0.5.0')
    ('XPZCNXM', None)
    """
    import re
    m = re.match(VERSION_RE, v or '')
    return m.group(5) if m else None


def read_record(serial):
    try:
        with open(state_path(serial)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def write_record(serial, rec):
    os.makedirs(os.path.dirname(state_path(serial)), exist_ok=True)
    tmp = state_path(serial) + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(rec, f)
    os.replace(tmp, state_path(serial))


def forget_record(serial):
    """restore / restore-stock are about to write: the tablet will no longer run what this computer flashed."""
    if serial and os.path.exists(state_path(serial)):
        os.remove(state_path(serial))


def fingerprint_of(pkg, m, plan, slot):
    """What a stage leaves on the tablet, to recognise it later: vbmeta_system's sha256 and super's first HEAD bytes."""
    vs = m['files'][plan[f'vbmeta_system{slot}']]
    with open(os.path.join(pkg, plan['super']), 'rb') as f:
        head = hashlib.sha256(f.read(HEAD)).hexdigest()
    return {'vbmeta_system': vs['sha256'], 'vbmeta_system_bytes': vs['bytes'], 'super_head': head}


def device_matches(src, slot, fp):
    cur = src.read_range(f'vbmeta_system{slot}', 0, fp['vbmeta_system_bytes'])
    return (hashlib.sha256(cur).hexdigest() == fp['vbmeta_system']
            and hashlib.sha256(src.read_range('super', 0, HEAD)).hexdigest() == fp['super_head'])


def known_path(pkg_id):
    return os.path.join(WORK, 'flash', 'packages', f'{pkg_id}.json')


def remember_package(pkg):
    """`package` is about to replace pkg: keep what is needed to recognise a tablet flashed from it.

    Records made before the fingerprint and version fields only name the package; without this, rebuilding the same
    Xiaomi version (to pick up fixes, the usual reason to upgrade) would leave such a record unverifiable.
    """
    mf = os.path.join(pkg, 'manifest.json')
    try:
        with open(mf) as f:
            m = json.load(f)
        info = {'version': m['inputs']['xiaomi']['version'],
                'fp': fingerprint_of(pkg, m, m['plan']['after_setup'], m['device']['slot'])}
    except (OSError, ValueError, KeyError, TypeError):
        return                              # not a complete package: nothing a record could point at
    path = known_path(file_sha256(mf))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + '.tmp', 'w') as f:
        json.dump(info, f)
    os.replace(path + '.tmp', path)


def recorded_package(pkg_id):
    """The package directory under work/out whose manifest.json has sha256 pkg_id (records made before the
    fingerprint and version fields existed only name the package)."""
    import glob
    for mf in sorted(glob.glob(os.path.join(WORK, 'out', '*', 'manifest.json'))):
        if file_sha256(mf) == pkg_id:
            return os.path.dirname(mf)
    return None


def upgrade_plan(src, pkg, m, slot, rec):
    """What `upgrade` writes: the Find Device super + vbmeta_system, and the boot chain images that differ.
    Returns (plan, version on the tablet, baseline fingerprint).

    Only onto what this computer flashed: the record says after-setup / upgrade, and the tablet's vbmeta_system and
    the start of super are what that run wrote (record "fp"), or what an interrupted upgrade was writing
    ("pending"). The baseline is the fingerprint the tablet actually matches; the caller stores it before the first
    write, so a run that fails before writing anything still finds the tablet acceptable afterwards. An upgrade cut
    between vbmeta_system and super leaves one part old and one new: that mix is accepted only for the very package
    that was being written (its fingerprint is "pending"), which rewrites both.
    """
    if rec.get('stage') not in ('after-setup', 'upgrade'):
        raise SourceError('这台电脑上没有这台平板两阶段都刷完（after-setup）的记录：upgrade 只用于已经用本项目刷好的平板。'
                          '没刷过就按 flash --stage first / after-setup 刷')
    old, base, pending = rec.get('version'), rec.get('fp'), rec.get('pending')
    if not base or not old:
        # a record from before these fields: take them from the package it names
        pkg_id = rec.get('package', '')
        opkg = recorded_package(pkg_id)
        if opkg:
            om = load_package(opkg)
            known = {'version': om['inputs']['xiaomi']['version'],
                     'fp': fingerprint_of(opkg, om, om['plan']['after_setup'], slot)}
        else:
            # rebuilt since: package kept the fingerprint of the one it replaced
            try:
                with open(known_path(pkg_id)) as f:
                    known = json.load(f)
            except (OSError, ValueError):
                raise SourceError('记录里没有平板现状的指纹，work/out 里也找不到记录所指的刷机包：核对不了平板上跑的是不是'
                                  '本项目刷的系统，不升级')
        old = old or known['version']
        base = base or known['fp']
    fps = [fp for fp in (base, pending) if fp]
    vs_now = src.read_range(f'vbmeta_system{slot}', 0, max(fp['vbmeta_system_bytes'] for fp in fps))
    head_now = hashlib.sha256(src.read_range('super', 0, HEAD)).hexdigest()

    def parts(fp):          # (vbmeta_system is this one's, start of super is this one's)
        return (hashlib.sha256(vs_now[:fp['vbmeta_system_bytes']]).hexdigest() == fp['vbmeta_system'],
                head_now == fp['super_head'])
    whole = [fp for fp in fps if all(parts(fp))]
    if whole:
        matched = whole[0]
    elif pending and all(a or b for a, b in zip(parts(base), parts(pending))):
        if fingerprint_of(pkg, m, m['plan']['after_setup'], slot) != pending:
            raise SourceError('上次升级写到一半中断了（vbmeta_system 和 super 开头一个是升级前的、一个是新的）：'
                              '只能用当时那个刷机包重跑 flash --stage upgrade，把它写完')
        matched = base      # the record keeps the same (base, pending) pair for the rerun
        print('上次升级写到一半中断了：用同一个刷机包重跑，把它写完')
    else:
        raise SourceError('平板上的 vbmeta_system / super 开头不是这台电脑上次刷的（之后用 restore、恢复原厂或别的工具刷过？）：'
                          '不升级。要装就按 flash --stage first 重刷')
    new = m['inputs']['xiaomi']['version']
    if version_key(new) is None:
        raise SourceError(f'刷机包的小米版本 {new} 认不出来：不升级')
    if version_key(old) is None:
        raise SourceError(f'记录里平板上的小米版本 {old} 认不出来：不升级')
    if version_key(new) < version_key(old):
        raise SourceError(f'刷机包的小米版本 {new} 比平板上的 {old} 旧：不降级（数据只能往新版本迁移）')
    if version_region(new) != version_region(old):
        raise SourceError(f'刷机包的小米版本 {new} 和平板上的 {old} 不是同一个机型 / 地区的包：不升级')
    print(f'小米版本：{old} → {new}')
    variant = m['plan']['after_setup']
    plan = dict(variant)
    abl_part = f'abl{slot}'
    if m['device'].get('abl'):
        f = m['files'][m['plan']['first'][abl_part]]
        cur = hashlib.sha256(src.read_range(abl_part, 0, f['bytes'])).hexdigest()
        if cur != abl_mod.PINNED['sha256']:
            raise SourceError(f'平板上的 {abl_part} 不是第一阶段写的 CN 279 abl：不能只升级，按 first 重刷')
    same = []
    for d, f in m['plan']['first'].items():
        if d in plan or d in (abl_part, f'vbmeta_system{slot}', 'super'):
            continue
        info = m['files'][f]
        target(src, d, info['bytes'])
        if hashlib.sha256(src.read_range(d, 0, info['bytes'])).hexdigest() != info['sha256']:
            plan[d] = f
        else:
            same.append(d)
    print(f'启动链：{"、".join(sorted(same)) or "无"}和平板上的一样，不写'
          + (f'；{"、".join(sorted(set(plan) - set(variant)))} 有变化，一起写' if set(plan) - set(variant) else ''))
    return plan, old, matched


def freedom_present(parts):
    """Names of the ArmadaOS Freedom partitions in the partition table (empty: not installed)."""
    return [n for n in FREEDOM_PARTS if n in parts]


def refuse_with_freedom(src, what):
    found = freedom_present(src.parts)
    if found:
        raise SourceError(
            f'平板上装了 ArmadaOS Freedom（分区表里有 {"、".join(found)}）。{what}会写回本工具的 boot / vbmeta：'
            'kShimC 启动菜单会被覆盖，Freedom 就进不去了；它重签过的 vbmeta_system 也和本工具的对不上，安卓会起不来。'
            '只换安卓系统用 flash --stage system（只写 super 和 vbmeta_system）')


def run(src, pkg, stage, backup_dir=None, dry_run=False, assume_yes=False):
    """Flash stage `first`, `after-setup`, `upgrade` or `system` of package directory pkg. Returns True when the
    tablet may reset."""
    if stage == 'system':
        return run_system(src, pkg, backup_dir, dry_run, assume_yes)
    refuse_with_freedom(src, f'flash --stage {stage} ')
    m = load_package(pkg)
    dev = m['device']
    slot = dev['slot']
    precheck(src, dev.get('chip_serial'), slot)
    table = table_of(src.parts)
    flags = {n: p.get('flags') for n, p in src.parts.items()}
    bdir, _ = device_backup(dev.get('chip_serial'), table, backup_dir)
    print(f'备份：{bdir}（已核对）')
    rb_dev = rollback_now(src, slot)
    rb_pkg = {int(k): int(v) for k, v in dev['rollback'].items()}
    low = {k: (rb_pkg.get(k), v) for k, v in rb_dev.items() if rb_pkg.get(k) is None or rb_pkg[k] < v}
    if low:
        raise SourceError(f'刷机包的回滚索引低于平板上现在的镜像 {low}（位置: (包, 设备)）：会启动不了，重建刷机包')
    find = stage != 'first'
    rec = read_record(dev['chip_serial'])
    if stage == 'upgrade':
        plan, old_version, base_fp = upgrade_plan(src, pkg, m, slot, rec)
    else:
        plan = m['plan']['first' if stage == 'first' else 'after_setup']
    allowed = {f'{n}{slot}' for n in BOOT_CHAIN} | {'super'}
    abl_part = f'abl{slot}'
    if stage == 'first' and dev.get('abl'):
        allowed.add(abl_part)
    bad = [d for d in plan if d not in allowed]
    if bad:
        raise SourceError(f'manifest 的写入计划里有不允许写的分区 {bad}')
    if abl_part in plan:
        # only the pinned ABL, and only over the stock ABL check saw (or over itself when a first run was cut short)
        f = m['files'][plan[abl_part]]
        target(src, abl_part, f['bytes'])
        if f['bytes'] != abl_mod.PINNED['bytes'] or f['sha256'] != abl_mod.PINNED['sha256']:
            raise SourceError('刷机包里的 abl 不是预期的 CN 17.0.04.279 abl：重新打包')
        cur = hashlib.sha256(src.read_range(abl_part, 0, f['bytes'])).hexdigest()
        if cur not in (dev['abl']['before_sha256'], abl_mod.PINNED['sha256']):
            raise SourceError(f'平板上的 {abl_part}（sha256 {cur[:16]}…）不是 check 时读到的那份：重跑 check 再打包')
    pkg_id = file_sha256(os.path.join(pkg, 'manifest.json'))
    if stage == 'after-setup':
        # only after a complete stage first of this very package on this tablet
        if rec.get('stage') != 'first' or rec.get('package') != pkg_id:
            raise SourceError('这台电脑上没有这个刷机包第一阶段完整写完的记录：先做 flash --stage first（它全部成功才会留记录）')
        cur = src.read_range(f'vbmeta_system{slot}', 0, m['files']['vbmeta_system_nofind.img']['bytes'])
        if hashlib.sha256(cur).hexdigest() != m['files']['vbmeta_system_nofind.img']['sha256']:
            raise SourceError('平板上的 vbmeta_system 不是这个刷机包第一阶段写的：先做 flash --stage first')
        with open(os.path.join(pkg, 'super_nofind.img'), 'rb') as f:
            want_head = hashlib.sha256(f.read(HEAD)).hexdigest()
        if hashlib.sha256(src.read_range('super', 0, HEAD)).hexdigest() != want_head:
            raise SourceError('平板上 super 的开头不是这个刷机包第一阶段写的：先做 flash --stage first')
    rows = [(d, file_desc(f, m['files'][f])) for d, f in sorted(plan.items())]
    for d, f in plan.items():
        target(src, d, m['files'][f]['bytes'])          # check every target before writing anything
    wipes = []
    if stage == 'first':
        for n in WIPE:
            nb = wipe_bytes(src, n)
            target(src, n, nb)
            wipes.append((n, nb))
    no_overlap(src, [(d, write_length(src, d, m['files'][f]['bytes'])) for d, f in plan.items()] + wipes)
    print(f'\n==== 写入计划（{stage}，槽 {slot}，平板 {short_serial(dev.get("chip_serial"))}）====\n{plan_lines(rows)}')
    for n, nb in wipes:
        print(f'  {n:18} ← 清零前 {nb / 2**20:.0f} MiB（恢复出厂，平板上的用户数据全部丢失）')
    if abl_part in plan:
        print(f'  （{abl_part}：平板上是联想原厂 495 的 abl，只认联想自己的密钥；换成 {abl_mod.PINNED["source"]}。'
              f'原来的在备份里，restore 会写回）')
    print('不写：GPT、另一个槽、' + ('abl 以外的' if abl_part in plan else '') + '底层固件、唯一数据分区（'
          + '、'.join(UNIQUE) + '）')
    if dry_run:
        print('\n--dry-run：检查都通过，什么都没写')
        return False
    check_space(max(m['files'][f]['bytes'] for f in plan.values()))
    # every authorisation before the first write: declining any of them leaves the tablet untouched
    if stage == 'after-setup':
        confirm('\n第二阶段只能在开机向导完成之后做（向导里没登录小米账号、没开查找设备）。向导已经完成了吗？',
                'setup-done', assume_yes)
    if stage == 'upgrade':
        confirm('\n升级不清数据，开机时系统自己迁移。userdata 不能回滚（TEE 防回滚）：升级后开不了机就只能清数据重刷；'
                'restore 能写回原系统，但新系统迁移过的数据它用不了，写回后同样要清数据。个人文件请先另外备份。确认升级吗？',
                'upgrade', assume_yes)
    confirm('\n确认写入上面这些分区？', 'flash', assume_yes)
    if wipes:
        confirm('\n第一阶段还要清零 metadata 和 userdata 开头（恢复出厂，平板上的用户数据全部丢失；'
                '不清的话新系统多半进不去）。不同意就什么都不写。', 'wipe', assume_yes)
    new_fp = fingerprint_of(pkg, m, plan, slot)
    version = m['inputs']['xiaomi']['version']

    def drop_record():
        # the first write is about to happen: an earlier completion record no longer describes the tablet.
        # An upgrade keeps it, marked: an interrupted upgrade may be run again (the tablet then matches either the old
        # or the new fingerprint), and the version only goes up from here, so the interrupted state cannot be
        # "upgraded" back to an older package.
        if stage == 'upgrade':
            # fp = what the tablet matched just now (also a baseline recovered for an old record, or a finished but
            # unrecorded pending one), so a run that stops before its first write leaves the tablet acceptable
            write_record(dev['chip_serial'], dict(rec, fp=base_fp, pending=new_fp,
                                                  version=version if version_key(version) >= version_key(old_version)
                                                  else old_version))
        elif os.path.exists(state_path(dev['chip_serial'])):
            os.remove(state_path(dev['chip_serial']))
    with tempfile.TemporaryDirectory(dir=WORK) as t:
        w = Writer(src, t, before_first_write=drop_record)
        # super last among the images: the boot chain is small and quick; super takes minutes
        for d, f in sorted(plan.items(), key=lambda kv: kv[0] == 'super'):
            w.write_file(d, os.path.join(pkg, f), m['files'][f])
        for n, _ in wipes:
            w.zero(n)
    same_table_after(src, table, flags)
    write_record(dev['chip_serial'], {'stage': stage, 'package': pkg_id, 'version': version, 'find': find,
                                       'fp': new_fp, 'finished': time.strftime('%Y-%m-%dT%H:%M:%S%z')})
    print('\n✅ 写入完成，全部读回一致，分区表和属性没变')
    if stage == 'first':
        print('开机后过开机向导：不要登录小米账号、不要开“查找设备”。向导完成后再进 9008 跑 flash --stage after-setup')
    return True


def verify_signature(img):
    """avbtool's own check that a bare vbmeta image is signed by the public key it embeds (verify_image would also
    want every image its descriptors cover next to it)."""
    from . import bootchain
    code = ('import sys; sys.path.insert(0, sys.argv[1]); import avbtool\n'
            'b = open(sys.argv[2], "rb").read(); h = avbtool.AvbVBMetaHeader(b[:256])\n'
            'n = 256 + h.authentication_data_block_size + h.auxiliary_data_block_size\n'
            'sys.exit(0 if h.algorithm_type and avbtool.verify_vbmeta_signature(h, b[:n]) else 1)')
    p = subprocess.run([sys.executable, '-c', code, bootchain.AVB_DIR, img], capture_output=True, text=True)
    if p.returncode:
        raise SourceError(f'{img} 的签名验证不过：{(p.stdout + p.stderr).strip()[-300:]}')


def resign_vbmeta_system(src_img, key_name, out):
    """src_img re-signed with the AOSP test key `key_name`: same descriptors, rollback index and flags.
    Returns the parsed result after avbtool has verified the signature with that key."""
    from . import bootchain                 # avbtool + the repository's test keys
    keys = {'testkey_rsa4096': (bootchain.K4096, 'SHA256_RSA4096'),
            'testkey_rsa2048': (bootchain.K2048, 'SHA256_RSA2048')}
    if key_name not in keys:
        raise SourceError(f'平板 vbmeta 链到 vbmeta_system 的公钥是 {key_name or "非测试密钥"}，本工具签不了')
    key, alg = keys[key_name]
    with open(src_img, 'rb') as f:
        old = avb.parse_vbmeta(avb.find_vbmeta(f.read()))
    bootchain.avbtool('make_vbmeta_image', '--output', out, '--key', key, '--algorithm', alg,
                      '--rollback_index', old['rollback'], '--flags', old['flags'],
                      '--padding_size', bootchain.VBMETA_PAD, '--include_descriptors_from_image', src_img)
    verify_signature(out)                   # the embedded key is checked to be `key_name` below
    with open(out, 'rb') as f:
        new = avb.parse_vbmeta(f.read())
    with open(src_img, 'rb') as f, open(out, 'rb') as g:
        same = avb.descriptor_blobs(avb.find_vbmeta(f.read())) == avb.descriptor_blobs(g.read())
    if not same or new['rollback'] != old['rollback'] or new['flags'] != old['flags'] \
            or (new['key'] or {}).get('name') != key_name:
        raise SourceError('重签后的 vbmeta_system 和刷机包里的描述符 / 回滚索引 / flags 不一致：没有写')
    return new


def run_system(src, pkg, backup_dir=None, dry_run=False, assume_yes=False):
    """Stage system: Android next to ArmadaOS Freedom gets a new super + vbmeta_system; the boot chain stays."""
    m = load_package(pkg)
    dev = m['device']
    slot = dev['slot']
    serial = dev.get('chip_serial')
    precheck(src, serial, slot)
    found = freedom_present(src.parts)
    if not found:
        raise SourceError('平板上没有 ArmadaOS Freedom 的分区：--stage system 只用于装了 Freedom 的平板。'
                          '没装时升级用 flash --stage after-setup（或 upgrade）')
    table = table_of(src.parts)
    flags = {n: p.get('flags') for n, p in src.parts.items()}
    bdir, _ = device_backup(serial, table, backup_dir)
    print(f'备份：{bdir}（已核对）')
    rec = read_record(serial)
    if not rec or rec.get('stage') not in ('first', 'after-setup', 'upgrade', 'system'):
        raise SourceError('这台电脑上没有这台平板用本工具刷入的记录（work/flash/<序列号>.json），'
                          '不知道该用带还是不带查找设备的 super')
    find = rec.get('find', rec['stage'] != 'first')
    variant = 'find' if find else 'nofind'
    new_v, old_v = m['inputs']['xiaomi'].get('version'), rec.get('version')
    if old_v and version_key(new_v) and version_key(old_v):
        if version_key(new_v) < version_key(old_v):
            raise SourceError(f'刷机包的小米版本 {new_v} 比平板上的 {old_v} 旧：不降级')
        if version_region(new_v) != version_region(old_v):
            raise SourceError(f'刷机包的小米版本 {new_v} 和平板上的 {old_v} 不是同一个机型 / 地区的包：不升级')

    top_blob = check.partition_vbmeta(src, f'vbmeta{slot}')
    if top_blob is None:
        raise SourceError(f'读不到平板上的 vbmeta{slot}')
    top = avb.parse_vbmeta(top_blob)
    chain = [c for c in top['chains'] if c['partition'] == 'vbmeta_system']
    if len(chain) != 1:
        raise SourceError(f'平板 vbmeta{slot} 里没有（或不止一条）链到 vbmeta_system 的描述符')
    chain = chain[0]
    with open(os.path.join(pkg, 'vbmeta.img'), 'rb') as f:
        pkg_top = f.read()
    if avb.descriptor_blobs(pkg_top) != avb.descriptor_blobs(top_blob):
        raise SourceError(f'刷机包 vbmeta 里 vendor、odm、vendor_dlkm、system_dlkm、init_boot、dtbo、vendor_boot 的描述符'
                          f'和平板 vbmeta{slot} 里的不一样（联想组件变了）。这种升级要连 vbmeta 一起换，'
                          '装了 Freedom 时不支持：没有写')
    src_name = f'vbmeta_system_{variant}.img'
    with open(os.path.join(pkg, src_name), 'rb') as f:
        pkg_vs = avb.parse_vbmeta(avb.find_vbmeta(f.read()))
    rb_dev = rollback_now(src, slot)
    if pkg_vs['rollback'] < rb_dev.get(chain['rollback_location'], 0):
        raise SourceError(f'刷机包 vbmeta_system 的回滚索引 {pkg_vs["rollback"]} 低于平板上的 '
                          f'{rb_dev.get(chain["rollback_location"])}：会启动不了')

    part_vs, sup = f'vbmeta_system{slot}', f'super_{variant}.img'
    target(src, 'super', m['files'][sup]['bytes'])
    with tempfile.TemporaryDirectory(dir=WORK) as t:
        vs = os.path.join(t, f'vbmeta_system_{variant}-{(chain["key"] or {}).get("name") or "key"}.img')
        resign_vbmeta_system(os.path.join(pkg, src_name), (chain['key'] or {}).get('name'), vs)
        if os.path.getsize(vs) > src.parts[part_vs]['bytes']:
            raise SourceError(f'重签后的 vbmeta_system 比 {part_vs} 分区还大')
        with open(vs, 'r+b') as f:            # zeros to the end of the partition: no tail of an older vbmeta_system
            f.truncate(src.parts[part_vs]['bytes'])
        vs_info = {'bytes': os.path.getsize(vs), 'sha256': file_sha256(vs)}
        target(src, part_vs, vs_info['bytes'])
        no_overlap(src, [(part_vs, src.parts[part_vs]['bytes']), ('super', m['files'][sup]['bytes'])])
        rows = [(part_vs, f'{src_name} 用 {chain["key"]["name"]} 重签、补零到整个分区（{vs_info["bytes"]} 字节，'
                          f'sha256 {vs_info["sha256"]}）'),
                ('super', file_desc(sup, m['files'][sup]))]
        print(f'\n==== 写入计划（system：装了 ArmadaOS Freedom，只换安卓系统；槽 {slot}，平板 {short_serial(serial)}）====\n'
              f'{plan_lines(rows)}')
        print(f'  小米 {old_v or "（记录里没有版本）"} → {new_v}；{"带" if find else "不带"}查找设备（按上次刷入的记录）')
        print(f'不写：boot（kShimC 启动菜单）、vbmeta、recovery、abl、init_boot、dtbo、vendor_boot、{"、".join(found)}、'
              '另一个槽、GPT、唯一数据分区；安卓数据保留')
        if dry_run:
            print('\n--dry-run：检查都通过，什么都没写')
            return False
        check_space(m['files'][sup]['bytes'])
        confirm('\n确认写入上面这些分区？', 'flash', assume_yes)
        w = Writer(src, t)
        w.write_file(part_vs, vs, vs_info)
        w.write_file('super', os.path.join(pkg, sup), m['files'][sup])
    same_table_after(src, table, flags)
    write_record(serial, {'stage': 'system', 'package': file_sha256(os.path.join(pkg, 'manifest.json')), 'find': find,
                          'version': new_v, 'finished': time.strftime('%Y-%m-%dT%H:%M:%S%z')})
    print(f'\n✅ 安卓系统已换成 {new_v}，全部读回一致；kShimC 菜单和 Freedom 没动')
    return True


def restore(src, backup_dir=None, assume_yes=False, dry_run=False):
    """Write the current slot's boot chain and super back from the device's own full backup."""
    serial = (src.sahara or {}).get('chip_serial')
    if not serial:
        raise SourceError('这次 edl 没打印芯片序列号，认不出平板：重新进 9008 再试')
    table = table_of(src.parts)
    flags = {n: p.get('flags') for n, p in src.parts.items()}
    bdir, st = device_backup(serial, table, backup_dir)
    slot = st.get('slot')
    if slot != src.slot:
        raise SourceError(f'备份时当前槽是 {slot}，现在是 {src.slot}：不能直接写回')
    rows = []
    for n in RESTORE_CHAIN + ('super',):
        part = n if n == 'super' else n + slot
        f = os.path.join(bdir, f'{part}.bin')
        if f'{part}.bin' not in st['items']:
            raise SourceError(f'备份里没有 {part}')
        target(src, part, st['items'][f'{part}.bin']['bytes'])
        rows.append((part, f))
    no_overlap(src, [(part, write_length(src, part, st['items'][f'{part}.bin']['bytes'])) for part, _ in rows])
    rb_dev, rb_bak = rollback_now(src, slot), rollback_of_backup(bdir, slot)
    low = {k: (rb_bak.get(k), v) for k, v in rb_dev.items() if rb_bak.get(k) is None or rb_bak[k] < v}
    if low:
        raise SourceError(f'备份里镜像的回滚索引低于平板上现在的 {low}（位置: (备份, 设备)）：写回后多半启动不了。'
                          '平板在备份之后升级过系统？用当时版本的联想官方包恢复')
    shown = [(part, file_desc(f, st['items'][f'{part}.bin'])) for part, f in rows]
    print(f'\n==== 写回备份（{bdir}，槽 {slot}）====\n{plan_lines(shown)}')
    if freedom_present(src.parts):
        # the backup's table equals today's, so it was taken with Freedom installed: its boot chain is the
        # installer's (kShimC menu, re-signed vbmeta) and writing it back keeps Freedom bootable
        print('（平板上装了 ArmadaOS Freedom；这份备份是装了 Freedom 之后做的，写回的是安装器的启动链，kShimC 菜单保留）')
    print('不写：GPT、另一个槽、abl 以外的底层固件、唯一数据分区；userdata 不动（备份里本来就没有）')
    if dry_run:
        print('\n--dry-run：检查都通过，什么都没写')
        return False
    check_space(max(st['items'][f'{part}.bin']['bytes'] for part, _ in rows))
    confirm('\n确认用备份写回上面这些分区？', 'restore', assume_yes)
    with tempfile.TemporaryDirectory(dir=WORK) as t:
        # the tablet goes back to its own system: the flash record no longer describes it (upgrade must refuse)
        w = Writer(src, t, before_first_write=lambda: forget_record(serial))
        for part, f in rows:
            w.write_file(part, f, st['items'][f'{part}.bin'])
    same_table_after(src, table, flags)
    print('\n✅ 已写回备份，全部读回一致，分区表和属性没变')
    return True
