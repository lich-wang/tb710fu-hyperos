"""restore-stock: put a TB710FU back to Lenovo's stock ZUXOS 1.5.04.495 over 9008 (rescue, or starting over).

This is the fallback for when the tablet's own backup is gone; with a backup, `restore` is the way back.

1. The Lenovo service flash package (stock_495.json: name, size, sha256, download address) is downloaded or taken
   from --package, and checked. It is password protected: the password is not in this repository; the user types
   it (or sets TB_STOCK_PASSWORD). It is passed to 7-Zip only, never printed or saved.
2. Every extracted file is checked against stock_495.json.
3. The write list is the one Lenovo's own flash script uses (rawprogram_unsparse0-half.xml + rawprogram1..5.xml,
   which leave persist alone), minus every unique-data partition (it would also write secdata and ddr).
4. The GPT of each LUN is computed offline from the package and patch*.xml for the LUN's real size (as fh_loader
   would patch it). A LUN whose GPT already equals it is left alone; any other LUN gets it only when every
   unique-data partition sits at exactly the same place in both, otherwise nothing is written at all.
5. Before the first write: chip serial (Sahara), rollback indexes of the images on the tablet must not be higher
   than the stock ones, everything listed and confirmed, and a read-only safety copy of all GPTs and unique-data
   partitions is saved. Every write is read back; the partition table is read again after the GPT writes.
Consequences: Android data wiped, partitions added after stock (e.g. ArmadaOS) removed from LUN0, firmware and
slot A boot chain back to stock.
"""
import getpass, hashlib, json, os, re, shutil, subprocess, tempfile, time, zlib
import xml.etree.ElementTree as ET

from . import layout
from . import avb, backup as backup_mod, check
from .flash import UNIQUE, confirm, file_sha256, overlaps, short_serial
from .sources import WORK, SourceError

TABLE = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stock_495.json')))
SS = layout.SECTOR
PROGRAMS = ('rawprogram_unsparse0-half.xml', 'rawprogram1.xml', 'rawprogram2.xml', 'rawprogram3.xml',
            'rawprogram4.xml', 'rawprogram5.xml')
GPT_LABELS = ('PrimaryGPT', 'BackupGPT')
BOOT_LUN = 1                         # Lenovo's script: fh_loader --setactivepartition=1 (xbl_a is on LUN1)


def stock_dir():
    return os.path.join(WORK, 'stock', TABLE['version'])


# ---------------------------------------------------------------- package

def fetch(path=None):
    """The checked archive: --package PATH, or downloaded into work/stock/ (resumable)."""
    a = TABLE['archive']
    dest = path or os.path.join(WORK, 'stock', a['name'])
    if not path and not (os.path.exists(dest) and os.path.getsize(dest) == a['bytes']):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        print(f'下载联想官方 {TABLE["version"]} 线刷包（{a["bytes"] / 2**30:.1f} GiB，可以断点续传）…\n  {a["url"]}', flush=True)
        if shutil.which('aria2c'):
            cmd = ['aria2c', '-c', '-x', '8', '-s', '8', '--max-tries=0', '--retry-wait=10', '--file-allocation=none',
                   '--auto-file-renaming=false', '--console-log-level=warn', '--summary-interval=60',
                   '-d', os.path.dirname(dest), '-o', os.path.basename(dest), a['url']]
        else:
            cmd = ['curl', '-fL', '-C', '-', '--retry', '20', '--retry-all-errors', '-o', dest, a['url']]
        if subprocess.run(cmd).returncode:
            raise SourceError('下载没完成：再跑一次会接着下载')
    if not os.path.isfile(dest):
        raise SourceError(f'{dest} 不存在')
    if os.path.getsize(dest) != a['bytes']:
        raise SourceError(f'{dest} 大小 {os.path.getsize(dest)}，应为 {a["bytes"]}：没下载完或不是这个包')
    print('核对线刷包 sha256 …', flush=True)
    if file_sha256(dest) != a['sha256']:
        raise SourceError(f'{dest} 的 sha256 不对：不是联想官方的 {a["name"]}')
    return dest


def password():
    pw = os.environ.get('TB_STOCK_PASSWORD')
    if pw:
        return pw
    print('这个联想官方线刷包有解压密码，本工具不带密码，需要你自己提供。')
    pw = getpass.getpass('解压密码（输入时不显示）：')
    if not pw:
        raise SourceError('没有输入密码')
    return pw


def extract(archive, pw=None):
    """image/ of the package into work/stock/<version>/image, every file checked against stock_495.json.
    The password is asked for only when the folder is not there yet (or no longer checks out)."""
    exe = shutil.which('7zz') or shutil.which('7z')
    if not exe:
        raise SourceError('需要 7-Zip（7zz 或 7z）')
    out = os.path.join(stock_dir(), 'image')
    if os.path.isdir(out) and verify_files(out, quiet=True):
        return out
    pw = pw or password()
    tmp = tempfile.mkdtemp(prefix='extract-', dir=_mk(stock_dir()))
    inner = TABLE['archive']['inner_dir']
    print('解压（约 16 GB）…', flush=True)
    # the password goes to 7-Zip on its command line only (not printed, not written anywhere)
    p = subprocess.run([exe, 'x', '-y', f'-o{tmp}', f'-p{pw}', archive, f'{inner}/*'], capture_output=True, text=True,
                       errors='replace')
    if p.returncode:
        shutil.rmtree(tmp, ignore_errors=True)
        if 'Wrong password' in p.stdout + p.stderr:
            raise SourceError('解压密码不对')
        raise SourceError(f'解压失败：{(p.stdout + p.stderr).strip()[-300:]}')
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.replace(os.path.join(tmp, inner), out)
    shutil.rmtree(tmp, ignore_errors=True)
    if not verify_files(out):
        raise SourceError('解压出来的文件和记录不一致')
    return out


def _mk(d):
    os.makedirs(d, exist_ok=True)
    return d


def verify_files(d, quiet=False):
    if not quiet:
        print(f'逐个核对 {len(TABLE["files"])} 个文件 …', flush=True)
    for n, v in TABLE['files'].items():
        p = os.path.join(d, n)
        if not os.path.isfile(p) or os.path.getsize(p) != v['bytes'] or file_sha256(p) != v['sha256']:
            if not quiet:
                print(f'  {n} 不对')
            return False
    return True


# ---------------------------------------------------------------- GPT from the package + patch*.xml

def _num(expr, nds):
    e = expr.strip().rstrip('.').replace('NUM_DISK_SECTORS', str(nds)).replace(' ', '')
    if not re.fullmatch(r'\d+([+-]\d+)*', e):
        raise SourceError(f'patch 里的表达式看不懂：{expr}')
    return sum(int(t) for t in re.findall(r'[+-]?\d+', e))


def lun_gpt(img, lun, nds):
    """(primary 6 sectors, backup 5 sectors) of LUN `lun` with `nds` sectors, patched like fh_loader would."""
    files = {f'gpt_main{lun}.bin': bytearray(open(os.path.join(img, f'gpt_main{lun}.bin'), 'rb').read()),
             f'gpt_backup{lun}.bin': bytearray(open(os.path.join(img, f'gpt_backup{lun}.bin'), 'rb').read())}
    for p in ET.parse(os.path.join(img, f'patch{lun}.xml')).getroot().iter('patch'):
        a = p.attrib
        buf = files.get(a.get('filename'))
        if buf is None:                    # "DISK" patches act on the device; the file ones give the same result
            continue
        off = _num(a['start_sector'], nds) * SS + int(a['byte_offset'])
        size = int(a['size_in_bytes'])
        v = a['value'].strip()
        m = re.fullmatch(r'CRC32\(([^,]+),([^)]+)\)', v)
        if m:
            s, n = _num(m.group(1), nds) * SS, _num(m.group(2), nds)
            val = zlib.crc32(bytes(buf[s:s + n]))
        else:
            val = _num(v, nds)
        buf[off:off + size] = val.to_bytes(size, 'little')
    main, back = bytes(files[f'gpt_main{lun}.bin']), bytes(files[f'gpt_backup{lun}.bin'])
    check_gpt(main, back, nds, lun)
    return main, back


def check_gpt(main, back, nds, lun):
    """Both headers consistent and their CRCs right for a LUN of nds sectors."""
    for name, blob, hdr_off, cur, alt in (('主', main, SS, 1, nds - 1), ('备份', back, len(back) - SS, nds - 1, 1)):
        h = bytearray(blob[hdr_off:hdr_off + 92])
        if h[:8] != b'EFI PART':
            raise SourceError(f'LUN{lun} {name} GPT 头不对')
        crc = int.from_bytes(h[16:20], 'little')
        h[16:20] = b'\0\0\0\0'
        if zlib.crc32(bytes(h)) != crc:
            raise SourceError(f'LUN{lun} {name} GPT 头 CRC 不对')
        if (int.from_bytes(h[24:32], 'little'), int.from_bytes(h[32:40], 'little')) != (cur, alt):
            raise SourceError(f'LUN{lun} {name} GPT 头的位置字段不对')
        n, esz = int.from_bytes(h[80:84], 'little'), int.from_bytes(h[84:88], 'little')
        start = 2 * SS if name == '主' else 0
        if zlib.crc32(blob[start:start + n * esz]) != int.from_bytes(h[88:92], 'little'):
            raise SourceError(f'LUN{lun} {name} GPT 分区项 CRC 不对')
    if main[2 * SS:6 * SS] != back[:4 * SS]:
        raise SourceError(f'LUN{lun} 主、备份 GPT 的分区项不一致')


def entries(main):
    """{name: (first_lba, sectors)} from a primary GPT."""
    return layout.gpt_partitions(main)


# ---------------------------------------------------------------- plan

def programs(img):
    """[(lun, start, file, label)] of the write list (GPT handled apart), and the unique-data entries skipped."""
    out, skipped = [], []
    for x in PROGRAMS:
        for e in ET.parse(os.path.join(img, x)).getroot().iter('program'):
            a = e.attrib
            f, label = a.get('filename', ''), a.get('label', '')
            if not f or label in GPT_LABELS:
                continue
            if label in UNIQUE:
                skipped.append((label, f))
                continue
            if a.get('sparse') == 'true':
                raise SourceError(f'{x}：{label} 是 sparse 条目，不支持')
            if f not in TABLE['files']:
                raise SourceError(f'{x}：{f} 不在核对表里')
            if not re.fullmatch(r'\d+', a.get('start_sector', '')):
                raise SourceError(f'{x}：{label} 的起始扇区 {a.get("start_sector")} 看不懂')
            if int(a.get('SECTOR_SIZE_IN_BYTES', SS)) != SS:
                raise SourceError(f'{x}：扇区大小不是 {SS}')
            out.append((int(a['physical_partition_number']), int(a['start_sector']), f, label))
    return out, skipped


def plan(src, img):
    """Everything to write, checked against the tablet's current partition table."""
    if sorted(src.luns) != list(range(6)):
        raise SourceError(f'这台设备的 LUN {sorted(src.luns)} 和 TB710FU 不一样')
    gpts, gpt_writes = {}, []
    for lun in range(6):
        nds = src.luns[lun]
        main, back = lun_gpt(img, lun, nds)
        gpts[lun] = entries(main)
        dev = {n: (p['start'], p['sectors']) for n, p in src.parts.items() if p['lun'] == lun}
        for u in UNIQUE:
            if (u in dev) != (u in gpts[lun]) or (u in dev and dev[u] != gpts[lun][u]):
                raise SourceError(f'LUN{lun} 的唯一数据分区 {u} 在设备上（{dev.get(u)}）和原厂分区表里'
                                  f'（{gpts[lun].get(u)}）位置不同：不能按原厂分区表写，什么都没写。请求助')
        cur_main = src_read(src, lun, 0, 6)
        cur_back = src_read(src, lun, nds - 5, 5)
        if cur_main == main and cur_back == back:
            continue
        gpt_writes.append({'lun': lun, 'main': main, 'back': back, 'nds': nds,
                           'removed': sorted(set(dev) - set(gpts[lun])), 'added': sorted(set(gpts[lun]) - set(dev)),
                           'moved': sorted(n for n in set(dev) & set(gpts[lun]) if dev[n] != gpts[lun][n])})
    writes, skipped = programs(img)
    for lun, start, f, label in writes:
        part = gpts[lun].get(label)
        if not part:
            raise SourceError(f'原厂分区表的 LUN{lun} 里没有 {label}')
        n = (TABLE['files'][f]['bytes'] + SS - 1) // SS
        if not (part[0] <= start and start + n <= part[0] + part[1]):
            raise SourceError(f'{f} 写到 LUN{lun} 扇区 {start} 会超出 {label}')
        rng = {'lun': lun, 'start': start, 'sectors': n}
        for u in UNIQUE:
            for lu, ents in gpts.items():
                if u in ents and overlaps(rng, {'lun': lu, 'start': ents[u][0], 'sectors': ents[u][1]}):
                    raise SourceError(f'{f} 的扇区和唯一数据分区 {u} 重叠')
    return {'gpt': gpt_writes, 'writes': writes, 'skipped': skipped, 'gpts': gpts}


def plan_slot_b(p):
    """The slot B counterpart of every slot A entry: same stock file, same offset inside the _b partition of the
    stock table. Needs the tablet's table to be the stock one already (p['gpt'] empty)."""
    # the slot attributes in the table change once Android has booted (boot_a marked successful, ...), so only the
    # partition layout has to be the stock one
    moved = [g for g in p['gpt'] if g['removed'] or g['added'] or g['moved']]
    if moved:
        raise SourceError('平板的分区布局还不是原厂的：先不加 --slot-b 跑一次 restore-stock，再单独写 B 槽')
    where = {n: (lun, s, c) for lun, ents in p['gpts'].items() for n, (s, c) in ents.items()}
    out = []
    for lun, start, f, label in p['writes']:
        if not label.endswith('_a'):
            continue
        b = label[:-2] + '_b'
        if b not in where:
            raise SourceError(f'原厂分区表里有 {label} 却没有 {b}')
        la, sa, ca = lun, *p['gpts'][lun][label]
        lb, sb, cb = where[b]
        if cb != ca:
            raise SourceError(f'{label} 和 {b} 大小不同（{ca} / {cb} 个扇区）')
        out.append((lb, sb + (start - sa), f, b))
    if not out:
        raise SourceError('清单里没有 A 槽条目')
    return out


def src_read(src, lun, start, count):
    with tempfile.TemporaryDirectory(dir=_mk(os.path.join(WORK, 'stock'))) as t:
        f = os.path.join(t, 'r.bin')
        src.read_sectors(lun, start, count, f)
        with open(f, 'rb') as fh:
            return fh.read()


# ---------------------------------------------------------------- writing

def safety_copy(src, serial):
    """Read-only copy of every LUN's GPT (both) and every unique-data partition, before anything is written."""
    d = os.path.join(WORK, 'stock-safety', f'{serial}-{time.strftime("%Y%m%d-%H%M%S")}')
    os.makedirs(d)
    print(f'先只读保存分区表和唯一数据分区 → {d}', flush=True)
    sums = []
    for lun, nds in sorted(src.luns.items()):
        for name, start, count in ((f'gpt_main{lun}.bin', 0, 6), (f'gpt_backup{lun}.bin', nds - 5, 5)):
            src.read_sectors(lun, start, count, os.path.join(d, name))
            sums.append(name)
    names = [u for u in UNIQUE if u in src.parts]
    src.read_to_files(names, [os.path.join(d, f'{u}.bin') for u in names])
    for u in names:
        if os.path.getsize(os.path.join(d, f'{u}.bin')) != src.parts[u]['bytes']:
            raise SourceError(f'{u} 读出的长度不对：什么都没写')
        sums.append(f'{u}.bin')
    with open(os.path.join(d, 'SHA256SUMS'), 'w') as f:
        for n in sums:
            f.write(f'{file_sha256(os.path.join(d, n))}  {n}\n')
    return d


def write_at(src, lun, start, path, work, what):
    n = os.path.getsize(path)
    if n % SS:                                  # firehose writes whole sectors: pad with zeros
        padded = os.path.join(work, 'pad.bin')
        with open(path, 'rb') as f, open(padded, 'wb') as o:
            shutil.copyfileobj(f, o, 1 << 22)
            o.truncate((n + SS - 1) // SS * SS)
        path, n = padded, os.path.getsize(padded)
    want = file_sha256(path)
    print(f'  写 {what}（LUN{lun} 扇区 {start}，{n / 2**20:.1f} MiB）…', flush=True)
    src.edl('ws', start, path, f'--lun={lun}')
    back = os.path.join(work, 'readback.bin')
    src.read_sectors(lun, start, n // SS, back)
    got = file_sha256(back, n) if os.path.getsize(back) >= n else None
    os.remove(back)
    if got != want:
        raise SourceError(f'{what} 读回不一致：已停止，平板留在 9008。把这段输出原样保存、发出来，不要开机')
    print(f'    读回一致（sha256 {want[:16]}…）', flush=True)


def full_backup(serial, src, backup_dir=None):
    """The latest complete, verified full backup of this tablet (same chip serial), holding every unique-data
    partition the tablet has. Its partition table may differ from today's (a rescue usually follows a change)."""
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
        if (st.get('device') or {}).get('chip_serial') != serial or not st.get('complete'):
            if backup_dir:
                raise SourceError(f'{d} 不是这台平板（{short_serial(serial)}）的完整备份')
            continue
        missing = [u for u in UNIQUE if u in src.parts and f'{u}.bin' not in st.get('items', {})]
        if missing:
            raise SourceError(f'{d} 里缺唯一数据分区 {missing}')
        backup_mod.load(d)                  # every recorded file present with its recorded sha256
        return d
    raise SourceError('没有这台平板的完整备份：先跑 tb710fu.py backup（9008 下做，安卓坏了也能做），备份目录另存一份，'
                      '再恢复原厂。原厂包里的 persist 等是空白模板，平板自己的这些数据只能靠备份找回')


def check_rollback(src, slot):
    """Images on the tablet must not carry higher rollback indexes than stock (stock would not boot). Each index is
    compared as soon as it is read: a higher one refuses at once; one that cannot be read is only a warning."""
    stock_rb = {int(k): v for k, v in TABLE['avb']['rollback'].items()}

    def compare(loc, idx, what):
        if idx > stock_rb.get(loc, 0):
            raise SourceError(f'平板上 {what} 的回滚索引 {idx} 比原厂 {TABLE["version"]} 的 {stock_rb.get(loc, 0)} 高'
                              f'（位置 {loc}）：刷原厂会启动不了，不刷')

    try:
        top = check.partition_vbmeta(src, f'vbmeta{slot}')
        v = avb.parse_vbmeta(top) if top else None
    except (SourceError, ValueError) as e:
        print(f'⚠ 读不到平板上的 vbmeta{slot}（系统坏了？）：{e}。按原厂 {TABLE["version"]} 继续')
        return
    if v is None:
        print(f'⚠ 平板上的 vbmeta{slot} 没有 AVB 签名（系统坏了？）。按原厂 {TABLE["version"]} 继续')
        return
    compare(v['rollback_location'], v['rollback'], f'vbmeta{slot}')
    for c in v['chains']:
        if c['partition'] not in ('boot', 'vbmeta_system'):
            continue
        name = c['partition'] + slot
        try:
            blob = check.partition_vbmeta(src, name)
            idx = avb.parse_vbmeta(blob)['rollback'] if blob else None
        except (SourceError, ValueError) as e:
            print(f'⚠ 读不到平板上 {name} 的回滚索引：{e}。按原厂 {TABLE["version"]} 继续')
            continue
        if idx is None:
            print(f'⚠ 平板上的 {name} 没有 AVB 签名。按原厂 {TABLE["version"]} 继续')
            continue
        compare(c['rollback_location'], idx, name)


def run(src, package=None, dry_run=False, assume_yes=False, slot_b=False, backup_dir=None):
    serial = (src.sahara or {}).get('chip_serial')
    if not serial:
        raise SourceError('这次 edl 没打印芯片序列号：拔线，长按电源 15–20 秒关机，重新进 9008 再试')
    archive = fetch(package)
    img = extract(archive)
    p = plan(src, img)
    bdir = full_backup(serial, src, backup_dir)          # both paths write: a full backup comes first
    print(f'这台平板的完整备份：{bdir}（已核对）')
    if slot_b:
        return run_slot_b(src, serial, img, p, dry_run, assume_yes)
    check_rollback(src, src.slot or '_a')
    print(f'\n==== 恢复联想官方 {TABLE["version"]}（平板 {short_serial(serial)}）====')
    for g in p['gpt']:
        print(f'  LUN{g["lun"]} 分区表 ← 原厂（按 {g["nds"]} 个扇区算好；去掉 {g["removed"] or "无"}，'
              f'新增 {g["added"] or "无"}，位置变化 {g["moved"] or "无"}）')
    if not p['gpt']:
        print('  分区表：和原厂一致，不写')
    for lun, start, f, label in p['writes']:
        v = TABLE['files'][f]
        print(f'  {label:18} ← {f}（{v["bytes"]} 字节，sha256 {v["sha256"]}）')
    print('不写（唯一数据分区）：' + '、'.join(sorted({l for l, _ in p['skipped']})) + '；其余唯一数据分区本来就不在清单里')
    print('后果：安卓数据清空；原厂以外的分区（例如 ArmadaOS）被删掉；底层固件和 A 槽启动链换回原厂')
    if dry_run:
        print('\n--dry-run：检查都通过，什么都没写')
        return False
    need = max(TABLE['files'][f]['bytes'] for _, _, f, _ in p['writes']) + (1 << 30)
    if shutil.disk_usage(WORK).free < need:
        raise SourceError(f'{WORK} 剩余空间不够读回核对（要 {need / 2**30:.1f} GiB）')
    confirm('\n确认把平板恢复成联想官方原厂系统？', 'stock', assume_yes)
    confirm('\n这会清空平板上的安卓数据，删掉原厂以外的分区（ArmadaOS 等）。不同意就什么都不写。', 'wipe', assume_yes)
    safety = safety_copy(src, serial)
    with tempfile.TemporaryDirectory(dir=WORK) as t:
        for g in p['gpt']:
            for blob, start, what in ((g['main'], 0, f'LUN{g["lun"]} 主分区表'),
                                      (g['back'], g['nds'] - 5, f'LUN{g["lun"]} 备份分区表')):
                f = os.path.join(t, 'gpt.bin')
                with open(f, 'wb') as o:
                    o.write(blob)
                write_at(src, g['lun'], start, f, t, what)
        if p['gpt']:
            parts = src.reread_parts()
            for lun, ents in p['gpts'].items():
                now = {n: (q['start'], q['sectors']) for n, q in parts.items() if q['lun'] == lun}
                if now != ents:
                    raise SourceError(f'写完分区表后重新读出的 LUN{lun} 和算好的不一样：停在这里，平板留在 9008。'
                                      f'安全副本在 {safety}')
            print('  重新读出的分区表和算好的一致', flush=True)
        for lun, start, f, label in p['writes']:
            write_at(src, lun, start, os.path.join(img, f), t, f'{label}（{f}）')
    src.edl('setbootablestoragedrive', BOOT_LUN)
    write_record(serial, 'a')
    print(f'\n✅ 已恢复联想官方 {TABLE["version"]}，全部读回一致。安全副本：{safety}')
    print('第一次开机会慢一些（重新建数据分区）。原厂分区表里两个槽都没有标成"当前"，和联想自己刷机一样，'
          '开机时由 abl 自己选 A 槽。开机后是原厂系统，需要重新设置')
    return True


def write_record(serial, what):
    os.makedirs(_mk(os.path.join(WORK, 'stock')), exist_ok=True)
    with open(os.path.join(WORK, 'stock', f'{serial}.json'), 'a') as f:
        f.write(json.dumps({'written': what, 'version': TABLE['version'], 'time': time.strftime('%Y-%m-%dT%H:%M:%S%z')}) + '\n')


def run_slot_b(src, serial, img, p, dry_run, assume_yes):
    """--slot-b: the stock images of slot A also into slot B (firmware and boot chain). No GPT, no super, metadata or
    userdata, so no data is lost; the slot attributes stay as they are (A stays the one that boots)."""
    writes = plan_slot_b(p)
    for lun, start, f, label in writes:
        q = src.parts.get(label)
        if not q or q['lun'] != lun or not (q['start'] <= start < q['start'] + q['sectors']):
            raise SourceError(f'{label} 在平板现在的分区表里的位置和原厂的不一样：什么都没写')
    print(f'\n==== B 槽恢复成联想官方 {TABLE["version"]}（平板 {short_serial(serial)}）====')
    for lun, start, f, label in writes:
        v = TABLE['files'][f]
        print(f'  {label:18} ← {f}（{v["bytes"]} 字节，sha256 {v["sha256"]}）')
    print('不写：分区表、A 槽、super、metadata、userdata、唯一数据分区；槽位属性不变（仍从 A 槽启动），安卓数据保留')
    if dry_run:
        print('\n--dry-run：检查都通过，什么都没写')
        return False
    confirm('\n确认把 B 槽写成联想官方原厂？', 'stock', assume_yes)
    safety = safety_copy(src, serial)
    with tempfile.TemporaryDirectory(dir=WORK) as t:
        for lun, start, f, label in writes:
            write_at(src, lun, start, os.path.join(img, f), t, f'{label}（{f}）')
    write_record(serial, 'b')
    print(f'\n✅ B 槽已恢复联想官方 {TABLE["version"]}，全部读回一致。安全副本：{safety}')
    return True
