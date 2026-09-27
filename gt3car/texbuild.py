"""Tex1 texture-set builder: a port of PDTools TextureSetBuilder (with GT4TrackKit's PD-layout packing:
palette merging, shared variant bitmaps, bitmap dedup, CSA-packed CLUT blocks, paint-colour CLUT patches).

Every ordering rule of the C# is kept (LINQ stable sorts, HashSet first-appearance order, and the .NET
Dictionary slot reuse that decides which free GS block is tried first), so the output is byte-identical."""

import math

import numpy as np

from . import gs
from .tex1 import TextureSet1, PGLUtexture, GSTransfer, ClutPatch
from .imagesharp import wu_quantize as quantize

MAX_BLOCKS = gs.MAX_BLOCKS


def pow2_up(v):
    """BitOperations.RoundUpToPowerOf2 (uint)."""
    if v <= 1:
        return v if v >= 0 else 0
    return 1 << (int(v) - 1).bit_length()


def is_pow2(v):
    return v > 0 and (v & (v - 1)) == 0


def log2_byte(v):
    """(byte)Math.Log(v, 2) = Log(v) / Log(2), truncated."""
    return int(math.log(v) / math.log(2)) & 0xFF


def ps2_alpha(a):
    """(byte)Math.Round(Normalize(a, 0, 255, 0, 128)) - banker's rounding, like Python's round."""
    return round((a / 255.0) * 128.0)


_PS2A = np.array([ps2_alpha(a) for a in range(256)], np.uint32)


def pack_rgba(rgba):
    """uint32 PackedValue (R | G<<8 | B<<16 | A<<24) of (..., 4) uint8 pixels."""
    rgba = np.asarray(rgba, np.uint8)
    return (rgba[..., 0].astype(np.uint32) | rgba[..., 1].astype(np.uint32) << 8 |
            rgba[..., 2].astype(np.uint32) << 16 | rgba[..., 3].astype(np.uint32) << 24)


def scale_alpha(c):
    """A packed colour with its alpha scaled 0-255 -> PS2 0-128."""
    return (c & 0x00FFFFFF) | (int(_PS2A[c >> 24]) << 24)


def tiled_from_linear(pal):
    """MakeTiledPaletteFromLinearPalette (256 entries)."""
    out = [0] * 256
    i = 0
    for ty in range(8):
        for tx in range(2):
            for y in range(2):
                for x in range(8):
                    out[(ty * 2 + y) * 16 + tx * 8 + x] = pal[i]
                    i += 1
    return out


def first_appearance_colours(packed, limit=None):
    """Distinct values in raster order (a HashSet filled in scan order, then ToList). Returns None when
    more than `limit` colours exist."""
    flat = packed.ravel()
    uniq, first = np.unique(flat, return_index=True)
    if limit is not None and len(uniq) > limit:
        return None
    return [int(v) for v in uniq[np.argsort(first, kind="stable")]]


class TextureConfig:
    def __init__(self, fmt=gs.PSMT4, wrap_s=gs.REGION_CLAMP, wrap_t=gs.REGION_CLAMP, is_texture_map=False,
                 repeat_w=0, repeat_h=0):
        self.fmt, self.wrap_s, self.wrap_t = fmt, wrap_s, wrap_t
        self.is_texture_map = is_texture_map
        self.repeat_w, self.repeat_h = repeat_w, repeat_h


class GSBlock:
    __slots__ = ("index", "csa")

    def __init__(self, index, csa):
        self.index, self.csa = index, csa


class NetDict:
    """System.Collections.Generic.Dictionary<int, GSBlock> enumeration order: entries live in slots, a removed
    slot goes on a LIFO free list and the next Add reuses it; enumeration walks the slots in index order."""

    def __init__(self):
        self.slots = []
        self.where = {}
        self.vals = {}
        self.free = []

    def add(self, key, val):
        if key in self.where:
            raise KeyError("An item with the same key has already been added. Key: %d" % key)
        if self.free:
            i = self.free.pop()
            self.slots[i] = key
        else:
            i = len(self.slots)
            self.slots.append(key)
        self.where[key] = i
        self.vals[key] = val

    def remove(self, key):
        i = self.where.pop(key, None)
        if i is None:
            return False
        self.slots[i] = None
        self.free.append(i)
        del self.vals[key]
        return True

    def __contains__(self, key):
        return key in self.where

    def get(self, key):
        return self.vals.get(key)

    def values(self):
        return [self.vals[k] for k in self.slots if k is not None]


class TextureTask:
    def __init__(self):
        self.tex = None               # PGLUtexture
        self.rgba = None              # (h, w, 4) uint8
        self.indexed = None           # (h, w) uint8
        self.fmt = None               # gs.PixelFormat
        self.packed = None            # bytes
        self.palette = None           # list of packed uint32 (PS2 alpha); PSMT8 = tiled order
        self.size_blocks = 0
        self.duplicate_of = None
        self.pinned = False
        self.colour_palettes = None   # per colour, LINEAR palette (PS2 alpha)
        self.unused_blocks = []
        self.first_free_v = -1

    @property
    def w(self):
        return self.rgba.shape[1]

    @property
    def h(self):
        return self.rgba.shape[0]


def _create_image_data(t, psm):
    if psm in (gs.PSMT4, gs.PSMT8):
        idx = t.indexed.ravel()
        t.packed = bytes(gs.pack4(idx)) if psm == gs.PSMT4 else idx.astype(np.uint8).tobytes()
    else:
        px = t.rgba.reshape(-1, 4).copy()
        px[:, 3] = _PS2A[px[:, 3]].astype(np.uint8)
        t.packed = px.tobytes()


class TextureSetBuilder:
    def __init__(self, colour_count=1):
        self.textures = []
        self.pglu = []
        self.unused = NetDict()
        self.used = []
        self.used_set = set()
        self.used_max = -1
        self.last_free_v = -1
        self.tbp = 0
        self.palette_block_end = 0
        self.gs = gs.GSMemory()
        self.colour_count = colour_count
        self.colour_variants = {}       # (colour, texture) -> rgba
        self.patch_sets = []

    @property
    def texture_count(self):
        return len(self.textures)

    def _use(self, b):
        self.used.append(b)
        self.used_set.add(b)
        if b > self.used_max:
            self.used_max = b

    # ---- adding images ----------------------------------------------------------------------------
    def add_image(self, rgba, cfg, resize=None):
        """rgba: (h, w, 4) uint8. resize(rgba, w, h) is used for the texture-map pow2 resize."""
        if cfg.is_texture_map:
            nw, nh = pow2_up(rgba.shape[1]), pow2_up(rgba.shape[0])
            if (nw, nh) != (rgba.shape[1], rgba.shape[0]):
                if resize is None:
                    raise ValueError("texture needs a power-of-two resize")
                rgba = resize(rgba, nw, nh)
        h, w = rgba.shape[:2]
        tex = PGLUtexture()
        tex.psm = cfg.fmt
        wp2, hp2 = pow2_up(w), pow2_up(h)
        if cfg.wrap_s == gs.REGION_CLAMP:
            tex.tw = log2_byte(wp2)
        else:
            if not is_pow2(cfg.repeat_w):
                raise ValueError("Repeat width must be a power of 2.")
            tex.tw = log2_byte(cfg.repeat_w)
        if cfg.wrap_t == gs.REGION_CLAMP:
            tex.th = log2_byte(hp2)
        else:
            if not is_pow2(cfg.repeat_h):
                raise ValueError("Repeat height must be a power of 2.")
            tex.th = log2_byte(cfg.repeat_h)
        tex.tbw = ((w + 63) // 64) & 0xFF
        if tex.psm in (gs.PSMT4, gs.PSMT8) and (tex.tbw & 1):
            tex.tbw += 1
        tex.wms, tex.wmt = cfg.wrap_s, cfg.wrap_t
        tex.maxu, tex.maxv = w - 1, h - 1
        if tex.psm in (gs.PSMT4, gs.PSMT8):
            tex.cld = 1
        tex.tcc = 1
        t = TextureTask()
        t.rgba = rgba
        t.fmt = gs.pixel_format(tex.psm)
        t.size_blocks = (t.fmt.last_block_index(w, h) + 1) & 0xFFFF
        t.unused_blocks, t.first_free_v = t.fmt.unused_blocks(w, h)
        t.tex = tex
        if tex.psm in (gs.PSMT4, gs.PSMT8):
            n = 256 if tex.psm == gs.PSMT8 else 16
            packed = pack_rgba(rgba)
            cols = first_appearance_colours(packed, n)
            full = [0] * n
            if cols is None:
                pal, idx = quantize(rgba, n)
                t.indexed = idx
                for i, c in enumerate(pal[:n]):
                    full[i] = c
            else:
                lut = {c: i for i, c in enumerate(cols)}
                uniq, inv = np.unique(packed.ravel(), return_inverse=True)
                t.indexed = np.array([lut[int(u)] for u in uniq], np.uint8)[inv].reshape(h, w)
                full[:len(cols)] = cols
            full = [scale_alpha(c) for c in full]
            t.palette = tiled_from_linear(full) if tex.psm == gs.PSMT8 else full
        self.pglu.append(tex)
        _create_image_data(t, tex.psm)
        self.textures.append(t)
        return len(self.textures) - 1

    def set_colour_variant(self, colour, index, rgba):
        if colour < 1:
            raise ValueError("colour 0 is the base texture")
        self.colour_variants[(colour, index)] = rgba

    # ---- build ---------------------------------------------------------------------------------------
    def build(self, resize=None):
        if len(self.patch_sets) <= 1:
            self._refine_for_colours(resize)
            self._share_variant_bitmaps()
            self._merge_shared_palettes()
        self._dedup_bitmaps()
        self._write_textures()
        self._write_palettes()
        ts_patches = self._write_colour_patches()
        if self.palette_block_end > self.tbp:
            self.tbp = self.palette_block_end
        ts = TextureSet1()
        ts.textures = self.pglu
        ts.clut_patch_sets = ts_patches
        ts.transfers = self._swizzled_transfers() if len(self.textures) >= 2 else self._transfers(ts_patches)
        ts.total_block_size = self.tbp & 0xFFFF
        return ts

    def _merge_shared_palettes(self):
        groups = {}
        for t in self.textures:
            if t.palette is not None and t.indexed is not None:
                groups.setdefault(t.tex.psm, []).append(t)
        for psm, group in groups.items():
            cap = 16 if psm == gs.PSMT4 else 0
            if cap == 0:
                continue
            pools = []
            for t in group:
                if t.pinned or t.colour_palettes is not None:
                    continue
                mine = list(dict.fromkeys(t.palette))
                pool = None
                for p in pools:
                    if len(set(p[0]) | set(mine)) <= cap:
                        pool = p
                        break
                if pool is None:
                    pools.append((list(mine), [t]))
                    continue
                for c in mine:
                    if c not in pool[0]:
                        pool[0].append(c)
                pool[1].append(t)
            for colours, members in pools:
                if len(members) < 2:
                    continue
                shared = [0] * cap
                shared[:len(colours)] = colours
                pos = {}
                for i, c in enumerate(colours):
                    pos.setdefault(c, i)
                for t in members:
                    m = np.array([pos[c] for c in t.palette], np.uint8)
                    t.indexed = m[t.indexed]
                    t.palette = shared
                    _create_image_data(t, t.tex.psm)

    def _col_table(self, t, nc):
        """keyOf / Col: per palette index, its colour in every paint colour."""
        return [tuple(t.palette[i] if c == 0 or t.colour_palettes is None else t.colour_palettes[c][i] for c in range(nc))
                for i in range(len(t.palette))]

    def _share_variant_bitmaps(self):
        nc = max(1, self.colour_count)
        cands = [t for t in self.textures if t.indexed is not None and t.palette is not None and not t.pinned
                 and t.tex.psm == gs.PSMT4]
        cands.sort(key=lambda t: -len(np.unique(t.indexed)))          # OrderByDescending, stable
        groups = []                                                     # [w, h, cls, members]
        for t in cands:
            h, w = t.indexed.shape
            keys = self._col_table(t, nc)[:16]
            key_id = {}
            kid = np.array([key_id.setdefault(k, len(key_id)) for k in keys], np.int64)
            pix = kid[t.indexed.ravel().astype(np.int64)]              # this texture's key class per pixel
            placed = False
            for g in groups:
                if g[0] != w or g[1] != h:
                    continue
                pairs = g[2] * 64 + pix                                  # (group class, own key) - both < 64
                uniq, first, inv = np.unique(pairs, return_index=True, return_inverse=True)
                if len(uniq) > 16:
                    continue
                order = np.argsort(np.argsort(first, kind="stable"), kind="stable")   # ids by first appearance
                g[2] = order[inv].astype(np.int64)
                g[3].append(t)
                placed = True
                break
            if not placed:
                uniq, first, inv = np.unique(pix, return_index=True, return_inverse=True)
                order = np.argsort(np.argsort(first, kind="stable"), kind="stable")
                groups.append([w, h, order[inv].astype(np.int64), [t]])
        for w, h, cls, members in groups:
            if len(members) < 2:
                continue
            shared = cls.astype(np.uint8).reshape(h, w)
            for t in members:
                old = t.indexed.ravel()
                table = self._col_table(t, nc)
                pals = [[0] * 16 for _ in range(nc)]
                # every pixel writes pals[c][cls] = Col(t, c, old); the last pixel of each class wins (all agree)
                last = {}
                for k, o in zip(cls.tolist(), old.tolist()):
                    last[k] = o
                for k, o in last.items():
                    for c in range(nc):
                        pals[c][k] = table[o][c]
                t.indexed = shared.copy()
                t.palette = pals[0]
                if t.colour_palettes is not None:
                    t.colour_palettes = pals
                t.pinned = True
                _create_image_data(t, t.tex.psm)

    def _refine_for_colours(self, resize):
        nc = max(1, self.colour_count)
        if nc < 2 or not self.colour_variants:
            return
        for ti, t in enumerate(self.textures):
            if t.indexed is None or t.palette is None:
                continue
            if not any((c, ti) in self.colour_variants for c in range(1, nc)):
                continue
            t8 = t.tex.psm == gs.PSMT8
            cap = 256 if t8 else 16
            h, w = t.h, t.w
            imgs = [t.rgba]
            for c in range(1, nc):
                v = self.colour_variants.get((c, ti), t.rgba)
                if v.shape[:2] != (h, w):
                    print("  WARNING: texture %d: its colour %d texture is %dx%d, the base is %dx%d - resized; "
                          "a colour texture should be a recolour of the base (Make colour textures redoes it)"
                          % (ti, c, v.shape[1], v.shape[0], w, h))
                    v = resize(v, w, h)
                imgs.append(v)
            stack = np.stack([pack_rgba(im).ravel().astype(np.uint64) for im in imgs], axis=1)   # (n, nc)
            uniq, first, inv = np.unique(stack, axis=0, return_index=True, return_inverse=True)
            inv = inv.ravel()
            order = np.argsort(first, kind="stable")
            if len(uniq) > cap:
                # resampled art blends colours: keep the base's indexing, average each index per colour
                idx = t.indexed.ravel().astype(np.int64)
                cnt = np.bincount(idx, minlength=256)
                base_linear = None if t8 else t.palette
                apals = []
                for c in range(nc):
                    im = imgs[c].reshape(-1, 4).astype(np.float64)
                    sums = [np.bincount(idx, weights=im[:, k], minlength=256) for k in range(4)]
                    pal = []
                    for i in range(cap):
                        if cnt[i] == 0:
                            pal.append(base_linear[i] if base_linear is not None else 0)
                            continue
                        r, g, b, a = (int(round(sums[k][i] / cnt[i])) for k in range(4))
                        a = ps2_alpha(a)
                        pal.append(r | g << 8 | b << 16 | a << 24)
                    apals.append(pal)
                if not t8:
                    apals[0] = t.palette
                t.colour_palettes = apals
                continue
            rank = np.empty(len(order), np.int64)
            rank[order] = np.arange(len(order))
            tuples = uniq[order]
            pals = [[0] * cap for _ in range(nc)]
            for c in range(nc):
                for i in range(len(tuples)):
                    pals[c][i] = scale_alpha(int(tuples[i][c]))
            t.indexed = rank[inv].astype(np.uint8).reshape(h, w)
            t.colour_palettes = pals
            t.palette = tiled_from_linear(pals[0]) if t8 else pals[0]
            _create_image_data(t, t.tex.psm)

    def _dedup_bitmaps(self):
        seen = {}
        for t in self.textures:
            if t.packed is None:
                continue
            key = (t.w, t.h, t.tex.psm, t.packed)
            if key in seen:
                t.duplicate_of = seen[key]
            else:
                seen[key] = t

    def _can_fit(self, blocks, csa=8):
        for block in self.unused.values():
            if len(blocks) == 1 and block.csa + csa <= 8:
                return block.index
            ok = True
            for b in blocks:
                bb = self.unused.get(block.index + b)
                if bb is None or (len(blocks) > 1 and bb.csa > 0):
                    ok = False
                    break
            if ok:
                return block.index
        return -1

    def _write_textures(self):
        for t in sorted(self.textures, key=lambda t: -t.size_blocks):
            if t.duplicate_of is not None:
                continue
            blocks = t.fmt.used_blocks(t.w, t.h)
            fit = self._can_fit(blocks)
            if fit != -1:
                for b in blocks:
                    self.unused.remove((fit + b) & 0xFFFF)
                    self._use((fit + b) & 0xFFFF)
                t.tex.tbp = fit & 0xFFFF
                self._gs_write(t)
                continue
            after = -1
            if self.tbp != 0 and self.last_free_v != -1:
                for bi in range(self.last_free_v, self.tbp):
                    if all(((bi + b) & 0xFFFF) not in self.used_set for b in blocks):
                        after = bi
                        break
                if after != -1:
                    for b in blocks:
                        self.unused.remove((after + b) & 0xFFFF)
                        self._use((after + b) & 0xFFFF)
                    t.tex.tbp = after & 0xFFFF
                    self.last_free_v = (after + t.first_free_v) & 0xFFFF
                    self.tbp = max(self.tbp, self.used_max + 1) & 0xFFFF
                    self._gs_write(t)
                    continue
            t.tex.tbp = self.tbp
            self.last_free_v = (self.tbp + t.first_free_v) & 0xFFFF
            for u in t.unused_blocks:
                idx = (self.tbp + u) & 0xFFFF
                self.unused.add(idx, GSBlock(idx, 0))
            for b in blocks:
                self._use((self.tbp + b) & 0xFFFF)
            if self.tbp + t.size_blocks >= MAX_BLOCKS:
                raise MemoryError("Textures take more space than the maximum GS memory capacity (%d >= %d)."
                                  % (self.tbp + t.size_blocks, MAX_BLOCKS))
            self._gs_write(t)
            self.tbp = (self.tbp + t.size_blocks) & 0xFFFF
        for t in self.textures:
            if t.duplicate_of is not None:
                t.tex.tbp = t.duplicate_of.tex.tbp
                t.tex.tbw = t.duplicate_of.tex.tbw

    def _gs_write(self, t):
        psm = t.tex.psm
        if psm == gs.PSMCT32:
            self.gs.write_ct32(t.tex.tbp, t.tex.tbw, t.w, t.h, np.frombuffer(t.packed, np.uint32))
        elif psm == gs.PSMT8:
            self.gs.write_t8(t.tex.tbp, t.tex.tbw, t.w, t.h, np.frombuffer(t.packed, np.uint8))
        elif psm == gs.PSMT4:
            self.gs.write_t4(t.tex.tbp, t.tex.tbw, t.w, t.h, np.frombuffer(t.packed, np.uint8))
        else:
            raise NotImplementedError("Format %d not implemented" % psm)

    def _fit_palette(self, psm, width, height, palette):
        blocks = gs.FMT_CT32.used_blocks(width, height)
        size = width * height * 4
        taken = min(size // 32, 8)
        csa = 0
        idx = self._can_fit(blocks, taken)
        if idx != -1:
            cbp = idx & 0xFFFF
            if len(blocks) == 1:
                block = self.unused.get(idx + blocks[0])
                csa = block.csa
                block.csa = (block.csa + taken) & 0xFF
                if block.csa >= 8:
                    self.unused.remove(block.index)
                    self._use(block.index & 0xFFFF)
                    self.tbp = max(self.tbp, block.index + 1) & 0xFFFF
            else:
                for b in blocks:
                    self.unused.remove((idx + b) & 0xFFFF)
                    self._use((idx + b) & 0xFFFF)
        else:
            if self.palette_block_end > self.tbp:
                self.tbp = self.palette_block_end & 0xFFFF
            for partial in self.unused.values():
                if partial.csa > 0 and partial.index >= self.tbp:
                    self.tbp = (partial.index + 1) & 0xFFFF
            cbp = self.tbp
            self.tbp = (self.tbp + len(blocks)) & 0xFFFF
            if len(blocks) == 1:
                csa = 0
                b0 = cbp + blocks[0]
                if taken < 8:
                    self.unused.add(b0, GSBlock(b0, taken))
                else:
                    self._use(b0 & 0xFFFF)
            else:
                for b in blocks:
                    self._use((cbp + b) & 0xFFFF)
        for b in blocks:
            self.palette_block_end = max(self.palette_block_end, cbp + b + 1)
        data = np.array(palette, np.uint32)
        if psm == gs.PSMT8:
            self.gs.write_ct32(cbp, 1, 16, 16, data, csa * 32)
        elif psm == gs.PSMT4:
            self.gs.write_ct32(cbp, 1, 8, 2, data, csa * 32)
        return cbp, csa & 0xFF

    def _write_palettes(self):
        for t in self.textures:
            if t.palette is not None:
                t8 = t.tex.psm == gs.PSMT8
                cbp, csa = self._fit_palette(t.tex.psm, 16 if t8 else 8, 16 if t8 else 2, t.palette)
                t.tex.cbp, t.tex.csa = cbp, csa

    def _write_colour_patches(self):
        nc = max(1, self.colour_count)
        if nc < 2 or len(self.patch_sets) > 1:
            return []
        sets = []
        for c in range(nc):
            ps = []
            for i, t in enumerate(self.textures):
                cbp, csa = t.tex.cbp, t.tex.csa
                if c > 0 and t.colour_palettes is not None and t.palette is not None:
                    t8 = t.tex.psm == gs.PSMT8
                    stored = tiled_from_linear(t.colour_palettes[c]) if t8 else list(t.colour_palettes[c])
                    if stored != list(t.palette):
                        cbp, csa = self._fit_palette(t.tex.psm, 16 if t8 else 8, 16 if t8 else 2, stored)
                ps.append(ClutPatch(csa, cbp, gs.PSMCT32, i))
            sets.append(ps)
        return sets

    def _transfers(self, patch_sets):
        out, uploaded = [], set()
        for t in self.textures:
            out.append(GSTransfer(t.tex.tbp, t.tex.tbw, t.fmt.psm, t.w, t.h, t.packed))
            if t.palette is not None:
                cbp = t.tex.cbp
                if cbp in uploaded:
                    continue
                if t.tex.psm == gs.PSMT8:
                    out.append(GSTransfer(cbp, 4, gs.PSMCT32, 16, 16, self.gs.read_ct32(cbp, 1, 16, 16).tobytes()))
                else:
                    out.append(GSTransfer(cbp, 1, gs.PSMCT32, 8, 8, self.gs.read_ct32(cbp, 1, 8, 8).tobytes()))
                uploaded.add(cbp)
        for ps in patch_sets:
            for p in ps:
                cbp = p.cbp
                if cbp in uploaded or p.tex >= len(self.textures):
                    continue
                t8 = self.textures[p.tex].tex.psm == gs.PSMT8
                side = 16 if t8 else 8
                out.append(GSTransfer(cbp, 4 if t8 else 1, gs.PSMCT32, side, side, self.gs.read_ct32(cbp, 1, side, side).tobytes()))
                uploaded.add(cbp)
        return out

    def _swizzled_transfers(self):
        out, tbp = [], 0
        for w, h in gs.swizzled_transfer_sizes(self.tbp * 256):
            data = self.gs.read_ct32(tbp, 1, w, h).tobytes()
            out.append(GSTransfer(tbp, 1, gs.PSMCT32, w, h, data))
            tbp += len(data) // 256
        return out

# Nenkai's research is the core of this human & machine made tool.
