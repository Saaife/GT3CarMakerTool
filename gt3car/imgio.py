"""Texture files the builder reads: PNG, BMP and TGA -> (h, w, 4) uint8 RGBA (stdlib + numpy).

Decoded the way the C# tool's ImageSharp 3 did - every variant below builds byte-identical to it (22 test files,
2026-09-25):
- BMP: 1/4/8-bit palettes (BITMAPCOREHEADER 3-byte or INFO 4-byte entries), RLE4/RLE8, 16-bit 5-5-5 or bitfields
  (channels widened by BIT REPLICATION, v << 3 | v >> 2), 24-bit, 32-bit BI_RGB (the 4th byte IS alpha - unless every
  one is 0, then opaque) or bitfields; bottom-up or top-down.
- TGA: colour-mapped, true-colour and greyscale, raw or RLE; 16-bit channels widened by rounding (v * 255 / 31);
  the 16-bit alpha bit and 32-bit alpha only count when the descriptor declares alpha bits; either origin/direction.
Anything else (JPG, ...) is refused with a clear message: save the texture as PNG, TGA or BMP."""

import struct

import numpy as np

from .png import decode_png, _SIG as PNG_SIG

FORMATS = "PNG, TGA or BMP"


def read_image(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] == PNG_SIG:
        return decode_png(data)
    if data[:2] == b"BM":
        return decode_bmp(data)
    if _looks_tga(data, path):
        return decode_tga(data)
    raise ValueError(f"'{path}': unsupported image format - save the texture as {FORMATS}")


# ---- helpers ------------------------------------------------------------------------------------------------
def _scale_bits(v, bits):
    """An n-bit channel to 8 bits the way ImageSharp does: through a float, value / max * 255, rounded."""
    if bits <= 0:
        return np.full(v.shape, 255, np.uint8)
    if bits == 8:
        return v.astype(np.uint8)
    mx = (1 << bits) - 1
    return np.clip(np.floor(v.astype(np.float64) * 255.0 / mx + 0.5), 0, 255).astype(np.uint8)


def _mask_shift(mask):
    if mask == 0:
        return 0, 0
    shift = (mask & -mask).bit_length() - 1
    bits = bin(mask >> shift).count("1")
    return shift, bits


def _replicate(v, bits):
    """An n-bit channel to 8 bits by bit replication (ImageSharp's BMP GetBytesFrom5BitValue: v << 3 | v >> 2)."""
    v = v.astype(np.uint32) << (8 - bits)
    return (v | (v >> bits)).astype(np.uint8)


def _from_masks(px, masks, scale=None):
    """px: uint32 pixels; masks: (r, g, b, a) bit masks -> RGBA."""
    out = np.empty(px.shape + (4,), np.uint8)
    for c, m in enumerate(masks):
        if m == 0:
            out[..., c] = 255 if c == 3 else 0
            continue
        shift, bits = _mask_shift(m)
        out[..., c] = (scale or _scale_bits)((px & m) >> shift, bits)
    return out


# ---- BMP ----------------------------------------------------------------------------------------------------
def decode_bmp(data):
    off = struct.unpack_from("<I", data, 10)[0]
    hsize = struct.unpack_from("<I", data, 14)[0]
    if hsize == 12:                                    # OS/2 BITMAPCOREHEADER
        w, h, _, bpp = struct.unpack_from("<hhHH", data, 18)
        comp, clr_used, pal_entry = 0, 0, 3
    else:
        w, h, _, bpp, comp = struct.unpack_from("<iiHHI", data, 18)
        clr_used = struct.unpack_from("<I", data, 46)[0] if hsize >= 40 else 0
        pal_entry = 4
    top_down = h < 0
    h = abs(h)
    masks = None
    if comp in (3, 6):                                 # BI_BITFIELDS / BI_ALPHABITFIELDS
        if hsize >= 52:
            masks = struct.unpack_from("<4I", data, 54) if hsize >= 56 or comp == 6 else struct.unpack_from("<3I", data, 54) + (0,)
        else:                                          # masks follow a 40-byte header
            n = 4 if comp == 6 else 3
            masks = struct.unpack_from("<%dI" % n, data, 14 + hsize) + ((0,) if n == 3 else ())
    pal = None
    if bpp <= 8:
        n = clr_used or (1 << bpp)
        p0 = 14 + hsize + (12 if comp == 3 and hsize == 40 else 16 if comp == 6 and hsize == 40 else 0)
        raw = np.frombuffer(data, np.uint8, n * pal_entry, p0).reshape(n, pal_entry)
        pal = np.zeros((256, 4), np.uint8)
        pal[:n, 0], pal[:n, 1], pal[:n, 2], pal[:n, 3] = raw[:, 2], raw[:, 1], raw[:, 0], 255
    if comp in (1, 2):                                 # RLE8 / RLE4
        idx = _bmp_rle(data, off, w, h, 8 if comp == 1 else 4)
        img = pal[idx]
    else:
        stride = ((w * bpp + 31) // 32) * 4
        rows = np.frombuffer(data, np.uint8, stride * h, off).reshape(h, stride)
        if bpp == 32:
            px = rows[:, :w * 4].reshape(h, w, 4)
            if masks is not None:
                p32 = px.view("<u4").reshape(h, w)
                img = _from_masks(p32.astype(np.uint32), masks)
            else:                                      # BI_RGB: BGRA - but all-zero 4th bytes = opaque (ImageSharp)
                img = np.empty((h, w, 4), np.uint8)
                img[..., 0], img[..., 1], img[..., 2] = px[..., 2], px[..., 1], px[..., 0]
                img[..., 3] = px[..., 3] if px[..., 3].any() else 255
        elif bpp == 24:
            px = rows[:, :w * 3].reshape(h, w, 3)
            img = np.empty((h, w, 4), np.uint8)
            img[..., 0], img[..., 1], img[..., 2], img[..., 3] = px[..., 2], px[..., 1], px[..., 0], 255
        elif bpp == 16:
            p16 = rows[:, :w * 2].copy().view("<u2").reshape(h, w).astype(np.uint32)
            img = _from_masks(p16, masks if masks is not None else (0x7C00, 0x03E0, 0x001F, 0), _replicate)
        elif bpp in (1, 2, 4, 8):
            bits = np.unpackbits(rows, axis=1) if bpp < 8 else None
            if bpp == 8:
                idx = rows[:, :w]
            else:
                b = bits[:, :w * bpp].reshape(h, w, bpp)
                idx = np.zeros((h, w), np.int64)
                for k in range(bpp):
                    idx = (idx << 1) | b[..., k]
            img = pal[idx]
        else:
            raise ValueError("BMP: %d bits per pixel is not supported" % bpp)
    return np.ascontiguousarray(img if top_down else img[::-1])


def _bmp_rle(data, off, w, h, bits):
    """RLE8 / RLE4 -> palette indices, bottom-up rows."""
    idx = np.zeros((h, w), np.int64)
    x = y = 0
    p = off
    while p + 1 < len(data) and y < h:
        n, c = data[p], data[p + 1]
        p += 2
        if n:                                          # a run
            for k in range(n):
                if x < w:
                    idx[y, x] = c if bits == 8 else ((c >> 4) if k % 2 == 0 else (c & 15))
                x += 1
        elif c == 0:                                   # end of line
            x, y = 0, y + 1
        elif c == 1:                                   # end of bitmap
            break
        elif c == 2:                                   # delta
            x += data[p]
            y += data[p + 1]
            p += 2
        else:                                          # absolute run of c pixels
            if bits == 8:
                for k in range(c):
                    if x < w:
                        idx[y, x] = data[p + k]
                    x += 1
                p += c + (c & 1)
            else:
                nb = (c + 1) // 2
                for k in range(c):
                    b = data[p + k // 2]
                    if x < w:
                        idx[y, x] = (b >> 4) if k % 2 == 0 else (b & 15)
                    x += 1
                p += nb + (nb & 1)
    return idx


# ---- TGA ----------------------------------------------------------------------------------------------------
def _looks_tga(data, path):
    if len(data) < 18:
        return False
    if data[-18:-2] == b"TRUEVISION-XFILE":
        return True
    idl, cmt, itype = data[0], data[1], data[2]
    bpp = data[16]
    return (str(path).lower().endswith(".tga") and cmt in (0, 1) and itype in (1, 2, 3, 9, 10, 11)
            and bpp in (8, 15, 16, 24, 32))


def decode_tga(data):
    idl, cmt, itype, cm_first, cm_len, cm_bits, _, _, w, h, bpp, desc = struct.unpack_from("<BBBHHBHHHHBB", data, 0)
    p = 18 + idl
    abits = desc & 0x0F
    pal = None
    if cmt == 1:
        eb = (cm_bits + 7) // 8
        raw = data[p:p + cm_len * eb]
        p += cm_len * eb
        pal = np.zeros((cm_first + cm_len, 4), np.uint8)
        pal[cm_first:] = _tga_pixels(np.frombuffer(raw, np.uint8).reshape(cm_len, eb), cm_bits, abits)
    bp = (bpp + 7) // 8
    npx = w * h
    if itype in (9, 10, 11):                           # RLE
        out = bytearray()
        while len(out) < npx * bp:
            hdr = data[p]
            p += 1
            cnt = (hdr & 0x7F) + 1
            if hdr & 0x80:
                out += data[p:p + bp] * cnt
                p += bp
            else:
                out += data[p:p + cnt * bp]
                p += cnt * bp
        raw = np.frombuffer(bytes(out[:npx * bp]), np.uint8).reshape(npx, bp)
    else:
        raw = np.frombuffer(data, np.uint8, npx * bp, p).reshape(npx, bp)
    if itype in (1, 9):                                # colour-mapped
        idx = raw[:, 0].astype(np.int64) if bp == 1 else raw.view("<u2")[:, 0].astype(np.int64)
        img = pal[idx]
    elif itype in (3, 11):                             # greyscale (8-bit, or 16 = grey + alpha)
        img = np.empty((npx, 4), np.uint8)
        img[:, 0] = img[:, 1] = img[:, 2] = raw[:, 0]
        img[:, 3] = raw[:, 1] if bp == 2 else 255
    else:
        img = _tga_pixels(raw, bpp, abits)
    img = img.reshape(h, w, 4)
    if not desc & 0x20:                                # bottom-up (the default)
        img = img[::-1]
    if desc & 0x10:                                    # right-to-left
        img = img[:, ::-1]
    return np.ascontiguousarray(img)


def _tga_pixels(raw, bits, abits):
    n = raw.shape[0]
    img = np.empty((n, 4), np.uint8)
    if bits in (15, 16):
        v = raw[:, 0].astype(np.uint32) | (raw[:, 1].astype(np.uint32) << 8)
        img[:, 0] = _scale_bits((v >> 10) & 31, 5)
        img[:, 1] = _scale_bits((v >> 5) & 31, 5)
        img[:, 2] = _scale_bits(v & 31, 5)
        img[:, 3] = np.where(v & 0x8000, 255, 0).astype(np.uint8) if (bits == 16 and abits) else 255
    elif bits == 24:
        img[:, 0], img[:, 1], img[:, 2], img[:, 3] = raw[:, 2], raw[:, 1], raw[:, 0], 255
    elif bits == 32:
        img[:, 0], img[:, 1], img[:, 2] = raw[:, 2], raw[:, 1], raw[:, 0]
        img[:, 3] = raw[:, 3] if abits else 255          # no alpha bits in the descriptor = opaque (ImageSharp)
    elif bits == 8:
        img[:, 0] = img[:, 1] = img[:, 2] = raw[:, 0]
        img[:, 3] = 255
    else:
        raise ValueError("TGA: %d-bit colour is not supported" % bits)
    return img

# Nenkai's research is the core of this human & machine made tool.
