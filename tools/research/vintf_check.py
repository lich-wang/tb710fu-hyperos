"""VINTF checks behind docs/porting.md (offline, on the mounts of mount.sh).

usage: sudo python3 vintf_check.py MNT VENDOR_PREFIX FRAMEWORK_PREFIX
  e.g. vintf_check.py mnt len_ xm_
Prints, for the vendor of VENDOR_PREFIX against the framework (system / system_ext / product) of FRAMEWORK_PREFIX:
  1. the vendor's target FCM level and the FCM levels the framework ships matrices for;
  2. whether every android.* HAL in the vendor manifests is accepted by the framework matrix of that level;
  3. which HALs the vendor's device compatibility matrix requires that no framework manifest provides.
Version rule (AOSP): a matrix range "2.5-7" (HIDL) accepts 2.5 and any later minor of major 2, "5-7" (AIDL)
accepts 5 or later; the upper end is what the framework may ask for, not a limit on what the vendor provides."""
import glob, os, re, sys
import xml.etree.ElementTree as ET

M, V, F = sys.argv[1:4]
FV = f'{F}system/system/etc/vintf'


def files(*g):
    o = []
    for x in g:
        o += sorted(glob.glob(os.path.join(M, x)))
    return o


def hals_of(paths, only_android=False):
    """{(format, name): {(version, file)}} from manifest files."""
    out = {}
    for f in paths:
        r = ET.parse(f).getroot()
        for h in r.iter('hal'):
            fmt, name = h.get('format', 'hidl'), h.findtext('name', '').strip()
            if only_android and not name.startswith('android.'):
                continue
            vers = [v.text.strip() for v in h.findall('version')]
            vers += [re.match(r'@([\d.]+)', q.text.strip()).group(1) for q in h.iter('fqname')
                     if q.text.strip().startswith('@')]
            if fmt == 'aidl' and not vers:
                vers = ['1']
            for v in vers or ['']:
                out.setdefault((fmt, name), set()).add((v, os.path.basename(f)))
    return out


def vendor_manifests():
    return files(f'{V}vendor/etc/vintf/manifest.xml', f'{V}vendor/etc/vintf/manifest_*.xml',
                 f'{V}vendor/etc/vintf/manifest/*.xml', f'{V}odm/etc/vintf/manifest*.xml', f'{V}odm/etc/vintf/manifest/*.xml')


def framework_manifests():
    return files(f'{FV}/manifest.xml', f'{FV}/manifest/*.xml', f'{F}system_ext/etc/vintf/manifest.xml',
                 f'{F}system_ext/etc/vintf/manifest/*.xml', f'{F}product/etc/vintf/manifest.xml',
                 f'{F}product/etc/vintf/manifest/*.xml')


def target_level(paths):
    level = None
    for f in paths:
        level = ET.parse(f).getroot().get('target-level') or level
    return level


def matrix(path):
    """{(format, name): [version ranges]} of a compatibility matrix, plus the HALs marked optional="false"."""
    out, required = {}, set()
    for h in ET.parse(path).getroot().iter('hal'):
        fmt, name = h.get('format', 'hidl'), h.findtext('name', '').strip()
        vs = [v.text.strip() for v in h.findall('version')] or (['1'] if fmt == 'aidl' else [])
        out.setdefault((fmt, name), []).extend(vs)
        if h.get('optional', 'false') == 'false':
            required.add((fmt, name))
    return out, required


def accepts(fmt, v, ranges):
    for r in ranges:
        lo = r.partition('-')[0]
        if fmt == 'aidl':
            if int(v) >= int(lo):
                return True
        else:
            vm, vn = map(int, v.split('.'))
            lm, ln = map(int, lo.split('.'))
            if vm == lm and vn >= ln:
                return True
    return False


vm = vendor_manifests()
level = target_level(vm)
names = [os.path.basename(p) for p in files(f'{FV}/compatibility_matrix.*.xml')]
levels = [re.match(r'compatibility_matrix\.(\w+)\.xml$', n).group(1) for n in names
          if re.match(r'compatibility_matrix\.\w+\.xml$', n) and 'device' not in n]
levels.sort(key=lambda s: (not s.isdigit(), int(s) if s.isdigit() else 0, s))
print(f'1. {V}vendor target-level {level}; {F} framework FCM levels: {", ".join(levels)}')
if level not in levels:
    print(f'   target-level {level} is not among the framework matrices')

vh = hals_of(vm, only_android=True)
fm, _ = matrix(os.path.join(M, FV, f'compatibility_matrix.{level}.xml'))
bad = 0
for k, vs in sorted(vh.items()):
    if k not in fm:
        print('   NOT IN FCM', k, sorted(vs))
        bad += 1
        continue
    for v, f in sorted(vs):
        if not accepts(k[0], v, fm[k]):
            print('   VERSION', k, v, 'FCM', fm[k], f)
            bad += 1
print(f'2. vendor android.* HALs accepted by FCM {level}: {len(vh) - bad}/{len(vh)}')

dcm = files(f'{V}vendor/etc/vintf/compatibility_matrix.xml', f'{V}odm/etc/vintf/compatibility_matrix.xml')
fh = hals_of(framework_manifests())
missing = []
for path in dcm:
    req_m, required = matrix(path)
    for k in sorted(required):
        have = {v for v, _ in fh.get(k, set())}
        if not have or (req_m[k] and not any(accepts(k[0], v, req_m[k]) for v in have if v)):
            missing.append((k, req_m[k], sorted(have)))
print(f'3. HALs the vendor device matrix requires that the framework manifests do not provide: {len(missing)}')
for k, want, have in missing:
    print('  ', k, 'wants', want, 'framework has', have or '-')
