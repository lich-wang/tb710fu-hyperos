"""flash: write a package made by `package` to this tablet over 9008, and put the backup back (`restore`).

Stages (manifest.json "plan"):
  first        boot, init_boot, dtbo, vendor_boot, vbmeta, vbmeta_system of the current slot + super without Find
               Device; then, separately confirmed, metadata and the start of userdata are zeroed (factory reset)
  after-setup  vbmeta_system + super with Find Device; data kept. Only allowed when the tablet is in stage first.
restore        boot chain of the current slot + super from the device's own full backup.

What is never done: writing the GPT, the other slot, firmware below the boot chain, or any partition whose sectors
overlap a unique-data partition (persist, modemst1/2, fsg, fsc, devinfo, frp, keystore, ssd, secdata,
uefivarstore, cdt, ddr, proinfo, lenovolock). Every write is read back; a mismatch stops at once and leaves the
tablet in 9008 (no reset). The partition table is compared again after the writes.
"""
import hashlib, json, os, shutil, tempfile

from . import avb, backup as backup_mod, check, layout
from .sources import WORK, SourceError

UNIQUE = ('persist', 'modemst1', 'modemst2', 'fsg', 'fsc', 'devinfo', 'frp', 'keystore', 'ssd', 'secdata',
          'uefivarstore', 'cdt', 'ddr', 'proinfo', 'lenovolock')
BOOT_CHAIN = ('boot', 'init_boot', 'dtbo', 'vendor_boot', 'vbmeta', 'vbmeta_system')
RESTORE_CHAIN = BOOT_CHAIN + ('recovery',)
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
    """The user types `word` to go on. assume_yes only for tests (TB_FLASH_ASSUME_YES)."""
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

class Writer:
    """Writes files / zeros to GPT-located partitions and reads every write back."""

    def __init__(self, src, workdir):
        self.src, self.work = src, workdir

    def write_file(self, name, path):
        n = os.path.getsize(path)
        p = target(self.src, name, n)
        want = file_sha256(path)
        print(f'  写 {name}（LUN {p["lun"]}，扇区 {p["start"]}，{n / 2**20:.1f} MiB）…', flush=True)
        self.src.edl('ws', p['start'], path, f'--lun={p["lun"]}')
        self.verify(name, p, n, want)

    def zero(self, name, nbytes=None):
        p = self.src.parts.get(name)
        if not p:
            raise SourceError(f'设备上没有 {name} 分区')
        n = min(nbytes or p['bytes'], p['bytes'])
        target(self.src, name, n)
        z = os.path.join(self.work, f'zero-{name}.bin')
        with open(z, 'wb') as f:
            f.truncate(n)
        print(f'  清零 {name} 前 {n / 2**20:.1f} MiB …', flush=True)
        self.src.edl('ws', p['start'], z, f'--lun={p["lun"]}')
        self.verify(name, p, n, file_sha256(z))
        os.remove(z)

    def verify(self, name, p, n, want):
        back = os.path.join(self.work, f'readback-{name}.bin')
        self.src.read_sectors(p['lun'], p['start'], n // layout.SECTOR, back)
        got = file_sha256(back, n) if os.path.getsize(back) >= n else None
        os.remove(back)
        if got != want:
            raise SourceError(f'{name} 读回和写入的不一致：停在这里，平板留在 9008，不要拔线重启。'
                              '把这段输出发出来；可以用 tb710fu.py restore 写回备份')
        print(f'    读回一致（sha256 {want[:16]}…）', flush=True)


def plan_lines(rows):
    return '\n'.join(f'  {dst:18} ← {src_desc}' for dst, src_desc in rows)


def run(src, pkg, stage, backup_dir=None, dry_run=False, assume_yes=False):
    """Flash stage `first` or `after-setup` of package directory pkg. Returns True when the tablet may reset."""
    m = load_package(pkg)
    dev = m['device']
    slot = dev['slot']
    precheck(src, dev.get('chip_serial'), slot)
    table = table_of(src.parts)
    bdir, _ = device_backup(dev.get('chip_serial'), table, backup_dir)
    print(f'备份：{bdir}（已核对）')
    rb_dev = rollback_now(src, slot)
    rb_pkg = {int(k): int(v) for k, v in dev['rollback'].items()}
    low = {k: (rb_pkg.get(k), v) for k, v in rb_dev.items() if rb_pkg.get(k) is None or rb_pkg[k] < v}
    if low:
        raise SourceError(f'刷机包的回滚索引低于平板上现在的镜像 {low}（位置: (包, 设备)）：会启动不了，重建刷机包')
    plan = m['plan']['first' if stage == 'first' else 'after_setup']
    allowed = {f'{n}{slot}' for n in BOOT_CHAIN} | {'super'}
    bad = [d for d in plan if d not in allowed]
    if bad:
        raise SourceError(f'manifest 的写入计划里有不允许写的分区 {bad}')
    if stage == 'after-setup':
        # only from stage first: the tablet's vbmeta_system must be the package's nofind one
        cur = src.read_range(f'vbmeta_system{slot}', 0, m['files']['vbmeta_system_nofind.img']['bytes'])
        if hashlib.sha256(cur).hexdigest() != m['files']['vbmeta_system_nofind.img']['sha256']:
            raise SourceError('平板上的 vbmeta_system 不是这个刷机包第一阶段写的：先做 flash --stage first')
    rows = [(d, f'{f}（sha256 {m["files"][f]["sha256"][:16]}…）') for d, f in sorted(plan.items())]
    for d, f in plan.items():
        target(src, d, m['files'][f]['bytes'])          # check every target before writing anything
    print(f'\n==== 写入计划（{stage}，槽 {slot}，平板 {dev.get("chip_serial")}）====\n{plan_lines(rows)}')
    if stage == 'first':
        print('  然后单独确认：清零 metadata、userdata 前 64 MiB（恢复出厂，平板上的数据全部丢失）')
    print('不写：GPT、另一个槽、底层固件、唯一数据分区（' + '、'.join(UNIQUE) + '）')
    if dry_run:
        print('\n--dry-run：检查都通过，什么都没写')
        return False
    need = max(m['files'][f]['bytes'] for f in plan.values()) + (1 << 30)
    free = shutil.disk_usage(WORK).free
    if free < need:
        raise SourceError(f'{WORK} 剩余 {free / 2**30:.1f} GiB，读回 super 需要 {need / 2**30:.1f} GiB')
    confirm('\n确认写入上面这些分区？', 'flash', assume_yes)
    with tempfile.TemporaryDirectory(dir=WORK) as t:
        w = Writer(src, t)
        # super last: the boot chain is small and quick; super takes minutes
        for d, f in sorted(plan.items(), key=lambda kv: kv[0] == 'super'):
            w.write_file(d, os.path.join(pkg, f))
        if stage == 'first':
            confirm('\n启动链和 super 已写好并读回一致。下面清零 metadata 和 userdata 开头（恢复出厂，'
                    '平板上的用户数据全部丢失；不清的话新系统多半进不去）。', 'wipe', assume_yes)
            for n, nbytes in WIPE.items():
                w.zero(n, nbytes)
    after = table_of(src.reread_parts())
    if after != table:
        raise SourceError('写完后分区表和写之前不一样：停在这里，平板留在 9008。把输出发出来')
    print('\n✅ 写入完成，全部读回一致，分区表没变')
    if stage == 'first':
        print('开机后过开机向导：不要登录小米账号、不要开“查找设备”。向导完成后再进 9008 跑 flash --stage after-setup')
    return True


def restore(src, backup_dir=None, assume_yes=False, dry_run=False):
    """Write the current slot's boot chain and super back from the device's own full backup."""
    serial = (src.sahara or {}).get('chip_serial')
    if not serial:
        raise SourceError('这次 edl 没打印芯片序列号，认不出平板：重新进 9008 再试')
    table = table_of(src.parts)
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
    print(f'\n==== 写回备份（{bdir}，槽 {slot}）====\n{plan_lines(rows)}')
    print('不写：GPT、另一个槽、底层固件、唯一数据分区；userdata 不动（备份里本来就没有）')
    if dry_run:
        print('\n--dry-run：检查都通过，什么都没写')
        return False
    confirm('\n确认用备份写回上面这些分区？', 'restore', assume_yes)
    with tempfile.TemporaryDirectory(dir=WORK) as t:
        w = Writer(src, t)
        for part, f in rows:
            w.write_file(part, f)
    after = table_of(src.reread_parts())
    if after != table:
        raise SourceError('写完后分区表和写之前不一样：停在这里，平板留在 9008。把输出发出来')
    print('\n✅ 已写回备份，全部读回一致')
    return True
