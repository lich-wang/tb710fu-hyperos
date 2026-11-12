"""framework: system, system_ext and product of the new super, from the official Xiaomi Pad 8 (yupei) images.

Every change and its reason is listed in docs/porting.md ("框架侧"). In short:
  system      media.settings.xml commented out (the Lenovo vendor has no media_profiles_vendor.xml: zygote aborts);
              product/pangu/system and mi_ext's system permission file moved in (Xiaomi's vendor overlays them at
              boot, the Lenovo vendor does not); Lenovo Tab Pen Plus key layout; tb710fu.rc (stylus, double tap
              to wake and touch sampling switches wired to the Lenovo touch driver's /proc nodes)
  system_ext  unchanged (the image is copied)
  product     mi_ext/product merged in (same reason as pangu); build.prop: 4 KiB pages, no lcd_density,
              Xiaomi Pad 8 identity taken from the yupei odm (the Lenovo odm would otherwise make the device
              "TB710FU" to the Xiaomi framework), stylus on, skiagl, incremental ".DEV" (Xiaomi account);
              the two USB NCM overlays from work/overlay/ when present
Files keep the uid/gid/mode/SELinux label of the official images; added files take them from a named source.

Output: work/build/framework/<version>[-nofind][-debug]/{system,system_ext,product}.img + framework.json.
Needs erofs-utils (fsck.erofs, mkfs.erofs).
"""
import glob, json, os, shutil, stat, subprocess, sys, tarfile, time, uuid

from . import layout
from .backup import file_sha256
from .sources import WORK, SourceError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from erofs_meta import Erofs  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KEYLAYOUT = os.path.join(REPO, 'hyperos', 'keylayout', 'Vendor_17ef_Product_617f.kl')
OVERLAYS = ('TB710FUTetheringOverlay.apk', 'TB710FUConnectivityOverlay.apk')
MTIME = 1230768000

# Xiaomi Pad 8 identity as the yupei odm reports it (odm/etc/build.prop of OS4.0.5.0.XPZCNXM)
YUPEI_ID = {'brand': 'Xiaomi', 'device': 'yupei', 'manufacturer': 'Xiaomi', 'model': '25097RP43C',
            'name': 'yupei', 'marketname': 'Xiaomi Pad 8', 'cert': '25097RP43C'}

TB710FU_RC = """# TB710FU: Xiaomi settings -> Lenovo touch driver switches (docs/porting.md)
on property:sys.boot_completed=1
    chown system system /proc/gesture_control
    chmod 0664 /proc/gesture_control
    chown system system /proc/support_pen
    chmod 0664 /proc/support_pen
    chown system system /proc/game_mode
    chmod 0664 /proc/game_mode
    chown system system /proc/game_edge
    chmod 0664 /proc/game_edge

on property:persist.sys.gesture_wakeup=2
    write /proc/gesture_control 1
on property:persist.sys.gesture_wakeup=0
    write /proc/gesture_control 0

on property:persist.sys.stylus.enable=1
    write /proc/support_pen 1
on property:persist.sys.stylus.enable=0
    write /proc/support_pen 0

on property:persist.sys.high_touch_sampling_rate.enable=1
    write /proc/game_mode 4
    write /proc/game_edge 4
on property:persist.sys.high_touch_sampling_rate.enable=0
    write /proc/game_mode 5
    write /proc/game_edge 0
"""

DEBUG_RC = """# TB710FU debug build: keep adb in the USB configuration
on property:sys.boot_completed=1 && property:sys.usb.config=mtp
    setprop sys.usb.config mtp,adb

on property:sys.boot_completed=1 && property:sys.usb.config=none
    setprop sys.usb.config adb
"""


def tool(name):
    p = shutil.which(name)
    if not p:
        raise SourceError(f'找不到 {name}：要 erofs-utils（macOS: brew install erofs-utils；Linux: apt install erofs-utils）')
    return p


def extract(img, dest):
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    p = subprocess.run([tool('fsck.erofs'), f'--extract={dest}', img], capture_output=True, text=True)
    if p.returncode:
        raise SourceError(f'fsck.erofs 解包 {img} 失败：{(p.stdout + p.stderr)[-400:]}')


def meta_of(img, cache):
    if os.path.exists(cache) and os.path.getmtime(cache) > os.path.getmtime(img):
        return json.load(open(cache))
    m = Erofs(img).walk()
    json.dump(m, open(cache, 'w'))
    return m


def move(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.lexists(dst) and not os.path.isdir(dst):
        os.remove(dst)
    shutil.move(src, dst)


class Stage:
    """An extracted partition plus the metadata of every path that did not come from its own image."""

    def __init__(self, name, root, meta, version=''):
        self.name, self.root, self.meta, self.extra, self.defaulted = name, root, meta, {}, []
        # fixed filesystem UUID: mkfs.erofs picks a random one, which makes every build of the same tree differ
        self.uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f'tb710fu:{version}:{name}'))

    def add(self, rel, meta, mode=None):
        m = dict(meta)
        if mode is not None:
            m['mode'] = mode
        self.extra[rel] = m

    def lookup(self, rel, is_dir):
        m = self.extra.get(rel) or self.meta.get(rel)
        if m:
            return m
        parent = self.extra.get(os.path.dirname(rel) or '.') or self.meta.get(os.path.dirname(rel) or '.') or {}
        self.defaulted.append(rel)
        return {'uid': parent.get('uid', 0), 'gid': parent.get('gid', 0),
                'mode': 0o040755 if is_dir else 0o100644,
                'xattrs': {k: v for k, v in parent.get('xattrs', {}).items() if k == 'security.selinux'}}

    def mkfs(self, img):
        """Write the staged tree as a PAX tar (with xattrs), drop the tree, then mkfs.erofs --tar=f.

        (mkfs.erofs 1.9 reads large members of a tar more than once, so the tar cannot come from a pipe.)
        """
        tpath = img + '.tar'
        t = tarfile.open(tpath, 'w', format=tarfile.PAX_FORMAT, encoding='utf-8', errors='surrogateescape')
        entries = ['.']
        for r, dirs, files in os.walk(self.root):
            dirs.sort()
            for x in dirs + sorted(files):
                entries.append(os.path.relpath(os.path.join(r, x), self.root))
        for rel in entries:
            full = os.path.join(self.root, rel)
            st = os.lstat(full)
            m = self.lookup(rel, stat.S_ISDIR(st.st_mode))
            ti = tarfile.TarInfo('.' if rel == '.' else './' + rel)
            ti.uid, ti.gid, ti.mode, ti.mtime = m['uid'], m['gid'], m['mode'] & 0o7777, MTIME
            ti.pax_headers = {f'SCHILY.xattr.{k}': bytes.fromhex(v).decode('utf-8', 'surrogateescape')
                              for k, v in m.get('xattrs', {}).items()}
            if stat.S_ISLNK(st.st_mode):
                ti.type, ti.linkname = tarfile.SYMTYPE, os.readlink(full)
                t.addfile(ti)
            elif stat.S_ISDIR(st.st_mode):
                ti.type = tarfile.DIRTYPE
                t.addfile(ti)
            else:
                ti.size = st.st_size
                with open(full, 'rb') as fh:
                    t.addfile(ti, fh)
        t.close()
        shutil.rmtree(self.root)                       # the tar has everything now; keeps the peak disk use down
        if os.path.exists(img):
            os.remove(img)
        p = subprocess.run([tool('mkfs.erofs'), '-b4096', '-zlz4hc,9', f'-T{MTIME}', f'-U{self.uuid}', '--tar=f', img, tpath],
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        os.remove(tpath)
        if p.returncode:
            raise SourceError(f'mkfs.erofs {self.name} 失败：{p.stderr.decode(errors="replace")[-400:]}')
        return len(entries)


# ---------------------------------------------------------------- edits

def edit_lines(path, fn):
    lines = open(path, encoding='utf-8', errors='surrogateescape').read().splitlines()
    out = fn(lines)
    open(path, 'w', encoding='utf-8', errors='surrogateescape').write('\n'.join(out) + '\n')


def system_prop(lines, debug):
    out = []
    for l in lines:
        if l.startswith('media.settings.xml=/vendor/etc/media_profiles_vendor.xml'):
            l = '# TB710FU: the Lenovo vendor has no media_profiles_vendor.xml\n#' + l
        if debug and l in ('ro.adb.secure=1', 'ro.debuggable=0'):
            l = l[:-1] + ('0' if 'secure' in l else '1')
        out.append(l)
    if debug:
        out += ['', '# TB710FU debug build', 'persist.sys.usb.config=adb', 'persist.service.adb.enable=1']
    return out


def product_prop(lines, miext, keep_finddevice, debug):
    drop = {'ro.product.build.no_bionic_page_size_macro', 'ro.sf.lcd_density'}
    out = []
    for l in lines:
        k = l.split('=', 1)[0] if '=' in l and not l.startswith('#') else None
        if k in drop:
            out.append('# removed for TB710FU (4 KiB-page kernel, native density): ' + l)
        elif k == 'ro.product.cpu.pagesize.max':
            out.append('ro.product.cpu.pagesize.max=4096')
        elif k == 'ro.build.version.incremental' and keep_finddevice and not l.endswith('.DEV'):
            # ".DEV" -> IS_STABLE_VERSION=false -> Xiaomi Account ignores the Find Device status check, which
            # always fails without Xiaomi's TEE. Do not turn on Find Device on the tablet.
            out.append(l + '.DEV')
        elif k and k.startswith('ro.product.product.'):
            continue                                       # replaced by the identity below
        else:
            out.append(l)
    out += ['', '# ---- merged from mi_ext/etc/build.prop (mi_ext is merged into product) ----']
    for l in miext:
        if l and not l.startswith('#') and not l.startswith('ro.vendor.build.ab_ota_partitions='):
            out.append(l)
    out += ['', '# ---- TB710FU ----',
            '# Xiaomi Pad 8 identity (from the yupei odm); product first, so the Lenovo odm does not win',
            'ro.product.property_source_order=product,odm,vendor,system_ext,system']
    out += [f'ro.product.product.{k}={v}' for k, v in YUPEI_ID.items()]
    out += ['persist.sys.stylus.enable=1',
            '# wireless display colours: Skia/Vulkan TextureView YUV->RGB is wrong here, GL is right',
            'debug.hwui.renderer=skiagl']
    if debug:
        out += ['', '# TB710FU debug build', 'ro.adb.secure=0', 'ro.debuggable=1']
    return out


def build(xiaomi_dir=None, keep_finddevice=True, debug=False):
    if not xiaomi_dir:
        # finished imports only: glob skips the hidden temporary folders of an import in progress
        c = sorted(glob.glob(os.path.join(WORK, 'xiaomi', '*', 'xiaomi.json')), key=os.path.getmtime)
        xiaomi_dir = os.path.dirname(c[-1]) if c else None
    if not xiaomi_dir or not os.path.exists(os.path.join(xiaomi_dir, 'xiaomi.json')):
        raise SourceError('没有小米官方组件：先跑 tb710fu.py import')
    info = json.load(open(os.path.join(xiaomi_dir, 'xiaomi.json')))
    tag = info['version'] + ('' if keep_finddevice else '-nofind') + ('-debug' if debug else '')
    out = os.path.join(WORK, 'build', 'framework', tag)
    stage = os.path.join(out, 'stage')
    os.makedirs(stage, exist_ok=True)
    img = {n: os.path.join(xiaomi_dir, f'{n}.img') for n in ('system', 'system_ext', 'product', 'mi_ext')}
    print(f'框架侧：{xiaomi_dir} → {out}')
    print('  读元数据、解包 system / product / mi_ext …', flush=True)
    meta = {n: meta_of(img[n], os.path.join(out, f'meta_{n}.json')) for n in ('system', 'product', 'mi_ext')}
    for n in ('system', 'product', 'mi_ext'):
        extract(img[n], os.path.join(stage, n))
    S = Stage('system', os.path.join(stage, 'system'), meta['system'], info['version'])
    P = Stage('product', os.path.join(stage, 'product'), meta['product'], info['version'])
    M = os.path.join(stage, 'mi_ext')
    changes = {'system': [], 'product': []}
    sysroot = os.path.join(S.root, 'system')

    # system
    edit_lines(os.path.join(sysroot, 'build.prop'), lambda ls: system_prop(ls, debug))
    changes['system'].append('build.prop: media.settings.xml 注释掉' + ('；调试版 adb' if debug else ''))
    kl = 'system/usr/keylayout/Vendor_17ef_Product_617f.kl'
    shutil.copyfile(KEYLAYOUT, os.path.join(S.root, kl))
    S.add(kl, meta['system']['system/usr/keylayout/Generic.kl'], 0o100644)
    changes['system'].append(kl)
    rc = 'system/etc/init/tb710fu.rc'
    open(os.path.join(S.root, rc), 'w').write(TB710FU_RC)
    S.add(rc, meta['system']['system/etc/init/apexd.rc'], 0o100644)
    changes['system'].append(rc)
    if debug:
        rc2 = 'system/etc/init/tb710fu_adb.rc'
        open(os.path.join(S.root, rc2), 'w').write(DEBUG_RC)
        S.add(rc2, meta['system']['system/etc/init/apexd.rc'], 0o100644)
    px = 'system/etc/permissions/privapp-permissions-system-miext.xml'
    move(os.path.join(M, px), os.path.join(S.root, px))
    S.add(px, meta['mi_ext'][px])
    changes['system'].append(px + '（来自 mi_ext）')
    pg = os.path.join(P.root, 'pangu', 'system')
    moved = 0
    for r, dirs, files in os.walk(pg, topdown=True):
        for x in sorted(dirs) + sorted(files):
            src = os.path.join(r, x)
            rel = os.path.relpath(src, pg)
            if '/com.tms.' in '/' + rel or rel.startswith('framework/oat') and 'com.tms.' in rel:
                continue                       # Xiaomi NFC payment jars (no NFC here)
            dst_rel = 'system/' + rel
            if os.path.isdir(src) and not os.path.islink(src):
                os.makedirs(os.path.join(S.root, dst_rel), exist_ok=True)
                if dst_rel not in meta['system']:
                    S.add(dst_rel, meta['product']['pangu/system/' + rel])
            else:
                shutil.copy2(src, os.path.join(S.root, dst_rel), follow_symlinks=False)
                S.add(dst_rel, meta['product']['pangu/system/' + rel])
                moved += 1
    shutil.rmtree(os.path.join(P.root, 'pangu'))
    changes['system'].append(f'product/pangu/system → system（{moved} 个文件，不含 com.tms.* NFC jar）')

    # product
    mp = os.path.join(M, 'product')
    merged = 0
    for r, dirs, files in os.walk(mp):
        for x in sorted(dirs) + sorted(files):
            src = os.path.join(r, x)
            rel = os.path.relpath(src, mp)
            dst = os.path.join(P.root, rel)
            if os.path.isdir(src) and not os.path.islink(src):
                os.makedirs(dst, exist_ok=True)
                if rel not in meta['product']:
                    P.add(rel, meta['mi_ext']['product/' + rel])
            else:
                if os.path.lexists(dst):
                    os.remove(dst)
                shutil.copy2(src, dst, follow_symlinks=False)
                P.add(rel, meta['mi_ext']['product/' + rel])
                merged += 1
    changes['product'].append(f'mi_ext/product 合并进来（{merged} 个文件）')
    miext_prop = open(os.path.join(M, 'etc', 'build.prop'), encoding='utf-8', errors='surrogateescape').read()
    edit_lines(os.path.join(P.root, 'etc', 'build.prop'),
               lambda ls: product_prop(ls, miext_prop.splitlines(), keep_finddevice, debug))
    changes['product'].append('build.prop：4K 页、去掉 lcd_density、小米平板 8 身份、手写笔、skiagl'
                              + ('、.DEV' if keep_finddevice else ''))
    for ov in OVERLAYS:
        src = os.path.join(WORK, 'overlay', ov)
        if os.path.exists(src):
            shutil.copyfile(src, os.path.join(P.root, 'overlay', ov))
            P.add('overlay/' + ov, meta['product']['overlay/NcmTetheringResCommon_Sys.apk'], 0o100644)
            changes['product'].append('overlay/' + ov)
        else:
            print(f'  ⚠ 没有 work/overlay/{ov}：USB 网络共享（NCM）在 Mac 上可能用不了（见 hyperos/README.md）')
    if not keep_finddevice:
        shutil.rmtree(os.path.join(P.root, 'priv-app', 'MIUIFindDeviceCN'), ignore_errors=True)
        changes['product'].append('去掉 priv-app/MIUIFindDeviceCN（开机向导用）')
    if debug:
        ak = os.path.join(P.root, 'etc', 'security', 'adb_keys')
        pub = os.path.expanduser('~/.android/adbkey.pub')
        if os.path.exists(pub):
            os.makedirs(os.path.dirname(ak), exist_ok=True)
            open(ak, 'a').write(open(pub).read())
            P.add('etc/security/adb_keys', meta['product']['etc/build.prop'], 0o100644)
            changes['product'].append('etc/security/adb_keys（本机 adb 公钥）')

    # images
    res = {'xiaomi': xiaomi_dir, 'version': info['version'], 'find_device': keep_finddevice, 'debug': debug,
           'changes': changes, 'images': {}}
    for st in (S, P):
        print(f'  打包 {st.name}（EROFS lz4hc）…', flush=True)
        t0 = time.time()
        n = st.mkfs(os.path.join(out, f'{st.name}.img'))
        print(f'    {n} 个条目，{time.time() - t0:.0f} 秒；没有元数据、按父目录默认的 {len(st.defaulted)} 个', flush=True)
        res.setdefault('defaulted', {})[st.name] = st.defaulted[:50]
    shutil.rmtree(M)
    shutil.copyfile(img['system_ext'], os.path.join(out, 'system_ext.img'))
    for n in ('system', 'system_ext', 'product'):
        p = os.path.join(out, f'{n}.img')
        with open(p, 'rb') as f:
            fs, _ = layout.fs_type(f.read(4096))
        res['images'][n] = {'bytes': os.path.getsize(p), 'sha256': file_sha256(p), 'fs': fs}
    res['created'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    with open(os.path.join(out, 'framework.json'), 'w') as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    return out, res


def report(out, res):
    print('\n==== 框架侧 ====')
    for n, i in res['images'].items():
        print(f'{n:11} {i["fs"]:5} {i["bytes"] / 2**30:6.2f} GiB  sha256 {i["sha256"][:16]}…')
    for part, cs in res['changes'].items():
        for c in cs:
            print(f'{part:8} {c}')
    for part, d in res.get('defaulted', {}).items():
        if d:
            print(f'⚠ {part}：{len(d)} 个条目没有元数据，按父目录默认，例如 {d[:3]}')
    print(f'\n✅ {out}')
    return True
