"""Read-only access to a tablet: 9008 (bkerler edl + firehose loader) or adb with root.

Both expose the same small interface used by the check:
  parts            {name: {'bytes', ...}}  (partitions known on the device)
  slot             '_a' / '_b' / None
  read(names)      {name: bytes} whole partitions
  read_range(name, offset, length) -> bytes
Nothing here writes to the device.
"""
import glob, hashlib, os, platform, re, shutil, subprocess, tempfile

from . import layout

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(TOOLS)
WORK = os.path.abspath(os.environ.get('TB_WORK', os.path.join(REPO, 'work')))

KNOWN_LOADERS = {   # sha256 -> description
    'ad4689b1c52458250d46829ee46a89ec974e69f8e82eee8d4950427919e0b9eb': 'prog_firehose_ddr-TB710FU.melf（9008 包里的 xbl_s_devprg_ns.melf）',
}


class SourceError(Exception):
    pass


def sha256(data):
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- 9008

def edl_bin():
    e = os.environ.get('EDL')
    if e:
        # a path (not a bare command name) is made absolute: edl runs with the log folder as working directory
        return os.path.abspath(e) if os.sep in e else e
    for c in (os.path.join(TOOLS, 'venv/bin/edl'), os.path.join(os.path.dirname(REPO), 'tools/venv/bin/edl')):
        if os.access(c, os.X_OK):
            return c
    return shutil.which('edl') or 'edl'


def find_loader(given=None):
    """Firehose programmer: --loader, $TB_LOADER, work/firehose/*, or ../firehose/* next to the repository."""
    cands = [given, os.environ.get('TB_LOADER')]
    for d in (os.path.join(WORK, 'firehose'), os.path.join(os.path.dirname(REPO), 'firehose')):
        cands += sorted(glob.glob(os.path.join(d, '*.melf')) + glob.glob(os.path.join(d, '*.elf')))
    for c in cands:
        if c and os.path.isfile(c):
            return os.path.abspath(c)
    raise SourceError('找不到 9008 firehose 加载器。用 --loader 指定，或放到 work/firehose/：'
                      '联想官方线刷包里的 xbl_s_devprg_ns.melf / prog_firehose_ddr*.melf。')


def usb_mode():
    """'9008', '900e', None, or 'unknown' when it cannot be detected (edl is the real check)."""
    try:
        if platform.system() == 'Darwin':
            # ioreg works on every macOS version (system_profiler's USB data type was renamed in newer releases)
            t = subprocess.run(['ioreg', '-p', 'IOUSB', '-l', '-w0'], capture_output=True, text=True, timeout=30).stdout
            for dev in re.split(r'\+-o ', t):
                v = re.search(r'"idVendor" = (\d+)', dev)
                p = re.search(r'"idProduct" = (\d+)', dev)
                if v and p and int(v.group(1)) == 0x05c6 and int(p.group(1)) in (0x9008, 0x900e):
                    return '%04x' % int(p.group(1))
            return None
        if platform.system() == 'Linux':
            for d in glob.glob('/sys/bus/usb/devices/*'):
                try:
                    v, p = (open(f'{d}/{n}').read().strip().lower() for n in ('idVendor', 'idProduct'))
                except OSError:
                    continue
                if v == '05c6' and p in ('9008', '900e'):
                    return p
            return None
    except Exception:
        pass
    return 'unknown'


class EdlSource:
    kind = 'edl'

    def __init__(self, loader, logfile):
        self.loader, self.logfile = loader, logfile
        mode = usb_mode()
        if mode == '900e':
            raise SourceError('平板在 900E（内核崩溃转储模式），不是 9008。拔线，长按电源 15–20 秒关机，'
                              '再按住音量+和音量−插线进 9008。')
        if mode is None:
            raise SourceError('没检测到 9008 设备。进入 9008：已 root 的系统里 `adb reboot edl`；'
                              '或拔线 → 长按电源 15–20 秒关机 → 按住音量+和音量−插线。')
        out = self.edl('printgpt', ok=r'Parsing Lun 0')
        self.sahara = layout.sahara_info(out)
        self.parts = layout.parse_printgpt(out)
        self.names_count = layout.printgpt_name_counts(out)
        self.luns = layout.parse_lun_sectors(out)
        if 'super' not in self.parts:
            raise SourceError('GPT 里没有 super 分区，不像是 TB710FU')
        self.slot = layout.active_slot(self.parts)
        self.props = {}

    def edl(self, *args, ok=None):
        cmd = [edl_bin(), *map(str, args), f'--loader={self.loader}']
        if args[0] != 'reset':          # edl's reset command does not accept --memory
            cmd.append('--memory=ufs')
        try:
            # edl writes edl_config.json into its working directory: keep it out of the repository
            p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace',
                               timeout=1800, cwd=os.path.dirname(os.path.abspath(self.logfile)))
        except FileNotFoundError:
            raise SourceError('找不到 edl。安装 bkerler/edl（pip install edlclient），或用 $EDL 指定。')
        out = p.stdout
        with open(self.logfile, 'a') as f:
            f.write(f'\n$ {" ".join(cmd)}\n{out}')
        bad = [l for l in out.splitlines() if re.search(r"Error:|doesn't exist|\bfailed\b|\bNAK\b", l)
               and not re.search(r"USBError|Couldn't detect", l)]
        if p.returncode or bad or (ok and not re.search(ok, out)):
            tail = '\n    '.join((bad or out.splitlines())[-10:])
            raise SourceError(f'edl {args[0]} 失败（退出码 {p.returncode}），完整输出在 {self.logfile}：\n    {tail}')
        return out

    def size(self, name):
        return self.parts[name]['bytes']

    def read(self, names):
        names = [n for n in names if n in self.parts]
        if not names:
            return {}
        with tempfile.TemporaryDirectory() as t:
            files = [os.path.join(t, f'{n}.bin') for n in names]
            self.edl('r', ','.join(names), ','.join(files))
            out = {}
            for n, f in zip(names, files):
                with open(f, 'rb') as fh:
                    out[n] = fh.read()
            return out

    def read_to_files(self, names, files):
        """Whole partitions by name straight into files (one edl run; names must be unique across LUNs)."""
        self.edl('r', ','.join(names), ','.join(files))

    def read_sectors(self, lun, start, count, path):
        self.edl('rs', start, count, path, f'--lun={lun}')

    def read_range(self, name, offset, length):
        p = self.parts[name]
        first = offset // layout.SECTOR
        count = (offset + length + layout.SECTOR - 1) // layout.SECTOR - first
        if first + count > p['sectors']:
            raise SourceError(f'{name}: 读取超出分区')
        with tempfile.TemporaryDirectory() as t:
            f = os.path.join(t, 'rs.bin')
            self.edl('rs', p['start'] + first, count, f, f'--lun={p["lun"]}')
            data = open(f, 'rb').read()
        skip = offset - first * layout.SECTOR
        return data[skip:skip + length]

    def reread_parts(self):
        """Partition table read again from the device (to compare after writing)."""
        return layout.parse_printgpt(self.edl('printgpt', ok=r'Parsing Lun 0'))

    def reset(self):
        self.edl('reset', '--resetmode=reset')


# ---------------------------------------------------------------- adb + root

class AdbSource:
    kind = 'adb'

    def __init__(self, serial=None, need_root=True):
        self.adb = [os.environ.get('ADB', 'adb')] + (['-s', serial] if serial else [])
        try:
            st = self.run(['get-state'], check=False, timeout=30).decode(errors='replace').strip()
        except (OSError, subprocess.SubprocessError):
            st = ''
        if st != 'device':
            raise SourceError('没有可用的 adb 设备（打开 USB 调试并授权；多台设备时用 --serial 或 ANDROID_SERIAL）。')
        out = self.run(['shell', 'getprop']).decode(errors='replace')
        self.props = dict(re.findall(r'^\[([^\]]+)\]: \[([^\]]*)\]', out, re.M))
        self.slot = self.props.get('ro.boot.slot_suffix') or None
        self.sahara = {}
        self.root = b'uid=0' in self.run(['shell', 'su', '-c', 'id'], check=False, timeout=60)
        if need_root and not self.root:
            raise SourceError('adb 没有 root（或 Magisk 没允许 Shell）。改用 9008 检查：不需要 root。')
        self.parts = {}
        if self.root:
            ls = self.su('ls /dev/block/by-name').decode(errors='replace').split()
            for n in ls:
                self.parts[n] = {'bytes': None}

    def run(self, args, check=True, timeout=600):
        p = subprocess.run(self.adb + args, capture_output=True, timeout=timeout)
        if check and p.returncode:
            raise SourceError(f'adb {" ".join(args[:3])} 失败：{p.stderr.decode(errors="replace").strip()}')
        return p.stdout

    def su(self, cmd):
        return self.run(['exec-out', 'su', '-c', cmd])

    def size(self, name):
        if self.parts[name]['bytes'] is None:
            self.parts[name]['bytes'] = int(self.su(f'blockdev --getsize64 /dev/block/by-name/{name}').strip())
        return self.parts[name]['bytes']

    def read(self, names):
        return {n: self.su(f'cat /dev/block/by-name/{n}') for n in names if n in self.parts}

    def read_range(self, name, offset, length):
        first = offset // layout.SECTOR
        count = (offset + length + layout.SECTOR - 1) // layout.SECTOR - first
        data = self.su(f'dd if=/dev/block/by-name/{name} bs={layout.SECTOR} skip={first} count={count} 2>/dev/null')
        skip = offset - first * layout.SECTOR
        return data[skip:skip + length]

    def reset(self):
        pass
