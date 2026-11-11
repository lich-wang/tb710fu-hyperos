"""Android Verified Boot pieces needed by the check: the root key compiled into ABL and vbmeta parsing.

Standard library only. Nothing here talks to a device.
"""
import hashlib, lzma, re, struct

# sha1 / sha256 of the AVB public key blob (`avbtool extract_public_key`) of the AOSP test keys
# (external/avb/test/data/testkey_rsa*.pem).
TESTKEYS_SHA1 = {
    '2597c218aae470a130f61162feaae70afd97f011': 'testkey_rsa4096',
    'cdbb77177f731920bbe0a0f94f84d9038ae0617d': 'testkey_rsa2048',
    '5227b569de003adc7f8ec3fc03e05dfbd969abad': 'testkey_rsa8192',
}
TESTKEYS_SHA256 = {
    '7728e30f50bfa5cea165f473175a08803f6a8346642b5aa10913e9d9e6defef6': 'testkey_rsa4096',
    '22de3994532196f61c039e90260d78a93a4c57362c7e789be928036e80b77c8c': 'testkey_rsa2048',
    'e15e2365469ce672a91d02cc8d9c2f29b787481e574d3b56ac774153d7ced614': 'testkey_rsa8192',
}

ALGS = {0: 'NONE', 1: 'SHA256_RSA2048', 2: 'SHA256_RSA4096', 3: 'SHA256_RSA8192',
        4: 'SHA512_RSA2048', 5: 'SHA512_RSA4096', 6: 'SHA512_RSA8192'}


def key_info(blob):
    """{'sha1', 'name'} of an AVB public key blob; name is the AOSP test key name or None."""
    if not blob:
        return None
    s1 = hashlib.sha1(blob).hexdigest()
    return {'sha1': s1, 'name': TESTKEYS_SHA1.get(s1)}


# ---------------------------------------------------------------- ABL

def avb_keys_in(data):
    """Every valid AVB public key blob in data -> [(offset, bits, blob)].

    Blob = be32 key_bits, be32 n0inv, modulus, R^2 mod n. A candidate is accepted only when n0inv and
    R^2 are consistent with the modulus, so random bytes never match."""
    found = []
    for m in re.finditer(b'\x00\x00(?:\x08|\x10|\x20)\x00', data):
        off = m.start()
        bits = struct.unpack_from('>I', data, off)[0]
        nb = bits // 8
        end = off + 8 + 2 * nb
        if end > len(data):
            continue
        n0inv = struct.unpack_from('>I', data, off + 4)[0]
        low = struct.unpack_from('>I', data, off + 8 + nb - 4)[0]
        if not low & 1 or not data[off + 8] & 0x80:
            continue
        if n0inv != (-pow(low, -1, 1 << 32)) % (1 << 32):
            continue
        n = int.from_bytes(data[off + 8:off + 8 + nb], 'big')
        if int.from_bytes(data[off + 8 + nb:end], 'big') != pow(2, 2 * bits, n):
            continue
        found.append((off, bits, bytes(data[off:end])))
    return found


def lzma_streams(data, depth=0):
    """Decompress every LZMA ('alone' format, as used in EDK2 firmware volumes) stream in data."""
    out = []
    for m in re.finditer(b'\x5d\x00\x00', data):
        try:
            dec = lzma.LZMADecompressor(lzma.FORMAT_ALONE).decompress(data[m.start():], max_length=64 << 20)
        except lzma.LZMAError:
            continue
        if len(dec) < 4096:
            continue
        out.append(dec)
        if depth < 2:
            out.extend(lzma_streams(dec, depth + 1))
    return out


def check_abl(data):
    """Root keys compiled into ABL's LinuxLoader (the key a locked bootloader accepts for vbmeta).

    -> {'keys': [{'bits', 'sha1', 'name'}], 'trusts_testkey': bool|None, 'signer': [...], 'error': str|None}"""
    res = {'keys': [], 'trusts_testkey': None, 'signer': [], 'error': None}
    if data[:4] != b'\x7fELF':
        res['error'] = '不是 ELF 文件（abl 应该是 ELF）'
        return res
    res['signer'] = sorted(set(s.decode().strip() for s in re.findall(
        rb'[\x20-\x7e]{6,}(?= *(?:Root CA|Attestation CA)\d?)', data)))
    seen = set()
    for chunk in [data] + lzma_streams(data):
        for _, bits, blob in avb_keys_in(chunk):
            k = key_info(blob)
            if k['sha1'] not in seen:
                seen.add(k['sha1'])
                res['keys'].append(dict(k, bits=bits))
    if res['keys']:
        res['trusts_testkey'] = any(k['name'] for k in res['keys'])
    else:
        res['error'] = '没在 abl 里找到 AVB 公钥（格式不认识，或 abl 读取不完整）'
    return res


def image_version(data):
    """QC_IMAGE_VERSION_STRING of a Qualcomm firmware image, if any."""
    m = re.search(rb'QC_IMAGE_VERSION_STRING=([\x20-\x7e]{3,80})', data)
    return m.group(1).decode() if m else None


# ---------------------------------------------------------------- vbmeta

FOOTER_SIZE = 64


def parse_footer(tail):
    """AVB footer from the last 64 bytes of a partition -> (vbmeta_offset, vbmeta_size) or None."""
    if len(tail) < FOOTER_SIZE or tail[-64:-60] != b'AVBf':
        return None
    _, _, _, _, off, size = struct.unpack('>4sLLQQQ28x', tail[-64:])
    return off, size


def footer_info(tail):
    """AVB footer -> {'original_size', 'vbmeta_offset', 'vbmeta_size'} or None."""
    if len(tail) < FOOTER_SIZE or tail[-64:-60] != b'AVBf':
        return None
    _, _, _, orig, off, size = struct.unpack('>4sLLQQQ28x', tail[-64:])
    return {'original_size': orig, 'vbmeta_offset': off, 'vbmeta_size': size}


def find_vbmeta(data):
    """vbmeta blob of a vbmeta image, or of a whole image with an AVB footer."""
    if data[:4] == b'AVB0':
        return data
    f = parse_footer(data)
    if f:
        return data[f[0]:f[0] + f[1]]
    return None


def parse_vbmeta(vb):
    """-> dict: alg, key, rollback, rollback_location, flags, release, chains, hashed."""
    if vb[:4] != b'AVB0':
        raise ValueError('不是 vbmeta（没有 AVB0）')
    (_, _, _, auth, aux, alg, _, _, _, _, pk_off, pk_size, _, _, d_off, d_size,
     rollback, flags, rb_loc, release) = struct.unpack('>4sLLQQLQQQQQQQQQQQLL47sx80x', vb[:256])
    a = 256 + auth
    r = {'alg': ALGS.get(alg, str(alg)), 'key': key_info(vb[a + pk_off:a + pk_off + pk_size]),
         'rollback': rollback, 'rollback_location': rb_loc, 'flags': flags,
         'release': release.split(b'\0')[0].decode(errors='replace'), 'chains': [], 'hashed': []}
    d, end = a + d_off, a + d_off + d_size
    while d + 16 <= end:
        tag, nbf = struct.unpack_from('>QQ', vb, d)
        body = d + 16
        if tag == 4:    # chain partition
            loc, nlen, klen, _ = struct.unpack_from('>LLLL', vb, body)
            p = body + 16 + 60
            r['chains'].append({'partition': vb[p:p + nlen].decode(errors='replace'), 'rollback_location': loc,
                                'key': key_info(vb[p + nlen:p + nlen + klen])})
        elif tag == 2:  # hash
            q = body + 8 + 32
            nlen = struct.unpack_from('>L', vb, q)[0]
            p = q + 16 + 60
            r['hashed'].append(vb[p:p + nlen].decode(errors='replace'))
        elif tag == 1:  # hashtree
            q = body + 4 + 8 * 3 + 4 * 3 + 8 * 2 + 32
            nlen = struct.unpack_from('>L', vb, q)[0]
            p = q + 16 + 60
            r['hashed'].append(vb[p:p + nlen].decode(errors='replace'))
        d = body + nbf
    return r


def key_str(k):
    if not k:
        return '（未签名）'
    return f'AOSP {k["name"]}' if k['name'] else f'非测试密钥（sha1 {k["sha1"]}）'


# ---------------------------------------------------------------- Lenovo advisory

# Lenovo security advisory LEN-221035 (CVE-2026-92777, 2026-10-13), "Minimum Fixed Version" (worldwide
# firmware). Builds below it are listed as affected. China firmware (ZUXOS) is not covered.
# Caution: the fixed version is no proof the test key is gone. The abl of ROW 17.5.04.059 (the listed fix for
# TB710FU) still embeds only testkey_rsa4096, so the version alone never decides; only the abl does.
LEN_221035 = {
    'TB710FU': '17.5.04.059', 'TB520FU': '17.5.10.309', 'TB321FU': '17.5.10.170', 'TB373FU': '17.5.10.057',
    'TB351FU': '17.5.10.199', 'TB570FU': '17.5.382', 'TB570ZU': '17.5.382', 'TB370FU': '17.0.280',
    'TB360ZU': '16.0.935',
}


def advisory_hint(model, text):
    """Guess from the build string alone (used when ABL cannot be read)."""
    m = re.search(r'ZUI[_ ]?(\d+(?:\.\d+){2,3})', text, re.I)
    if re.search(r'ZUXOS|_PRC\b', text, re.I) and not m:
        return '国行 ZUXOS 固件：联想公告不覆盖，无法按版本推断'
    if not m:
        return '没认出 ZUI 版本号（可能已不是原厂系统），无法按版本推断'
    ver, fixed = m.group(1), LEN_221035.get(model)
    if not fixed:
        return f'ZUI {ver}：联想公告里没有 {model}'
    t = lambda v: tuple(int(x) for x in v.split('.'))
    if t(ver) < t(fixed):
        return f'ZUI {ver} 低于公告修复版 {fixed}：按公告属于受影响版本（测试密钥）'
    return (f'ZUI {ver} ≥ 公告修复版 {fixed}：公告列为已修复，但实测 ROW 17.5.04.059 的 abl 仍内置测试密钥，'
            '要以读出的 abl 为准')
