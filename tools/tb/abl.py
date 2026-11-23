"""abl: the AOSP-capable Lenovo ABL that replaces the stock ZUXOS 1.5.04.495 ABL.

Lenovo's stock ZUXOS 1.5.04.495 ABL carries the AOSP test key only as a second copy: the key it verifies vbmeta
with is Lenovo's own (the same evidence as the Y700 Gen 4 OEM ABL, which rejects test-key vbmeta while it holds
such a copy). A test-key boot chain therefore also needs the ABL of CN ZUI 17.0.04.279, which holds only the test
key. The "495 no-unlock supplement" writes exactly that ABL, and the reference tablet has run it for months.

`tb710fu.py abl SOURCE` takes that ABL from the official Lenovo CN 17.0.04.279 9008 package (.7z / .zip / folder,
member abl.elf) or from an abl image (e.g. abl_a.bin of a backup), pads it to the 1 MiB partition and checks it
byte for byte. Output: work/abl/abl.img + abl.json. Read only: nothing here talks to the device.
"""
import json, os, tempfile, time

from . import avb
from .backup import file_sha256
from .sources import WORK, SourceError, sha256

PARTITION_BYTES = 1 << 20

# The one replacement ABL: CN ZUI 17.0.04.279 abl.elf (241464 bytes, sha256 45106e3b…) zero-padded to the partition.
PINNED = {
    'bytes': PARTITION_BYTES,
    'sha256': '6cfa16e65c2dadd0fc557dd356e6c383c693b4c3b7f0a651d92bb9f6e38b74a8',
    'elf_sha256': '45106e3b7864be760a2631d9a12780604040153ac23541e11e2e23e028dd0489',
    'source': 'CN ZUI 17.0.04.279 abl.elf（= ZUXOS 1.5.04.495 免解锁补充包的 abl.img）',
}

# ABLs that embed the test key only as a second copy and verify with Lenovo's key (sha256 without the zero padding).
OEM_EFFECTIVE = {
    '8787d92eb449c1f619807ee73f54eee120607ed539575b8a26a8378ee282a93e': 'ZUXOS 1.5.04.495（联想官方原厂）',
}


def classify(data):
    """check_abl() of avb plus the decision for the known Lenovo ABLs:

    trusts_testkey True    only test keys (the 279 / 354 / 414 / ROW 059 ABLs, and the pinned one);
    trusts_testkey False   with replace=True: a known stock 495 ABL that the pinned ABL replaces;
    otherwise              what check_abl says (unknown mixed ABLs stay "cannot tell")."""
    res = avb.check_abl(data)
    known = OEM_EFFECTIVE.get(sha256(data.rstrip(b'\0')))
    if known:
        res.update(trusts_testkey=False, replace=True, known=known, error=None,
                   effective='联想自己的密钥（测试密钥只是第二份副本）')
    return res


def output_dir():
    return os.path.join(WORK, 'abl')


def load():
    """The resolved ABL (path, info), checked again; SourceError when `abl` has not been run."""
    d = output_dir()
    p = os.path.join(d, 'abl.img')
    if not os.path.isfile(p):
        raise SourceError('平板的 abl 是联想原厂 495（只认联想自己的密钥），刷机包要同时换 abl：'
                          '先跑 tb710fu.py abl <联想官方 CN 17.0.04.279 线刷包>')
    if os.path.getsize(p) != PINNED['bytes'] or file_sha256(p) != PINNED['sha256']:
        raise SourceError(f'{p} 不是预期的 abl（大小或 sha256 不对）：重跑 tb710fu.py abl')
    return p, dict(PINNED)


def _pad_check(data, where):
    if len(data) > PARTITION_BYTES or data[PARTITION_BYTES:].strip(b'\0'):
        raise SourceError(f'{where}：比 abl 分区（1 MiB）还大，不是 abl')
    padded = data[:PARTITION_BYTES].ljust(PARTITION_BYTES, b'\0')
    if sha256(padded) != PINNED['sha256']:
        raise SourceError(f'{where}：不是 {PINNED["source"]}（补齐到 1 MiB 后 sha256 {sha256(padded)}，'
                          f'应为 {PINNED["sha256"]}）')
    return padded


def resolve(source):
    """Take the pinned ABL from an official Lenovo package or an abl image -> (path, info)."""
    from .lenovo import Package
    if not os.path.exists(source):
        raise SourceError(f'{source} 不存在')
    low = source.lower()
    if os.path.isfile(source) and not low.endswith(('.zip', '.7z')):
        if os.path.getsize(source) > 2 * PARTITION_BYTES:
            raise SourceError(f'{source}：太大，不是 abl 镜像（要联想官方线刷包或 abl 镜像）')
        with open(source, 'rb') as f:
            data = _pad_check(f.read(), source)
        origin = {'kind': 'image', 'path': os.path.abspath(source)}
    else:
        pkg = Package(source)
        try:
            name = pkg.find('abl.elf')
            if not name:
                raise SourceError(f'{source} 里没有 abl.elf：要联想官方 CN 17.0.04.279 的 9008 线刷包')
            with pkg.open(name) as f:
                data = f.read(2 * PARTITION_BYTES + 1)
            data = _pad_check(data, f'{source}:{name}')
        finally:
            pkg.close()
        origin = {'kind': 'package', 'path': os.path.abspath(source), 'member': name}
    res = classify(data)
    if res.get('trusts_testkey') is not True:
        raise SourceError('这份 abl 不是只认测试密钥的（不该发生：sha256 已核对）')
    d = output_dir()
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.abl-', dir=d)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        os.chmod(tmp, 0o644)
        if file_sha256(tmp) != PINNED['sha256']:
            raise SourceError(f'写 {tmp} 后读回不一致')
        os.replace(tmp, os.path.join(d, 'abl.img'))
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    info = dict(PINNED, origin=origin, keys=res['keys'], signer=res['signer'],
                created=time.strftime('%Y-%m-%dT%H:%M:%S%z'))
    with open(os.path.join(d, 'abl.json'), 'w') as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    return os.path.join(d, 'abl.img'), info


def report(path, info):
    print(f'abl：{path}')
    print(f'  来源 {info["origin"]["path"]}' + (f'（{info["origin"]["member"]}）' if info['origin'].get('member') else ''))
    print(f'  {info["bytes"]} 字节，sha256 {info["sha256"]}')
    print(f'  {info["source"]}；内置根公钥 {", ".join(k["name"] or k["sha1"] for k in info["keys"])}；'
          f'签名证书 {", ".join(info["signer"]) or "?"}')
    print('\n✅ 刷机包会在设备 abl 是联想原厂 495 时把当前槽 abl 换成它')
    return True

