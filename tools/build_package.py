#!/usr/bin/env python3
"""Build a complete TB710FU HyperOS 4 9008 (EDL) flash package from the downloaded inputs.

Inputs (not in this repository; put them in work/ or pass the paths):
  - HyperOS 3.0.305 port package for TB710FU (Coolapk @兰微卡鱼): the zip, or its extracted folder
    (--port-zip / --port-dir). Gives the 9008 firmware set, GPT, the HyperOS 3 super and the author's
    original boot images.
  - 1.5.04.495_WITHOUT_UBL supplement (.7z, or already copied over the port's images/)  (--supplement)
  - Xiaomi Pad 8 (yupei) OS4.0.5.0.XPZCNXM full OTA zip                                 (--ota)

Output: work/package/TB710FU-HyperOS4-OS4.0.5.0/ (flash it with tools/flash_package.py):
  images/        rawprogram0-5.xml, patch0-5.xml, GPT, firmware for both slots (= the tested set),
                 *_v3.img (slot A boot chain), super.img (HyperOS 4 WITHOUT Find Device, for the setup wizard)
  super_find.img HyperOS 4 WITH Find Device (written after the setup wizard)
  firehose/      9008 programmer
  zero_64M.bin   to clear metadata and the head of userdata
  manifest.json  sha256 of every file

Every input is checked against the hashes of the reference device; a mismatch stops the build.
Steps are cached in work/: re-running skips what is already done.
"""
import argparse, glob, json, os, platform, re, shutil, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from package.common import *  # noqa: E402,F401

PY = sys.executable
SHA_CACHE_PATH = os.path.join(WORK, '.sha_cache.json')


def load_cache():
    try:
        return json.load(open(SHA_CACHE_PATH))
    except (OSError, ValueError):
        return {}


CACHE = load_cache()


def H(path):
    d = sha256(path, CACHE)
    os.makedirs(WORK, exist_ok=True)
    json.dump(CACHE, open(SHA_CACHE_PATH, 'w'))
    return d


def clone(src, dst):
    """Copy without using extra space where the filesystem allows it."""
    if os.path.lexists(dst):
        os.remove(dst)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if platform.system() == 'Darwin':
        subprocess.run(['cp', '-c', src, dst], check=True)
    else:
        subprocess.run(['cp', '--reflink=auto', src, dst], check=True)


def need(*tools):
    miss = [t for t in tools if not shutil.which(t)]
    if miss:
        die('missing tools: ' + ', '.join(miss) + '\n  macOS: brew install android-platform-tools payload-dumper-go '
            'erofs-utils zstd p7zip apktool openjdk')


def step(n, msg):
    log(f'\n== [{time.strftime("%H:%M:%S")}] {n}. {msg}')


def find_files(root, name):
    out = []
    for r, ds, fs in os.walk(root):
        ds[:] = [d for d in ds if d not in ('package', 'build_os4', 'hyperos4_yupei')]
        if name in fs:
            out.append(os.path.join(r, name))
    return sorted(out)


def first_glob(*patterns):
    for p in patterns:
        m = sorted(glob.glob(p))
        if m:
            return m[-1]
    return None


# ---------------------------------------------------------------- 1. port package
def port_tree(a):
    if a.port_dir:
        return os.path.abspath(a.port_dir)
    z = a.port_zip or first_glob(f'{WORK}/HyperOS3.0.305*TB710FU*.zip', f'{WORK}/*/HyperOS3.0.305*TB710FU*.zip')
    if not z:
        if os.path.isdir(f'{WORK}/HyperOS3.0.305'):
            return f'{WORK}/HyperOS3.0.305'
        die(f'port package not found: put HyperOS3.0.305_A16_TB710FU*.zip (or its extracted folder '
            f'HyperOS3.0.305/) in {WORK}, or pass --port-zip / --port-dir')
    dst = f'{WORK}/port_pkg'
    if not os.path.exists(f'{dst}/.extracted'):
        need('7z')
        log(f'  extracting {z}')
        shutil.rmtree(dst, ignore_errors=True)
        run(['7z', 'x', '-y', f'-o{dst}', z], stdout=subprocess.DEVNULL)
        open(f'{dst}/.extracted', 'w').write(z + '\n')
    return dst


def images_dir_of(tree):
    c = [os.path.dirname(p) for p in find_files(tree, 'rawprogram0.xml')]
    if len(c) != 1:
        die(f'expected exactly one rawprogram0.xml under {tree}, found {c}')
    return c[0]


def supplement_dir(a, tree):
    s = a.supplement or first_glob(f'{WORK}/1.5.04.495_WITHOUT_UBL*.7z', f'{tree}/1.5.04.495_WITHOUT_UBL*.7z',
                                   f'{tree}/*/1.5.04.495_WITHOUT_UBL*.7z')
    if not s:
        return None
    if os.path.isdir(s):
        return s
    dst = f'{WORK}/supplement'
    if not os.path.exists(f'{dst}/.extracted'):
        need('7z')
        shutil.rmtree(dst, ignore_errors=True)
        run(['7z', 'x', '-y', f'-o{dst}', s, '-x!*/EDL/*'], stdout=subprocess.DEVNULL)
        open(f'{dst}/.extracted', 'w').write(s + '\n')
    c = [os.path.dirname(p) for p in find_files(dst, 'vbmeta_system.img')]
    return c[0] if c else None


def firmware_set(images, supp):
    """{filename: path} of the tested 9008 image set; dies on any hash mismatch."""
    want = tested_firmware()
    fw, bad = {}, []
    for n, sha in sorted(want.items()):
        cands = ([os.path.join(supp, n)] if supp and n in SUPPLEMENT_FILES else []) + [os.path.join(images, n)]
        got = None
        for c in cands:
            if os.path.isfile(c) and H(c) == sha:
                got = c
                break
        if got:
            fw[n] = got
        else:
            have = [(c, H(c)[:16]) for c in cands if os.path.isfile(c)]
            bad.append(f'{n}: want {sha[:16]}…, have {have or "missing"}')
    if bad:
        die('9008 images differ from the tested set (wrong port/supplement version?):\n  ' + '\n  '.join(bad) +
            ('' if supp else '\n  (the 1.5.04.495_WITHOUT_UBL supplement was not found: pass --supplement)'))
    return fw


def firehose(tree, images):
    for p in find_files(tree, FIREHOSE) + find_files(tree, 'xbl_s_devprg_ns.melf'):
        if H(p) == FIREHOSE_SHA:
            return p
    die(f'firehose {FIREHOSE} (sha256 {FIREHOSE_SHA[:16]}…) not found in {tree}')


# ---------------------------------------------------------------- 2. v3 boot chain
def v3_chain(a, tree):
    out = f'{WORK}/v3'
    if all(os.path.isfile(f'{out}/{n}') and H(f'{out}/{n}') == s for n, s in V3_SHA.items()):
        log('  work/v3 already built and verified')
        return out
    src = None
    for d in ([a.author_dir] if a.author_dir else []) + [f'{WORK}/author'] + \
            sorted({os.path.dirname(p) for p in find_files(tree, 'boot.img')}):
        if d and all(os.path.isfile(f'{d}/{n}') and H(f'{d}/{n}') == s for n, s in AUTHOR_SHA.items()) \
                and os.path.isfile(f'{d}/vendor_boot.img'):
            src = d
            break
    if not src:
        die("the port author's original boot.img/init_boot.img/dtbo.img/vendor_boot.img were not found "
            f'(boot.img sha256 {AUTHOR_SHA["boot.img"][:16]}…). They are in the port zip; put them in {WORK}/author/ '
            'or pass --author-dir')
    if not (os.path.isfile(f'{TOOLS}/avb/avbtool.py') and os.path.isfile(f'{TOOLS}/avb/testkey_rsa4096.pem')
            and os.path.isfile(f'{TOOLS}/avb/testkey_rsa2048.pem')):
        die('tools/avb/ needs avbtool.py, testkey_rsa4096.pem, testkey_rsa2048.pem (AOSP external/avb)')
    log(f'  author images: {src}')
    run([PY, f'{TOOLS}/make_v3_bootchain.py', src, out])
    bad = [n for n, s in V3_SHA.items() if H(f'{out}/{n}') != s]
    if bad:
        die(f'v3 boot chain differs from the tested one: {bad}')
    return out


# ---------------------------------------------------------------- 3. HyperOS 4 super
def extract_erofs(img, dst):
    if os.path.isdir(dst) and os.listdir(dst):
        return
    shutil.rmtree(dst, ignore_errors=True)
    run(['fsck.erofs', f'--extract={dst}', img], stdout=subprocess.DEVNULL)


def port_super(tree):
    P = f'{WORK}/port'
    os.makedirs(P, exist_ok=True)
    if not all(os.path.isfile(f'{P}/{n}.img') and H(f'{P}/{n}.img') == s for n, s in PORT_SHA.items()) \
            or not os.path.isfile(f'{P}/system_a.img') or not os.path.isfile(f'{P}/product_a.img'):
        sup = f'{P}/super.img'
        if not os.path.isfile(sup):
            z = find_files(tree, 'super.img.zst')
            raw = [p for p in find_files(tree, 'super.img') if os.path.getsize(p) > 1 << 30]
            if z:
                run(['zstd', '-d', '-f', z[0], '-o', sup])
            elif raw:
                clone(raw[0], sup)
            else:
                die(f'the port super (super.img.zst) was not found under {tree}')
        run([PY, f'{TOOLS}/lp_unpack.py', sup, P, 'system_a', 'system_ext_a', 'product_a', *PORT_SHA])
        bad = [n for n, s in PORT_SHA.items() if H(f'{P}/{n}.img') != s]
        if bad:
            die(f'port super: {bad} differ from the tested vendor side (different port version?)')
    for p in ('system', 'product'):
        extract_erofs(f'{P}/{p}_a.img', f'{P}/x_{p}_a')


def yupei(a):
    Y = f'{WORK}/hyperos4_yupei'
    parts = ('system', 'system_ext', 'product', 'mi_ext')
    if not all(os.path.isfile(f'{Y}/img/{p}.img') for p in parts):
        ota = a.ota or first_glob(f'{WORK}/{OTA_GLOB}')
        if not ota:
            die(f'yupei OTA not found: put {OTA_GLOB} in {WORK} or pass --ota')
        os.makedirs(f'{Y}/img', exist_ok=True)
        run(['payload-dumper-go', '-p', ','.join(parts), '-o', f'{Y}/img', ota])
    for p in parts:
        extract_erofs(f'{Y}/img/{p}.img', f'{Y}/x_{p}')


def overlays():
    O = f'{WORK}/overlay'
    apks = {'TB710FUTetheringOverlay.apk': 'src_tether', 'TB710FUConnectivityOverlay.apk': 'src_eth'}
    if all(os.path.isfile(f'{O}/{n}') for n in apks):
        return
    need('apktool')
    jdk = os.environ.get('JAVA_HOME') or next((d for d in ('/opt/homebrew/opt/openjdk', '/usr/local/opt/openjdk')
                                               if os.path.isdir(d)), '')
    jarsigner = shutil.which('jarsigner', path=f'{jdk}/bin') if jdk else None
    keytool = shutil.which('keytool', path=f'{jdk}/bin') if jdk else None
    jarsigner, keytool = jarsigner or shutil.which('jarsigner'), keytool or shutil.which('keytool')
    if not (jarsigner and keytool) or subprocess.run([keytool, '-help'], capture_output=True).returncode:
        die('a JDK (jarsigner, keytool) is needed to sign the overlays: brew install openjdk')
    os.makedirs(O, exist_ok=True)
    ks, pw = f'{WORK}/keys/overlay.jks', f'{WORK}/keys/overlay.pass'
    if not os.path.isfile(ks):
        os.makedirs(os.path.dirname(ks), exist_ok=True)
        open(pw, 'w').write(os.urandom(12).hex())
        os.chmod(pw, 0o600)
        subprocess.run([keytool, '-genkeypair', '-keystore', ks, '-alias', 'TB710FU', '-keyalg', 'RSA',
                        '-keysize', '2048', '-validity', '10000', '-dname', 'CN=TB710FU overlay',
                        '-storepass:file', pw, '-keypass:file', pw], check=True, capture_output=True)
    for n, src in apks.items():
        u = f'{O}/{n}.unsigned'
        tmp = f'{O}/src_{src}'                  # apktool leaves build/ in its input: build from a copy
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(f'{REPO}/hyperos/overlay/{src}', tmp)
        run(['apktool', 'b', tmp, '-o', u], stdout=subprocess.DEVNULL)
        shutil.rmtree(tmp)
        subprocess.run([jarsigner, '-keystore', ks, '-storepass:file', pw, '-sigalg', 'SHA256withRSA',
                        '-digestalg', 'SHA-256', '-sigfile', 'TB710FU', '-signedjar', f'{O}/{n}', u, 'TB710FU'],
                       check=True, capture_output=True)
        os.remove(u)
        log(f'  built {O}/{n}')


def build_super(a, tree):
    need('zstd', 'fsck.erofs', 'mkfs.erofs', 'payload-dumper-go')
    port_super(tree)
    yupei(a)
    overlays()
    B = f'{WORK}/build_os4/out'
    tag = '_debug' if a.debug else ''
    nofind, find = f'{B}/super_nofind{tag}.img', f'{B}/super_find{tag}.img'
    if os.path.isfile(nofind) and os.path.isfile(find) and not a.rebuild:
        log(f'  using existing {nofind} and {find} (--rebuild to build again)')
        return nofind, find
    env = dict(os.environ, TB_WORK=WORK)
    env.pop('TB_NO_FINDDEVICE', None)
    if a.debug:
        env['TB_DEBUG'] = '1'
    run([PY, f'{TOOLS}/build_os4.py'], env=env)                     # system, system_ext, product (with Find Device)
    os.replace(f'{B}/product.img', f'{B}/product_find.img')
    run([PY, f'{TOOLS}/build_os4.py', 'product'], env=dict(env, TB_NO_FINDDEVICE='1'))
    os.replace(f'{B}/product.img', f'{B}/product_nofind.img')
    P = f'{WORK}/port'
    v = [f'vendor={P}/vendor_a.img', f'odm={P}/odm_a.img', f'vendor_dlkm={P}/vendor_dlkm_a.img',
         f'system_dlkm={P}/system_dlkm_a.img', f'system={B}/system.img', f'system_ext={B}/system_ext.img']
    run([PY, f'{TOOLS}/make_super.py', nofind, hex(SUPER_SIZE), f'product={B}/product_nofind.img', *v])
    run([PY, f'{TOOLS}/make_super.py', find, hex(SUPER_SIZE), f'product={B}/product_find.img', *v])
    return nofind, find


def check_super(p):
    with open(p, 'rb') as f:
        f.seek(4096)
        if f.read(4) != b'gDla':
            die(f'{p}: no liblp geometry (not a raw super image)')
    if os.path.getsize(p) > SUPER_SIZE:
        die(f'{p}: larger than the super partition')


# ---------------------------------------------------------------- 4. package
def assemble(a, fw, fh, v3, nofind, find):
    out = os.path.abspath(a.out or f'{WORK}/package/{PKG_NAME}{"-debug" if a.debug else ""}')
    I = f'{out}/images'
    if os.path.exists(out):
        shutil.rmtree(out)
    for n, p in fw.items():
        clone(p, f'{I}/{n}')
    for n in V3_SLOT_A.values():
        clone(f'{v3}/{n}', f'{I}/{n[:-4]}_v3.img')
    clone(nofind, f'{I}/super.img')
    clone(find, f'{out}/super_find.img')
    clone(fh, f'{out}/firehose/{FIREHOSE}')
    with open(f'{out}/zero_64M.bin', 'wb') as f:
        f.truncate(64 << 20)
    # slot A boot chain -> the *_v3.img files; everything else stays as in the port's rawprogram
    seen = set()
    for i in range(6):
        x = f'{I}/rawprogram{i}.xml'
        t = open(x, newline='').read()          # keep the CRLF line ends of the original
        lines = t.split('\n')
        for k, l in enumerate(lines):
            m = re.search(r'label="([^"]+)"', l)
            if m and m.group(1) in V3_SLOT_A and '<program' in l:
                n = V3_SLOT_A[m.group(1)]
                assert f'filename="{n}"' in l, l
                lines[k] = l.replace(f'filename="{n}"', f'filename="{n[:-4]}_v3.img"')
                seen.add(m.group(1))
        os.remove(x)            # it is a clone of the input: replace, never write through
        open(x, 'w', newline='').write('\n'.join(lines))
    if seen != set(V3_SLOT_A):
        die(f'rawprogram: slot A entries not found: {set(V3_SLOT_A) - seen}')
    check_rawprogram_safe(I)
    for x, lun, lab, f, *_ in rawprogram_entries(I):
        if f and not os.path.isfile(f'{I}/{f}'):
            die(f'{x}: {lab} -> {f} missing')
    files = {}
    for r, _, fs in os.walk(out):
        for n in sorted(fs):
            p = os.path.join(r, n)
            rel = os.path.relpath(p, out)
            # the super images are clones of the (cached) build outputs; everything else is hashed as written
            files[rel] = H(nofind) if rel == 'images/super.img' else H(find) if rel == 'super_find.img' else sha256(p)
    man = {
        'name': os.path.basename(out),
        'created': time.strftime('%Y-%m-%d %H:%M:%S'),
        'device': 'TB710FU',
        'debug': bool(a.debug),
        'super_start_sector': SUPER_START,
        'stage1_super': 'images/super.img',
        'stage2_super': 'super_find.img',
        'slot_a_v3': {k: f'images/{v[:-4]}_v3.img' for k, v in V3_SLOT_A.items()},
        'files': files,
    }
    json.dump(man, open(f'{out}/manifest.json', 'w'), indent=1)
    shutil.copyfile(os.path.join(HERE_PKG, 'README.package.md'), f'{out}/README.md')
    return out


HERE_PKG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'package')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--port-zip', help='HyperOS3.0.305_A16_TB710FU*.zip')
    ap.add_argument('--port-dir', help='the extracted port package (instead of --port-zip)')
    ap.add_argument('--supplement', help='1.5.04.495_WITHOUT_UBL*.7z or its extracted folder')
    ap.add_argument('--author-dir', help="folder with the author's original boot/init_boot/dtbo/vendor_boot.img")
    ap.add_argument('--ota', help=OTA_GLOB)
    ap.add_argument('--super-nofind', help='use this prebuilt super (setup wizard) instead of building one')
    ap.add_argument('--super-find', help='use this prebuilt super (with Find Device) instead of building one')
    ap.add_argument('--debug', action='store_true', help='TB_DEBUG build: adb without authorization from early boot')
    ap.add_argument('--rebuild', action='store_true', help='build the super images again even if they exist')
    ap.add_argument('--out', help=f'output folder (default work/package/{PKG_NAME})')
    a = ap.parse_args()
    if bool(a.super_nofind) != bool(a.super_find):
        die('--super-nofind and --super-find go together')
    log(f'work dir: {WORK}')

    step(1, 'port package and 9008 firmware')
    tree = port_tree(a)
    images = images_dir_of(tree)
    supp = supplement_dir(a, tree)
    log(f'  port images: {images}\n  supplement:  {supp or "(not found, using the port images as they are)"}')
    fw = firmware_set(images, supp)
    fh = firehose(tree, images)
    log(f'  {len(fw)} firmware files match the tested set; firehose OK')

    step(2, 'v3 boot chain (slot A)')
    v3 = v3_chain(a, tree)

    step(3, 'HyperOS 4 super images')
    if a.super_nofind:
        nofind, find = os.path.abspath(a.super_nofind), os.path.abspath(a.super_find)
        log('  prebuilt super images (not rebuilt)')
    else:
        nofind, find = build_super(a, tree)
    for p in (nofind, find):
        check_super(p)

    step(4, 'assemble the package')
    out = assemble(a, fw, fh, v3, nofind, find)
    log(f'\npackage: {out}')
    log(f'  stage 1 super (setup wizard, no Find Device): {H(nofind)[:16]}…')
    log(f'  stage 2 super (with Find Device):             {H(find)[:16]}…')
    log(f'next: python3 tools/flash_package.py backup, then python3 tools/flash_package.py full --pkg {out}')


if __name__ == '__main__':
    main()
