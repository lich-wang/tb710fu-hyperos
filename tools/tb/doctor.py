"""doctor: is this computer ready, are the downloads there, and can the user put the tablet into 9008?

Read only. `doctor` checks the computer, the tools and the downloads; `doctor --usb` then walks the user through
entering 9008 by hand (the button combination), confirms that edl can talk to the tablet (printgpt, read only)
and resets it. Nothing is written to the tablet.
"""
import glob, hashlib, os, platform, re, shutil, subprocess, sys, time

from . import sources
from .sources import TOOLS, WORK, SourceError

AVB_DIR, MKB_DIR = os.path.join(TOOLS, 'avb'), os.path.join(TOOLS, 'mkbootimg')
TESTKEY_SHA1 = {'testkey_rsa4096.pem': '2597c218aae470a130f61162feaae70afd97f011',
                'testkey_rsa2048.pem': 'cdbb77177f731920bbe0a0f94f84d9038ae0617d'}
MIN_FREE = 150 << 30

# yupei full OTA verified by this project: file name, size, md5 prefix (the 10 hex digits in its name), address
XIAOMI = [('yupei-ota_full-OS4.0.5.0.XPZCNXM-user-17.0-4fe489f542.zip', 9922261403, '4fe489f542',
           'https://bkt-sgp-miui-ota-update-alisgp.oss-ap-southeast-1.aliyuncs.com/OS4.0.5.0.XPZCNXM/'
           'yupei-ota_full-OS4.0.5.0.XPZCNXM-user-17.0-4fe489f542.zip')]
LOLINET = 'https://mirrors.lolinet.com/firmware/lenowow/2025/Yoga_Tab_2025/'
LENOVO = [('CN 17.5.04.414', 'TB710FU_CN/TB710FU_CN_OPEN_USER_Q00019.0_A16_ZUI_17.5.04.414_ST_260320.7z', 9517603070),
          ('CN 17.0.04.354', 'TB710FU_CN/TB710FU_CN_OPEN_USER_QSM8650_V_ZUI_17.0.04.354_ST_251111.7z', 9201119510),
          ('CN 17.0.04.279', 'TB710FU_CN/TB710FU_CN_OPEN_USER_QSM8650_V_ZUI_17.0.04.279_ST_250808.7z', 9055214792)]


class Report:
    def __init__(self):
        self.bad, self.warn = 0, 0

    def ok(self, msg):
        print(f'  ✅ {msg}')

    def fail(self, msg, fix=None):
        self.bad += 1
        print(f'  ❌ {msg}' + (f'\n     → {fix}' if fix else ''))

    def note(self, msg, fix=None):
        self.warn += 1
        print(f'  ⚠ {msg}' + (f'\n     → {fix}' if fix else ''))


def run_out(cmd, timeout=30):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, errors='replace', timeout=timeout)
        return p.returncode, p.stdout + p.stderr
    except (OSError, subprocess.SubprocessError) as e:
        return None, str(e)


def which_any(*names, extra=()):
    for n in names:
        p = shutil.which(n)
        if p:
            return p
        for d in extra:
            c = os.path.join(d, n)
            if os.access(c, os.X_OK):
                return c
    return None


def install_hint(mac, linux):
    return f'macOS: brew install {mac}；Debian / Ubuntu: sudo apt install {linux}'


# ---------------------------------------------------------------- computer and tools

def check_computer(r):
    print('\n[电脑与工具]')
    system = platform.system()
    if system in ('Darwin', 'Linux'):
        r.ok(f'系统 {system} {platform.release()}（{platform.machine()}）')
    else:
        r.fail(f'系统 {system}：暂不支持', '用 macOS 或 Linux（Windows 的 9008 驱动和 erofs 工具还没整理）')
    if sys.version_info >= (3, 8):
        r.ok(f'Python {platform.python_version()}')
    else:
        r.fail(f'Python {platform.python_version()} 太旧', '要 3.8 以上')
    e2 = ('/opt/homebrew/opt/e2fsprogs/sbin', '/usr/local/opt/e2fsprogs/sbin', '/sbin', '/usr/sbin')
    missing = [n for n in ('e2fsck', 'debugfs', 'resize2fs', 'dumpe2fs') if not which_any(n, extra=e2)]
    if missing:
        r.fail(f'e2fsprogs 缺 {", ".join(missing)}', install_hint('e2fsprogs', 'e2fsprogs'))
    else:
        r.ok('e2fsprogs')
    mk = which_any('mkfs.erofs')
    if not mk or not which_any('fsck.erofs'):
        r.fail('没有 erofs-utils（mkfs.erofs / fsck.erofs）', install_hint('erofs-utils', 'erofs-utils'))
    else:
        _, out = run_out([mk, '--help'])
        if '--tar' in out:
            r.ok('erofs-utils（mkfs.erofs 支持 --tar）')
        else:
            r.fail('mkfs.erofs 不支持 --tar（版本太旧）', '要 erofs-utils 1.7 以上')
    if which_any('lz4'):
        r.ok('lz4')
    else:
        r.fail('没有 lz4', install_hint('lz4', 'lz4'))
    if which_any('7zz', '7z'):
        r.ok('7-Zip（读联想 .7z 包）')
    else:
        r.note('没有 7zz / 7z：只有要读 .7z 包（pull --package、取 9008 加载器）时才需要', install_hint('sevenzip', '7zip'))
    edl = sources.edl_bin()
    code, out = run_out([edl, '--help'])
    if code is None or 'usage' not in out.lower():
        r.fail(f'edl 跑不起来（{edl}）',
               'python3 -m venv tools/venv && tools/venv/bin/pip install git+https://github.com/bkerler/edl')
    else:
        r.ok(f'edl（{edl}）')
    if which_any('adb'):
        r.ok('adb（可选：已 root 时 adb reboot edl、刷完后取日志）')
    else:
        r.note('没有 adb（可选，刷完后取日志要用）', install_hint('android-platform-tools', 'adb'))


def check_aosp(r):
    print('\n[AOSP 工具和测试密钥]')
    avb = os.path.join(AVB_DIR, 'avbtool.py')
    if not os.path.isfile(avb):
        r.fail('tools/avb/avbtool.py 没有', 'AOSP external/avb 的 avbtool.py 放到 tools/avb/')
        return
    r.ok('tools/avb/avbtool.py')
    for key, want in TESTKEY_SHA1.items():
        p = os.path.join(AVB_DIR, key)
        if not os.path.isfile(p):
            r.fail(f'tools/avb/{key} 没有', 'AOSP external/avb/test/data 里的测试密钥')
            continue
        tmp = os.path.join(WORK, f'.doctor-{key}.bin')
        os.makedirs(WORK, exist_ok=True)
        code, out = run_out([sys.executable, avb, 'extract_public_key', '--key', p, '--output', tmp])
        got = hashlib.sha1(open(tmp, 'rb').read()).hexdigest() if code == 0 and os.path.exists(tmp) else None
        if os.path.exists(tmp):
            os.remove(tmp)
        if got == want:
            r.ok(f'{key}（就是 AOSP 测试密钥）')
        else:
            r.fail(f'{key} 不是 AOSP 的测试密钥（公钥 sha1 {got}）', '从 AOSP external/avb/test/data 重新取')
    for n in ('mkbootimg.py', 'unpack_bootimg.py'):
        if os.path.isfile(os.path.join(MKB_DIR, n)):
            r.ok(f'tools/mkbootimg/{n}')
        else:
            r.fail(f'tools/mkbootimg/{n} 没有', 'AOSP system/tools/mkbootimg（连同 gki/ 目录）放到 tools/mkbootimg/')


# ---------------------------------------------------------------- downloads and disk

def md5_prefix(path):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 22), b''):
            h.update(b)
    return h.hexdigest()


def check_downloads(r, verify):
    print('\n[下载]')
    try:
        loader = sources.find_loader()
        sha = hashlib.sha256(open(loader, 'rb').read()).hexdigest()
        if sha in sources.KNOWN_LOADERS:
            r.ok(f'9008 加载器 {loader}（已知：{sources.KNOWN_LOADERS[sha]}）')
        else:
            r.note(f'9008 加载器 {loader} 不是已验证过的那个（sha256 {sha[:16]}…）',
                   '多半也能用；连不上时换联想官方包里的 xbl_s_devprg_ns.melf')
    except SourceError:
        r.fail('没有 9008 加载器（firehose）',
               '从联想官方 9008 包里取出 xbl_s_devprg_ns.melf 放到 work/firehose/，例如：'
               f'7zz e 联想包.7z -owork/firehose -r xbl_s_devprg_ns.melf（包的地址见下面）')
    for name, size, md5p, url in XIAOMI:
        hits = glob.glob(os.path.join(WORK, '**', name), recursive=True) + glob.glob(os.path.join(WORK, '..', name))
        if not hits:
            r.fail(f'没有小米底包 {name}', f'下载到 work/：{url}\n       （{size} 字节；建议 aria2c -c -x 16 -s 16 <地址>）')
            continue
        p = hits[0]
        got = os.path.getsize(p)
        if got != size:
            r.fail(f'{p} 大小 {got}，应为 {size}（没下完？）', 'aria2c -c 断点续传')
        elif verify:
            print(f'     校验 md5（{size / 2**30:.1f} GiB，要一会儿）…', flush=True)
            if md5_prefix(p).startswith(md5p):
                r.ok(f'小米底包 {p}（大小、md5 都对）')
            else:
                r.fail(f'{p} 的 md5 和文件名里的 {md5p} 对不上（下载损坏）', '删掉重新下载')
        else:
            r.ok(f'小米底包 {p}（大小对；加 --verify-downloads 校验 md5）')
    pk = glob.glob(os.path.join(WORK, '**', 'TB710FU_*.7z'), recursive=True)
    if pk:
        r.ok('联想官方包：' + '、'.join(os.path.basename(p) for p in pk))
    else:
        r.note('没有联想官方 9008 包：平板还是原厂系统时不需要（pull 从你自己的备份取）；'
               '已刷过别的系统、或要取 9008 加载器时才需要',
               '国行包（海外 ROW 包 pull --package 用不了）：\n' + '\n'.join(
                   f'       {v}：{LOLINET}{f}（{s} 字节）' for v, f, s in LENOVO))


def check_disk(r):
    print('\n[磁盘]')
    os.makedirs(WORK, exist_ok=True)
    free = shutil.disk_usage(WORK).free
    msg = f'{WORK} 所在磁盘剩余 {free / 2**30:.0f} GiB'
    if free >= MIN_FREE:
        r.ok(msg)
    else:
        r.fail(msg + f'，建议至少 {MIN_FREE >> 30} GiB', '备份 25 GB + 下载 20 GB + 构建 40 GB + 读回 11 GB，还要留余量')


# ---------------------------------------------------------------- USB

def check_usb_host(r):
    print('\n[USB / 9008 驱动]')
    system = platform.system()
    if system == 'Darwin':
        r.ok('macOS 不需要装 9008 驱动（edl 直接用 libusb）')
        r.note('公司电脑的安全软件可能拦 USB 设备', '只要 doctor --usb 能连上就没问题；连不上先换一台自己的电脑')
    elif system == 'Linux':
        rules = ' '.join(open(f, errors='replace').read() for f in glob.glob('/etc/udev/rules.d/*.rules')
                         + glob.glob('/lib/udev/rules.d/*.rules'))
        if re.search(r'05c6', rules, re.I):
            r.ok('有 05c6（高通 9008）的 udev 规则')
        elif os.geteuid() == 0:
            r.note('没有 05c6 的 udev 规则（现在是 root，也能用）')
        else:
            r.fail('没有 05c6（高通 9008）的 udev 规则：普通用户打不开 9008 设备',
                   'sudo cp tools/venv/lib/python3*/site-packages/edlclient/Drivers/51-edl.rules /etc/udev/rules.d/ 2>/dev/null'
                   ' || 参考 bkerler/edl 仓库 Drivers/51-edl.rules；然后 sudo udevadm control -R 并重新插线')
        code, out = run_out(['systemctl', 'is-active', 'ModemManager'])
        if out.strip() == 'active':
            r.note('ModemManager 在运行，可能抢占 9008 设备', 'sudo systemctl stop ModemManager（刷完再启动）')
    r.note('用能传数据的线，直接插电脑上的口，不要经过扩展坞 / 集线器')


def guide_9008():
    print('''
进入 9008（这次请用按键手动进，确认你会）：
  1. 拔掉数据线；
  2. 长按电源键 15–20 秒，直到平板彻底关机（屏幕全黑、不再亮起）；
  3. 同时按住 音量+ 和 音量−，不要松手；
  4. 插上数据线（另一头已经插在电脑上）；
  5. 屏幕保持全黑就对了（9008 没有任何画面），再松开音量键。
  没反应：重复 1–5；屏幕亮了 / 出现联想 logo：说明没进去，拔线重来。''')


def usb_practice(r, loader=None, timeout=180):
    print('\n[9008 实操]')
    mode = sources.usb_mode()
    if mode == '9008':
        print('  平板已经在 9008（继续用它测通信）')
    else:
        guide_9008()
        print(f'\n  等待 9008 设备（05C6:9008，最多 {timeout} 秒）…', flush=True)
        end = time.time() + timeout
        while time.time() < end:
            mode = sources.usb_mode()
            if mode in ('9008', '900e') or mode == 'unknown':
                break
            time.sleep(2)
    if mode == '900e':
        r.fail('平板在 900E（内核崩溃转储模式），不是 9008', '拔线，长按电源 15–20 秒关机，再按上面的步骤进 9008')
        return
    if mode is None:
        r.fail('等不到 9008 设备', '确认第 2 步彻底关机、第 3 步两个音量键一起按住再插线；换线 / 换口')
        return
    os.makedirs(os.path.join(WORK, 'device'), exist_ok=True)
    log = os.path.join(WORK, 'device', f'edl-doctor-{time.strftime("%Y%m%d-%H%M%S")}.log')
    try:
        src = sources.EdlSource(sources.find_loader(loader), log)
    except SourceError as e:
        r.fail(f'检测到 9008，但 edl 连不上：{e}', f'日志 {log}')
        return
    sn = (src.sahara or {}).get('chip_serial')
    r.ok(f'edl 能和平板通信（只读 printgpt）：{len(src.parts)} 个分区，当前槽 {src.slot or "?"}'
         + (f'，芯片序列号 {sn[:3]}…{sn[-3:]}' if sn else '（这次没打印芯片序列号：firehose 已在跑）'))
    try:
        src.reset()
        r.ok('已让平板退出 9008 重启（你已经会手动进 9008 了）')
    except SourceError as e:
        r.note(f'复位失败：{e}', '拔线，长按电源键开机')


def run(usb=False, verify=False, loader=None):
    r = Report()
    print(f'环境检查（工作目录 {WORK}）')
    check_computer(r)
    check_aosp(r)
    check_downloads(r, verify)
    check_disk(r)
    check_usb_host(r)
    if usb:
        usb_practice(r, loader)
    print('\n==== 结论 ====')
    if r.bad:
        print(f'❌ {r.bad} 项没准备好（上面每项都给了处理办法），处理完再跑一次 doctor')
    else:
        print('✅ 电脑这边准备好了' + ('，9008 也通了' if usb else '；再跑 doctor --usb 练习手动进 9008'))
    if r.warn:
        print(f'⚠ {r.warn} 项提示，看一下是否和你有关')
    return r.bad == 0
