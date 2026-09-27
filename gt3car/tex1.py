"""Tex1 texture sets (PDTools TextureSet1): read, decode to RGBA, serialize."""

import numpy as np

from .binio import Reader, Writer, align_up
from . import gs

MAGIC = 0x31786554
HEADER_SIZE = 0x30


# ---- GS register records (0x28 bytes = 5 LE u64, fields packed from bit 0) ------------------------
_TEX0 = (("tbp", 14), ("tbw", 6), ("psm", 6), ("tw", 4), ("th", 4), ("tcc", 1), ("tfx", 2),
         ("cbp", 14), ("cpsm", 4), ("csm", 1), ("csa", 5), ("cld", 3))
_TEX1 = (("lcm", 1), ("pad01", 1), ("mxl", 3), ("mmag", 1), ("mmin", 3), ("mtba", 1), (None, 9),
         ("l", 2), (None, 11), ("k", 12), (None, 20))
_MIP1 = (("tbp1", 14), ("tbw1", 6), ("tbp2", 14), ("tbw2", 6), ("tbp3", 14), ("tbw3", 6), (None, 4))
_MIP2 = (("tbp4", 14), ("tbw4", 6), ("tbp5", 14), ("tbw5", 6), ("tbp6", 14), ("tbw6", 6), (None, 4))
_CLAMP = (("wms", 2), ("wmt", 2), ("minu", 10), ("maxu", 10), ("minv", 10), ("maxv", 10), (None, 20))
_LAYOUT = (_TEX0, _TEX1, _MIP1, _MIP2, _CLAMP)


class PGLUtexture:
    """The 0x28-byte register block of one texture. Fields are plain attributes (see _LAYOUT)."""

    def __init__(self):
        for grp in _LAYOUT:
            for name, _ in grp:
                if name:
                    setattr(self, name, 0)
        self.mmag = 1          # SCE_GS_LINEAR (PDTools defaults)
        self.mmin = 1
        self.wms = 1           # SCE_GS_CLAMP
        self.wmt = 1

    @classmethod
    def read(cls, r):
        t = cls()
        v = int.from_bytes(r.bytes(0x28), "little")
        bit = 0
        for grp in _LAYOUT:
            for name, n in grp:
                if name:
                    setattr(t, name, (v >> bit) & ((1 << n) - 1))
                bit += n
        return t

    def to_bytes(self):
        v, bit = 0, 0
        for grp in _LAYOUT:
            for name, n in grp:
                if name:
                    v |= (getattr(self, name) & ((1 << n) - 1)) << bit
                bit += n
        return v.to_bytes(0x28, "little")

    def copy(self):
        t = PGLUtexture()
        t.__dict__.update(self.__dict__)
        return t


class GSTransfer:
    __slots__ = ("data_offset", "bp", "bw", "fmt", "width", "height", "data")

    def __init__(self, bp=0, bw=0, fmt=0, width=0, height=0, data=b""):
        self.data_offset = 0
        self.bp, self.bw, self.fmt, self.width, self.height, self.data = bp, bw, fmt, width, height, data

    @classmethod
    def read(cls, r, base):
        t = cls()
        t.data_offset = r.u32()
        t.bp = r.u16()
        t.bw = r.u8()
        t.fmt = r.u8()
        t.width = r.u16()
        t.height = r.u16()
        r.pos = base + t.data_offset
        t.data = r.bytes(gs.data_size(t.width, t.height, t.fmt))
        return t

    def write(self, w):
        w.u32(self.data_offset)
        w.u16(self.bp)
        w.u8(self.bw)
        w.u8(self.fmt)
        w.u16(self.width)
        w.u16(self.height)


class ClutPatch:
    __slots__ = ("csa", "cbp", "fmt", "tex")

    def __init__(self, csa=0, cbp=0, fmt=0, tex=0):
        self.csa, self.cbp, self.fmt, self.tex = csa, cbp, fmt, tex

    @classmethod
    def from_bits(cls, bits):
        return cls(bits & 0x1F, (bits >> 5) & 0x3FFF, (bits >> 19) & 0xF, bits >> 23)

    def bits(self):
        return ((self.csa & 0x1F) | (self.cbp & 0x3FFF) << 5 | (self.fmt & 0xF) << 19 | (self.tex << 23)) & 0xFFFFFFFF


class ClutAnimation:
    def __init__(self, tex):
        self.tex = tex
        self.palettes = []
        self.durations = []


class TextureSet1:
    def __init__(self):
        self.total_block_size = 0
        self.textures = []          # PGLUtexture
        self.transfers = []         # GSTransfer
        self.clut_patch_sets = []   # list of list[ClutPatch]
        self.clut_anims = []
        self._gs = None

    # ---- read ---------------------------------------------------------------------------------
    @classmethod
    def read(cls, data, base=0):
        r = Reader(data, base)
        if len(data) - base < HEADER_SIZE:
            raise ValueError("TextureSet1 header size too small")
        if r.u32() != MAGIC:
            raise ValueError("Expected Tex1 magic")
        r.u32()
        r.u32()
        size = r.i32()
        if len(data) - base < size:
            raise ValueError("Texture data provided is smaller than texture set specified size.")
        ts = cls()
        r.pos = base + 0x10
        r.i16()
        ts.total_block_size = r.u16()
        ntex, ntr = r.u16(), r.u16()
        tex_off, tr_off, patch_off, anim_off = r.u32(), r.u32(), r.u32(), r.u32()
        r.pos = base + tex_off
        for _ in range(ntex):
            ts.textures.append(PGLUtexture.read(r))
        for i in range(ntr):
            r.pos = base + tr_off + i * 12
            ts.transfers.append(GSTransfer.read(r, base))
        if anim_off != 0 and anim_off + 8 <= size:
            ts._read_anims(r, base, anim_off, size)
        if patch_off != 0 and patch_off + 4 <= size:
            r.pos = base + patch_off
            n = r.u32()
            if n > 0 and patch_off + 4 + n * 4 <= size:
                offs = r.many("I", n)
                for po in offs:
                    if po + 4 > size:
                        continue
                    r.pos = base + po
                    cnt = r.u32()
                    if cnt == 0 or po + 4 + cnt * 4 > size:
                        continue
                    ts.clut_patch_sets.append([ClutPatch.from_bits(r.u32()) for _ in range(cnt)])
        return ts

    def _read_anims(self, r, base, anim_off, size):
        r.pos = base + anim_off
        cnt, recs = r.u32(), r.u32()
        if not (0 < cnt < 4096 and recs + cnt * 32 <= size):
            return
        for i in range(cnt):
            r.pos = base + recs + i * 32
            r.u16()
            r.u16()
            r.u16()
            keys = r.u16()
            dest = r.u32()
            keys_off = r.u32()
            if keys == 0 or keys_off + keys * 8 > size:
                continue
            owner = -1
            for t, tex in enumerate(self.textures):
                if owner >= 0:
                    break
                pal_off, best = 0, -1
                for tr in self.transfers:
                    if tr.fmt == gs.PSMCT32 and tr.bp <= tex.cbp and tr.bp > best:
                        best, pal_off = tr.bp, tr.data_offset
                if best >= 0 and pal_off + (tex.cbp - best) * 256 + tex.csa * 32 == dest:
                    owner = t
            if owner < 0:
                continue
            an = ClutAnimation(owner)
            for k in range(keys):
                r.pos = base + keys_off + k * 8
                sec = r.f32()
                po = r.u32()
                if po + 64 > size:
                    an.palettes.clear()
                    break
                r.pos = base + po
                an.palettes.append(r.bytes(64))
                an.durations.append(sec)
            if len(an.palettes) >= 2:
                self.clut_anims.append(an)

    # ---- GS memory + decode ---------------------------------------------------------------------
    def gs_memory(self):
        if self._gs is None:
            m = gs.GSMemory()
            for t in self.transfers:
                if t.fmt == gs.PSMCT16:
                    m.write_ct16(t.bp, t.bw, t.width, t.height, np.frombuffer(t.data, np.uint16))
                elif t.fmt in (gs.PSMCT32, gs.PSMCT24):
                    m.write_ct32(t.bp, t.bw, t.width, t.height, np.frombuffer(t.data[:len(t.data) // 4 * 4], np.uint32))
                elif t.fmt in (gs.PSMT4, gs.PSMT4HL, gs.PSMT4HH):
                    m.write_t4(t.bp, t.bw, t.width, t.height, t.data)
                elif t.fmt == gs.PSMT8:
                    m.write_t8(t.bp, t.bw, t.width, t.height, np.frombuffer(t.data, np.uint8))
                else:
                    raise NotImplementedError("transfer format %d not supported" % t.fmt)
            self._gs = m
        return self._gs

    def invalidate(self):
        self._gs = None

    def image(self, index, var_index=0, crop=True):
        """RGBA uint8 array (h, w, 4) exactly as PDTools GetTextureImage decodes it."""
        tex = self.textures[index]
        patch = None
        if var_index < len(self.clut_patch_sets):
            patch = next((p for p in self.clut_patch_sets[var_index] if p.tex == index), None)
        return decode(self.gs_memory(), tex, patch, crop)

    # ---- serialize ----------------------------------------------------------------------------
    def serialize(self, w):
        """Write at w.pos (TextureSet1.Serialize). Leaves w.pos at the unpadded end like PDTools."""
        base = w.pos
        w.write(bytes(HEADER_SIZE))
        tex_off = w.pos - base
        for t in self.textures:
            w.write(t.to_bytes())
        anim_patch, anim_off = [], 0
        live = [a for a in self.clut_anims if 0 <= a.tex < len(self.textures) and len(a.palettes) >= 2]
        if live:
            w.align(0x10)
            pool, pal_pos = [], []
            for an in live:
                for p in an.palettes:
                    if p not in pool:
                        pool.append(p)
            for p in pool:
                pal_pos.append(w.pos)
                w.write(p)
            key_pos = []
            for an in live:
                key_pos.append(w.pos)
                for f, p in enumerate(an.palettes):
                    w.f32(max(an.durations[f], 0.01) if f < len(an.durations) else 0.5)
                    w.u32(pal_pos[pool.index(p)] - base)
            recs = w.pos
            for i, an in enumerate(live):
                w.u16(0)
                w.u16(0)
                w.u16(16)
                w.u16(len(an.palettes))
                anim_patch.append((w.pos, an.tex))
                w.u32(0)
                w.u32(key_pos[i] - base)
                w.write(bytes(16))
            anim_off = w.pos - base
            w.u32(len(live))
            w.u32(recs - base)
            w.u32(0)
            w.u32(0)
            w.align(0x10)
        tr_off = w.pos - base
        data_off = align_up(tr_off + len(self.transfers) * 12, 0x10)
        last = w.pos
        last_pad = w.pos
        for i, t in enumerate(self.transfers):
            w.pos = base + tr_off + i * 12
            t.data_offset = data_off
            t.write(w)
            w.pos = base + data_off
            w.write(t.data)
            last = w.pos
            w.align(0x10)
            last_pad = last
            data_off = w.pos - base
        patch_off = 0
        if self.clut_patch_sets:
            patch_off = w.pos - base
            w.u32(len(self.clut_patch_sets))
            table = w.pos
            last = w.pos + len(self.clut_patch_sets) * 4
            for i, ps in enumerate(self.clut_patch_sets):
                w.pos = table + i * 4
                w.u32(last - base)
                w.pos = last
                w.u32(len(ps))
                for p in ps:
                    w.u32(p.bits())
                last = w.pos
            w.align(0x10)
            last = w.pos
            last_pad = w.pos
        if anim_patch:
            save = w.pos
            for pos, ti in anim_patch:
                pt = self.textures[ti]
                pal_off, best = 0, -1
                for t in self.transfers:
                    if t.fmt == gs.PSMCT32 and t.bp <= pt.cbp and t.bp > best:
                        best, pal_off = t.bp, t.data_offset
                if best < 0:
                    continue
                w.pos = pos
                w.u32(pal_off + (pt.cbp - best) * 256 + pt.csa * 32)
            w.pos = save
        size = last - base
        w.pos = base
        w.u32(MAGIC)
        w.u32(0)
        w.u32(0)
        w.u32(size)
        w.u16(0)
        w.u16(self.total_block_size)
        w.u16(len(self.textures))
        w.u16(len(self.transfers))
        w.u32(tex_off)
        w.u32(tr_off)
        w.u32(patch_off)
        w.u32(anim_off)
        w.pos = last_pad

    def to_bytes(self):
        w = Writer()
        self.serialize(w)
        return w.getvalue()


# ---- decode ----------------------------------------------------------------------------------------
def _norm_alpha(a):
    """(byte)Normalize(a, 0, 128, 0, 255): a/128*255 in double, truncated, wrapped to a byte."""
    return (np.floor(a.astype(np.float64) / 128.0 * 255.0).astype(np.int64) & 0xFF).astype(np.uint8)


def _ct16_to_32(p16):
    p16 = p16.astype(np.uint32)
    r = (p16 & 0x1F) << 3
    g = ((p16 >> 5) & 0x1F) << 3
    b = ((p16 >> 10) & 0x1F) << 3
    a = np.where((p16 >> 15) == 1, 0x80, 0).astype(np.uint32)
    return r | g << 8 | b << 16 | a << 24


def tiled_palette(pal):
    """MakeTiledPalette: 8x2 tiles of a 16x16 CLUT."""
    out = np.zeros_like(pal)
    i = 0
    for ty in range(8):
        for tx in range(2):
            for y in range(2):
                for x in range(8):
                    out[(ty * 2 + y) * 16 + tx * 8 + x] = pal[i]
                    i += 1
    return out


_TILE_IDX = None


def _tile_index():
    global _TILE_IDX
    if _TILE_IDX is None:
        _TILE_IDX = tiled_palette(np.arange(256))
    return _TILE_IDX


def read_palette(m, psm, cbp, csa, pal_fmt):
    """uint32 CLUT (16 or 256 entries) as PDTools reads it (alpha still 0-128, untiled)."""
    n_w, n_h = (8, 2) if psm in (gs.PSMT4, gs.PSMT4HH, gs.PSMT4HL) else (16, 16)
    if pal_fmt == gs.PSMCT32:
        return m.read_ct32(cbp, 1, n_w, n_h, csa * 32)
    if pal_fmt == gs.PSMCT16:
        return _ct16_to_32(m.read_ct16(cbp, 1, n_w, n_h, csa * 32))
    raise NotImplementedError("Invalid or not supported palette format %d" % pal_fmt)


def decode(m, tex, patch=None, crop=True):
    fw, fh = 1 << tex.tw, 1 << tex.th
    cbp = patch.cbp if patch is not None else tex.cbp
    csa = patch.csa if patch is not None else tex.csa
    pal_fmt = patch.fmt if patch is not None and patch.fmt in (gs.PSMCT16, gs.PSMCT32) else tex.cpsm
    psm = tex.psm
    if psm in (gs.PSMT4, gs.PSMT4HH, gs.PSMT4HL, gs.PSMT8):
        if psm == gs.PSMT8:
            idx = m.read_t8(tex.tbp, tex.tbw, fw, fh)
        else:
            idx = m.read_t4_indices(tex.tbp, tex.tbw, fw, fh)
        pal = read_palette(m, psm, cbp, csa, pal_fmt)
        rgba = pal.view(np.uint8).reshape(-1, 4).copy()
        rgba[:, 3] = _norm_alpha(rgba[:, 3])
        if psm == gs.PSMT8:
            rgba = rgba[_tile_index()]
        img = rgba[idx.astype(np.int64)]
    elif psm in (gs.PSMCT32, gs.PSMCT24):
        img = m.read_ct32(tex.tbp, tex.tbw, fw, fh).view(np.uint8).reshape(-1, 4).copy()
        if psm == gs.PSMCT24:
            img[:, 3] = 0xFF
        else:
            img[:, 3] = _norm_alpha(img[:, 3])
    else:
        raise NotImplementedError("Not implemented format: %d" % psm)
    img = img.reshape(fh, fw, 4)
    if crop:
        cw = min(tex.maxu + 1, fw) if tex.wms in (gs.REGION_CLAMP, gs.REGION_REPEAT) else fw
        ch = min(tex.maxv + 1, fh) if tex.wmt in (gs.REGION_CLAMP, gs.REGION_REPEAT) else fh
        if cw >= 1 and ch >= 1 and (cw < fw or ch < fh):
            img = img[:ch, :cw]
    return np.ascontiguousarray(img)

# Nenkai's research is the core of this human & machine made tool.
