"""Xiaomi Camera (MiuiCamera.apk) front camera fix for the Lenovo camera HAL (#48).

Xiaomi Camera finds its cameras through a role table (role id -> camera id) that it fills only from Xiaomi's vendor
tags com.xiaomi.cameraid.role.cameraIds / .cameraId. The Lenovo CamX HAL does not publish them, so every camera gets
role -1, the front role (1) resolves to camera -1 and the front camera never opens ("无法连接到相机"). The HAL itself
is fine: camera 0 is the main back camera (txd_ov13b10) and camera 1 the front one (lce_gc13a2).

Two edits to the official APK, both located by stable anchors (log strings, unobfuscated class names), never by the
obfuscated class names, and refused when an anchor is missing or ambiguous:

  1. role table: where the role adapter fills its SparseIntArray table (the method that logs "All available camera
     ids"), add, just before the synchronized block ends, "role 1 -> camera 1" and "role 0 -> camera 0" when those
     roles are missing. A HAL that does publish the tags keeps its own values; the accessors stay unchanged.
  2. MIVILutSaver.testPermission(): its write test into Xiaomi's persist layout always fails on the Lenovo persist
     (which must not be written) and shows "权限受限，请联系研发". The single call is replaced by "true".

The APK is then rebuilt with apktool, zip-aligned and signed with the AOSP test key (tools/apksign/, see
tools/THIRD_PARTY.md); its prebuilt oat/ (compiled against the original dex) is dropped by the caller.
Needs Java, apktool, zipalign and apksigner (Android SDK build-tools).
"""
import glob, os, re, shutil, subprocess

from .sources import TOOLS, SourceError

ROLE_TAG = 'Camera2CompatAdapterRole'
ROLE_ANCHOR = 'All available camera ids'
LUT_CALL = 'Lcom/xiaomi/camera/mivi/filter/MIVILutSaver;->testPermission()Z'
KEY_DIR = os.path.join(TOOLS, 'apksign')
# The fixed role -> camera ids of TB710FU (verified on the device: CAM_START_DEV of each sensor).
ROLES = ((1, 1), (0, 0))
MARK = '# tb710fu (#48)'


def _methods(text):
    """(start, end) character spans of every .method ... .end method block."""
    return [(m.start(), m.end()) for m in re.finditer(r'^\.method .*?^\.end method$', text, re.M | re.S)]


def patch_role_table(text):
    """Return the role adapter smali with the missing roles filled in, or None when this file is not the adapter.

    >>> src = '''.class public final Lr6/e;
    ... .field public volatile h:Landroid/util/SparseIntArray;
    ... .method public final a(Z)V
    ...     .locals 12
    ...     const-string v0, "Camera2CompatAdapterRole"
    ...     const-string v0, "All available camera ids: "
    ...     monitor-enter p0
    ...     iput-object v0, p0, Lr6/e;->h:Landroid/util/SparseIntArray;
    ...     :cond_8
    ...     monitor-exit p0
    ...     return-void
    ... .end method'''
    >>> out = patch_role_table(src)
    >>> '.locals 14' in out and 'indexOfKey' in out and out.index('indexOfKey') < out.index('monitor-exit p0')
    True
    >>> patch_role_table(out) == out       # already patched: unchanged
    True
    >>> patch_role_table('.class public final La;') is None
    True
    """
    if ROLE_TAG not in text or ROLE_ANCHOR not in text:
        return None
    if MARK in text:
        return text
    cls = re.search(r'^\.class .*?(L[^;\s]+;)\s*$', text, re.M).group(1)
    hits = []
    for s, e in _methods(text):
        body = text[s:e]
        if ROLE_ANCHOR not in body:
            continue
        put = re.search(r'^\s*iput-object v\d+, p0, %s->(\w+):Landroid/util/SparseIntArray;\s*$' % re.escape(cls),
                        body, re.M)
        if not put:
            continue
        ex = re.search(r'^(\s*)monitor-exit p0\s*$', body[put.end():], re.M)
        if not ex:
            continue
        hits.append((s, e, put.group(1), put.end() + ex.start()))
    if len(hits) != 1:
        raise SourceError(f'小米相机：找到 {len(hits)} 处角色表构建位置（应为 1）：这个版本没核对过，不打补丁')
    s, e, field, at = hits[0]
    body = text[s:e]
    loc = re.search(r'^(\s*)\.locals (\d+)\s*$', body, re.M)
    nloc = int(loc.group(2))
    params = _param_registers(body.split('\n', 1)[0])
    # two fresh registers above every existing local; 4-bit operands (35c invoke, 22c iget) need them below 16,
    # and the parameter registers move up by two with them
    if nloc + 2 + params > 16:
        raise SourceError(f'小米相机：角色表方法的寄存器太多（locals {nloc} + 参数 {params}），这个版本没核对过')
    a, b = f'v{nloc}', f'v{nloc + 1}'
    ind = loc.group(1)
    code = [f'{ind}{MARK}: roles the Lenovo HAL does not publish', f'{ind}iget-object {a}, p0, {cls}->{field}:Landroid/util/SparseIntArray;']
    for role, cam in ROLES:
        done = f':tb710fu_role{role}'
        code += [f'{ind}const/4 {b}, {role:#x}',
                 f'{ind}invoke-virtual {{{a}, {b}}}, Landroid/util/SparseIntArray;->indexOfKey(I)I',
                 f'{ind}move-result {b}',
                 f'{ind}if-gez {b}, {done}',
                 f'{ind}const/4 {b}, {role:#x}']
        code += ([f'{ind}invoke-virtual {{{a}, {b}, {b}}}, Landroid/util/SparseIntArray;->put(II)V'] if role == cam else
                 [f'{ind}const/4 {a}, {cam:#x}', f'{ind}invoke-virtual {{{a}, {b}, {a}}}, Landroid/util/SparseIntArray;->put(II)V'])
        code += [f'{ind}{done}']
    body = body[:at] + '\n'.join(code) + '\n' + body[at:]
    body = body[:loc.start()] + f'{ind}.locals {nloc + 2}' + body[loc.end():]
    return text[:s] + body + text[e:]


def _param_registers(decl):
    """Registers taken by the parameters of a .method line (p0 included for non-static methods)."""
    sig = re.search(r'\((.*?)\)', decl).group(1)
    n = 0 if ' static ' in f' {decl} ' else 1
    for t in re.findall(r'\[*(?:L[^;]+;|[ZBSCIJFD])', sig):
        n += 2 if t in ('J', 'D') else 1
    return n


def patch_lut_check(text):
    """Replace the MIVILutSaver.testPermission() call and its move-result with "true"; None when absent.

    >>> src = '''    invoke-static {}, Lcom/xiaomi/camera/mivi/filter/MIVILutSaver;->testPermission()Z
    ...
    ...     move-result p0
    ...
    ...     if-nez p0, :cond_1'''
    >>> out = patch_lut_check(src)
    >>> 'testPermission' in out, 'const/16 p0, 0x1' in out
    (False, True)
    """
    if LUT_CALL not in text:
        return None
    pat = re.compile(r'^(\s*)invoke-static \{\}, %s\s*\n(?:\s*\n)*\s*move-result ([vp]\d+)\s*$' % re.escape(LUT_CALL), re.M)
    hits = pat.findall(text)
    if len(hits) != 1 or text.count(LUT_CALL) != 1:
        raise SourceError(f'小米相机：MIVILutSaver.testPermission() 调用有 {text.count(LUT_CALL)} 处（应为 1）：不打补丁')
    ind, reg = hits[0]
    # const/16 has an 8-bit register field, so any v / p register of the method fits
    return pat.sub(f'{ind}{MARK}: the persist write test cannot pass on the Lenovo persist\n{ind}const/16 {reg}, 0x1', text)


def patch_tree(decoded):
    """Patch an apktool-decoded tree in place; returns the two patched smali paths (relative)."""
    found = {'role': [], 'lut': []}
    for path in sorted(glob.glob(os.path.join(decoded, 'smali*', '**', '*.smali'), recursive=True)):
        text = open(path, encoding='utf-8').read()
        for key, fn in (('role', patch_role_table), ('lut', patch_lut_check)):
            out = fn(text)
            if out is None:
                continue
            found[key].append(os.path.relpath(path, decoded))
            if out != text:
                open(path, 'w', encoding='utf-8').write(out)
                text = out
    for key, what in (('role', '角色表'), ('lut', 'MIVILutSaver.testPermission()')):
        if len(found[key]) != 1:
            raise SourceError(f'小米相机：{what} 应在 1 个文件里，找到 {len(found[key])} 个：这个版本没核对过，不打补丁')
    return found['role'][0], found['lut'][0]


# ---------------------------------------------------------------- build

def find(name):
    """Path of java / apktool / zipalign / apksigner, or None (macOS's /usr/bin/java stub does not count)."""
    if name == 'java':
        home = os.environ.get('JAVA_HOME')
        cands = ([os.path.join(home, 'bin', 'java')] if home else []) + [shutil.which('java')] + \
            ['/opt/homebrew/opt/openjdk/bin/java', '/usr/local/opt/openjdk/bin/java']
        return next((c for c in cands if c and os.access(c, os.X_OK) and _java_ok(c)), None)
    return shutil.which(name) or _sdk_tool(name)


def tool_env():
    """Environment with the found Java first on PATH (apktool and the SDK's apksigner script start java)."""
    env = dict(os.environ)
    java = find('java')
    if java:
        env['PATH'] = os.path.dirname(java) + os.pathsep + env.get('PATH', '')
        env.setdefault('JAVA_HOME', os.path.dirname(os.path.dirname(os.path.realpath(java))))
    return env


def tools():
    """Paths of java, apktool, zipalign, apksigner; SourceError naming what is missing."""
    found, missing = {}, []
    for n in ('java', 'apktool', 'zipalign', 'apksigner'):
        p = find(n)
        if p:
            found[n] = p
        else:
            missing.append(n)
    if missing:
        raise SourceError('小米相机前摄修复要 ' + '、'.join(missing) +
                          '：JDK + apktool；zipalign / apksigner 在 Android SDK build-tools 里（见 docs/testing.md）')
    return found


def _sdk_tool(name):
    roots = [os.environ.get('ANDROID_HOME'), os.environ.get('ANDROID_SDK_ROOT'),
             os.path.expanduser('~/Library/Android/sdk'), '/opt/homebrew/share/android-commandlinetools']
    for r in filter(None, roots):
        cands = sorted(glob.glob(os.path.join(r, 'build-tools', '*', name)))
        if cands:
            return cands[-1]
    return None


def _java_ok(p):
    try:
        return subprocess.run([p, '-version'], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def run(cmd, what):
    p = subprocess.run(cmd, capture_output=True, text=True, env=tool_env())
    if p.returncode:
        raise SourceError(f'{what} 失败：{(p.stdout + p.stderr).strip()[-600:]}')
    return p.stdout


def build(apk, out_apk, work):
    """Patched, aligned and signed copy of apk at out_apk; returns what changed (for framework.json)."""
    t = tools()
    for n in ('testkey.pk8', 'testkey.x509.pem'):
        if not os.path.isfile(os.path.join(KEY_DIR, n)):
            raise SourceError(f'缺 tools/apksign/{n}（仓库自带，重新检出）')
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    dec = os.path.join(work, 'decoded')
    run([t['apktool'], 'd', '-r', '-f', '-o', dec, apk], 'apktool 解包小米相机')
    role, lut = patch_tree(dec)
    unsigned = os.path.join(work, 'unsigned.apk')
    run([t['apktool'], 'b', '-o', unsigned, dec], 'apktool 重新打包小米相机')
    aligned = os.path.join(work, 'aligned.apk')
    run([t['zipalign'], '-f', '-p', '4', unsigned, aligned], 'zipalign')
    run([t['apksigner'], 'sign', '--key', os.path.join(KEY_DIR, 'testkey.pk8'),
         '--cert', os.path.join(KEY_DIR, 'testkey.x509.pem'), '--out', out_apk, aligned], 'apksigner 签名')
    run([t['apksigner'], 'verify', out_apk], 'apksigner 核对签名')
    shutil.rmtree(work, ignore_errors=True)
    return {'role_table': role, 'lut_check': lut}
