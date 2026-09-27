"""PS2 GS local memory (4 MB) with PDTools' swizzle tables, vectorised with numpy.

Addresses are computed exactly as PDTools.Files.Textures.PS2.GSMemory does (including its quirks:
PSMT4/PSMT8 halve the buffer width, a CT32 read/write takes an extra byte offset)."""

import numpy as np

PSMCT32, PSMCT24, PSMCT16, PSMCT16S, PSMT8, PSMT4 = 0, 1, 2, 10, 19, 20
PSMT8H, PSMT4HL, PSMT4HH = 27, 36, 44

REPEAT, CLAMP, REGION_CLAMP, REGION_REPEAT = 0, 1, 2, 3

MAX_BLOCKS = 16384

_BL32 = np.array([0, 1, 4, 5, 16, 17, 20, 21, 2, 3, 6, 7, 18, 19, 22, 23,
                  8, 9, 12, 13, 24, 25, 28, 29, 10, 11, 14, 15, 26, 27, 30, 31], np.int64)
_CW32 = np.array([0, 1, 4, 5, 8, 9, 12, 13, 2, 3, 6, 7, 10, 11, 14, 15], np.int64)

_BL16 = np.array([0, 2, 8, 10, 1, 3, 9, 11, 4, 6, 12, 14, 5, 7, 13, 15,
                  16, 18, 24, 26, 17, 19, 25, 27, 20, 22, 28, 30, 21, 23, 29, 31], np.int64)
_CW16 = np.array([0, 1, 4, 5, 8, 9, 12, 13, 0, 1, 4, 5, 8, 9, 12, 13,
                  2, 3, 6, 7, 10, 11, 14, 15, 2, 3, 6, 7, 10, 11, 14, 15], np.int64)
_CH16 = np.array([0] * 8 + [1] * 8 + [0] * 8 + [1] * 8, np.int64)

_BL8 = _BL32
_CW8 = np.array([
    [0, 1, 4, 5, 8, 9, 12, 13, 0, 1, 4, 5, 8, 9, 12, 13,
     2, 3, 6, 7, 10, 11, 14, 15, 2, 3, 6, 7, 10, 11, 14, 15,
     8, 9, 12, 13, 0, 1, 4, 5, 8, 9, 12, 13, 0, 1, 4, 5,
     10, 11, 14, 15, 2, 3, 6, 7, 10, 11, 14, 15, 2, 3, 6, 7],
    [8, 9, 12, 13, 0, 1, 4, 5, 8, 9, 12, 13, 0, 1, 4, 5,
     10, 11, 14, 15, 2, 3, 6, 7, 10, 11, 14, 15, 2, 3, 6, 7,
     0, 1, 4, 5, 8, 9, 12, 13, 0, 1, 4, 5, 8, 9, 12, 13,
     2, 3, 6, 7, 10, 11, 14, 15, 2, 3, 6, 7, 10, 11, 14, 15]], np.int64)
_CB8 = np.array([0] * 8 + [2] * 8 + [0] * 8 + [2] * 8 + [1] * 8 + [3] * 8 + [1] * 8 + [3] * 8, np.int64)

_BL4 = _BL16
_r0 = [0, 1, 4, 5, 8, 9, 12, 13] * 4
_r1 = [2, 3, 6, 7, 10, 11, 14, 15] * 4
_r2 = [8, 9, 12, 13, 0, 1, 4, 5] * 4
_r3 = [10, 11, 14, 15, 2, 3, 6, 7] * 4
_CW4 = np.array([_r0 + _r1 + _r2 + _r3, _r2 + _r3 + _r0 + _r1], np.int64)
_CB4 = np.array(([0] * 8 + [2] * 8 + [4] * 8 + [6] * 8) * 2 + ([1] * 8 + [3] * 8 + [5] * 8 + [7] * 8) * 2, np.int64)


def _grid(w, h, x0=0, y0=0):
    ys, xs = np.mgrid[y0:y0 + h, x0:x0 + w]
    return xs.ravel().astype(np.int64), ys.ravel().astype(np.int64)


def addr32(dbp, dbw, w, h, x0=0, y0=0):
    """Word address of every pixel (row-major) of a PSMCT32 rectangle."""
    x, y = _grid(w, h, x0, y0)
    page = x // 64 + (y // 32) * dbw
    px, py = x % 64, y % 32
    block = _BL32[px // 8 + (py // 8) * 8]
    bx, by = px % 8, py % 8
    return dbp * 64 + page * 2048 + block * 64 + (by // 2) * 16 + _CW32[bx + (by % 2) * 8]


def addr16(dbp, dbw, w, h, x0=0, y0=0):
    """Byte address of every PSMCT16 pixel."""
    x, y = _grid(w, h, x0, y0)
    page = x // 64 + (y // 64) * dbw
    px, py = x % 64, y % 64
    block = _BL16[px // 16 + (py // 8) * 4]
    bx, by = px % 16, py % 8
    k = bx + (by % 2) * 16
    word = dbp * 64 + page * 2048 + block * 64 + (by // 2) * 16 + _CW16[k]
    return word * 4 + _CH16[k] * 2


def addr8(dbp, dbw, w, h, x0=0, y0=0):
    """Byte address of every PSMT8 pixel."""
    dbw >>= 1
    x, y = _grid(w, h, x0, y0)
    page = x // 128 + (y // 64) * dbw
    px, py = x % 128, y % 64
    block = _BL8[px // 16 + (py // 16) * 8]
    bx, by = px % 16, py % 16
    column = by // 4
    k = bx + (by % 4) * 16
    word = dbp * 64 + page * 2048 + block * 64 + column * 16 + _CW8[column & 1, k]
    return word * 4 + _CB8[k]


def addr4(dbp, dbw, w, h, x0=0, y0=0):
    """(byte address, nibble 0 low / 1 high) of every PSMT4 pixel."""
    dbw >>= 1
    x, y = _grid(w, h, x0, y0)
    page = x // 128 + (y // 128) * dbw
    px, py = x % 128, y % 128
    block = _BL4[px // 32 + (py // 16) * 4]
    bx, by = px % 32, py % 16
    column = by // 4
    k = bx + (by % 4) * 32
    word = dbp * 64 + page * 2048 + block * 64 + column * 16 + _CW4[column & 1, k]
    cb = _CB4[k]
    return word * 4 + (cb >> 1), cb & 1


class GSMemory:
    SIZE = 4 * 1024 * 1024

    def __init__(self):
        self.mem = np.zeros(self.SIZE, np.uint8)

    # ---- CT32 (data: uint32 array, row-major) --------------------------------------------------
    def write_ct32(self, dbp, dbw, w, h, data, offset=0):
        a = addr32(dbp, dbw, w, h) + offset // 4
        words = self.mem.view(np.uint32)
        words[a] = np.asarray(data, np.uint32)[:len(a)]

    def read_ct32(self, dbp, dbw, w, h, offset=0):
        a = addr32(dbp, dbw, w, h) + offset // 4
        return self.mem.view(np.uint32)[a].copy()

    # ---- CT16 --------------------------------------------------------------------------------
    def write_ct16(self, dbp, dbw, w, h, data):
        a = addr16(dbp, dbw, w, h)
        d = np.asarray(data, np.uint16)[:len(a)]
        self.mem[a] = (d & 0xFF).astype(np.uint8)
        self.mem[a + 1] = (d >> 8).astype(np.uint8)

    def read_ct16(self, dbp, dbw, w, h, offset=0):
        a = addr16(dbp, dbw, w, h) + (offset // 4) * 4
        return self.mem[a].astype(np.uint16) | (self.mem[a + 1].astype(np.uint16) << 8)

    # ---- T8 (data: one byte per pixel) ---------------------------------------------------------
    def write_t8(self, dbp, dbw, w, h, data):
        a = addr8(dbp, dbw, w, h)
        self.mem[a] = np.frombuffer(bytes(data), np.uint8)[:len(a)] if not isinstance(data, np.ndarray) else data[:len(a)]

    def read_t8(self, dbp, dbw, w, h):
        return self.mem[addr8(dbp, dbw, w, h)].copy()

    # ---- T4 (data: packed, pixel 0 = low nibble) ------------------------------------------------
    def write_t4(self, dbp, dbw, w, h, data):
        a, nib = addr4(dbp, dbw, w, h)
        src = np.frombuffer(bytes(data), np.uint8) if not isinstance(data, np.ndarray) else data
        n = len(a)
        i = np.arange(n)
        need = (n + 1) // 2
        if len(src) < need:
            src = np.concatenate([src, np.zeros(need - len(src), np.uint8)])
        v = (src[i >> 1] >> ((i & 1) * 4).astype(np.uint8)) & 0xF
        lo, hi = nib == 0, nib == 1
        self.mem[a[lo]] = (self.mem[a[lo]] & 0xF0) | v[lo]
        self.mem[a[hi]] = (self.mem[a[hi]] & 0x0F) | (v[hi] << 4)

    def read_t4_indices(self, dbp, dbw, w, h):
        """One index (0-15) per pixel."""
        a, nib = addr4(dbp, dbw, w, h)
        return (self.mem[a] >> (nib * 4).astype(np.uint8)) & 0xF

    def read_t4(self, dbp, dbw, w, h):
        """Packed like PDTools' ReadTexPSMT4 (pixel 0 in the low nibble)."""
        return pack4(self.read_t4_indices(dbp, dbw, w, h))


def pack4(idx):
    idx = np.asarray(idx, np.uint8)
    if len(idx) % 2:
        idx = np.concatenate([idx, np.zeros(1, np.uint8)])
    return (idx[0::2] & 0xF) | ((idx[1::2] & 0xF) << 4)


def unpack4(data, n):
    d = np.frombuffer(bytes(data), np.uint8) if not isinstance(data, np.ndarray) else data
    out = np.empty(len(d) * 2, np.uint8)
    out[0::2] = d & 0xF
    out[1::2] = d >> 4
    return out[:n]


def bits_per_pixel(psm):
    return {PSMCT32: 32, PSMCT24: 24, PSMCT16: 16, PSMCT16S: 16, PSMT8: 8, PSMT4: 4, PSMT8H: 4,
            PSMT4HL: 4, PSMT4HH: 4, 48: 32, 49: 32, 50: 16, 58: 16}[psm]


def data_size(w, h, psm):
    """Tex1Utils.GetDataSize: Math.Round(w*h*bpp/8, AwayFromZero)."""
    v = w * h * bits_per_pixel(psm)
    return (v + 4) // 8   # halves round up (away from zero for positives)


# ---- GSPixelFormat helpers (block bookkeeping used by the texture-set builder) --------------------
class PixelFormat:
    def __init__(self, psm, page_cols, col_w, col_h, layout):
        self.psm = psm
        self.page_cols = page_cols
        self.page_rows = 32 // page_cols
        self.block_w = col_w
        self.block_h = col_h * 4
        self.page_w = self.block_w * page_cols
        self.page_h = self.block_h * self.page_rows
        self.layout = [int(v) for v in layout]
        self._inv = {v: i for i, v in enumerate(self.layout)}

    def pages_per_row(self, width):
        return max(1, ((width + self.page_w - 1) // self.page_w * self.page_w) // self.page_w)

    def last_block_index(self, x, y):
        if x == 0 and y == 0:
            return 0
        x = max(0, x - 1)
        y = max(0, y - 1)
        ppr = self.pages_per_row(x)                    # PDTools passes the DECREMENTED x
        page_x, page_y = x // self.page_w, y // self.page_h
        page = page_x + page_y * ppr
        px, py = x - page_x * self.page_w, y - page_y * self.page_h
        return (page * 32 + self.layout[px // self.block_w + (py // self.block_h) * self.page_cols]) & 0xFFFF

    def block_index(self, x, y, ppr):
        page_x, page_y = x // self.page_w, y // self.page_h
        page = page_x + page_y * ppr
        px, py = x - page_x * self.page_w, y - page_y * self.page_h
        return (page * 32 + self.layout[px // self.block_w + (py // self.block_h) * self.page_cols]) & 0xFFFF

    def position_of_block(self, block, ppr):
        if block >= MAX_BLOCKS:
            raise ValueError("Block index is above GS memory capacity (%d >= %d)." % (block, MAX_BLOCKS))
        page = block // 32
        page_x = (page % ppr) * self.page_w
        page_y = (page // ppr) * self.page_h
        idx = self._inv[block % 32]
        return page_x + (idx % self.page_cols) * self.block_w, page_y + (idx // self.page_cols) * self.block_h

    def unused_blocks(self, w, h):
        last = self.last_block_index(w, h)
        ppr = self.pages_per_row(w)
        out, first_free_v = [], -1
        for b in range(last + 1):
            bx, by = self.position_of_block(b, ppr)
            if bx >= w or by >= h:
                out.append(b)
                if first_free_v == -1 and by >= h:
                    first_free_v = b
        return out, first_free_v

    def used_blocks(self, w, h):
        last = self.last_block_index(w, h)
        ppr = self.pages_per_row(w)
        out = []
        for b in range(last + 1):
            bx, by = self.position_of_block(b, ppr)
            if bx < w and by < h:
                out.append(b)
        return out

    def tbw_width(self, x):
        return (x + self.page_w - 1) // self.page_w * self.page_w

    def palette_dims(self):
        if self.psm == PSMT4:
            return 8, 2
        if self.psm == PSMT8:
            return 16, 16
        raise ValueError("PSMCT32 does not support palettes.")


FMT_CT32 = PixelFormat(PSMCT32, 8, 8, 2, _BL32)
FMT_T8 = PixelFormat(PSMT8, 8, 16, 4, _BL8)
FMT_T4 = PixelFormat(PSMT4, 4, 32, 4, _BL4)


def pixel_format(psm):
    return {PSMT4: FMT_T4, PSMT8: FMT_T8, PSMCT32: FMT_CT32}[psm]


def swizzled_transfer_sizes(size):
    """Tex1Utils.CalculateSwizzledTransferSizes (PD: one 64 x pages*32 transfer, then 32x32, 32x16, ...)."""
    out = []
    if size >= 0x2000:
        pages = size // 0x2000
        size %= 0x2000
        out.append((64, 32 * pages))
    step, tw, th = 0x1000, 32, 32
    while size > 0:
        if size >= step:
            size %= step
            out.append((tw, th))
        if th == tw // 2:
            tw >>= 1
            th = tw
        else:
            th >>= 1
        step >>= 1
    return out

# Nenkai's research is the core of this human & machine made tool.
