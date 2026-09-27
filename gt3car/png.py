"""Minimal PNG reader / writer (stdlib zlib + numpy). Reads every colour type and bit depth,
tRNS and Adam7; writes 8-bit RGBA."""

import struct
import zlib

import numpy as np

_SIG = b"\x89PNG\r\n\x1a\n"


def write_png(path, rgba):
    """rgba: uint8 array (h, w, 4)."""
    with open(path, "wb") as f:
        f.write(encode_png(rgba))


def encode_png(rgba):
    rgba = np.ascontiguousarray(rgba, np.uint8)
    h, w = rgba.shape[:2]
    raw = np.zeros((h, w * 4 + 1), np.uint8)
    raw[:, 1:] = rgba.reshape(h, w * 4)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return _SIG + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw.tobytes(), 6)) + chunk(b"IEND", b"")


def read_png(path):
    with open(path, "rb") as f:
        return decode_png(f.read())


def _unfilter(data, pos, w, h, bpp_bits, channels):
    """Returns (rows as uint8 array (h, stride), new pos). Rows are undone as a wavefront over the pixel
    diagonals (a pixel depends only on its left, up and up-left neighbours), so every step is one numpy op."""
    stride = (w * bpp_bits + 7) // 8
    bpp = max(1, bpp_bits // 8)
    blk = np.frombuffer(data, np.uint8, h * (stride + 1), pos).reshape(h, stride + 1)
    pos += h * (stride + 1)
    ft = blk[:, 0].astype(np.int64)
    raw = blk[:, 1:]
    if ft.max(initial=0) > 4:
        raise ValueError("bad PNG filter %d" % ft.max())
    if not np.isin(ft, (1, 3, 4)).any():
        out = raw.copy()
        for y in range(1, h):                 # Up rows add the finished row above
            if ft[y] == 2:
                out[y] = out[y] + out[y - 1]
        if h and ft[0] == 2:
            pass                              # the row above the first is zero
        return out, pos
    ncol = stride // bpp
    r = raw.reshape(h, ncol, bpp).astype(np.int16)
    o = np.zeros((h + 1, ncol + 1, bpp), np.int16)
    for d in range(h + ncol - 1):
        y0, y1 = max(0, d - ncol + 1), min(h - 1, d)
        ys = np.arange(y0, y1 + 1)
        xs = d - ys
        a = o[ys + 1, xs]
        b = o[ys, xs + 1]
        c = o[ys, xs]
        f = ft[ys][:, None]
        pa, pb, pc = np.abs(b - c), np.abs(a - c), np.abs(a + b - 2 * c)
        paeth = np.where((pa <= pb) & (pa <= pc), a, np.where(pb <= pc, b, c))
        pred = np.where(f == 1, a, np.where(f == 2, b, np.where(f == 3, (a + b) >> 1, np.where(f == 4, paeth, 0))))
        o[ys + 1, xs + 1] = (r[ys, xs] + pred) & 0xFF
    return o[1:, 1:].reshape(h, stride).astype(np.uint8), pos


def _samples(rows, w, depth, channels):
    """(h, w*channels) integer samples."""
    h = rows.shape[0]
    if depth == 8:
        return rows[:, :w * channels].astype(np.int64)
    if depth == 16:
        r = rows[:, :w * channels * 2].astype(np.int64)
        return (r[:, 0::2] << 8) | r[:, 1::2]
    per = 8 // depth
    bits = np.unpackbits(rows, axis=1) if depth == 1 else None
    if depth == 1:
        return bits[:, :w * channels].astype(np.int64)
    out = np.zeros((h, rows.shape[1] * per), np.int64)
    for k in range(per):
        out[:, k::per] = (rows >> (8 - depth * (k + 1))) & ((1 << depth) - 1)
    return out[:, :w * channels]


def _to8(v, depth):
    if depth == 8:
        return v.astype(np.uint8)
    if depth == 16:
        return (((v * 255) + 32895) >> 16).astype(np.uint8)      # ImageSharp's 16 -> 8 bit
    return (v * 255 // ((1 << depth) - 1)).astype(np.uint8)


def decode_png(data):
    if data[:8] != _SIG:
        raise ValueError("not a PNG file")
    pos = 8
    idat = bytearray()
    plte = trns = None
    while pos < len(data):
        n, tag = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if tag == b"IHDR":
            w, h, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif tag == b"PLTE":
            plte = np.frombuffer(body, np.uint8).reshape(-1, 3)
        elif tag == b"tRNS":
            trns = body
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
    raw = zlib.decompress(bytes(idat))
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    bits = depth * channels
    if interlace:
        img = np.zeros((h, w, channels), np.int64)
        p = 0
        for x0, y0, dx, dy in ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2)):
            pw, ph = (w - x0 + dx - 1) // dx, (h - y0 + dy - 1) // dy
            if pw <= 0 or ph <= 0:
                continue
            rows, p = _unfilter(raw, p, pw, ph, bits, channels)
            img[y0::dy, x0::dx] = _samples(rows, pw, depth, channels).reshape(ph, pw, channels)
    else:
        rows, _ = _unfilter(raw, 0, w, h, bits, channels)
        img = _samples(rows, w, depth, channels).reshape(h, w, channels)
    out = np.zeros((h, w, 4), np.uint8)
    if ctype == 3:
        pal = np.zeros((256, 4), np.uint8)
        pal[:, 3] = 255
        pal[:len(plte), :3] = plte
        if trns:
            t = np.frombuffer(trns, np.uint8)
            pal[:len(t), 3] = t
        return pal[img[:, :, 0]]
    if ctype in (0, 4):
        g = _to8(img[:, :, 0], depth)
        out[:, :, 0] = out[:, :, 1] = out[:, :, 2] = g
        out[:, :, 3] = _to8(img[:, :, 1], depth) if ctype == 4 else 255
        if ctype == 0 and trns and len(trns) >= 2:
            key = struct.unpack(">H", trns[:2])[0]
            out[:, :, 3][img[:, :, 0] == key] = 0
    else:
        for c in range(3):
            out[:, :, c] = _to8(img[:, :, c], depth)
        out[:, :, 3] = _to8(img[:, :, 3], depth) if ctype == 6 else 255
        if ctype == 2 and trns and len(trns) >= 6:
            key = struct.unpack(">HHH", trns[:6])
            m = (img[:, :, 0] == key[0]) & (img[:, :, 1] == key[1]) & (img[:, :, 2] == key[2])
            out[:, :, 3][m] = 0
    return out

# Nenkai's research is the core of this human & machine made tool.
