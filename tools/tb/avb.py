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


def elf_extent(data):
    """End of the last ELF segment (ELF32/64, little endian): bytes after it are not part of ABL."""
    try:
        if data[4] == 2:    # ELFCLASS64: p_offset at +8, p_filesz at +32
            phoff, = struct.unpack_from('<Q', data, 0x20)
            phentsize, phnum = struct.unpack_from('<HH', data, 0x36)
            fmt, o_off, o_size = '<Q', 8, 32
        else:               # ELFCLASS32: p_offset at +4, p_filesz at +16
            phoff, = struct.unpack_from('<I', data, 0x1c)
            phentsize, phnum = struct.unpack_from('<HH', data, 0x2a)
            fmt, o_off, o_size = '<I', 4, 16
        ends = []
        for i in range(phnum):
            h = phoff + i * phentsize
            off, size = struct.unpack_from(fmt, data, h + o_off)[0], struct.unpack_from(fmt, data, h + o_size)[0]
            if size:
                ends.append(off + size)
    except (struct.error, IndexError):
        return len(data)
    return min(max(ends), len(data)) if ends else len(data)


def check_abl(data):
    """Root keys compiled into ABL's LinuxLoader (the key a locked bootloader accepts for vbmeta).

    Only the bytes inside the ELF's segments are searched. Trusted = every AVB key found is an AOSP test key;
    not trusted = none is; both kinds present -> None (cannot tell which one LinuxLoader verifies with).
    -> {'keys': [{'bits', 'sha1', 'name'}], 'trusts_testkey': bool|None, 'signer': [...], 'error': str|None}"""
    res = {'keys': [], 'trusts_testkey': None, 'signer': [], 'error': None}
    if data[:4] != b'\x7fELF':
        res['error'] = '不是 ELF 文件（abl 应该是 ELF）'
        return res
    data = data[:elf_extent(data)]
    res['signer'] = sorted(set(s.decode().strip() for s in re.findall(
        rb'[\x20-\x7e]{6,}(?= *(?:Root CA|Attestation CA)\d?)', data)))
    seen = set()
    for chunk in [data] + lzma_streams(data):
        for _, bits, blob in avb_keys_in(chunk):
            k = key_info(blob)
            if k['sha1'] not in seen:
                seen.add(k['sha1'])
                res['keys'].append(dict(k, bits=bits))
    named = [bool(k['name']) for k in res['keys']]
    if not named:
        res['error'] = '没在 abl 里找到 AVB 公钥（格式不认识，或 abl 读取不完整）'
    elif all(named):
        res['trusts_testkey'] = True
    elif not any(named):
        res['trusts_testkey'] = False
    else:
        res['error'] = 'abl 里同时有 AOSP 测试公钥和其他公钥，认不出 LinuxLoader 用哪个校验 vbmeta'
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


def find_vbmeta(data):
    """vbmeta blob of a vbmeta image, or of a whole image with an AVB footer."""
    if data[:4] == b'AVB0':
        return data
    f = parse_footer(data)
    if f:
        return data[f[0]:f[0] + f[1]]
    return None


def _span(vb, off, size, what, limit=None):
    limit = len(vb) if limit is None else min(limit, len(vb))
    if off < 0 or size < 0 or off + size > limit:
        raise ValueError(f'vbmeta 损坏或读取不完整（{what} 越界）')
    return vb[off:off + size]


def parse_vbmeta(vb):
    """-> dict: alg, key, rollback, rollback_location, flags, release, chains, hashed. ValueError if malformed.

    Only the embedded public key is read: the signature itself is NOT verified ('signature_verified': False)."""
    if vb[:4] != b'AVB0':
        raise ValueError('不是 vbmeta（没有 AVB0）')
    if len(vb) < 256:
        raise ValueError('vbmeta 头不完整（不足 256 字节）')
    (_, _, _, auth, aux, alg, _, _, _, _, pk_off, pk_size, _, _, d_off, d_size,
     rollback, flags, rb_loc, release) = struct.unpack('>4sLLQQLQQQQQQQQQQQLL47sx80x', vb[:256])
    a = 256 + auth
    _span(vb, a, aux, '辅助块')
    if pk_off + pk_size > aux or d_off + d_size > aux:
        raise ValueError('vbmeta 损坏（公钥或描述符超出辅助块）')
    r = {'alg': ALGS.get(alg, str(alg)), 'key': key_info(_span(vb, a + pk_off, pk_size, '公钥')),
         'rollback': rollback, 'rollback_location': rb_loc, 'flags': flags,
         'release': release.split(b'\0')[0].decode(errors='replace'), 'chains': [], 'hashed': [],
         'signature_verified': False}
    d, end = a + d_off, a + d_off + d_size
    try:
        while d + 16 <= end:
            tag, nbf = struct.unpack_from('>QQ', vb, d)
            body = d + 16
            if body + nbf > end:
                raise ValueError('vbmeta 损坏（描述符越界）')
            lim = body + nbf    # every field must stay inside this descriptor
            if tag == 4:    # chain partition
                loc, nlen, klen, _ = struct.unpack_from('>LLLL', _span(vb, body, 16, '链式描述符', lim))
                p = body + 16 + 60
                r['chains'].append({'partition': _span(vb, p, nlen, '链式分区名', lim).decode(errors='replace'),
                                    'rollback_location': loc,
                                    'key': key_info(_span(vb, p + nlen, klen, '链式公钥', lim))})
            elif tag == 2:  # hash
                q = body + 8 + 32
                nlen = struct.unpack('>L', _span(vb, q, 4, 'hash 描述符', lim))[0]
                r['hashed'].append(_span(vb, q + 16 + 60, nlen, '分区名', lim).decode(errors='replace'))
            elif tag == 1:  # hashtree
                q = body + 4 + 8 * 3 + 4 * 3 + 8 * 2 + 32
                nlen = struct.unpack('>L', _span(vb, q, 4, 'hashtree 描述符', lim))[0]
                r['hashed'].append(_span(vb, q + 16 + 60, nlen, '分区名', lim).decode(errors='replace'))
            d = body + nbf
    except struct.error:
        raise ValueError('vbmeta 损坏（描述符不完整）')
    return r


def key_str(k):
    if not k:
        return '（未签名）'
    return f'AOSP {k["name"]}' if k['name'] else f'非测试密钥（sha1 {k["sha1"]}）'


# ---------------------------------------------------------------- Lenovo advisory

# Lenovo security advisory LEN-221035 (CVE-2026-92777, 2026-10-13), "Minimum Fixed Version" (worldwide
# firmware). Builds below it are listed as affected (= AVB test key). China firmware (ZUXOS) is not covered.
LEN_221035 = {
    'TB710FU': '17.5.04.059', 'TB520FU': '17.5.10.309', 'TB321FU': '17.5.10.170', 'TB373FU': '17.5.10.057',
    'TB351FU': '17.5.10.199', 'TB570FU': '17.5.382', 'TB570ZU': '17.5.382', 'TB370FU': '17.0.280',
    'TB360ZU': '16.0.935',
}


def advisory_hint(model, text):
    """Guess from the build string alone (used when ABL cannot be read)."""
    if re.search(r'ZUXOS|_PRC\b', text, re.I):
        return '国行 ZUXOS 固件：联想公告不覆盖，无法按版本推断'
    m = re.search(r'ZUI[_ ]?(\d+(?:\.\d+){2,3})', text, re.I)
    if not m:
        return '没认出 ZUI 版本号（可能已不是原厂系统），无法按版本推断'
    ver, fixed = m.group(1), LEN_221035.get(model)
    if not fixed:
        return f'ZUI {ver}：联想公告里没有 {model}'
    t = lambda v: tuple(int(x) for x in v.split('.'))
    if t(ver) < t(fixed):
        return f'ZUI {ver} 低于公告修复版 {fixed}：按公告属于受影响版本（测试密钥）'
    return f'ZUI {ver} ≥ 公告修复版 {fixed}：按公告已修复，ABL 多半不再信任测试密钥'
