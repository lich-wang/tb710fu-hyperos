#!/usr/bin/env python3
"""TB710FU: Xiaomi HyperOS from official sources on a locked bootloader.

  tb710fu.py check                     9008 (default): read-only check of the connected tablet
  tb710fu.py check --adb               same over adb (needs root: Magisk, allow Shell)
  tb710fu.py check FILE|DIR|ZIP|7Z...  offline: abl / vbmeta of an official package or of dumped images

The check answers whether the tablet's bootloader (ABL) trusts the public AOSP test key, and writes the
device profile (work/device/latest.json: slot, rollback indexes, super layout, firmware fingerprint) that
the later steps (pull / build / flash) use. Nothing is ever written to the device.

Options: --loader FILE (9008 firehose programmer; default work/firehose/*), --serial (adb), --no-firmware
(skip the firmware fingerprint, faster), --no-reset (stay in 9008 afterwards).
Python 3.8+, standard library only; 9008 needs bkerler/edl (`pip install edlclient`, or $EDL).
"""
import argparse, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tb import check, sources  # noqa: E402


def cmd_check(a):
    if a.paths:
        for p in a.paths:
            if not os.path.exists(p):
                sys.exit(f'{p}: 不存在')
        return 0 if check.check_files(a.paths) else 1
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
    except sources.SourceError as e:
        sys.exit(f'\n{e}')
    path = check.save(prof)
    check.report(prof)
    print(f'\n设备档案：{path}')
    if src.kind == 'edl' and not a.no_reset:
        try:
            src.reset()
            print('已让平板退出 9008 重启。')
        except sources.SourceError as e:
            print(f'复位失败（拔线长按电源键重启即可）：{e}')
    return 0 if prof['ok'] else 1


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
    a = ap.parse_args()
    return {'check': cmd_check}[a.cmd](a)


if __name__ == '__main__':
    sys.exit(main())
