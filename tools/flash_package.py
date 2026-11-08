#!/usr/bin/env python3
"""Flash a package made by build_package.py to a TB710FU in 9008 (EDL) mode.

  flash_package.py backup              back up GPT + every partition except userdata/super (do this first!)
  flash_package.py full                stage 1: GPT, firmware of both slots, v3 boot chain on slot A,
                                       HyperOS 4 super WITHOUT Find Device; clears metadata + userdata
                                       (= factory reset). Afterwards: setup wizard, NO Xiaomi account there.
  flash_package.py super-find          stage 2, after the wizard: super WITH Find Device, data kept
  flash_package.py verify              read back what `full` wrote and compare (no writes)

Options: --pkg DIR (default: newest work/package/*), --backup DIR (default: newest work/backup/*),
         --yes (skip the typed confirmation; only after the user agreed to the printed plan),
         --dry-run (all offline checks + the plan, nothing is sent to the device).
Enter 9008: rooted Android `adb reboot edl`, or unplug, hold power 15-20 s, hold vol+ and vol- and plug in.
EDL: bkerler/edl; $EDL, else tools/venv/bin/edl, else `edl` in PATH.

Never written: persist modemst1/2 fsg fsc devinfo frp keystore ssd secdata uefivarstore cdt ddr
(+ proinfo, lenovolock). The package's rawprogram is checked for that before anything is sent.
"""
import argparse, glob, json, os, platform, re, subprocess, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from package.common import *  # noqa: E402,F401

A = None


def edl_bin():
    e = os.environ.get('EDL')
    if e:
        return e
    for c in (os.path.join(TOOLS, 'venv/bin/edl'), os.path.join(os.path.dirname(REPO), 'tools/venv/bin/edl')):
        if os.access(c, os.X_OK):
            return c
    return 'edl'


def loader():
    if A.loader:
        return A.loader
    if A.pkg_dir:
        return os.path.join(A.pkg_dir, 'firehose', FIREHOSE)
    die('--loader (prog_firehose_ddr-TB710FU.melf) or --pkg is needed')


def edl(*args, ok=None, quiet=False):
    """Run edl; ok = regex that must appear in the output. Returns the output; dies on failure."""
    cmd = [edl_bin(), *map(str, args), f'--loader={loader()}', '--memory=ufs']
    log('  $ ' + ' '.join(cmd))
    if A.dry_run:
        return ''
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace')
    out = p.stdout
    with open(A.log, 'a') as f:
        f.write(f'\n$ {" ".join(cmd)}\n{out}')
    # edl reports a failed program/read as "Error:..." / "... failed" and keeps going (qfil does not stop),
    # so the output is the real check. Seen in successful runs and harmless: "USBError(5, 'Input/Output Error')"
    # while connecting, "Couldn't detect TargetName / MaxPayloadSizeFromTargetinBytes".
    bad = [l for l in out.splitlines() if re.search(r"Error:|doesn't exist|\bfailed\b|\bNAK\b", l)
           and not re.search(r"USBError|Couldn't detect", l)]
    if p.returncode or bad or (ok and not re.search(ok, out)):
        tail = '\n    '.join((bad or out.splitlines())[-15:])
        die(f'edl failed (exit {p.returncode}); full output in {A.log}:\n    {tail}')
    if not quiet:
        for l in out.splitlines():
            if re.search(r'Wrote|programming|bootable|patching ok|Dumped|Reset', l):
                log('    ' + l.strip()[:160])
    return out


def usb_mode():
    """'9008', '900e' or None (Qualcomm EDL / crash-dump mode)."""
    try:
        if platform.system() == 'Darwin':
            t = subprocess.run(['system_profiler', 'SPUSBDataType'], capture_output=True, text=True, timeout=30).stdout
            for pid in ('9008', '900e'):
                if re.search(rf'Product ID: 0x{pid}\b[\s\S]{{0,200}}?Vendor ID: 0x05c6', t, re.I):
                    return pid
            return None
        for d in glob.glob('/sys/bus/usb/devices/*'):
            try:
                v, p = (open(f'{d}/{n}').read().strip().lower() for n in ('idVendor', 'idProduct'))
            except OSError:
                continue
            if v == '05c6' and p in ('9008', '900e'):
                return p
    except Exception:  # detection is best effort; edl itself is the real check
        return 'unknown'
    return None


def need_9008():
    if A.dry_run or os.environ.get('TB_NO_USB_CHECK'):   # TB_NO_USB_CHECK: tests with a fake edl
        return
    m = usb_mode()
    if m == '900e':
        die('the tablet is in 900E (kernel crash dump), not 9008. Do not flash over a crash; dump it first '
            '(see docs/boot-chain.md), then enter 9008 with the keys.')
    if m is None:
        die('no 9008 device. Enter 9008: rooted Android `adb reboot edl`, or unplug, hold power 15-20 s, '
            'hold vol+ and vol- and plug in.')


def confirm(text):
    log(text)
    if A.dry_run:
        log('\n(dry run: the edl commands below are only printed, nothing is sent)')
        return
    if A.yes:
        log('(--yes given)')
        return
    if input('\nType "yes" to write to the tablet: ').strip() != 'yes':
        die('cancelled, nothing was written')


def find_pkg():
    if A.pkg:
        d = os.path.abspath(A.pkg)
    else:
        c = sorted(glob.glob(f'{WORK}/package/*/manifest.json'), key=os.path.getmtime)
        if not c:
            return None
        d = os.path.dirname(c[-1])
    if not os.path.isfile(f'{d}/manifest.json'):
        die(f'{d}: no manifest.json (not a package made by build_package.py)')
    return d


def verify_manifest(pkg, only=None):
    m = json.load(open(f'{pkg}/manifest.json'))
    cache_p = f'{pkg}/.sha_cache.json'
    try:
        cache = {k: v for k, v in json.load(open(cache_p)).items()}
    except (OSError, ValueError):
        cache = {}
    log(f'  checking sha256 of {len(only or m["files"])} package files (the super images take a while the first time)')
    bad = []
    for rel, s in m['files'].items():
        if only and rel not in only:
            continue
        p = f'{pkg}/{rel}'
        if not os.path.isfile(p) or sha256(p, cache) != s:
            bad.append(rel)
    json.dump(cache, open(cache_p, 'w'))
    if bad:
        die(f'package files changed or missing: {bad}')
    return m


def find_backup():
    if A.backup:
        d = os.path.abspath(A.backup)
    else:
        c = sorted(d for d in glob.glob(f'{WORK}/backup/*') + glob.glob(f'{WORK}/backup') if os.path.isdir(d))
        d = c[-1] if c else None
    if not d or not os.path.isdir(d):
        die('no backup found. Run `flash_package.py backup` first (or pass --backup DIR with your own backup).')
    have = {}
    for r, _, fs in os.walk(d):
        for n in fs:
            have.setdefault(os.path.splitext(n)[0], os.path.join(r, n))
    miss = [u for u in UNIQUE if u not in have or os.path.getsize(have[u]) == 0]
    if miss:
        die(f'backup {d} lacks (or has empty) unique partitions: {miss}. Back up first; they cannot be recreated.')
    if not any(re.match(r'gpt_(main|primary)', k) for k in have):
        die(f'backup {d} has no GPT')
    return d


# ---------------------------------------------------------------- device checks
def device_lun0():
    out = edl('printgpt', '--lun=0', ok=r'super:', quiet=True)
    return parse_printgpt(out)


def compare_lun0(pkg):
    want = layout_key(parse_gpt(open(f'{pkg}/images/gpt_main0.bin', 'rb').read()))
    if A.dry_run:
        return []
    have = layout_key(device_lun0())
    diff = []
    for n in sorted(set(want) | set(have)):
        if want.get(n) != have.get(n):
            diff.append(f'    {n:16s} device {have.get(n)}  ->  package {want.get(n)}')
    return diff


# ---------------------------------------------------------------- readback
def readback(pkg, entries, label):
    """Read each (lun, start, file) back and compare with the file. Returns the list of mismatches."""
    bad = []
    with tempfile.TemporaryDirectory() as t:
        for i, (lun, start, rel) in enumerate(entries, 1):
            p = f'{pkg}/{rel}'
            size = os.path.getsize(p)
            n = (size + SECTOR - 1) // SECTOR
            o = f'{t}/rb.bin'
            log(f'  [{i}/{len(entries)}] {label(lun, start)} <- {rel}')
            edl('rs', start, n, o, f'--lun={lun}', quiet=True)
            if A.dry_run:
                continue
            with open(o, 'rb') as f, open(p, 'rb') as g:
                ok = True
                while True:
                    b = g.read(16 << 20)
                    if not b:
                        break
                    if f.read(len(b)) != b:
                        ok = False
                        break
            if not ok:
                bad.append(rel)
                log('      MISMATCH')
    return bad


def written_entries(pkg):
    """(lun, start_sector, relpath, label) for every file `full` programs, except GPT and super."""
    out = []
    for x, lun, lab, f, start, num in rawprogram_entries(f'{pkg}/images'):
        if not f or lab in ('PrimaryGPT', 'BackupGPT', 'super') or not start.isdigit():
            continue
        out.append((lun, int(start), f'images/{f}', lab))
    return out


def verify_full(pkg, m):
    names = {}
    ents = []
    for lun, start, rel, lab in written_entries(pkg):
        names[(lun, start)] = lab
        ents.append((lun, start, rel))
    if A.verify == 'a':
        ents = [e for e in ents if not names[(e[0], e[1])].endswith('_b')]
    bad = readback(pkg, ents, lambda lun, s: f'{names[(lun, s)]:16s} (LUN{lun} @{s})')
    bad += verify_super(pkg, m['stage1_super'])
    return bad


def verify_super(pkg, rel):
    """Compare the liblp metadata head (1 MiB) of super; with --verify-super-full every used byte."""
    p = f'{pkg}/{rel}'
    with tempfile.TemporaryDirectory() as t:
        if A.verify_super_full:
            n = (os.path.getsize(p) + SECTOR - 1) // SECTOR
        else:
            n = 256
        o = f'{t}/super.bin'
        log(f'  super (LUN0 @{SUPER_START}, {n * SECTOR >> 20} MiB) <- {rel}')
        edl('rs', SUPER_START, n, o, '--lun=0', quiet=True)
        if A.dry_run:
            return []
        with open(o, 'rb') as f, open(p, 'rb') as g:
            left = min(n * SECTOR, os.path.getsize(p))
            while left:
                k = min(left, 16 << 20)
                if f.read(k) != g.read(k):
                    log('      MISMATCH')
                    return [rel]
                left -= k
    return []


# ---------------------------------------------------------------- commands
def cmd_backup():
    d = os.path.abspath(A.out or f'{WORK}/backup/{time.strftime("%Y%m%d-%H%M%S")}')
    log(f'backup to {d}  (read only; about 5 GB, 5-15 min)')
    need_9008()
    os.makedirs(d, exist_ok=True)
    A.log = f'{d}/edl.log'
    edl('rl', d, '--skip=userdata,super', '--genxml', ok=r'Dumped|Dumping')
    if A.dry_run:
        return
    lines = []
    for r, _, fs in os.walk(d):
        for n in sorted(fs):
            if n.endswith('.bin'):
                p = os.path.join(r, n)
                lines.append(f'{sha256(p)}  {os.path.relpath(p, d)}')
    open(f'{d}/SHA256SUMS', 'w').write('\n'.join(lines) + '\n')
    A.backup = d
    find_backup()
    log(f'\nbackup OK: {d}\nCopy it somewhere else as well. persist/modemst*/fsg/fsc/... cannot be recreated.')


def plan_full(pkg, m, diff):
    rows = {}
    for lun, start, rel, lab in written_entries(pkg):
        rows.setdefault(rel, []).append(lab)
    lines = ['', f'package: {pkg} ({m["name"]}, built {m["created"]}{", DEBUG build" if m.get("debug") else ""})',
             '', 'Will write (everything on the tablet is erased, the result is a factory-new HyperOS 4):',
             '  GPT of LUN0-5 (stock TB710FU layout from the port package)']
    for rel, labs in sorted(rows.items(), key=lambda x: x[1][0]):
        lines.append(f'  {", ".join(labs):34s} <- {rel}  {m["files"][rel][:16]}…')
    lines += [f'  super{"":29s} <- {m["stage1_super"]}  {m["files"][m["stage1_super"]][:16]}…  (no Find Device)',
              '  metadata, userdata (first 64 MiB) <- zeros (factory reset)',
              '', 'Never written: ' + ' '.join(KEEP)]
    if diff:
        lines += ['', '!! The partition table on the tablet differs from the stock one in this package:'] + diff + \
                 ['!! Flashing restores the stock layout. Everything in partitions that move or disappear',
                  '!! (e.g. a second OS installed by another installer) is lost.']
    lines += ['', 'Afterwards: the tablet restarts; it may stop once in fastboot (misc requests it) -> `fastboot reboot`.',
              'Setup wizard: do NOT log in to a Xiaomi account. Then 9008 again and run `flash_package.py super-find`.']
    return '\n'.join(lines)


def cmd_full():
    pkg = A.pkg_dir or die('no package; build one with tools/build_package.py or pass --pkg')
    A.log = f'{pkg}/flash-{time.strftime("%Y%m%d-%H%M%S")}.log'
    m = verify_manifest(pkg)
    check_rawprogram_safe(f'{pkg}/images')
    bk = find_backup()
    log(f'  backup: {bk}')
    need_9008()
    diff = compare_lun0(pkg)
    confirm(plan_full(pkg, m, diff))
    if diff and not A.yes and input('The partition table will change. Type "restore stock" to go on: ').strip() != 'restore stock':
        die('cancelled, nothing was written')

    log(f'\n== {time.strftime("%H:%M:%S")} programming (10-20 min, do not touch the cable)')
    rp = ','.join(f'rawprogram{i}.xml' for i in range(6))
    pt = ','.join(f'patch{i}.xml' for i in range(6))
    edl('qfil', rp, pt, f'{pkg}/images', ok=r'raw programming ok')
    log(f'== {time.strftime("%H:%M:%S")} factory reset: clear metadata and the head of userdata')
    edl('w', 'metadata', f'{pkg}/zero_64M.bin', ok=r'Wrote')
    edl('w', 'userdata', f'{pkg}/zero_64M.bin', ok=r'Wrote')
    log(f'== {time.strftime("%H:%M:%S")} check partition table')
    if not A.dry_run:
        want = layout_key(parse_gpt(open(f'{pkg}/images/gpt_main0.bin', 'rb').read()))
        if layout_key(device_lun0()) != want:
            die('LUN0 GPT on the tablet does not match the package after programming. Not resetting; '
                f'see {A.log}')
    if A.verify != 'none':
        log(f'== {time.strftime("%H:%M:%S")} read back and compare')
        bad = verify_full(pkg, m)
        if bad:
            die(f'read-back mismatch: {bad}. The tablet was NOT reset; do not reboot it, report {A.log}.')
        log('  all read-back checks OK')
    edl('reset', '--resetmode=reset', quiet=True)
    log('\nDONE (stage 1). Next:\n'
        '  1. If the tablet shows fastboot: `fastboot reboot`.\n'
        '  2. Setup wizard: do NOT log in to a Xiaomi account (activation lock "cloudsp_fid 参数不能为空").\n'
        '  3. Enter 9008 again and run: python3 tools/flash_package.py super-find\n'
        '  Never turn on "Find device".')


def cmd_super_find():
    pkg = A.pkg_dir or die('no package; pass --pkg')
    A.log = f'{pkg}/flash-{time.strftime("%Y%m%d-%H%M%S")}.log'
    m = json.load(open(f'{pkg}/manifest.json'))
    rel = m['stage2_super']
    verify_manifest(pkg, only={rel})
    need_9008()
    if not A.dry_run:
        su = device_lun0().get('super')
        if su != (SUPER_START, SUPER_SIZE // SECTOR):
            die(f'super on the tablet is at {su}, expected ({SUPER_START}, {SUPER_SIZE // SECTOR}). '
                'Was the stage 1 package flashed?')
    confirm(f'\nWill write:\n  super <- {rel}  {m["files"][rel][:16]}…  (with Find Device)\n'
            'Data is kept. Nothing else is written.')
    log(f'\n== {time.strftime("%H:%M:%S")} write super (about 10 GB, 5-10 min)')
    edl('w', 'super', f'{pkg}/{rel}', ok=r'Wrote')
    bad = verify_super(pkg, rel)
    if bad:
        die(f'super read-back mismatch. Not resetting; report {A.log}.')
    edl('reset', '--resetmode=reset', quiet=True)
    log('\nDONE (stage 2). Log in to the Xiaomi account in Settings. Never turn on "Find device".')


def cmd_verify():
    pkg = A.pkg_dir or die('no package; pass --pkg')
    A.log = f'{pkg}/verify-{time.strftime("%Y%m%d-%H%M%S")}.log'
    m = verify_manifest(pkg)
    need_9008()
    bad = verify_full(pkg, m)
    log('\nall OK' if not bad else f'\nMISMATCH: {bad}')
    if not A.dry_run:
        edl('reset', '--resetmode=reset', quiet=True)


def main():
    global A
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('backup', 'full', 'super-find', 'verify'))
    ap.add_argument('--pkg')
    ap.add_argument('--backup', help='existing backup folder (must contain the unique partitions and a GPT)')
    ap.add_argument('--out', help='backup: output folder (default work/backup/<time>)')
    ap.add_argument('--loader', help=f'firehose programmer (default: the one in the package)')
    ap.add_argument('--verify', choices=('all', 'a', 'none'), default='all',
                    help='read back after `full`: every file (default), slot A + shared only, or none')
    ap.add_argument('--verify-super-full', action='store_true', help='read back all of super, not only its metadata')
    ap.add_argument('--yes', action='store_true', help='do not ask (only after the user agreed to the plan)')
    ap.add_argument('--dry-run', action='store_true', help='offline checks and the plan; nothing is sent')
    A = ap.parse_args()
    A.log = os.path.join(tempfile.gettempdir(), 'tb710fu-edl.log')
    A.pkg_dir = find_pkg()
    if A.pkg_dir and A.command != 'backup':
        A.pkg_dir = os.path.abspath(A.pkg_dir)
    {'backup': cmd_backup, 'full': cmd_full, 'super-find': cmd_super_find, 'verify': cmd_verify}[A.command]()


if __name__ == '__main__':
    main()
