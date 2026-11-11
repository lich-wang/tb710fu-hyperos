"""Are the vendor's android.* HAL versions accepted by the framework FCM for its target level? (docs/porting.md)
usage: sudo python3 vintf_check.py MNT VENDOR_PREFIX FRAMEWORK_SYSTEM_ETC_VINTF"""
import glob, os, re, sys
import xml.etree.ElementTree as ET
M, V, FV = sys.argv[1:4]

def files(*g):
    o = []
    for x in g: o += sorted(glob.glob(os.path.join(M, x)))
    return o

def vendor_hals():
    out, level = {}, None
    for f in files(f'{V}vendor/etc/vintf/manifest.xml', f'{V}vendor/etc/vintf/manifest_pineapple.xml',
                   f'{V}vendor/etc/vintf/manifest/*.xml', f'{V}odm/etc/vintf/manifest*.xml', f'{V}odm/etc/vintf/manifest/*.xml'):
        r = ET.parse(f).getroot(); level = r.get('target-level') or level
        for h in r.iter('hal'):
            fmt, name = h.get('format', 'hidl'), h.findtext('name', '').strip()
            if not name.startswith('android.'): continue
            vers = [v.text.strip() for v in h.findall('version')]
            vers += [re.match(r'@([\d.]+)', q.text.strip()).group(1) for q in h.iter('fqname') if q.text.strip().startswith('@')]
            if fmt == 'aidl' and not vers: vers = ['1']
            for v in vers: out.setdefault((fmt, name), set()).add((v, os.path.basename(f)))
    return out, level

def fcm(level):
    out = {}
    r = ET.parse(os.path.join(M, FV, f'compatibility_matrix.{level}.xml')).getroot()
    for h in r.iter('hal'):
        fmt, name = h.get('format', 'hidl'), h.findtext('name', '').strip()
        for v in h.findall('version'):
            out.setdefault((fmt, name), []).append(v.text.strip())
        if fmt == 'aidl' and not h.findall('version'): out.setdefault((fmt, name), []).append('1')
    return out

def ok(fmt, v, ranges):
    for r in ranges:
        lo, _, hi = r.partition('-')
        if fmt == 'aidl':
            if int(lo) <= int(v) <= int(hi or lo): return True
        else:
            vm, vn = map(int, v.split('.')); lm, ln = map(int, lo.split('.'))
            hn = int(hi) if hi else ln
            if vm == lm and ln <= vn <= hn: return True
    return False

vh, level = vendor_hals()
fm = fcm(level)
print(f'{V}: target-level {level}, {len(vh)} android.* HALs')
bad = 0
for k, vs in sorted(vh.items()):
    if k not in fm:
        print('  NOT IN FCM', k, sorted(vs)); bad += 1; continue
    for v, f in sorted(vs):
        if not ok(k[0], v, fm[k]):
            print('  VERSION', k, v, 'FCM', fm[k], f); bad += 1
print('problems:', bad)
