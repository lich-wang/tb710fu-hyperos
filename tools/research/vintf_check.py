"""VINTF checks behind docs/porting.md (offline, on the mounts of mount.sh).

usage: sudo python3 vintf_check.py MNT VENDOR_PREFIX FRAMEWORK_PREFIX [VENDOR_SKU [ODM_SKU]]
  e.g. vintf_check.py mnt len_ xm_ pineapple
Manifests are chosen like libvintf: vendor/etc/vintf/manifest_<VENDOR_SKU>.xml (ro.boot.product.vendor.sku) or else
manifest.xml, odm/etc/vintf/manifest_<ODM_SKU>.xml (ro.boot.product.hardware.sku) or else manifest.xml, plus the
manifest/*.xml fragments of each. With several SKU manifests and no SKU given the script stops.

Prints, for the vendor of VENDOR_PREFIX against the framework (system / system_ext / product) of FRAMEWORK_PREFIX:
  1. the vendor's target FCM level and the FCM levels the framework ships matrices for;
  2. android.* HALs of the vendor manifest that the framework matrix of that level does not accept, and HALs that
     matrix requires (optional="false") but the vendor manifest does not serve;
  3. HALs the vendor's device compatibility matrix requires that the framework manifests do not serve.
Requirements are matched per interface and instance (regex-instance too). Version rule (AOSP): a range "2.5-7"
(HIDL) accepts 2.5 and any later minor of major 2, "5-7" (AIDL) accepts 5 or later; the upper end is what the
framework may ask for, not a limit on what the vendor provides. Exit status 1 when any check finds a problem."""
import glob, os, re, sys
import xml.etree.ElementTree as ET

M, V, F = sys.argv[1:4]
VSKU = sys.argv[4] if len(sys.argv) > 4 else None
OSKU = sys.argv[5] if len(sys.argv) > 5 else None
FV = f'{F}system/system/etc/vintf'


def files(*g):
    o = []
    for x in g:
        o += sorted(glob.glob(os.path.join(M, x)))
    return o


def pick(d, sku, what):
    """manifest_<sku>.xml or manifest.xml of directory d (relative to M), plus its fragments."""
    skus = [os.path.basename(p)[9:-4] for p in files(f'{d}/manifest_*.xml')]
    if sku:
        main = files(f'{d}/manifest_{sku}.xml') or files(f'{d}/manifest.xml')
    elif len(skus) > 1:
        sys.exit(f'{d} has manifests for SKUs {skus}: give the {what} SKU')
    else:
        main = files(f'{d}/manifest_*.xml') or files(f'{d}/manifest.xml')
    return main + files(f'{d}/manifest/*.xml')


# ---------------------------------------------------------------- manifests: what is served

def entries(paths):
    """[(format, name, version, interface, instance)] served by these manifests, read in load order (vendor,
    its fragments, odm, its fragments). A HAL with override="true" first removes the earlier declarations it
    replaces, like libvintf: AIDL the same name, HIDL the same name and major version (all majors when it
    declares no version, i.e. disables the HAL)."""
    out = []
    for f in paths:
        for h in ET.parse(f).getroot().iter('hal'):
            fmt, name = h.get('format', 'hidl'), h.findtext('name', '').strip()
            vers = [v.text.strip() for v in h.findall('version')] or (['1'] if fmt == 'aidl' else [''])
            new = []
            for q in h.iter('fqname'):
                s = q.text.strip()
                m = re.fullmatch(r'@([\d.]+)::(\w+)/(.+)', s) or re.fullmatch(r'(?:@(\d+)::)?(\w+)/(.+)', s)
                if m:
                    new.append((fmt, name, m.group(1) or vers[0], m.group(2), m.group(3)))
            for i in h.findall('interface'):
                for inst in i.findall('instance'):
                    for v in vers:
                        new.append((fmt, name, v, i.findtext('name', '').strip(), inst.text.strip()))
            if not h.findall('fqname') and not h.findall('interface') and h.findall('version'):
                for v in vers:
                    new.append((fmt, name, v, None, None))
            if h.get('override') == 'true':
                # majors from the explicit <version> elements and from the instances' own versions
                majors = {v.split('.')[0] for v in (x.text.strip() for x in h.findall('version')) if v}
                majors |= {e[2].split('.')[0] for e in new if e[2]}
                out = [e for e in out if not (e[0] == fmt and e[1] == name and
                                              (fmt == 'aidl' or not majors or e[2].split('.')[0] in majors))]
            out += new
    return out


def target_level(paths):
    level = None
    for f in paths:
        level = ET.parse(f).getroot().get('target-level') or level
    return level


# ---------------------------------------------------------------- matrices: what is required

def matrix(path):
    """[{fmt, name, ranges, optional, needs: [(interface, instance, is_regex)]}]"""
    out = []
    for h in ET.parse(path).getroot().iter('hal'):
        fmt = h.get('format', 'hidl')
        ranges = [v.text.strip() for v in h.findall('version')] or (['1'] if fmt == 'aidl' else [])
        needs = []
        for i in h.findall('interface'):
            n = i.findtext('name', '').strip()
            needs += [(n, x.text.strip(), False) for x in i.findall('instance')]
            needs += [(n, x.text.strip(), True) for x in i.findall('regex-instance')]
        out.append({'fmt': fmt, 'name': h.findtext('name', '').strip(), 'ranges': ranges,
                    'optional': h.get('optional', 'false') != 'false', 'needs': needs})
    return out


def accepts(fmt, v, ranges):
    if not ranges:
        return True
    for r in ranges:
        lo = r.partition('-')[0]
        try:
            if fmt == 'aidl':
                if int(v) >= int(lo):
                    return True
            else:
                vm, vn = map(int, v.split('.'))
                lm, ln = map(int, lo.split('.'))
                if vm == lm and vn >= ln:
                    return True
        except ValueError:
            continue
    return False


def unmet(req, served):
    """Requirements of one matrix HAL that no served entry satisfies -> [str]."""
    mine = [e for e in served if e[0] == req['fmt'] and e[1] == req['name'] and accepts(e[0], e[2], req['ranges'])]
    if not req['needs']:
        return [] if mine else [f'{req["name"]} {req["ranges"]}']
    bad = []
    for iface, inst, rx in req['needs']:
        ok = any(e[3] == iface and e[4] is not None and (re.fullmatch(inst, e[4]) if rx else e[4] == inst)
                 for e in mine)
        if not ok:
            bad.append(f'{req["fmt"]} {req["name"]} {req["ranges"]} {iface}/{inst}{" (regex)" if rx else ""}')
    return bad


problems = 0
vm = pick(f'{V}vendor/etc/vintf', VSKU, 'vendor') + pick(f'{V}odm/etc/vintf', OSKU, 'odm')
level = target_level(vm)
names = [os.path.basename(p) for p in files(f'{FV}/compatibility_matrix.*.xml')]
levels = [re.match(r'compatibility_matrix\.(\w+)\.xml$', n).group(1) for n in names
          if re.match(r'compatibility_matrix\.\w+\.xml$', n) and 'device' not in n]
levels.sort(key=lambda s: (not s.isdigit(), int(s) if s.isdigit() else 0, s))
print(f'vendor manifests: {", ".join(os.path.relpath(p, M) for p in vm)}')
print(f'1. {V}vendor target-level {level}; {F} framework FCM levels: {", ".join(levels)}')
if level not in levels:
    sys.exit(f'   target-level {level} is not among the framework matrices')

served = entries(vm)
fcm = matrix(os.path.join(M, FV, f'compatibility_matrix.{level}.xml'))
by_name = {}
for r in fcm:
    by_name.setdefault((r['fmt'], r['name']), []).extend(r['ranges'])
android = sorted({(e[0], e[1], e[2]) for e in served if e[1].startswith('android.')})
hals = sorted({(f, n) for f, n, _ in android})
rejected = []
for f, n, v in android:
    if (f, n) not in by_name:
        rejected.append(f'{f} {n} {v}: not in FCM {level}')
    elif not accepts(f, v, by_name[(f, n)]):
        rejected.append(f'{f} {n} {v}: FCM {level} has {by_name[(f, n)]}')
missing_fcm = [m for r in fcm if not r['optional'] for m in unmet(r, served)]
bad_hals = {tuple(x.split()[:2]) for x in rejected}
print(f'2. vendor android.* HALs accepted by FCM {level}: {len(hals) - len(bad_hals)}/{len(hals)}; '
      f'required by FCM {level} but not served by the vendor: {len(missing_fcm)}')
for x in rejected + missing_fcm:
    print('  ', x)
problems += len(rejected) + len(missing_fcm)

fw = entries(files(f'{FV}/manifest.xml', f'{FV}/manifest/*.xml', f'{F}system_ext/etc/vintf/manifest.xml',
                   f'{F}system_ext/etc/vintf/manifest/*.xml', f'{F}product/etc/vintf/manifest.xml',
                   f'{F}product/etc/vintf/manifest/*.xml'))
missing_fw = []
for path in files(f'{V}vendor/etc/vintf/compatibility_matrix.xml', f'{V}odm/etc/vintf/compatibility_matrix.xml'):
    missing_fw += [m for r in matrix(path) if not r['optional'] for m in unmet(r, fw)]
print(f'3. required by the vendor device matrix but not served by the framework manifests: {len(missing_fw)}')
for x in missing_fw:
    print('  ', x)
problems += len(missing_fw)
sys.exit(1 if problems else 0)
