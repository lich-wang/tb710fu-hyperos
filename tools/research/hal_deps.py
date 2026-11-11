"""Can yupei's Xiaomi HAL binaries run on the Lenovo vendor? (docs/porting.md)

usage: sudo python3 hal_deps.py MNT   (len_/xm_ mounts, see mount.sh)
Resolves NEEDED transitively (through every provider: Lenovo vendor/odm, VNDK v34 apex, LLNDK/system, and
libraries only yupei has, which are pulled in and listed), then checks every strong undefined dynamic symbol of
the yupei objects against what that object's resolved providers export (bionic libraries only when they are
in its NEEDED closure, from the runtime apex). Weak undefined symbols may stay unresolved and are not reported.
A failing readelf / nm stops the script; a dependency that cannot be read counts as a failure."""
import glob, os, re, shutil, subprocess, sys, tempfile, zipfile

M = sys.argv[1]


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode:
        sys.exit(f'{" ".join(cmd)} failed ({p.returncode}): {p.stderr.strip()[:300]}')
    return p.stdout


def names(pat):
    return {os.path.basename(p): p for p in glob.glob(pat, recursive=True)}


def needed(p):
    return re.findall(r'\(NEEDED\).*\[(.+?)\]', run(['readelf', '-d', p]))


def nm(p, undefined):
    """undefined=True -> {strong undefined symbols}; False -> {defined symbols}."""
    out = run(['nm', '-D', '--undefined-only' if undefined else '--defined-only', p])
    syms = set()
    for line in out.splitlines():
        f = line.split()
        if len(f) < 2:
            continue
        kind, name = f[-2], f[-1].split('@')[0]
        if undefined and kind in ('w', 'v'):       # weak: allowed to stay unresolved
            continue
        syms.add(name)
    return syms


lenlib = {**names(f'{M}/len_vendor/lib64/**/*.so'), **names(f'{M}/len_odm/lib64/**/*.so')}
ylib = {**names(f'{M}/xm_vendor/lib64/**/*.so'), **names(f'{M}/xm_odm/lib64/**/*.so')}
roots = ['bin/hw/vendor.xiaomi.hardware.displayfeature_aidl-service', 'bin/displayfeature',
         'bin/hw/vendor.xiaomi.hardware.micharge-service', 'bin/hw/vendor.xiaomi.hardware.misys.core-service',
         'bin/hypsys_vendor']

t = tempfile.mkdtemp()
mounted = []


def mount_apex(apex, name):
    d = os.path.join(t, name)
    os.makedirs(d + '.img')
    zipfile.ZipFile(apex).extract('apex_payload.img', d + '.img')
    os.makedirs(d)
    subprocess.run(['mount', '-o', 'ro,loop', os.path.join(d + '.img', 'apex_payload.img'), d], check=True)
    mounted.append(d)
    return d


failed = False
try:
    vd = mount_apex(glob.glob(f'{M}/xm_system_ext/apex/com.android.vndk.v34.apex')[0], 'vndk')
    rd = mount_apex(glob.glob(f'{M}/xm_system/system/apex/com.android.runtime*.apex')[0], 'runtime')
    vndk = {n: os.path.join(vd, 'lib64', n) for n in os.listdir(os.path.join(vd, 'lib64'))}
    llndk = set()
    for f in glob.glob(f'{M}/xm_system/system/etc/*llndk*.txt') + glob.glob(f'{vd}/etc/*llndk*.txt'):
        llndk |= set(open(f).read().split())
    llndk |= {'libc.so', 'libm.so', 'libdl.so', 'liblog.so', 'libandroid_net.so', 'libvndksupport.so',
              'libbinder_ndk.so', 'libsync.so', 'libnativewindow.so', 'libvulkan.so', 'libEGL.so', 'libGLESv2.so',
              'libGLESv3.so', 'libGLESv1_CM.so', 'libmediandk.so', 'libft2.so', 'libselinux.so', 'libz.so'}
    bionic = {os.path.basename(b): b for b in glob.glob(f'{rd}/lib64/bionic/*.so')}

    def provider(n):
        """(path or None, origin) of the library a vendor process would load for NEEDED n."""
        if n in lenlib:
            return lenlib[n], 'lenovo'
        if n in vndk:
            return vndk[n], 'vndk'
        if n in llndk:
            p = bionic.get(n) or (f'{M}/xm_system/system/lib64/{n}'
                                  if os.path.exists(f'{M}/xm_system/system/lib64/{n}') else None)
            return p, 'llndk'
        if n in ylib:
            return ylib[n], 'yupei'
        return None, 'missing'

    for r in roots:
        p = f'{M}/xm_vendor/{r}'
        if not os.path.exists(p):
            print(f'{r}: not in yupei vendor')
            failed = True
            continue
        pulled, missing, unreadable, seen, todo = [], [], [], set(), needed(p)
        objs, provs = [p], {}
        while todo:
            n = todo.pop()
            if n in seen:
                continue
            seen.add(n)
            q, origin = provider(n)
            if origin == 'missing':
                missing.append(n)
                continue
            if not q:
                unreadable.append(n)      # an LLNDK name without a file to read here
                continue
            provs[n] = q
            if origin == 'yupei':
                pulled.append(n)
                objs.append(q)
            todo += needed(q)             # keep walking through every provider
        exported = set()
        for q in provs.values():
            exported |= nm(q, False)
        for o in objs:
            exported |= nm(o, False)
        und = set()
        for o in objs:
            und |= nm(o, True)
        miss = sorted(und - exported)
        failed |= bool(missing or miss or unreadable)
        print(f'{r}\n   from yupei: {sorted(pulled)}\n   UNRESOLVED libraries: {sorted(missing)}'
              + (f'\n   NOT CHECKED (no file to read): {sorted(unreadable)}' if unreadable else '')
              + f'\n   {len(objs)} objects, {len(und)} strong undefined symbols, unresolved {len(miss)} {miss[:12]}')
finally:
    for d in reversed(mounted):
        if subprocess.run(['umount', d]).returncode:
            print(f'could not umount {d}; leaving {t}', file=sys.stderr)
            break
    else:
        shutil.rmtree(t, ignore_errors=True)
sys.exit(1 if failed else 0)
