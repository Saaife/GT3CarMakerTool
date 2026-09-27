"""GTM1 model sets (PDTools ModelSet1 + ModelSet1Serializer)."""

from .binio import Reader, Writer
from .commands import read_model, write_model
from .shape import PGLUshape
from .tex1 import TextureSet1
from .f32 import f32_bits

MAGIC = 0x314D5447


class PGLUmaterial:
    __slots__ = ("ambient", "diffuse", "specular", "unk_color", "unk", "flags", "unk2", "unk3")

    def __init__(self, ambient=(1.0, 1.0, 1.0, 1.0), diffuse=(1.0, 1.0, 1.0, 1.0), specular=(0.0, 0.0, 0.0, 0.0),
                 unk_color=(0.0, 0.0, 0.0, 1.0), unk=0.0, flags=0, unk2=127.0, unk3=1.0):
        self.ambient, self.diffuse, self.specular, self.unk_color = tuple(ambient), tuple(diffuse), tuple(specular), tuple(unk_color)
        self.unk, self.flags, self.unk2, self.unk3 = unk, flags, unk2, unk3

    @classmethod
    def read(cls, r):
        f = r.many("f", 16)
        unk = r.f32()
        flags = r.u32()
        unk2 = r.f32()
        unk3 = r.f32()
        return cls(f[0:4], f[4:8], f[8:12], f[12:16], unk, flags, unk2, unk3)

    def floats(self):
        return list(self.ambient) + list(self.diffuse) + list(self.specular) + list(self.unk_color)

    def write(self, w):
        for v in self.floats():
            w.f32bits(f32_bits(v))
        w.f32bits(f32_bits(self.unk))
        w.u32(self.flags)
        w.f32bits(f32_bits(self.unk2))
        w.f32bits(f32_bits(self.unk3))

    def key(self):
        """ModelSet1Serializer.GetMatTableKey entry: float bits of every field + flags."""
        vals = self.floats() + [self.unk, self.unk2, self.unk3]
        return "".join("%08X" % (f32_bits(v) & 0xFFFFFFFF) for v in vals) + "%08X;" % self.flags


class ModelSet1:
    def __init__(self):
        self.models = []            # list of command lists
        self.shapes = []
        self.materials = []
        self.texture_sets = []
        self.boundings = []         # (x, y, z, w)
        self.variation_texsets = []
        self.variation_materials = []

    @classmethod
    def read(cls, data, base=0):
        r = Reader(data, base)
        if r.u32() != MAGIC:
            raise ValueError("Not a model set stream.")
        ms = cls()
        r.u32()
        r.pos += 8
        n_models, n_shapes, n_mats, n_tex, n_vtex, n_vmat = r.many("H", 6)
        r.pos += 4
        mt, st, mo, tt, bo, vtt, vmo = r.many("I", 7)
        r.pos = base + mt
        offs = r.many("i", n_models)
        for o in offs:
            r.pos = base + o
            ms.models.append(read_model(r))
        r.pos = base + st
        offs = r.many("i", n_shapes)
        for o in offs:
            ms.shapes.append(PGLUshape.read(data, base + o))
        for i in range(n_mats):
            r.pos = base + mo + i * 0x50
            ms.materials.append(PGLUmaterial.read(r))
        sets = {}

        def tset(o):
            # one object per OFFSET: PD's variation tables point back at the same sets (a race rim's 6 variations
            # share set 0 and repeat sets 1/2) - the writer relies on that identity to lay them out like PD
            if o not in sets:
                sets[o] = TextureSet1.read(data, base + o)
            return sets[o]
        r.pos = base + tt
        offs = r.many("i", n_tex)
        for o in offs:
            if o == 0:
                continue
            ms.texture_sets.append(tset(o))
        r.pos = base + vtt
        voffs = r.many("i", n_vtex)
        for o in voffs:
            if o == 0:
                continue
            r.pos = base + o
            toffs = r.many("i", n_tex)
            ms.variation_texsets.append([tset(t) for t in toffs])
        for i in range(n_models):
            r.pos = base + bo + i * 16
            ms.boundings.append(tuple(r.many("f", 4)))
        r.pos = base + vmo
        voffs = r.many("i", n_vmat)
        for o in voffs:
            r.pos = base + o
            ms.variation_materials.append([PGLUmaterial.read(r) for _ in range(n_mats)])
        return ms

    def _write_variation_texsets(self, w, base):
        """PD's layout for variation texture sets (every race rim that blurs: 3 sets x 6 variations = 3 distance
        levels x 2 wheel models; engine 0x2243a0 picks variation [level + 3 * model] sets 1/2 for the spinning face).
        [variation table][base table] -> base sets (0x40-aligned) -> for each later variation that brings NEW sets:
        its table right after the previous set, then those sets -> the tables of variations that only reuse sets,
        packed at the end. Variation 0 IS the base table. Sets are shared by identity."""
        n = len(self.texture_sets)
        vtt = w.pos
        w.pos += len(self.variation_texsets) * 4
        tt = w.pos
        w.pos += n * 4
        at = {}

        def put(ts):
            w.align(0x40)
            at[id(ts)] = w.pos - base
            ts.serialize(w)
        for ts in self.texture_sets:
            put(ts)
        tables = [None] * len(self.variation_texsets)
        deferred = []
        for v, sets in enumerate(self.variation_texsets):
            if len(sets) != n:
                raise ValueError("variation %d has %d texture sets, the model set has %d" % (v, len(sets), n))
            if v == 0 and all(a is b for a, b in zip(sets, self.texture_sets)):
                tables[0] = tt
                continue
            new = [ts for ts in sets if id(ts) not in at]
            if not new:
                deferred.append(v)
                continue
            w.align(0x10)
            tables[v] = w.pos
            w.pos += n * 4
            for ts in dict((id(x), x) for x in new).values():
                put(ts)
        for v in deferred:
            tables[v] = w.pos
            w.pos += n * 4
        last = w.pos
        for i, ts in enumerate(self.texture_sets):
            w.pos = tt + i * 4
            w.u32(at[id(ts)])
        for v, sets in enumerate(self.variation_texsets):
            w.pos = vtt + v * 4
            w.u32(tables[v] - base)
            if tables[v] != tt:
                w.pos = tables[v]
                for ts in sets:
                    w.u32(at[id(ts)])
        w.pos = base + 0x16
        w.u16(n)
        w.u16(len(self.variation_texsets))
        w.pos = base + 0x2C
        w.u32(tt - base)
        w.pos = base + 0x34
        w.u32(vtt - base)
        w.pos = last
        return last

    # ---- ModelSet1Serializer --------------------------------------------------------------------
    def serialize(self, w=None):
        """ModelSet1Serializer.Write at w.pos (alignments are ABSOLUTE, as in PDTools: a GTTW's set at 0x20
        lays out differently from a body at 0). Returns the bytes when no writer is given."""
        own = w is None
        if own:
            w = Writer()
        base = w.pos
        w.pos = base + 0x50
        # materials
        mat_off = w.pos
        for m in self.materials:
            m.write(w)
        last = w.pos
        w.pos = base + 0x14
        w.u16(len(self.materials))
        w.pos = base + 0x28
        w.u32(mat_off - base)
        w.pos = last
        # variation material tables
        vmt = w.pos
        w.pos += len(self.variation_materials) * 4
        w.align(0x10)
        last = w.pos
        seen = {}
        for i, table in enumerate(self.variation_materials):
            key = "".join(m.key() for m in table)
            w.pos = vmt + i * 4
            if key in seen:
                w.u32(seen[key])
                w.pos = last
            else:
                off = last - base
                seen[key] = off
                w.u32(off)
                w.pos = last
                for m in table:
                    m.write(w)
            last = w.pos
        w.align(0x10)
        last = w.pos
        w.pos = base + 0x1A
        w.u16(len(self.variation_materials))
        w.pos = base + 0x38
        w.u32(vmt - base)
        w.pos = last
        # model / shape offset tables
        mtab = w.pos
        w.pos += len(self.models) * 4
        w.align(0x10)
        stab = w.pos
        w.pos += len(self.shapes) * 4
        w.align(0x10)
        # boundings
        bo = w.pos
        for i in range(len(self.models)):
            for v in self.boundings[i]:
                w.f32bits(f32_bits(v))
        last = w.pos
        w.pos = base + 0x30
        w.u32(bo - base)
        w.pos = last
        # models
        last = w.pos
        for i, cmds in enumerate(self.models):
            w.pos = mtab + i * 4
            w.u32(last - base)
            w.pos = last
            write_model(w, cmds)
            last = w.pos
        w.align(0x10)
        last = w.pos
        w.pos = base + 0x10
        w.u16(len(self.models))
        w.pos = base + 0x20
        w.u32(mtab - base)
        w.pos = last
        # shapes
        last = w.pos
        for i, s in enumerate(self.shapes):
            w.pos = stab + i * 4
            w.u32(last - base)
            w.pos = last
            s.write(w)
            last = w.pos
        w.pos = base + 0x12
        w.u16(len(self.shapes))
        w.pos = base + 0x24
        w.u32(stab - base)
        w.pos = last
        w.align(0x10)
        # texture sets
        if self.variation_texsets:
            last = self._write_variation_texsets(w, base)
        else:
            tt = w.pos
            w.pos += len(self.texture_sets) * 4
            w.align(0x40)
            last = w.pos
            for i, ts in enumerate(self.texture_sets):
                w.pos = tt + i * 4
                w.u32(last - base)
                w.pos = last
                ts.serialize(w)
                w.align(0x40)
                last = w.pos
            w.pos = base + 0x16
            w.u16(len(self.texture_sets))
            w.pos = base + 0x2C
            w.u32(tt - base)
            w.pos = base + 0x18
            w.u16(0)
            w.pos = last
        end = w.pos
        w.pos = base
        w.u32(MAGIC)
        w.pos = end
        return w.getvalue() if own else None

# Nenkai's research is the core of this human & machine made tool.
