"""PGLUshape (VIF packet geometry): read, write, decode (PDTools GetShapeData) and the GT3-native
shape encoder (GT4TrackKit Gt4ShapeBuilder, the framings the car builder uses)."""

import struct

import numpy as np

from .binio import Reader
from .f32 import f32, bits_f32, f32_bits

# GIF / prim
PRIM_TRISTRIP, PRIM_IIP, PRIM_TME, PRIM_FGE, PRIM_ABE = 0x04, 0x08, 0x10, 0x20, 0x40
REG_RGBAQ, REG_ST, REG_XYZF2 = 0x01, 0x02, 0x04

OP_STMOD, OP_STMASK, OP_STROW, OP_STCOL, OP_UNPACK = 0x05, 0x20, 0x30, 0x31, 0x60
UNPACK_USN = 0x4000
COMPRESSED_POS_DIVISOR = 500.0

MAX_PACKET_VERTS = 60
MAX_TWEEN_PACKET_VERTS = 30


class GIFTag:
    __slots__ = ("nloop", "eop", "id", "pre", "prim", "flag", "regs")

    def __init__(self, nloop=0, eop=False, pre=False, prim=0, flag=0, regs=(0, 0, 0, 0), id=0):
        self.nloop, self.eop, self.pre, self.prim, self.flag, self.regs, self.id = nloop, eop, pre, prim, flag, list(regs), id

    @classmethod
    def read(cls, r):
        g = cls()
        f = r.u16()
        g.nloop = f & 0x7FFF
        g.eop = (f >> 15) & 1 != 0
        r.u16()
        f2 = r.u32()
        g.id = f2 & 0x3FFF
        g.pre = (f2 >> 14) & 1 != 0
        g.prim = (f2 >> 15) & 0x7FF
        g.flag = (f2 >> 26) & 3
        regs = r.u32()
        g.regs = [(regs >> (4 * i)) & 0xF for i in range(4)]
        return g

    def write(self, w):
        w.u16(((1 if self.eop else 0) << 15) | (self.nloop & 0x7FFF))
        w.u16(0)
        w.u32((3 << 28) | ((self.flag & 3) << 26) | ((self.prim & 0x7FF) << 15) | ((1 if self.pre else 0) << 14) | (self.id & 0x3FFF))
        v = 0
        for i in range(4):
            v |= (self.regs[i] & 0xF) << (4 * i)
        w.u32(v)


class VIFCommand:
    """data: list of tuples; dtype 'i' (int32), 'h' (int16) or 'B' (byte) per element."""
    __slots__ = ("vu", "num", "op", "irq", "gif", "data", "dtype", "row", "mask")

    def __init__(self, vu=0, num=0, op=0, gif=None, data=None, dtype=None):
        self.vu, self.num, self.op, self.irq, self.gif = vu, num, op, False, gif
        self.data = data if data is not None else []
        self.dtype = dtype
        self.row = self.mask = None

    @classmethod
    def read(cls, r):
        c = cls()
        c.vu = r.u16()
        c.num = r.u8()
        bits = r.u8()
        c.op = bits & 0x7F
        c.irq = (bits >> 7) & 1 == 1
        if c.vu == 0xC0C0:
            c.gif = GIFTag.read(r)
        elif c.op == OP_STROW:
            c.row = r.many("i", 4)
        elif c.op == OP_STMASK:
            c.mask = r.many("i", 1)
        elif c.op == OP_STCOL:
            c.mask = r.many("i", 4)
        elif c.op & OP_UNPACK:
            ft = c.op & 3
            n = ((c.op >> 2) & 3) + 1
            if ft != 3:
                code = "ihB"[ft]
                c.dtype = code
                size = struct.calcsize(code) * n
                fmt = struct.Struct("<%d%s" % (n, code))
                b, p = r.b, r.pos
                if p + size * c.num > len(b):
                    raise EOFError("VIF unpack past the end")
                c.data = [fmt.unpack_from(b, p + k * size) for k in range(c.num)]
                r.pos = p + size * c.num
        r.align(4)
        return c

    def write(self, w):
        w.u16(self.vu)
        w.u8(self.num)
        w.u8(((1 if self.irq else 0) << 7) | (self.op & 0x7F))
        if self.vu == 0xC0C0:
            self.gif.write(w)
        elif self.op == OP_STROW and self.row is not None:
            w.many("i", self.row)
        elif self.op in (OP_STMASK, OP_STCOL) and self.mask is not None:
            w.many("i", self.mask)
        elif self.data:
            code = self.dtype
            n = len(self.data[0])
            fmt = struct.Struct("<%d%s" % (n, code))
            w.write(b"".join(fmt.pack(*e) for e in self.data))
        w.align(4)


class VIFPacket:
    __slots__ = ("commands",)

    def __init__(self, commands=None):
        self.commands = commands if commands is not None else []

    @classmethod
    def read(cls, r, qwc):
        p = cls()
        base = r.pos
        while r.pos < base + qwc * 16:
            p.commands.append(VIFCommand.read(r))
        return p

    def write(self, w):
        for c in self.commands:
            c.write(w)
        w.align(0x10)


class VIFDescriptor:
    __slots__ = ("offset", "qwc", "tex", "mat")

    def __init__(self, tex=0, mat=0):
        self.offset = self.qwc = 0
        self.tex, self.mat = tex, mat


class PGLUshape:
    def __init__(self, unk1=0, unk2=0, unk3=0):
        self.unk1, self.unk2, self.unk3 = unk1, unk2, unk3
        self.total_strip_verts = 0
        self.num_triangles = 0
        self.descriptors = []
        self.packets = []

    @classmethod
    def read(cls, data, base):
        r = Reader(data, base)
        s = cls()
        r.i32()
        r.u32()
        bits = r.u8()
        s.unk1 = bits & 0x1F
        s.unk2 = bits >> 5
        s.unk3 = r.u8()
        n = r.u16()
        s.total_strip_verts = r.u16()
        s.num_triangles = r.u16()
        for _ in range(n):
            d = VIFDescriptor()
            d.offset = r.u32()
            d.qwc = r.u16()
            f = r.u16()
            d.tex = f & 0x1FF
            d.mat = (f >> 9) & 0x7F
            s.descriptors.append(d)
        for d in s.descriptors:
            r.pos = base + d.offset
            s.packets.append(VIFPacket.read(r, d.qwc))
        return s

    def write(self, w):
        base = w.pos
        w.u32(0)
        w.u32(0)
        w.u8(((self.unk2 & 7) << 5) | (self.unk1 & 0x1F))
        w.u8(self.unk3)
        w.u16(len(self.descriptors))
        w.u16(self.total_strip_verts)
        w.u16(self.num_triangles)
        desc_pos = w.pos
        w.pos += len(self.descriptors) * 8
        w.align(0x10)
        last = w.pos
        for i, p in enumerate(self.packets):
            start = w.pos
            p.write(w)
            last = w.pos
            qwc = (w.pos - start) // 16
            w.pos = desc_pos + i * 8
            w.u32(start - base)
            w.u16(qwc)
            d = self.descriptors[i]
            w.u16(((d.mat << 9) | (d.tex & 0x1FF)) & 0xFFFF)
            w.pos = last
        w.pos = last

    # ---- decode (PDTools GetShapeData, GT3 paths + the compressed chain) -------------------------
    def decode(self):
        out = ShapeData(self)
        face_start = 1
        stmod = 0
        row = [0, 0, 0]
        for j, packet in enumerate(self.packets):
            desc = self.descriptors[j]
            nloop = -1
            for c in packet.commands:
                if c.vu == 0xC0C0 and c.gif is not None:
                    nloop = c.gif.nloop
                    break
            second = -1
            pv, puv, pn, pc = [], [], [], []
            resets = None
            for c in packet.commands:
                if c.op == OP_STMOD:
                    stmod = c.vu & 3
                    continue
                if c.op == OP_STROW:
                    if c.row is not None:
                        row = [c.row[0], c.row[1], c.row[2]]
                    continue
                if c.vu == 0xC0C0 or not c.data:
                    continue
                if not (c.op & OP_UNPACK):
                    continue
                addr = c.vu & 0x3FF
                usn = (c.vu & UNPACK_USN) != 0
                if addr < 0x40:
                    if nloop > 0 and addr >= nloop and second < 0:
                        second = addr
                    while len(pv) < addr:
                        pv.append(pv[-1] if pv else (0.0, 0.0, 0.0))
                    for e in c.data:
                        if c.dtype == "h" and len(e) >= 3:
                            vx, vy, vz = e[0], e[1], e[2]
                        elif c.dtype == "B" and len(e) >= 3:
                            vx, vy, vz = (e[0], e[1], e[2]) if usn else (_s8(e[0]), _s8(e[1]), _s8(e[2]))
                        elif c.dtype == "i" and len(e) >= 3:
                            if usn:
                                pv.append((bits_f32(e[0]), bits_f32(e[1]), bits_f32(e[2])))
                                continue
                            vx, vy, vz = e[0], e[1], e[2]
                        else:
                            continue
                        if stmod == 2:
                            row = [row[0] + vx, row[1] + vy, row[2] + vz]
                            o = row
                        elif stmod == 1:
                            o = [row[0] + vx, row[1] + vy, row[2] + vz]
                        else:
                            o = [vx, vy, vz]
                        out.is_compressed = True
                        pv.append(tuple(f32(float(np.float32(k) / np.float32(COMPRESSED_POS_DIVISOR))) for k in o))
                elif addr < 0x80:
                    if c.dtype == "B" and len(c.data[0]) == 1:
                        resets = c
                        continue
                    first = c.data[0]
                    if not (len(first) == 3 and c.dtype in ("B", "i")):
                        while len(puv) < addr - 0x40:
                            puv.append(puv[-1] if puv else (0.0, 0.0))
                    for e in c.data:
                        if c.dtype == "i" and len(e) == 2 and usn:
                            puv.append((bits_f32(e[0]), float(np.float32(1.0) - np.float32(bits_f32(e[1])))))
                        elif len(e) == 2:
                            if c.dtype == "h":
                                u, v = e
                            elif c.dtype == "B":
                                u, v = (e[0], e[1]) if usn else (_s8(e[0]), _s8(e[1]))
                            else:
                                u, v = e
                            if stmod == 2:
                                row = [row[0] + u, row[1] + v, row[2]]
                                ou, ov = row[0], row[1]
                            elif stmod == 1:
                                ou, ov = row[0] + u, row[1] + v
                            else:
                                ou, ov = u, v
                            puv.append((float(np.float32(ou) / np.float32(4096)),
                                        float(np.float32(1.0) - np.float32(ov) / np.float32(4096))))
                        elif c.dtype == "i" and len(e) == 3:
                            pn.append((bits_f32(e[0]), bits_f32(e[1]), bits_f32(e[2])))
                        elif c.dtype == "B" and len(e) == 3:
                            v = np.array([_s8(e[0]), _s8(e[1]), _s8(e[2])], np.float32) + np.float32(1e-6)
                            ln = np.sqrt(np.float32(v[0] * v[0] + v[1] * v[1]) + np.float32(v[2] * v[2]))
                            pn.append(tuple(float(k) for k in (v / np.float32(ln))))
                else:
                    for e in c.data:
                        if c.dtype == "B" and len(e) >= 4:
                            pc.append((e[0], e[1], e[2], e[3]))
                        elif c.dtype == "B" and len(e) == 3:
                            pc.append((e[0], e[1], e[2], 0x80))
                        elif c.dtype == "i" and len(e) == 3:
                            pn.append((bits_f32(e[0]), bits_f32(e[1]), bits_f32(e[2])))
            tween = None
            if nloop > 0 and len(pv) > nloop:
                if second >= nloop and len(pv) >= second + nloop:
                    tween = pv[second:second + nloop]
                del pv[nloop:]
            if nloop > 0 and len(puv) > nloop:
                del puv[nloop:]
            if nloop > 0 and len(pc) > nloop:
                del pc[nloop:]
            tween_n = pn[nloop:2 * nloop] if nloop > 0 and len(pn) >= 2 * nloop else None
            if nloop > 0 and len(pn) > nloop:
                del pn[nloop:]
            if not pv:
                continue
            if resets is None or len(resets.data) < 2:
                continue
            nv = len(pv)
            out.vertices.extend(pv)
            out.normals.extend(pn)
            if tween is not None and len(tween) == nv:
                out.tween.extend(tween)
                out.has_morph = True
                out.tween_normals.extend(tween_n if tween_n is not None and len(tween_n) == nv else pn)
            else:
                out.tween.extend(pv)
                out.tween_normals.extend(pn)
            if puv:
                while len(puv) < nv:
                    puv.append(puv[-1])
            if pc:
                while len(pc) < nv:
                    pc.append(pc[-1])
            out.uvs.extend(puv)
            out.colors.extend(pc)
            ri = 1
            nxt = (resets.data[ri][0] + 6) // 3
            odd = False
            fi = 0
            for _ in range(nv):
                if fi + 2 >= nv:
                    break
                if not odd:
                    out.faces.append((face_start + fi, face_start + fi + 1, face_start + fi + 2, desc.mat, desc.tex))
                    odd = True
                else:
                    out.faces.append((face_start + fi + 2, face_start + fi + 1, face_start + fi, desc.mat, desc.tex))
                    odd = False
                if fi + 3 == nxt:
                    ri += 1
                    if ri < len(resets.data):
                        nxt += (resets.data[ri][0] + 6) // 3
                    else:
                        break
                    fi += 3
                    odd = False
                else:
                    fi += 1
            face_start += nv
        out.uses_external = any(d.tex == 511 for d in self.descriptors)
        return out


def _s8(b):
    return b - 256 if b >= 128 else b


class ShapeData:
    def __init__(self, shape):
        self.unk1, self.unk2, self.unk3 = shape.unk1, shape.unk2, shape.unk3
        self.vertices, self.uvs, self.normals, self.colors = [], [], [], []
        self.faces = []                   # (a, b, c, mat, tex), 1-based
        self.tween, self.tween_normals = [], []
        self.has_morph = False
        self.is_compressed = False
        self.uses_external = False


# ---- encoder (Gt4ShapeBuilder: GT3-native, env-map and tween framings) --------------------------------
class StripVertex:
    __slots__ = ("pos", "uv", "color", "normal", "pos_next", "normal_next", "color_next")

    def __init__(self, pos, uv=(0.0, 0.0), color=(0x80, 0x80, 0x80, 0x80), normal=(0.0, 0.0, 0.0),
                 pos_next=(0.0, 0.0, 0.0), normal_next=(0.0, 0.0, 0.0), color_next=None):
        self.pos, self.uv, self.color, self.normal = pos, uv, color, normal
        self.pos_next, self.normal_next, self.color_next = pos_next, normal_next, color_next


class StripGroup:
    def __init__(self, tex=0, mat=0, prim=PRIM_TRISTRIP | PRIM_IIP | PRIM_TME | PRIM_FGE):
        self.tex, self.mat, self.prim = tex, mat, prim
        self.strips = []


TWEEN_NONE, TWEEN_WAVE, TWEEN_FLASH = 0, 1, 2


def build_shape(groups, native=False, unk1=0, env_map=False, unk2=0, tween=TWEEN_NONE, lit=False):
    if not native and not env_map and tween == TWEEN_NONE:
        raise NotImplementedError("only the GT3-native framings are ported")
    if env_map:
        shape = PGLUshape(2, 0, 0x01)
    else:
        shape = PGLUshape(unk1, unk2, 0x00)
    cap = MAX_TWEEN_PACKET_VERTS if tween != TWEEN_NONE else MAX_PACKET_VERTS
    totals = [0, 0]
    for g in groups:
        if not g.strips:
            continue
        chunk, cv = [], 0
        for strip in g.strips:
            if len(strip) < 3:
                raise ValueError("Each strip needs >= 3 vertices.")
            if len(strip) > cap:
                raise ValueError("A single strip has %d verts (> %d); split it upstream." % (len(strip), cap))
            if cv + len(strip) > cap and chunk:
                _emit(shape, g, chunk, env_map, tween, lit, totals)
                chunk, cv = [], 0
            chunk.append(strip)
            cv += len(strip)
        if chunk:
            _emit(shape, g, chunk, env_map, tween, lit, totals)
    shape.total_strip_verts = totals[0] & 0xFFFF
    shape.num_triangles = totals[1] & 0xFFFF
    return shape


def _emit(shape, g, strips, env_map, tween, lit, totals):
    verts = [v for s in strips for v in s]
    for s in strips:
        totals[1] += len(s) - 2
    n = len(verts)
    totals[0] += n
    reset = [(len(strips) & 0xFF,)]
    for s in strips:
        b = 3 * len(s) - 6
        if b < 0 or b > 255:
            raise ValueError("Strip length %d out of range for reset byte (3K-6 must be 0..255, so K 2..87)." % len(s))
        reset.append((b,))

    def gif():
        return VIFCommand(0xC0C0, 1, 0x68, gif=GIFTag(nloop=n, eop=True, pre=True, prim=g.prim, flag=0,
                                                       regs=(REG_ST, REG_RGBAQ, REG_XYZF2, REG_RGBAQ)))

    def vec3(addr, sel):
        return VIFCommand(addr, n & 0xFF, 0x68, data=[tuple(f32_bits(k) for k in sel(v)) for v in verts], dtype="i")

    def colors(addr, sel):
        return VIFCommand(addr, n & 0xFF, 0x6E, data=[tuple(sel(v)[:4]) for v in verts], dtype="B")

    def uvs(addr, op):
        return VIFCommand(addr, n & 0xFF, op, data=[(f32_bits(v.uv[0]), f32_bits(v.uv[1])) for v in verts], dtype="i")

    def resets(op):
        return VIFCommand(0xC040, (len(strips) + 1) & 0xFF, op, data=list(reset), dtype="B")

    cmds = []
    if tween != TWEEN_NONE:
        cmds += [gif(), vec3(0xC000, lambda v: v.pos), vec3(0xC020, lambda v: v.pos_next), resets(0x62)]
        if tween == TWEEN_WAVE:
            cmds += [vec3(0xC080, lambda v: v.normal), vec3(0xC0A0, lambda v: v.normal_next)]
        else:
            cmds += [colors(0xC080, lambda v: v.color), colors(0xC0A0, lambda v: v.color_next or v.color)]
        cmds += [uvs(0xC040, 0x74), uvs(0xC060, 0x74)]
    elif env_map:
        cmds += [gif(), vec3(0xC000, lambda v: v.pos), resets(0x62), colors(0xC080, lambda v: v.color),
                 VIFCommand(0xC040, n & 0xFF, 0x78, data=[tuple(f32_bits(k) for k in v.normal) for v in verts], dtype="i")]
    else:
        cmds += [gif(), vec3(0xC000, lambda v: v.pos), resets(0x62),
                 vec3(0xC080, lambda v: v.normal) if lit else colors(0xC080, lambda v: v.color), uvs(0xC040, 0x74)]
    shape.packets.append(VIFPacket(cmds))
    shape.descriptors.append(VIFDescriptor(g.tex, g.mat))

# Nenkai's research is the core of this human & machine made tool.
