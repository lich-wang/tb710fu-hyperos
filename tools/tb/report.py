"""report: the facts a flash report needs, as text to paste into the issue form (read only, no device access).

Collected from what the earlier steps wrote under work/: the device profile (check), the latest Lenovo parts
(pull), the latest Xiaomi import (import), the latest flash package (package) and the repository revision.
The chip serial is shortened; nothing from the backup or the images themselves is included.
"""
import glob, json, os, subprocess

from .sources import REPO, WORK

FW_SHOWN = ('abl', 'xbl', 'tz', 'hyp', 'aop', 'modem', 'uefi')


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _latest(pattern):
    c = sorted(glob.glob(os.path.join(WORK, pattern)), key=os.path.getmtime)
    return c[-1] if c else None


def short_serial(s):
    return f'{s[:3]}…{s[-3:]}' if s and len(s) > 6 else (s or '?')


def revision():
    try:
        r = subprocess.run(['git', '-C', REPO, 'describe', '--always', '--dirty'], capture_output=True, text=True,
                           timeout=10)
        return r.stdout.strip() or '?'
    except (OSError, subprocess.SubprocessError):
        return '?'


def fingerprint(props):
    for k in ('ro.system.build.fingerprint', 'ro.vendor.build.fingerprint', 'ro.build.fingerprint'):
        if props.get(k):
            return props[k]
    return next((v for k, v in props.items() if k.endswith('build.fingerprint')), None)


def collect():
    out = [f'工具版本：{revision()}']
    prof = _load(os.path.join(WORK, 'device', 'latest.json'))
    if not prof:
        out.append('设备档案：没有（还没跑 check）')
    else:
        dev = prof.get('device') or {}
        abl = prof.get('abl') or {}
        keys = ', '.join(f'RSA{k.get("bits")} {k.get("name") or "非测试密钥 " + k.get("sha1", "")[:12]}'
                         for k in abl.get('keys', [])) or '没找到'
        out += [
            f'设备档案：{prof.get("created")}（check {"通过" if prof.get("ok") else "没通过"}，读取方式 {dev.get("source")}）',
            f'  芯片序列号 {short_serial(dev.get("chip_serial"))}  HWID {dev.get("hwid", "?")}  当前槽 {prof.get("slot")}',
            f'  ABL：{ {"trusted": "信任 AOSP 测试密钥", "not_trusted": "不信任测试密钥", "unknown": "判断不了"}.get(prof.get("testkey"), prof.get("testkey"))}'
            f'；内置公钥 {keys}；签名证书 {", ".join(abl.get("signer", [])) or "?"}',
            f'  回滚索引 {dict(sorted((prof.get("rollback") or {}).items()))}',
        ]
        for k in ('ro.product.model', 'ro.build.display.id', 'ro.vendor.build.fingerprint'):
            if dev.get(k):
                out.append(f'  {k} = {dev[k]}')
        if dev.get('advisory_hint'):
            out.append(f'  按联想公告推断：{dev["advisory_hint"]}')
        fw = prof.get('firmware') or {}
        shown = [f'{n} {fw[n]["version"]}' for n in FW_SHOWN if fw.get(n, {}).get('version')]
        if shown:
            out.append('  底层固件：' + '；'.join(shown))
        sup = prof.get('super') or {}
        if sup.get('content'):
            out.append(f'  super 里的系统：{ {"stock": "像联想原厂", "modified": "已不是原厂", "unknown": "认不出"}.get(sup["content"])}')
        for e in prof.get('errors', []):
            out.append(f'  ✗ {e}')
        for w in prof.get('warnings', []):
            out.append(f'  ⚠ {w}')
    lj = _latest('lenovo/*/lenovo.json')
    if lj:
        l = _load(lj) or {}
        parts = l.get('partitions') or {}
        out.append(f'联想组件：{os.path.basename(os.path.dirname(lj))}（来源 {l.get("source")}'
                   + (f'，包 {os.path.basename(l["package"])}' if l.get('package') else '') + '）')
        for n in ('system', 'vendor'):
            fp = fingerprint((parts.get(n) or {}).get('props') or {})
            if fp:
                out.append(f'  {n} 指纹 {fp}')
    xj = _latest('xiaomi/*/xiaomi.json')
    if xj:
        x = _load(xj) or {}
        out.append(f'小米组件：{x.get("version")}（{"已验证" if x.get("verified") else "未验证版本"}）'
                   f'  {(x.get("metadata") or {}).get("post-build", "")}')
    mj = _latest('out/*/manifest.json')
    if mj:
        m = _load(mj) or {}
        inp = m.get('inputs') or {}
        d = m.get('device') or {}
        out += [f'刷机包：{os.path.basename(os.path.dirname(mj))}（{m.get("created")}）',
                f'  小米 {(inp.get("xiaomi") or {}).get("version")}；联想 {(inp.get("lenovo") or {}).get("system")}',
                f'  绑定芯片序列号 {short_serial(d.get("chip_serial"))}、槽 {d.get("slot")}；'
                f'dm-verity {"开" if inp.get("verity", True) else "关（--no-verity）"}'
                + ('；调试版' if inp.get('debug') else '')]
    else:
        out.append('刷机包：没有（还没跑 package）')
    return out


def run():
    print('\n'.join(collect()))
    print('\n（把上面整段贴到刷机报告的“工具生成的信息”一栏；芯片序列号已缩写）')
    return True
