"""Android sparse images (the format of super chunks in Qualcomm/Lenovo 9008 packages)."""
import struct

MAGIC = 0xED26FF3A
RAW, FILL, DONT_CARE, CRC32 = 0xCAC1, 0xCAC2, 0xCAC3, 0xCAC4
COPY = 1 << 22


def is_sparse(head):
    return len(head) >= 4 and struct.unpack_from('<I', head)[0] == MAGIC


def expanded_size(f):
    """Size in bytes once expanded (reads only the header)."""
    f.seek(0)
    h = f.read(28)
    blk, blocks = struct.unpack_from('<II', h, 12)
    return blk * blocks


def unsparse(f, out, base=0):
    """Expand the sparse image in file object f into out (opened r+b) at byte offset base.

    Zero fills and don't-care chunks are skipped, so out must already read as zeros there (a new or truncated
    file). Returns the number of bytes the image covers.
    """
    f.seek(0)
    h = f.read(28)
    magic, major, _, fhs, chs, blk, blocks, chunks, _ = struct.unpack('<IHHHHIIII', h)
    if magic != MAGIC or major != 1:
        raise ValueError('不是 Android sparse 镜像')
    f.seek(fhs)
    pos = 0
    for _ in range(chunks):
        ch = f.read(chs)
        ctype, _, nblk, total = struct.unpack_from('<HHII', ch)
        data_len = total - chs
        n = nblk * blk
        if ctype == RAW:
            if data_len != n:
                raise ValueError('sparse 原始块长度不对')
            out.seek(base + pos)
            left = n
            while left:
                b = f.read(min(left, COPY))
                if not b:
                    raise ValueError('sparse 镜像被截断')
                out.write(b)
                left -= len(b)
        elif ctype == FILL:
            pat = f.read(4)
            if pat != b'\0\0\0\0':
                out.seek(base + pos)
                piece = pat * (COPY // 4)
                left = n
                while left:
                    k = min(left, COPY)
                    out.write(piece[:k])
                    left -= k
        elif ctype == DONT_CARE:
            pass
        elif ctype == CRC32:
            f.read(data_len)
            continue
        else:
            raise ValueError(f'未知的 sparse 块类型 0x{ctype:x}')
        pos += n
    if pos != blk * blocks:
        raise ValueError('sparse 镜像块数不对')
    return pos


def write_sparse(data, path, blk=4096):
    """Minimal writer (tests only): raw / zero-fill / don't-care chunks."""
    assert len(data) % blk == 0
    chunks, i = [], 0
    nblk = len(data) // blk
    while i < nblk:
        b = data[i * blk:(i + 1) * blk]
        j = i + 1
        if b == b'\0' * blk:
            while j < nblk and data[j * blk:(j + 1) * blk] == b'\0' * blk:
                j += 1
            kind = DONT_CARE if (i // 7) % 2 else FILL
            body = b'' if kind == DONT_CARE else b'\0\0\0\0'
        else:
            while j < nblk and data[j * blk:(j + 1) * blk] != b'\0' * blk:
                j += 1
            kind, body = RAW, data[i * blk:j * blk]
        chunks.append(struct.pack('<HHII', kind, 0, j - i, 12 + len(body)) + body)
        i = j
    with open(path, 'wb') as o:
        o.write(struct.pack('<IHHHHIIII', MAGIC, 1, 0, 28, 12, blk, nblk, len(chunks), 0))
        for c in chunks:
            o.write(c)
