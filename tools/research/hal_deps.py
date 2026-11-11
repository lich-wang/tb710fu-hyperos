"""Can yupei's Xiaomi HAL binaries run on the Lenovo vendor? (docs/porting.md)

usage: sudo python3 hal_deps.py MNT   (len_/xm_ mounts, see mount.sh)
Resolves NEEDED transitively against Lenovo vendor/odm + VNDK v34 apex + LLNDK (libraries only yupei has are
pulled in and listed), then checks every undefined dynamic symbol against the providers (bionic from the runtime apex)."""
import glob, os, re, subprocess, sys, zipfile, tempfile
M = sys.argv[1]
def names(pat): return {os.path.basename(p): p for p in glob.glob(pat, recursive=True)}
lenlib = {**names(f'{M}/len_vendor/lib64/**/*.so'), **names(f'{M}/len_odm/lib64/**/*.so')}
ylib = {**names(f'{M}/xm_vendor/lib64/**/*.so'), **names(f'{M}/xm_odm/lib64/**/*.so')}
# VNDK v34 apex libs
t = tempfile.mkdtemp()
ap = glob.glob(f'{M}/xm_system_ext/apex/com.android.vndk.v34.apex')[0]
z = zipfile.ZipFile(ap); z.extract('apex_payload.img', t)
os.makedirs(f'{t}/m', exist_ok=True)
subprocess.run(['mount', '-o', 'ro,loop', f'{t}/apex_payload.img', f'{t}/m'], check=True)
vndk = set(os.listdir(f'{t}/m/lib64'))
llndk = set()
for f in glob.glob(f'{M}/xm_system/system/etc/*llndk*.txt') + glob.glob(f'{t}/m/etc/*llndk*.txt'):
    llndk |= set(open(f).read().split())
llndk |= {'libc.so', 'libm.so', 'libdl.so', 'liblog.so', 'libandroid_net.so', 'libvndksupport.so', 'libbinder_ndk.so',
          'libsync.so', 'libnativewindow.so', 'libvulkan.so', 'libEGL.so', 'libGLESv2.so', 'libGLESv3.so', 'libGLESv1_CM.so',
          'libmediandk.so', 'libft2.so', 'libselinux.so', 'libz.so'}
def needed(p):
    return re.findall(r'\(NEEDED\).*\[(.+?)\]', subprocess.run(['readelf', '-d', p], capture_output=True, text=True).stdout)
roots = ['bin/hw/vendor.xiaomi.hardware.displayfeature_aidl-service', 'bin/displayfeature',
         'bin/hw/vendor.xiaomi.hardware.micharge-service', 'bin/hw/vendor.xiaomi.hardware.misys.core-service',
         'bin/hypsys_vendor']
for r in roots:
    p = f'{M}/xm_vendor/{r}'
    if not os.path.exists(p):
        print(f'{r}: not in yupei vendor'); continue
    pulled, missing, seen, todo = [], [], set(), needed(p)
    while todo:
        n = todo.pop()
        if n in seen: continue
        seen.add(n)
        if n in lenlib or n in vndk or n in llndk: continue
        if n in ylib:
            pulled.append(n); todo += needed(ylib[n])
        else:
            missing.append(n)
    print(f'{r}\n   from yupei: {sorted(pulled)}\n   UNRESOLVED: {sorted(missing)}')
subprocess.run(['umount', f'{t}/m'])

# bionic (libc/libm/libdl) lives in the runtime apex
rt = glob.glob(f'{M}/xm_system/system/apex/com.android.runtime*.apex')[0]
zipfile.ZipFile(rt).extract('apex_payload.img', t + '/rt'); os.makedirs(t + '/rtm', exist_ok=True)
subprocess.run(['mount', '-o', 'ro,loop', t + '/rt/apex_payload.img', t + '/rtm'], check=True)
BIONIC = set()
for b in glob.glob(t + '/rtm/lib64/bionic/*.so'):
    out = subprocess.run(['nm', '-D', '--defined-only', b], capture_output=True, text=True).stdout
    BIONIC |= {l.split()[-1].split('@')[0] for l in out.splitlines() if l.strip()}
subprocess.run(['umount', t + '/rtm'])
# ---- symbol check: undefined dynamic symbols of each yupei object vs what the resolved providers export
subprocess.run(['mount', '-o', 'ro,loop', f'{t}/apex_payload.img', f'{t}/m'], check=True)
def prov(n):
    for d in (lenlib, ylib):
        if n in d and (d is lenlib or n not in lenlib and n not in os.listdir(f'{t}/m/lib64')): return d[n]
    if os.path.exists(f'{t}/m/lib64/{n}'): return f'{t}/m/lib64/{n}'
    for c in (f'{M}/xm_system/system/lib64/{n}',):
        if os.path.exists(c): return c
    return None
def syms(p, undefined):
    flag = '--undefined-only' if undefined else '--defined-only'
    out = subprocess.run(['nm', '-D', flag, p], capture_output=True, text=True).stdout
    return {l.split()[-1].split('@')[0] for l in out.splitlines() if l.strip()}
for r in roots:
    p = f'{M}/xm_vendor/{r}'
    objs, seen, todo = [p], set(), needed(p)
    while todo:
        n = todo.pop()
        if n in seen: continue
        seen.add(n)
        if n in ylib and n not in lenlib and n not in os.listdir(f'{t}/m/lib64'):
            objs.append(ylib[n]); todo += needed(ylib[n])
    exported = set(BIONIC)
    for o in objs:
        for n in needed(o):
            q = prov(n)
            if q: exported |= syms(q, False)
        exported |= syms(o, False)
    und = set()
    for o in objs: und |= syms(o, True)
    miss = sorted(s for s in und - exported if not s.startswith(('__cxa', '_ITM', '__gmon', '__register', '__deregister')))
    print(f'{r}: {len(objs)} objects, {len(und)} undefined symbols, unresolved {len(miss)}', miss[:12])
subprocess.run(['umount', f'{t}/m'])
