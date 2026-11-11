#!/usr/bin/env python3
"""TB710FU: Xiaomi HyperOS from official sources on a locked bootloader.

  tb710fu.py check                     9008 (default): read-only check of the connected tablet
  tb710fu.py check --adb               same over adb (needs root: Magisk, allow Shell)
  tb710fu.py check FILE|DIR|ZIP|7Z...  offline: abl / vbmeta of an official package or of dumped images
  tb710fu.py backup                    9008: full read-only backup (GPT + everything but userdata), resumable
  tb710fu.py pull                      Lenovo stock parts for the build, from a full backup of the tablet
  tb710fu.py pull --package ZIP|7Z|DIR  ... or from an official Lenovo 9008 package
  tb710fu.py import OTA.zip            Xiaomi Pad 8 (yupei) parts from the official Xiaomi full OTA package
  tb710fu.py vendor                    vendor side of the new super from the Lenovo stock parts (docs/porting.md)
  tb710fu.py framework                 system, system_ext, product from the imported Xiaomi images (docs/porting.md)
  tb710fu.py bootchain                 boot chain + verity footers, test-key signed at the device's rollback indexes

The check answers whether the tablet's bootloader (ABL) trusts the public AOSP test key, and writes the
device profile (work/device/latest.json: slot, rollback indexes, super layout, firmware fingerprint) that
the later steps (pull / build / flash) use. Nothing is ever written to the device.

Options: --loader FILE (9008 firehose programmer; default work/firehose/*), --serial (adb), --no-firmware
(skip the firmware fingerprint, faster), --no-reset (stay in 9008 afterwards).
Python 3.8+, standard library only; 9008 needs bkerler/edl (`pip install edlclient`, or $EDL).
"""
import argparse, json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tb import backup, bootchain, check, framework, lenovo, sources, vendor, xiaomi  # noqa: E402


def cmd_check(a):
    if a.paths:
        for p in a.paths:
            if not os.path.exists(p):
                sys.exit(f'{p}: 不存在')
        return 0 if check.check_files(a.paths) else 1
    src = None
    try:
        if a.adb:
            src = sources.AdbSource(a.serial)
        else:
            os.makedirs(os.path.join(sources.WORK, 'device'), exist_ok=True)
            log = os.path.join(sources.WORK, 'device', f'edl-{time.strftime("%Y%m%d-%H%M%S")}.log')
            loader = sources.find_loader(a.loader)
            print(f'9008 只读检查（加载器 {loader}，日志 {log}）…', flush=True)
            src = sources.EdlSource(loader, log)
        prof = check.run_check(src, firmware=not a.no_firmware)
        path = check.save(prof)
        check.report(prof)
        print(f'\n设备档案：{path}')
    except sources.SourceError as e:
        print(f'\n{e}', file=sys.stderr)
        prof = None
    finally:
        # leave 9008 even when the check failed half way, unless asked to stay
        if src is not None and src.kind == 'edl' and not a.no_reset:
            try:
                src.reset()
                print('已让平板退出 9008 重启。')
            except sources.SourceError as e:
                print(f'复位失败（拔线长按电源键重启即可）：{e}')
    if prof is None:
        return 1
    return 0 if prof['ok'] else 1


def edl_source(a):
    os.makedirs(os.path.join(sources.WORK, 'device'), exist_ok=True)
    log = os.path.join(sources.WORK, 'device', f'edl-{time.strftime("%Y%m%d-%H%M%S")}.log')
    loader = sources.find_loader(a.loader)
    print(f'9008（加载器 {loader}，日志 {log}）…', flush=True)
    return sources.EdlSource(loader, log)


def reset(src, a):
    if src.kind == 'edl' and not a.no_reset:
        try:
            src.reset()
            print('已让平板退出 9008 重启。')
        except sources.SourceError as e:
            print(f'复位失败（拔线长按电源键重启即可）：{e}')


def do_backup(a, resume=True):
    src = edl_source(a)
    try:
        return backup.run(src, getattr(a, 'out', None), src.sahara.get('chip_serial'), resume=resume)
    finally:
        reset(src, a)


def cmd_backup(a):
    try:
        do_backup(a)
    except sources.SourceError as e:
        sys.exit(f'\n{e}')
    return 0


def cmd_pull(a):
    try:
        if a.package:
            prof = None
            pf = os.path.join(sources.WORK, 'device', 'latest.json')
            if os.path.exists(pf):
                prof = json.load(open(pf))
            out, info = lenovo.from_package(a.package, prof)
        else:
            bdir = a.backup or (None if a.new_backup else backup.latest())
            if not bdir:
                print('先做完整备份（只读，二十多 GB，要十几分钟到半小时）。')
                bdir = do_backup(a, resume=not a.new_backup)
            st = backup.load(bdir)
            out, info = lenovo.from_backup(bdir, st)
    except sources.SourceError as e:
        sys.exit(f'\n{e}')
    ok = lenovo.report(out, info)
    if not ok and not a.package:
        print('\n平板上的系统已经不是联想原厂：用联想官方 9008 包取件：tb710fu.py pull --package 包.zip')
    return 0 if ok else 1


def cmd_import(a):
    try:
        out, info = xiaomi.import_ota(a.ota, force=a.force)
    except sources.SourceError as e:
        sys.exit(f'\n{e}')
    return 0 if xiaomi.report(out, info) else 1


def cmd_vendor(a):
    try:
        out, res = vendor.build(a.lenovo)
    except sources.SourceError as e:
        sys.exit(f'\n{e}')
    return 0 if vendor.report(out, res) else 1


def cmd_framework(a):
    try:
        out, res = framework.build(a.xiaomi, keep_finddevice=not a.no_finddevice, debug=a.debug)
    except sources.SourceError as e:
        sys.exit(f'\n{e}')
    return 0 if framework.report(out, res) else 1


def cmd_bootchain(a):
    try:
        out, res = bootchain.build(a.lenovo, a.vendor_dir, a.framework_dir, a.backup, a.profile, verity=not a.no_verity)
    except sources.SourceError as e:
        sys.exit(f'\n{e}')
    return 0 if bootchain.report(out, res) else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('check', help='检查平板（或固件文件）是否信任 AOSP 测试密钥，生成设备档案')
    c.add_argument('paths', nargs='*', help='离线检查：固件文件夹 / .zip / .7z / 镜像文件')
    c.add_argument('--adb', action='store_true', help='用 adb（需要 root）而不是 9008')
    c.add_argument('--serial', default=os.environ.get('ANDROID_SERIAL'), help='adb 序列号')
    c.add_argument('--loader', help='9008 firehose 加载器')
    c.add_argument('--no-firmware', action='store_true', help='不读底层固件做指纹（更快）')
    c.add_argument('--no-reset', action='store_true', help='检查完留在 9008')
    b = sub.add_parser('backup', help='9008 完整只读备份（GPT + 除 userdata 外所有分区），断了可以接着读')
    b.add_argument('--out', help='备份目录（默认 work/backup/<芯片序列号>-<时间>，有没备份完的会接着读）')
    b.add_argument('--loader', help='9008 firehose 加载器')
    b.add_argument('--no-reset', action='store_true', help='备份完留在 9008')
    p = sub.add_parser('pull', help='取出构建要用的联想原厂组件（从平板的完整备份，或联想官方包）')
    p.add_argument('--package', help='联想官方 9008 包（.zip / .7z / 解压后的文件夹）')
    p.add_argument('--backup', help='用这个备份目录（默认最新的完整备份；没有就先备份）')
    p.add_argument('--new-backup', action='store_true', help='不用已有备份，重新备份')
    p.add_argument('--out', help=argparse.SUPPRESS)
    p.add_argument('--loader', help='9008 firehose 加载器')
    p.add_argument('--no-reset', action='store_true', help='备份完留在 9008')
    i = sub.add_parser('import', help='导入小米平板 8（yupei）官方全量 OTA 包')
    i.add_argument('ota', help='yupei-ota_full-*.zip')
    i.add_argument('--force', action='store_true', help='允许没验证过的版本')
    v = sub.add_parser('vendor', help='用联想原厂组件构建 vendor 侧（vendor 改 fstab，odm/dlkm 原样）')
    v.add_argument('--lenovo', help='pull 的输出目录（默认最新的）')
    f = sub.add_parser('framework', help='用小米官方组件构建 system / system_ext / product')
    f.add_argument('--xiaomi', help='import 的输出目录（默认最新的）')
    f.add_argument('--no-finddevice', action='store_true',
                   help='去掉"查找设备"：清数据后第一次开机向导用（否则激活检查过不去）')
    f.add_argument('--debug', action='store_true', help='调试版：开机就有免授权 adb')
    c = sub.add_parser('bootchain', help='启动链 + 各分区 verity，按设备回滚索引用测试密钥签名')
    c.add_argument('--lenovo', help='pull 的输出目录（默认最新）')
    c.add_argument('--vendor-dir', help='vendor 的输出目录（默认最新）')
    c.add_argument('--framework-dir', help='framework 的输出目录（默认最新）')
    c.add_argument('--backup', help='设备完整备份（默认最新）')
    c.add_argument('--profile', help='设备档案（默认 work/device/latest.json）')
    c.add_argument('--no-verity', action='store_true', help='vbmeta flags=1，关闭 dm-verity（备用方案）')
    a = ap.parse_args()
    return {'check': cmd_check, 'backup': cmd_backup, 'pull': cmd_pull, 'import': cmd_import,
            'vendor': cmd_vendor, 'framework': cmd_framework, 'bootchain': cmd_bootchain}[a.cmd](a)


if __name__ == '__main__':
    sys.exit(main())
