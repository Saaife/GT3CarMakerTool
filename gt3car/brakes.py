"""data/race/brake.bin ("GTBR") - the brake textures EVERY car shares; a car picks its disc + caliper by number in its
car info (GTCI BrakeParameters, one record front, one rear). RE'd 2026-09-25 (layout: Nenkai's GTBR template; engine
PAL core: loader 0x2230a8 -> 0x222e88 relocates +0x10/+0x20/+0x24, setter 0x223110, draw 0x2239fc/0x223a44):

  0x00 'GTBR'  0x04 load base (0 in the file)  0x0C file size  0x10 -> TextureSet1
  0x14 disc start (1)  0x18 caliper start (8)  0x1C glow texture (0)  0x20 -> disc params  0x24 -> caliper params
  disc params    16 B per disc     (int flag, 3 unused)          - textures [disc start, caliper start)
  caliper params 32 B per caliper  (3 floats, 5 ints)            - textures [caliper start, count)

The game: disc texture = disc start + (GTCI disc - 1)   -> the GTCI disc number IS the texture number
          caliper      = caliper start + (GTCI caliper - 2), and GTCI caliper 1 = NO caliper (the draw is skipped)

Adding keeps every existing texture byte-exact (all cars point at them): the new images get their own texture set,
moved to the first free GS page after the old data and merged in; new discs go in front of the calipers and the caliper
start moves up by one each - caliper numbers are relative, so no existing car changes."""

import struct

from . import gs
from .binio import Writer
from .imgio import read_image
from .tex1 import TextureSet1
from .texbuild import TextureSetBuilder, TextureConfig

MAGIC = b"GTBR"
HEADER = 0x30
DISC_REC, CAL_REC = 16, 32


class BrakeFile:
    def __init__(self, data):
        if data[:4] != MAGIC:
            raise ValueError("not a GT3 brake file (GTBR)")
        (_, _, _, self.size, tso, self.disc_start, self.caliper_start, self.glow,
         dpo, cpo) = struct.unpack_from("<4s9I", data, 0)
        self.tail = data[0x28:HEADER]
        self.texset = TextureSet1.read(data, tso)
        n = len(self.texset.textures)
        nd, nc = self.caliper_start - self.disc_start, n - self.caliper_start
        self.disc_params = [data[dpo + DISC_REC * i:dpo + DISC_REC * (i + 1)] for i in range(nd)]
        self.caliper_params = [data[cpo + CAL_REC * i:cpo + CAL_REC * (i + 1)] for i in range(nc)]

    @classmethod
    def load(cls, path):
        with open(path, "rb") as f:
            return cls(f.read())

    # ---- what a car's GTCI number means ----------------------------------------------------------------------
    def discs(self):
        """[(GTCI disc number, texture index, flag)]"""
        return [(t, t, struct.unpack_from("<i", self.disc_params[t - self.disc_start])[0])
                for t in range(self.disc_start, self.caliper_start)]

    def calipers(self):
        """[(GTCI caliper number, texture index, (x, y, z))] - GTCI 1 = no caliper"""
        return [(t - self.caliper_start + 2, t, struct.unpack_from("<3f", self.caliper_params[t - self.caliper_start]))
                for t in range(self.caliper_start, len(self.texset.textures))]

    def image(self, tex):
        return self.texset.image(tex)

    # ---- writing ---------------------------------------------------------------------------------------------
    def to_bytes(self):
        dpo = HEADER
        cpo = dpo + DISC_REC * len(self.disc_params)
        tso = cpo + CAL_REC * len(self.caliper_params)
        w = Writer()
        w.write(bytes(HEADER))
        w.write(b"".join(self.disc_params))
        w.write(b"".join(self.caliper_params))
        w.pos = tso
        self.texset.serialize(w)
        data = bytearray(w.getvalue())
        struct.pack_into("<4s9I", data, 0, MAGIC, 0, 0, len(data), tso, self.disc_start, self.caliper_start,
                         self.glow, dpo, cpo)
        data[0x28:HEADER] = self.tail
        return bytes(data)

    # ---- adding ------------------------------------------------------------------------------------------------
    def add(self, discs=(), calipers=(), caliper_like=None):
        """discs / calipers: lists of (h, w, 4) RGBA images (discs 64x64 like PD's, calipers 32x32; other sizes are
        resampled to those). Returns ([new GTCI disc numbers], [new GTCI caliper numbers]). An image already in the
        file (same pixels at the same size) is reused instead of added again."""
        disc_ids, cal_ids, new_d, new_c = [], [], [], []
        for img in discs:
            hit = self._find(img, range(self.disc_start, self.caliper_start))
            disc_ids.append(hit if hit is not None else ("new", len(new_d)))
            if hit is None:
                new_d.append(img)
        for img in calipers:
            hit = self._find(img, range(self.caliper_start, len(self.texset.textures)))
            cal_ids.append(hit - self.caliper_start + 2 if hit is not None else ("new", len(new_c)))
            if hit is None:
                new_c.append(img)
        if new_d or new_c:
            add = self._build(new_d, new_c)
            ts = self.texset
            # the first free GS PAGE (32 blocks) after the old data: swizzled texels keep their page layout
            end = max(t.bp + (len(t.data) + 255) // 256 for t in ts.transfers)
            off = (end + 31) // 32 * 32
            for t in add.transfers:
                t.bp += off
            for t in add.textures:
                t.tbp += off
                t.cbp += off
            ts.transfers += add.transfers
            nd = len(new_d)
            dtex, ctex = add.textures[:nd], add.textures[nd:]
            ts.textures[self.caliper_start:self.caliper_start] = dtex        # discs sit in front of the calipers
            ts.textures += ctex
            like = self.caliper_params[(caliper_like - 2) if caliper_like and caliper_like >= 2 else 0]
            self.disc_params += [struct.pack("<4i", 1, 0, 0, 0)] * nd
            self.caliper_params += [like] * len(ctex)
            first_disc = self.caliper_start
            self.caliper_start += nd
            ts.total_block_size = max(t.bp + (len(t.data) + 255) // 256 for t in ts.transfers)
            ts.invalidate()
            n_before_c = len(ts.textures) - len(ctex)
            disc_ids = [d if not isinstance(d, tuple) else first_disc + d[1] for d in disc_ids]
            cal_ids = [c if not isinstance(c, tuple) else (n_before_c + c[1]) - self.caliper_start + 2 for c in cal_ids]
        return disc_ids, cal_ids

    def _find(self, img, rng):
        import numpy as np
        for t in rng:
            try:
                have = self.texset.image(t)
            except Exception:
                continue
            if have.shape == img.shape and np.array_equal(have, img):
                return t
        return None

    @staticmethod
    def _build(discs, calipers):
        """PD's formats: discs 64x64 PSMT8 (256 colours), calipers 32x32 PSMT4 (16) - other sizes resampled."""
        from .build import bicubic
        b = TextureSetBuilder()
        for img, (w, fmt) in [(d, (64, gs.PSMT8)) for d in discs] + [(c, (32, gs.PSMT4)) for c in calipers]:
            if img.shape[:2] != (w, w):
                img = bicubic(img, w, w)
            b.add_image(img, TextureConfig(fmt=fmt), resize=bicubic)
        return b.build(resize=bicubic)


def load_images(paths):
    return [read_image(p) for p in paths]


def _fingerprint(img, kind):
    import hashlib
    return kind + ":" + hashlib.sha1(b"%dx%d|" % (img.shape[1], img.shape[0]) + img.tobytes()).hexdigest()


def add_to_file(path, discs=(), calipers=(), caliper_like=None, out=None):
    """Adds custom brake art to a brake.bin (in place unless `out`): the FIRST change keeps a copy as <file>.bak.
    A sidecar <file>.gt3carmaker.json remembers which source image became which number, so exporting the same car
    again reuses its entries (a quantized caliper no longer matches its source pixel for pixel).
    Returns ([GTCI disc numbers], [GTCI caliper numbers]) in the order given."""
    import json
    import os
    import shutil
    b = BrakeFile.load(path)
    side = (out or path) + ".gt3carmaker.json"
    known = {}
    if os.path.exists(side):
        with open(side, encoding="utf-8") as f:
            known = json.load(f)
    nd, nc = len(b.disc_params), len(b.caliper_params)

    def valid(kind, num):
        return (1 <= num <= nd) if kind == "disc" else (2 <= num <= nc + 1)

    want_d = [(img, _fingerprint(img, "disc")) for img in discs]
    want_c = [(img, _fingerprint(img, "caliper")) for img in calipers]
    todo_d = [img for img, fp in want_d if not (fp in known and valid("disc", known[fp]))]
    todo_c = [img for img, fp in want_c if not (fp in known and valid("caliper", known[fp]))]
    ds, cs = b.add(todo_d, todo_c, caliper_like)
    it_d, it_c = iter(ds), iter(cs)
    res_d, res_c = [], []
    for img, fp in want_d:
        known[fp] = known[fp] if fp in known and valid("disc", known[fp]) else next(it_d)
        res_d.append(known[fp])
    for img, fp in want_c:
        known[fp] = known[fp] if fp in known and valid("caliper", known[fp]) else next(it_c)
        res_c.append(known[fp])
    data = b.to_bytes()
    dst = out or path
    old = b""
    if os.path.exists(dst):
        with open(dst, "rb") as f:
            old = f.read()
    if data != old:
        if not out and not os.path.exists(path + ".bak"):
            shutil.copyfile(path, path + ".bak")
        with open(dst, "wb") as f:
            f.write(data)
    with open(side, "w", encoding="utf-8") as f:
        json.dump(known, f, indent=1)
    return res_d, res_c

# Nenkai's research is the core of this human & machine made tool.
