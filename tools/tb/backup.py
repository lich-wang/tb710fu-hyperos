"""Full 9008 backup (read only): GPT of every LUN + every partition except userdata, resumable.

Layout of a backup directory (work/backup/<chip serial>-<time>/):
  backup.json      device, partition table, what has been read (bytes + sha256), complete flag
  SHA256SUMS       written once the backup is complete
  gpt_main<L>.bin / gpt_backup<L>.bin, <partition>.bin
The state is saved after every step, so an interrupted backup continues where it stopped (same device,
same partition table). super is read in 1 GiB pieces for the same reason.
"""
import hashlib, json, os, re, shutil, time

from . import layout
from .sources import WORK, SourceError

SKIP = ('userdata',)
BIG = 4 << 30                # other partitions this large are not system partitions (e.g. a Linux root): skipped
BATCH = 1 << 30              # small partitions are read by name, up to this many bytes per edl run
SUPER_CHUNK = 1 << 30
UNIQUE = ('persist', 'modemst1', 'modemst2', 'fsg', 'fsc', 'devinfo', 'frp', 'keystore', 'ssd', 'secdata',
          'uefivarstore', 'cdt', 'ddr')


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 22), b''):
            h.update(b)
    return h.hexdigest()


def _save(d, state):
    tmp = os.path.join(d, 'backup.json.tmp')
    with open(tmp, 'w') as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(d, 'backup.json'))


def _table(src):
    return {n: {k: p[k] for k in ('lun', 'start', 'sectors')} for n, p in sorted(src.parts.items())}


def plan(src):
    """(partitions to read, skipped [(name, bytes, reason)])"""
    take, skipped = [], []
    for n, p in sorted(src.parts.items(), key=lambda kv: (kv[1]['lun'], kv[1]['start'])):
        if not p['bytes']:
            continue
        if n in SKIP:
            skipped.append((n, p['bytes'], '用户数据'))
        elif p['bytes'] >= BIG and n != 'super':
            skipped.append((n, p['bytes'], '超过 4 GiB，不是安卓系统分区'))
        else:
            take.append(n)
    return take, skipped


def _read_state(d):
    with open(os.path.join(d, 'backup.json')) as f:
        return json.load(f)


def same_device(st, src):
    """True only when both the backup and the connected device report the same chip serial (or HWID + PK hash
    when there is no serial). Missing identity on either side -> False: never merge two devices' backups."""
    a, b = st.get('device') or {}, src.sahara or {}
    if a.get('chip_serial') and b.get('chip_serial'):
        return a['chip_serial'] == b['chip_serial']
    return False


def find_resumable(src):
    """Latest incomplete backup of this device (same chip serial) with the same partition table, or None."""
    root = os.path.join(WORK, 'backup')
    table = _table(src)
    for d in sorted((os.path.join(root, x) for x in os.listdir(root)) if os.path.isdir(root) else [], reverse=True):
        try:
            st = _read_state(d)
        except (OSError, ValueError):
            continue
        if not st.get('complete') and st.get('table') == table and same_device(st, src):
            return d
    return None


def verify_items(d, items, hashes=True):
    """Names of recorded files that are missing, have the wrong size or (hashes=True) the wrong sha256."""
    bad = []
    for name, v in sorted(items.items()):
        p = os.path.join(d, name)
        if not os.path.isfile(p) or os.path.getsize(p) != v['bytes'] or (hashes and file_sha256(p) != v['sha256']):
            bad.append(name)
    return bad


def _private_dir(path):
    """Create path readable by the owner only (backups hold IMEI, calibration and keys). An existing directory is
    left as it is (it may be the user's own), with a warning when others can read it."""
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        os.mkdir(path, 0o700)
        os.chmod(path, 0o700)          # mkdir's mode is filtered by umask
    elif os.stat(path).st_mode & 0o077:
        print(f'  ⚠ {path} 其他用户可以访问；里面会有唯一数据分区，建议 chmod 700')


def gib(n):
    return f'{n / 2**30:.1f} GiB'


def run(src, dest=None, ident=None, resume=True):
    """Back up src (an EdlSource) into dest (new or resumed). Returns the backup directory.

    resume=False never picks up an unfinished backup on its own (an explicit dest is still continued)."""
    take, skipped = plan(src)
    missing_luns = sorted({p['lun'] for p in src.parts.values()} - {int(k) for k in src.luns})
    if missing_luns:
        raise SourceError(f'printgpt 没给出 LUN {missing_luns} 的大小，读不了这些 LUN 的 GPT，不备份')
    dest = dest or (find_resumable(src) if resume else None)
    resumed = bool(dest and os.path.exists(os.path.join(dest, 'backup.json')))
    if not dest:
        safe = re.sub(r'[^0-9A-Za-z_.-]', '_', str(ident or 'device')).strip('.')[:64] or 'device'
        dest = os.path.join(WORK, 'backup', f'{safe}-{time.strftime("%Y%m%d-%H%M%S")}')
    _private_dir(dest)
    if resumed:
        st = _read_state(dest)
        if st.get('table') != _table(src):
            raise SourceError(f'{dest} 是另一个分区表的备份，不能接着读。换一个目录。')
        mine, theirs = (src.sahara or {}).get('chip_serial'), (st.get('device') or {}).get('chip_serial')
        if mine and theirs and mine != theirs:
            raise SourceError(f'{dest} 是另一台平板（芯片序列号 {theirs}）的备份，不能接着读。换一个目录。')
        if not same_device(st, src):    # only reachable with an explicit --out
            print('  ⚠ 这次没读到芯片序列号，核对不了是不是同一台平板；你指定了这个目录，按同一台处理')
        print('  核对已经读好的文件 …', flush=True)
        bad = verify_items(dest, st['items'])
        for name in bad:
            print(f'  {name} 丢失或和记录不符，重新读')
            del st['items'][name]
            if os.path.exists(os.path.join(dest, name)):
                os.remove(os.path.join(dest, name))
    else:
        st = {'schema': 1, 'created': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'device': dict(src.sahara),
              'slot': src.slot, 'luns': src.luns, 'table': _table(src), 'items': {}, 'complete': False,
              'skipped': [{'name': n, 'bytes': b, 'reason': r} for n, b, r in skipped]}
    items = st['items']
    total = sum(src.parts[n]['bytes'] for n in take)
    left = sum(src.parts[n]['bytes'] for n in take if f'{n}.bin' not in items)
    if 'super' not in items and st.get('super_done'):
        left -= st['super_done']
    free = shutil.disk_usage(dest).free
    print(f'{"接着" if resumed else "开始"}备份到 {dest}')
    print(f'  {len(take)} 个分区，共 {gib(total)}，还要读 {gib(left)}；磁盘剩余 {gib(free)}')
    for n, b, r in skipped:
        print(f'  跳过 {n}（{gib(b)}，{r}）')
    if free < left + (1 << 30):
        raise SourceError('磁盘空间不够')
    _save(dest, st)
    t0 = time.time()

    # GPT of every LUN: primary (MBR + header + the whole entry array) and the backup copy at the end
    for lun, total_sectors in sorted(src.luns.items(), key=lambda kv: int(kv[0])):
        lun = int(lun)
        main, back = f'gpt_main{lun}.bin', f'gpt_backup{lun}.bin'
        if main not in items:
            p = os.path.join(dest, main)
            src.read_sectors(lun, 0, 2, p)
            with open(p, 'rb') as f:
                h = layout.gpt_header(f.read())
            n = h['entries_lba'] + h['entries_sectors']
            if h['entries_lba'] < 2 or n > 64:
                raise SourceError(f'LUN {lun} 的 GPT 头不合理（分区项从 LBA {h["entries_lba"]} 起，共 {n} 扇区）')
            src.read_sectors(lun, 0, n, p)
            if os.path.getsize(p) != n * layout.SECTOR:
                raise SourceError(f'LUN {lun} 的 GPT 读到的长度不对')
            with open(p, 'rb') as f:
                layout.gpt_partitions(f.read())    # the entry array is complete
            items[main] = {'bytes': os.path.getsize(p), 'sha256': file_sha256(p)}
            _save(dest, st)
        if back not in items:
            h = layout.gpt_header(open(os.path.join(dest, main), 'rb').read())
            n = h['entries_sectors'] + 1
            p = os.path.join(dest, back)
            src.read_sectors(lun, h['alternate_lba'] - n + 1, n, p)
            items[back] = {'bytes': os.path.getsize(p), 'sha256': file_sha256(p)}
            _save(dest, st)

    # small partitions, by name, in batches
    batch, size = [], 0
    todo = [n for n in take if n != 'super' and f'{n}.bin' not in items]
    for i, n in enumerate(todo):
        batch.append(n)
        size += src.parts[n]['bytes']
        if size >= BATCH or i == len(todo) - 1:
            files = [os.path.join(dest, f'{x}.bin') for x in batch]
            src.read_to_files(batch, files)
            for x, p in zip(batch, files):
                if os.path.getsize(p) != src.parts[x]['bytes']:
                    raise SourceError(f'{x}: 读到 {os.path.getsize(p)} 字节，分区是 {src.parts[x]["bytes"]}')
                items[f'{x}.bin'] = {'bytes': src.parts[x]['bytes'], 'sha256': file_sha256(p)}
            _save(dest, st)
            done = sum(v['bytes'] for v in items.values())
            print(f'  {done / 2**30:5.1f} GiB  {", ".join(batch[:6])}{" …" if len(batch) > 6 else ""}', flush=True)
            batch, size = [], 0

    # super in pieces, appended to super.bin
    if 'super' in take and 'super.bin' not in items:
        p = src.parts['super']
        path = os.path.join(dest, 'super.bin')
        done = st.get('super_done', 0)
        if not os.path.exists(path) or os.path.getsize(path) < done:
            done = 0
        with open(path, 'r+b' if os.path.exists(path) else 'wb') as out:
            out.truncate(done)
        chunk_sectors = SUPER_CHUNK // layout.SECTOR
        start_done, start_t = done, time.time()
        while done < p['bytes']:
            first = done // layout.SECTOR
            count = min(chunk_sectors, p['sectors'] - first)
            piece = os.path.join(dest, 'super.piece')
            src.read_sectors(p['lun'], p['start'] + first, count, piece)
            if os.path.getsize(piece) != count * layout.SECTOR:
                raise SourceError('super: 这一段读到的长度不对')
            with open(path, 'r+b') as out, open(piece, 'rb') as f:
                out.seek(done)
                shutil.copyfileobj(f, out, 1 << 22)
            os.remove(piece)
            done += count * layout.SECTOR
            st['super_done'] = done
            _save(dest, st)
            rate = (done - start_done) / max(time.time() - start_t, 1)
            print(f'  super {gib(done)} / {gib(p["bytes"])}（{rate / 2**20:.0f} MiB/s）', flush=True)
        print('  校验 super …', flush=True)
        items['super.bin'] = {'bytes': p['bytes'], 'sha256': file_sha256(path)}
        st.pop('super_done', None)
        _save(dest, st)

    missing = [n for n in take if f'{n}.bin' not in items]
    if missing:
        raise SourceError(f'备份不完整：{missing}')
    with open(os.path.join(dest, 'SHA256SUMS'), 'w') as f:
        for k in sorted(items):
            f.write(f'{items[k]["sha256"]}  {k}\n')
    st['complete'] = True
    st['finished'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    _save(dest, st)
    print(f'备份完成：{dest}（用时 {(time.time() - t0) / 60:.0f} 分钟）')
    print('  里面有 persist、modemst1/2、fsg 等唯一数据（IMEI、校准、密钥），丢了找不回来：另外复制一份到别的地方保存。')
    return dest


def load(d):
    """backup.json of a finished backup: complete, and every recorded file present with its recorded sha256."""
    try:
        st = _read_state(d)
    except (OSError, ValueError):
        raise SourceError(f'{d} 不是本工具做的备份（没有 backup.json）')
    if not st.get('complete'):
        raise SourceError(f'{d} 还没备份完，先接着跑 backup')
    print(f'核对备份 {d} …', flush=True)
    bad = verify_items(d, st.get('items', {}))
    if bad:
        raise SourceError(f'{d} 里这些文件丢失或和记录不符：{", ".join(bad)}。重新备份。')
    return st


def latest():
    root = os.path.join(WORK, 'backup')
    for d in sorted((os.path.join(root, x) for x in os.listdir(root)) if os.path.isdir(root) else [], reverse=True):
        try:
            if _read_state(d).get('complete'):
                return d
        except (OSError, ValueError):
            continue
    return None
