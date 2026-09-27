"""gt3-car-build: a complete GT3 car from a car spec, no donor (port of GT4TrackKit Gt3CarBuild.cs).

Writes, like PD's disc: cars/day, cars/night, cars/eve (= night), menu/cars, wheel, menu/wheel - or one file
with spec.target = race | menu."""

import json
import math
import os

from . import commands as C
from . import gs
from .carinfo import carinfo_read, carinfo_write, tire_write, wheel_write, container, GTTR_MAGIC
from .f32 import f32, v_add, v_sub, v_cross, v_len, v_len_sq, v_div, v_min, v_max
from .infopoints import info_defaults, apply_info_points, body_points, auto_hull, auto_ground
from .modelset import ModelSet1, PGLUmaterial
from .imgio import read_image
from .imagesharp import resize as bicubic
from .shape import StripGroup, StripVertex, build_shape, MAX_PACKET_VERTS, MAX_TWEEN_PACKET_VERTS, TWEEN_WAVE
from .shape import PRIM_TRISTRIP, PRIM_IIP, PRIM_TME, PRIM_FGE, PRIM_ABE
from .spec import (load_spec, sp, dget, DRAW_DEFAULTS, MORPH_DEFAULTS, parse_kind, GsState, target_state, transition,
                   shape_flags, morph_param, fnum, CUTOUT_BLEND, SOFT, SOFT_Z, DEPTH, REFLECT, REFLECT_MASKED,
                   ADDITIVE, GLINT, BLEND, SHADOW, LAMP)
from .texbuild import TextureSetBuilder, TextureConfig, pow2_up
from .tex1 import TextureSet1
from .rimblur import pd_blur_sets, attach

DAY, NIGHT, MENU = 0, 1, 2
RACE_CAP, MENU_CAP = 0xA8000, 0xC8000
LOD_LEVELS = 4
MAX_TEX_DIM = 1024

log = print


def car_path(base, p):
    return p if os.path.isabs(p) else os.path.join(base, p)


def levels(spec, key, default):
    lv = sp(spec, "lod_levels")
    return lv[key] if isinstance(lv, dict) and key in lv else default


def share_of(share, k):
    """LOD k reuses LOD j (j < k, j not itself reused) - or -1."""
    if share is not None and k < len(share) and share[k] >= 0 and share[k] < k and _share_own(share, share[k]):
        return share[k]
    return -1


def _share_own(share, j):
    return j >= len(share) or share[j] < 0


# ---- images (loaded once, in memory - the C# tool resampled through temp PNGs) ------------------------------
class Images:
    def __init__(self):
        self.cache = {}

    def load(self, path):
        key = os.path.normcase(os.path.abspath(path))
        if key not in self.cache:
            self.cache[key] = read_image(path)      # PNG, TGA or BMP
        return self.cache[key]


def _cap_size(img, max_dim):
    """CapTextureSize: an image over max_dim is resampled to a power of two <= max_dim."""
    h, w = img.shape[:2]
    if max_dim <= 0 or (w <= max_dim and h <= max_dim):
        return img
    k = f32(max_dim / f32(float(max(w, h))))
    nw = max(8, 1 << int(math.floor(math.log2(max(8.0, f32(w * k))))))
    nh = max(8, 1 << int(math.floor(math.log2(max(8.0, f32(h * k))))))
    return bicubic(img, nw, nh)


def _lod_size(img, div):
    """LodTexture: PD's low-detail copy, width and height / div, kept powers of two."""
    if div <= 1:
        return img
    h, w = img.shape[:2]
    nw, nh = max(1, w // div), max(1, h // div)
    nw, nh = 1 << (nw.bit_length() - 1), 1 << (nh.bit_length() - 1)
    if nw == w and nh == h:
        return img
    return bicubic(img, nw, nh)


def texture_config(img, wrap, psm):
    """MakeTextureConfig."""
    cfg = TextureConfig(fmt=gs.PSMT4 if psm == 4 else gs.PSMT8, is_texture_map=True)
    w = (wrap or "repeat").lower()
    if w == "region":
        return cfg
    if w == "region_pad":
        cfg.is_texture_map = False
        return cfg
    cfg.repeat_w, cfg.repeat_h = pow2_up(img.shape[1]), pow2_up(img.shape[0])
    cfg.wrap_s = cfg.wrap_t = gs.CLAMP if w == "clamp" else gs.REPEAT
    return cfg


def build_tex_sets(mats, label, images, used=None, lod_div=1, colours=1):
    """BuildTexSets for the car path (no budget split: one builder; mats = MaterialDto-like dicts with
    name/texture/wrap/psm/color_textures). Returns (sets, mat_set, mat_local)."""
    builders, mat_set, mat_local = [], [0] * len(mats), [-1] * len(mats)
    key_to = {}
    for m, md in enumerate(mats):
        if used is not None and m not in used:
            continue
        path = md.get("texture")
        if not path:
            continue
        if not os.path.isfile(path):
            log("  [%s] texture not found: %s" % (label, path))
            continue
        wrap = md.get("wrap") or "region"
        psm = md["psm"] if md.get("psm") in (4, 8) else 8
        cts = md.get("color_textures")
        key = ("%s|%s|%d" % (path, wrap, psm) + ("|" + "|".join(c or "" for c in cts) if cts is not None else "")).upper()
        if key in key_to:
            mat_set[m], mat_local[m] = key_to[key]
            continue
        if not builders:
            builders.append(TextureSetBuilder(max(1, colours)))
        si = len(builders) - 1
        img = _lod_size(_cap_size(images.load(path), MAX_TEX_DIM), lod_div)
        cfg = texture_config(img, wrap, psm)
        local = builders[si].add_image(img, cfg, resize=bicubic)
        if colours > 1 and cts is not None:
            for c in range(1, min(colours, len(cts))):
                if not cts[c] or not os.path.isfile(cts[c]):
                    continue
                vimg = _lod_size(_cap_size(images.load(cts[c]), MAX_TEX_DIM), lod_div)
                if cfg.is_texture_map:
                    nw, nh = pow2_up(vimg.shape[1]), pow2_up(vimg.shape[0])
                    if (nw, nh) != (vimg.shape[1], vimg.shape[0]):
                        vimg = bicubic(vimg, nw, nh)
                builders[si].set_colour_variant(c, local, vimg)
        mat_set[m], mat_local[m] = si, local
        key_to[key] = (si, local)
        if img.shape[1] > 256 or img.shape[0] > 256:
            log("  [%s] WARNING: texture %s is %dx%d (PSMT%d). Textures >256x256 risk VRAM overflow - prefer <=256x256 or PSMT4."
                % (label, os.path.basename(path), img.shape[1], img.shape[0], psm))
    sets = [b.build(resize=bicubic) for b in builders if b.texture_count > 0]
    return sets, mat_set, mat_local


# ---- materials ----------------------------------------------------------------------------------------
def _col(a, v, alpha):
    if a is not None and len(a) >= 3:
        return (fnum(a[0]), fnum(a[1]), fnum(a[2]), fnum(a[3]) if len(a) > 3 else alpha)
    return (v, v, v, alpha)


def pglu_material(m):
    preset = (m.get("preset") or "plain").lower()
    amb, dif, spc, emi, power, flags = {"paint": (1.0, 1.0, 1.0, 0.0, 8.0, 0), "chrome": (0.0, 0.0, 0.5, 0.0, 20.0, 0),
                                        "emissive": (1.0, 1.0, 0.0, 1.0, 0.0, 1), "none": (0.0, 0.0, 0.0, 0.0, 0.0, 0)
                                        }.get(preset, (1.0, 1.0, 0.0, 0.0, 0.0, 0))
    mp, mf = fnum(m.get("power")), int(m.get("flags") or 0)
    return PGLUmaterial(_col(m.get("ambient"), amb, 1.0 if amb > 0 else 0.0), _col(m.get("diffuse"), dif, 1.0 if dif > 0 else 0.0),
                        _col(m.get("specular"), spc, 0.0), _col(m.get("emissive"), emi, 0.0),
                        mp if mp != 0 else power, mf if mf != 0 else flags, 0.0, 0.0)


def _c4(a):
    return (fnum(a[0]), fnum(a[1]), fnum(a[2]), fnum(a[3]) if len(a) > 3 else 0.0) if a is not None and len(a) >= 3 else (0.0, 0.0, 0.0, 0.0)


def pglu_from_values(v):
    return PGLUmaterial(_c4(v.get("ambient")), _c4(v.get("diffuse")), _c4(v.get("specular")), _c4(v.get("emissive")),
                        fnum(v.get("power")), int(v.get("flags") or 0), fnum(v.get("unk2"), 127.0), fnum(v.get("unk3"), 1.0))


def pglu_variant(b):
    return PGLUmaterial(b.ambient, b.diffuse, b.specular, b.unk_color, b.unk, b.flags, 127.0, 1.0)


def _fkey(x):
    return "nan" if x != x else repr(x)


def tex_materials(spec, base, night):
    out = []
    for m in sp(spec, "materials") or []:
        tex = m.get("night_texture") if night and m.get("night_texture") else m.get("texture")
        cts = m.get("color_textures")
        out.append({"name": m.get("name"), "texture": car_path(base, tex) if tex else None,
                    "wrap": m.get("wrap") if m.get("wrap") is not None else "region",
                    "psm": m["psm"] if m.get("psm") in (4, 8) else 4,
                    "color_textures": [car_path(base, x) if x else None for x in cts] if cts is not None else None})
    return out


# ---- geometry helpers -------------------------------------------------------------------------------------
def car_strips(tris, cap=MAX_PACKET_VERTS):
    """PD-winding triangle strips (Gt3CarBuild.CarStrips)."""
    n = len(tris)
    used = [False] * n
    by_edge = {}
    for i, t in enumerate(tris):
        if t[0] == t[1] or t[1] == t[2] or t[0] == t[2]:
            used[i] = True
            continue
        for k in range(3):
            by_edge.setdefault((t[k], t[(k + 1) % 3]), []).append(i)

    def third(tri, a, b):
        t = tris[tri]
        return t[0] if t[0] != a and t[0] != b else (t[1] if t[1] != a and t[1] != b else t[2])

    strips = []
    for s in range(n):
        if used[s]:
            continue
        t = tris[s]
        best = best_tris = None
        for r in range(3):
            strip = [t[r], t[(r + 1) % 3], t[(r + 2) % 3]]
            took = [s]
            mine = {s}
            while len(strip) < cap:
                a, b = strip[-2], strip[-1]
                need = (b, a) if (len(strip) - 2) % 2 == 1 else (a, b)
                nxt = -1
                for ci in by_edge.get(need, ()):
                    if not used[ci] and ci not in mine:
                        nxt = ci
                        break
                if nxt < 0:
                    break
                mine.add(nxt)
                took.append(nxt)
                strip.append(third(nxt, need[0], need[1]))
            if best is None or len(strip) > len(best):
                best, best_tris = strip, took
        for x in best_tris:
            used[x] = True
        strips.append(best)
    return strips


def smooth_normals(pos, tris):
    n = [(0.0, 0.0, 0.0)] * len(pos)
    for t in tris:
        fn = v_cross(v_sub(pos[t[1]], pos[t[0]]), v_sub(pos[t[2]], pos[t[0]]))
        for k in range(3):
            n[t[k]] = v_add(n[t[k]], fn)
    out = []
    for v in n:
        out.append(v_div(v, v_len(v)) if v_len_sq(v) > f32(1e-20) else (0.0, 1.0, 0.0))
    return out


def boundary_verts(tris):
    edges = {}
    for t in tris:
        for k in range(3):
            a, b = t[k], t[(k + 1) % 3]
            key = (a, b) if a < b else (b, a)
            edges[key] = edges.get(key, 0) + 1
    out = set()
    for (a, b), n in edges.items():
        if n == 1:
            out.add(a)
            out.add(b)
    return out


def vertex_colours(d, kind, m, nv):
    cs = m.get("colors")
    has = cs is not None and len(cs) == nv
    bnd = boundary_verts(m.get("triangles") or []) if kind == SHADOW and not has else None
    out = []
    for i in range(nv):
        if has:
            c = cs[i]
            out.append((int(c[0]) & 0xFF, int(c[1]) & 0xFF, int(c[2]) & 0xFF, (int(c[3]) if len(c) > 3 else 128) & 0xFF))
        elif kind in (REFLECT, REFLECT_MASKED):
            g = min(255, max(0, int(dget(d, "reflect_strength", DRAW_DEFAULTS))))
            out.append((g, g, g, 128))
        elif kind == SHADOW:
            out.append((0, 0, 0, 0 if i in bnd else 77))
        else:
            out.append((128, 128, 128, 128))
    return out


def bbox8(mn, mx):
    return [(mn[0], mn[1], mn[2]), (mn[0], mn[1], mx[2]), (mn[0], mx[1], mn[2]), (mn[0], mx[1], mx[2]),
            (mx[0], mn[1], mn[2]), (mx[0], mn[1], mx[2]), (mx[0], mx[1], mn[2]), (mx[0], mx[1], mx[2])]


def car_bbox8(mn, mx):
    return bbox8((0.0, 0.0, 0.0), (0.0, 0.0, 0.0)) if mn[0] > mx[0] else bbox8(mn, mx)


FMAX = 3.4028234663852886e38


def _v3(a):
    return (f32(float(a[0])), f32(float(a[1])), f32(float(a[2])))


# ---- the draw emitter -----------------------------------------------------------------------------------
class Emitter:
    def __init__(self, ms, spec):
        self.ms, self.spec = ms, spec
        self.tex_index = {}
        self.per_lod_tex = None
        self.pool_tex = None
        self.bounds = []
        self.mn = self.mx = None
        self.cur = None
        self.o = None
        self.tex_table = 0
        self.fog = False
        self.variant = DAY
        self.by_value = {}
        self.by_spec = {}
        self.source = []
        self.share = None
        self.levels = LOD_LEVELS

    def pglu_source(self, j):
        return self.source[j] if j < len(self.source) else None

    def pglu_mat(self, sm):
        if sm in self.by_spec:
            return self.by_spec[sm]
        mat = self.spec["materials"][sm]
        pm = pglu_material(mat)
        key = (tuple(_fkey(x) for x in pm.floats()), _fkey(pm.unk), pm.flags,
               json.dumps(mat.get("color_materials"), sort_keys=True, default=str) if mat.get("color_materials") is not None else None)
        if key not in self.by_value:
            self.ms.materials.append(pm)
            self.by_value[key] = len(self.ms.materials)
            self.source.append(mat)
        idx = self.by_value[key]
        self.by_spec[sm] = idx
        return idx

    def empty(self):
        self.bounds.append(((1.0, 1.0, 1.0), (-1.0, -1.0, -1.0)))
        return []

    def model(self, branches, lod_select, tex_table, v):
        self.variant = v
        self.mn, self.mx = (FMAX, FMAX, FMAX), (-FMAX, -FMAX, -FMAX)
        self.fog = False
        inner = []
        if lod_select:
            sel = C.LODSelect((0.0, 0.0, 0.0), 3.0, [])
            for k in range(self.levels):
                j = share_of(self.share, k)
                if j >= 0:
                    sel.branches.append(list(sel.branches[j]))
                    continue
                br = []
                if k < len(branches) and branches[k]:
                    if self.per_lod_tex is not None and k < len(self.per_lod_tex):
                        self.tex_index = self.per_lod_tex[k]
                    self.emit(br, branches[k], tex_table if tex_table >= 0 else k)
                sel.branches.append(br)
            inner.append(sel)
        elif branches and branches[0]:
            self.emit(inner, branches[0], max(0, tex_table))
        inner.append(C.Cmd(C.ENABLE_RENDERING))
        cmds = []
        if self.fog:
            cmds.append(C.Cmd(C.STORE_FOG_COLOR))
        cmds.append(C.BBoxRender(car_bbox8(self.mn, self.mx), inner))
        if self.fog:
            cmds.append(C.Cmd(C.COPY_FOG_COLOR))
        self.bounds.append((self.mn, self.mx))
        return cmds

    def shadow_model(self, meshes, lod_select):
        draws = [None if m is None else [{"kind": "shadow", "mesh": m, "lit": False}] for m in meshes]
        return self.model(draws, lod_select, -1 if lod_select else 0, self.variant)

    def emit(self, o, draws, tex_table):
        self.o, self.cur, self.tex_table = o, GsState.defaults(), tex_table
        o.append(C.Cmd(C.SET_TEX_TABLE_BYTE, tex_table & 0xFF))
        for d in draws:
            self.draw(d)
        transition(self.o, self.cur, GsState.defaults())

    def draw(self, d):
        if d is None:
            return
        kind = parse_kind(d.get("kind"))
        if kind == LAMP:
            self.lamp(d)
            return
        m = d.get("mesh")
        if m is None or not m.get("triangles"):
            return
        tgt = target_state(kind).clone()
        ar = d.get("alpha_ref")
        if ar is not None and tgt.alpha_test is True:
            tgt.af_ref = min(255, max(0, int(ar)))
        tgt.cull = not dget(d, "double_sided", DRAW_DEFAULTS)
        ext_tex = int(dget(d, "external_tex", DRAW_DEFAULTS))
        if ext_tex >= 0:
            tgt.ext_tex = ext_tex
            if tgt.blend is None:
                tgt.blend = (0, 1, 0, 1, 0)
        elif kind in (REFLECT, REFLECT_MASKED):
            tgt.ext_tex = 0
            tgt.ext_tint = dget(d, "reflect_tint", DRAW_DEFAULTS)
        if tgt.fog_black is True:
            self.fog = True
        transition(self.o, self.cur, tgt)
        mo = d.get("morph")
        if mo is not None:
            # a KEYFRAME CHAIN (engine 0x2262b0): opcode 53 {first, count} draws shape first + trunc(ratio * count)
            # blended by the fraction, so segment k (key k -> key k+1) must sit at first + k. PD's steering parts: 3 keys.
            nseg = max(1, len(self._morph_keys(d)) - 1)
            ms_ = self.build_shape(d, kind, 0)
            for k in range(1, nseg):
                self.build_shape(d, kind, k)
            want = int(dget(mo, "mult", MORPH_DEFAULTS))
            if want != nseg and want > 1:
                log("  '%s': %d morph key(s) -> %d segment(s) (the spec asked for %d; the chain length is the key count)"
                    % (d.get("name"), nseg + 1, nseg, want))
            self.o.append(C.Cmd(C.PUSH_MATRIX))
            mt = mo.get("matrix")
            if mt is not None and len(mt) == 16:
                self.o.append(C.Cmd(C.MULT_MATRIX, *[fnum(x) for x in mt]))
                self.o.append(C.Cmd(C.PUSH_MATRIX))
            param = morph_param(dget(mo, "driver", MORPH_DEFAULTS))
            if param > 0:
                self.o.append(C.CallModelCallback(param, []))
            else:
                self.o.append(C.Cmd(C.SHAPE_TWEEN_RATIO, fnum(mo.get("ratio"))))
            self.o.append(C.Cmd(C.OP53, ms_ & 0xFFFF, nseg & 0xFFFF))
            self.o.append(C.Cmd(C.POP_MATRIX))
            if mt is not None and len(mt) == 16:
                self.o.append(C.Cmd(C.POP_MATRIX))
            return
        shape = self.build_shape(d, kind)
        self.o.append(C.call_shape(shape))
        if dget(d, "stamp", DRAW_DEFAULTS):
            self.draw({"kind": "mask", "mesh": m, "mat": dget(d, "mat", DRAW_DEFAULTS), "lit": False,
                       "double_sided": dget(d, "double_sided", DRAW_DEFAULTS)})
        refl = d.get("reflect")
        if refl in ("plain", "masked"):
            self.draw({"kind": "reflect_masked" if refl == "masked" else "reflect", "mesh": m,
                       "reflect_strength": dget(d, "reflect_strength", DRAW_DEFAULTS),
                       "reflect_tint": dget(d, "reflect_tint", DRAW_DEFAULTS),
                       "double_sided": dget(d, "double_sided", DRAW_DEFAULTS)})

    def lamp(self, d):
        cb = C.CallModelCallback(0, [])
        off = d.get("lamp_off_night") if self.variant == NIGHT and d.get("lamp_off_night") is not None else d.get("lamp_off")
        for branch_draws in (off or [], d.get("lamp_on") or []):
            save_o, save_state = self.o, self.cur.clone()
            br = []
            self.o = br
            if branch_draws:
                br.append(C.Cmd(C.SET_TEX_TABLE_BYTE, self.tex_table & 0xFF))
            for bd in branch_draws:
                self.draw(bd)
            transition(self.o, self.cur, save_state)
            self.cur, self.o = save_state, save_o
            cb.branches.append(br)
        self.o.append(cb)

    @staticmethod
    def _morph_keys(d):
        """An animated part's key poses: [the mesh, target, extra_keys...] (vertex lists) - [] when not animated or
        the target does not match the mesh."""
        mo, m = d.get("morph"), d.get("mesh") or {}
        nv = len(m.get("vertices") or [])
        tg = mo.get("target", MORPH_DEFAULTS["target"]) if mo is not None else None
        if mo is None or tg is None or len(tg) != nv:
            return []
        keys = [m["vertices"], tg]
        for k in mo.get("extra_keys") or []:
            if k is None or len(k) != nv:
                break
            keys.append(k)
        return keys

    def build_shape(self, d, kind, seg=0):
        m = d["mesh"]
        verts = m.get("vertices") or []
        nv = len(verts)
        tris = [(int(t[0]), int(t[1]), int(t[2])) for t in m["triangles"]]
        keys = self._morph_keys(d)
        morph = bool(keys)
        mo = d.get("morph")
        if morph:
            # segment seg of the chain: key seg (A buffers) -> key seg + 1 (B buffers)
            kpos = [[_v3(v) for v in k] for k in keys]
            given = [m.get("normals"), mo.get("target_normals")] + list(mo.get("extra_key_normals") or [])
            knrm = []
            for i, kp in enumerate(kpos):
                g = given[i] if i < len(given) else None
                knrm.append([_v3(n) for n in g] if g is not None and len(g) == nv else smooth_normals(kp, tris))
            pos, nrm, tpos, tnrm = kpos[seg], knrm[seg], kpos[seg + 1], knrm[seg + 1]
            for kp in kpos:
                for p in kp:
                    self.mn, self.mx = v_min(self.mn, p), v_max(self.mx, p)
        else:
            pos = [_v3(v) for v in verts]
            for p in pos:
                self.mn, self.mx = v_min(self.mn, p), v_max(self.mx, p)
            ns = m.get("normals")
            nrm = [_v3(n) for n in ns] if ns is not None and len(ns) == nv else smooth_normals(pos, tris)
            tpos = tnrm = None
        col = vertex_colours(d, kind, m, nv)
        ext = kind in (REFLECT, REFLECT_MASKED)
        env_tex = ext and bool(dget(d, "env_texture", DRAW_DEFAULTS))
        shadow = kind == SHADOW
        dmat = int(dget(d, "mat", DRAW_DEFAULTS))
        ext_tex = int(dget(d, "external_tex", DRAW_DEFAULTS))
        tm = m.get("tri_materials")
        tri_mat = [int(x) for x in tm] if tm is not None else [dmat] * len(tris)
        uvs = m.get("uvs")
        uv32 = [(f32(float(u[0])), f32(float(u[1]))) for u in uvs] if uvs is not None else None
        groups_by, order = {}, []
        for t in range(len(tris)):
            key = -1 if (ext and not env_tex) or (shadow and tm is None) else tri_mat[t]
            if key not in groups_by:
                groups_by[key] = []
                order.append(key)
            groups_by[key].append(t)
        groups = []
        for mat in order:
            if ext and not env_tex:
                tex = 511
            elif ext_tex >= 0:
                tex = 511
            else:
                tex = self.tex_index.get(mat, 0) if mat >= 0 else 0
            prim = PRIM_TRISTRIP | PRIM_IIP
            if kind != DEPTH:
                prim |= PRIM_FGE
            if tex != 0:
                prim |= PRIM_TME
            if ext or shadow or ext_tex >= 0 or kind in (SOFT, SOFT_Z, ADDITIVE, GLINT, BLEND, CUTOUT_BLEND):
                prim |= PRIM_ABE
            g = StripGroup(tex, 0 if (ext and not env_tex) or mat < 0 else self.pglu_mat(mat), prim)
            for strip in car_strips([tris[t] for t in groups_by[mat]], MAX_TWEEN_PACKET_VERTS if morph else MAX_PACKET_VERTS):
                g.strips.append([StripVertex(pos[vi], uv32[vi] if uv32 is not None and vi < len(uv32) else (0.0, 0.0),
                                             col[vi], nrm[vi], tpos[vi] if morph else pos[vi],
                                             tnrm[vi] if morph else nrm[vi]) for vi in strip])
            if g.strips:
                groups.append(g)
        lit = bool(dget(d, "lit", DRAW_DEFAULTS))
        unk1, unk3 = shape_flags(kind, lit, ext, all(g.tex == 0 for g in groups))
        if env_tex:
            unk3 = 0x01 if all(g.tex in (0, 511) for g in groups) else 0x00
        if d.get("unk1") is not None:
            unk1 = int(d["unk1"]) & 0xFF
        if d.get("unk3") is not None:
            unk3 = int(d["unk3"]) & 0xFF
        if morph:
            sh = build_shape(groups, native=True, unk1=1, unk2=2, tween=TWEEN_WAVE, lit=True)
        elif ext:
            sh = build_shape(groups, native=True, env_map=True)
        else:
            sh = build_shape(groups, native=True, unk1=unk1, lit=((unk1 ^ (unk1 >> 2)) & 1) != 0)
        if morph:
            unk1, unk3 = 1, 0
        sh.unk1, sh.unk3 = unk1, unk3
        self.ms.shapes.append(sh)
        return len(self.ms.shapes) - 1


# ---- the builder -------------------------------------------------------------------------------------------
class CarBuilder:
    def __init__(self, spec, base):
        self.spec, self.base = spec, base
        self.images = Images()
        self.set_colours = 1

    def colours_for(self, container, set_index):
        cs = sp(self.spec, "color_sets")
        if isinstance(cs, dict) and container in cs:
            arr = cs[container]
            return arr[set_index] if set_index < len(arr) else 0
        return max(1, int(sp(self.spec, "colors") or 1))

    def tex_set(self, ms, mats, used, divisor, label):
        textured = {m for m in used if 0 <= m < len(mats) and mats[m].get("texture")}
        mp = {}
        if not textured:
            ms.texture_sets.append(TextureSet1())
            return mp
        sets, _, local = build_tex_sets(mats, label, self.images, textured, divisor, max(1, self.set_colours))
        if len(sets) != 1 or sets[0].total_block_size > 1024:
            raise ValueError("%s: textures take %d GS blocks; a car LOD binds ONE set of at most 1024 blocks (256 KB - "
                             "PD's largest is 1012). Shrink or merge textures." % (label, sum(s.total_block_size for s in sets)))
        ms.texture_sets.append(sets[0])
        for m in textured:
            mp[m] = local[m] + 1
        return mp

    def variation_tables(self, ms, e, container):
        ct = sp(self.spec, "color_tables")
        n = ct[container] if isinstance(ct, dict) and container in ct else max(1, int(sp(self.spec, "colors") or 1))
        ms.variation_materials = []
        for c in range(n):
            row = []
            for j in range(len(ms.materials)):
                sm = e.pglu_source(j)
                cm = sm.get("color_materials") if sm is not None else None
                if cm is not None and c < len(cm) and cm[c] is not None:
                    row.append(pglu_from_values(cm[c]))
                else:
                    row.append(pglu_variant(ms.materials[j]))
            ms.variation_materials.append(row)

    def extras(self, v):
        out = []
        for x in sp(self.spec, "extra_models") or []:
            if x is None:
                continue
            var = (x.get("variant") or "race").lower()
            ok = v == DAY if var == "day" else v == NIGHT if var == "night" else v == MENU if var == "menu" else v != MENU
            if ok:
                out.append(x)
        return out

    def place_extras(self, ms, e, v):
        last = {}
        for x in self.extras(v):
            mi = int(x.get("model") or 0)
            if 0 <= mi < 7:
                last[mi] = x                     # GroupBy(model).Last(), groups in first-appearance order
        for mi, x in last.items():
            if v != MENU and e.per_lod_tex:
                e.tex_index = e.per_lod_tex[0]
            lods = x.get("lods") or []
            sel = bool(x.get("lod_select"))
            e.levels = len(lods) if sel and len(lods) > 0 else LOD_LEVELS
            model = e.model(lods, sel, -1 if sel and v != MENU else 0, v)
            e.levels = LOD_LEVELS
            ms.models[mi] = model
            e.bounds[mi] = e.bounds[-1]
            e.bounds.pop()

    @staticmethod
    def used_mats(draws):
        used = set()

        def walk(ds):
            for d in ds or []:
                if d is None:
                    continue
                mat = int(dget(d, "mat", DRAW_DEFAULTS))
                if mat >= 0:
                    used.add(mat)
                m = d.get("mesh")
                if m is not None and m.get("tri_materials") is not None:
                    used.update(int(x) for x in m["tri_materials"] if int(x) >= 0)
                walk(d.get("lamp_off"))
                walk(d.get("lamp_on"))
                walk(d.get("lamp_off_night"))
        walk(draws)
        return used

    @staticmethod
    def _bounding(mn, mx, empty_zero=True):
        if empty_zero and mn[0] > mx[0]:
            return (0.0, 0.0, 0.0, 0.0)
        c = tuple(f32(f32(a + b) / 2.0) for a, b in zip(mn, mx))
        return c + (f32(v_len(v_sub(mx, mn)) / 2.0),)

    # ---- info / tyre / rim ------------------------------------------------------------------------------
    def build_info(self):
        spec, base = self.spec, self.base
        ip = sp(spec, "info_points")
        if sp(spec, "info_file"):
            raw = open(car_path(base, spec["info_file"]), "rb").read()
            if ip is None:
                return raw
            ci = carinfo_read(raw)
            apply_info_points(ci, ip)
            return carinfo_write(ci)
        info = sp(spec, "info")
        ci = json.loads(json.dumps(info)) if info is not None else info_defaults()
        if ip is not None:
            apply_info_points(ci, ip)
        return carinfo_write(ci)

    def build_tire(self):
        spec, base = self.spec, self.base
        if sp(spec, "tire_file"):
            t = open(car_path(base, spec["tire_file"]), "rb").read()
            if int.from_bytes(t[:4], "little") != GTTR_MAGIC:
                raise ValueError("%s is not a GTTR tyre file" % spec["tire_file"])
            return t
        if not sp(spec, "tire_texture"):
            raise ValueError("the spec needs tire_file or tire_texture")
        # PD's tyre (285 of 285): ONE 128x64 PSMT4 picture, region clamp; TOP half = tread, BOTTOM half = sidewall
        path = car_path(base, spec["tire_texture"])
        img = self.images.load(path)
        if img.shape[:2] != (64, 128):
            self.images.cache[os.path.normcase(os.path.abspath(path))] = bicubic(img, 128, 64)
        mats = [{"name": "tire", "texture": path, "psm": 4, "wrap": "region"}]
        sets, _, _ = build_tex_sets(mats, "tyre", self.images, {0})
        th = sp(spec, "tire_header")
        return tire_write(int(th[0]) & 0xFFFFFFFF, int(th[1]) & 0xFFFFFFFF, fnum(th[2]), sets[0])

    def build_rim(self, rim_lods, rear_lods, menu=False):
        spec = self.spec
        ms = ModelSet1()
        lods = rim_lods if rim_lods else []
        if not lods:
            raise ValueError("the spec has no rim LODs (spec.rim)")
        rear = rear_lods if rear_lods and any(l for l in rear_lods) else None
        lv = levels(spec, "menu_rim", 0) if menu else levels(spec, "rim", LOD_LEVELS)
        rlv = levels(spec, "menu_rear_rim", 0) if menu else levels(spec, "rear_rim", LOD_LEVELS)
        if lv == 0:
            lods = [lods[0]]
        if rear is not None and rlv == 0:
            rear = [rear[0]]
        e = Emitter(ms, spec)
        key = "mrim" if menu else "rim"
        self.set_colours = self.colours_for(key, 0)
        allds = [d for l in list(lods) + list(rear or []) for d in (l or [])]
        mats = tex_materials(spec, self.base, False)
        faces = ([], []) if menu else (self._faces(lods, mats), self._faces(rear or [], mats))
        face_ids = {id(d) for d in faces[0] + faces[1]}
        used = self.used_mats([d for d in allds if id(d) not in face_ids])   # the face is never drawn from set 0
        e.tex_index = self.tex_set(ms, mats, used, 1, "rim")
        e.share = None if menu else sp(spec, "rim_lod_share")
        e.levels = lv if lv > 0 else LOD_LEVELS
        ms.models.append(e.model(lods, lv > 0, 0, DAY))
        if rear is not None:
            e.share = None if menu else sp(spec, "rear_rim_lod_share")
            e.levels = rlv if rlv > 0 else LOD_LEVELS
            ms.models.append(e.model(rear, rlv > 0, 0, DAY))
        e.share = None
        e.levels = LOD_LEVELS
        ms.boundings = [self._bounding(mn, mx, empty_zero=False) for mn, mx in e.bounds]
        self.variation_tables(ms, e, key)
        if faces[0] or faces[1]:
            pdf = sp(spec, "rim_blur_file")
            pd = pd_blur_sets(open(car_path(self.base, pdf), "rb").read()) if pdf else None
            front = self._face_image(mats, faces[0] or faces[1])
            rear = self._face_image(mats, faces[1]) if faces[1] and faces[0] else None
            attach(ms, pd, front, rear, max(1, self.set_colours), log)
        rep = "(rim: %d shapes, %d tris)" % (len(ms.shapes), sum(s.num_triangles for s in ms.shapes))
        return wheel_write(ms, 0), rep

    def _faces(self, lods, mats):
        """The race rim's FACE draws (external texture slot 1 - see rimblur.py). A face without a picture can't
        get its spinning-wheel sets: it is drawn as a plain part instead (an untextured face, never slot garbage)."""
        out = []
        for l in lods or []:
            for d in l or []:
                if d is None or int(dget(d, "external_tex", DRAW_DEFAULTS)) != 1:
                    continue
                if parse_kind(d.get("kind")) in (REFLECT, REFLECT_MASKED):
                    continue
                if self._face_path(d, mats) is None:
                    log("  WARNING rim face '%s' has no picture: drawn as a plain part (re-import the car, or give it "
                        "a texture, for PD's spinning-wheel face)" % (d.get("name") or "?"))
                    d["external_tex"] = -1
                    continue
                out.append(d)
        return out

    def _face_path(self, d, mats):
        m = int(dget(d, "mat", DRAW_DEFAULTS))
        tm = (d.get("mesh") or {}).get("tri_materials")
        for j in ([m] if m >= 0 else []) + [int(x) for x in (tm or []) if int(x) >= 0]:
            if j < len(mats) and mats[j].get("texture") and os.path.isfile(mats[j]["texture"]):
                return j
        return None

    def _face_image(self, mats, faces):
        j = self._face_path(faces[0], mats)
        md = mats[j]
        cols = [None] + [self.images.load(c) if c and os.path.isfile(c) else None
                         for c in (md.get("color_textures") or [None])[1:]]
        return self.images.load(md["texture"]), cols, os.path.basename(md["texture"])

    # ---- body -------------------------------------------------------------------------------------------
    def build_body(self, v):
        spec = self.spec
        ms = ModelSet1()
        mats = tex_materials(spec, self.base, v == NIGHT)
        e = Emitter(ms, spec)
        pool = sp(spec, "headlight_pool")
        if v == MENU:
            body = sp(spec, "menu_body")
            if body is None:
                b = sp(spec, "body") or []
                body = b[0] if b else []
            front = sp(spec, "menu_rim_front")
            rear = sp(spec, "menu_rim_rear")
            if rear is None:
                rear = front
            allds = list(body or [])
            if front is not None:
                allds += front
            if rear is not None:
                allds += rear
            if pool is not None:
                allds.append(pool)
            mgd = sp(spec, "menu_ground_draws")
            if mgd is not None:
                allds += mgd
            for x in self.extras(v):
                for l in x.get("lods") or []:
                    if l is not None:
                        allds += l
            self.set_colours = self.colours_for("menu", 0)
            e.tex_index = self.tex_set(ms, mats, self.used_mats(allds), 1, "menu")
            ms.models.append(e.model([body], False, 0, MENU))
            ms.models.append(e.empty())
            ms.models.append(e.empty() if front is None else e.model([front], False, 0, MENU))
            ms.models.append(e.empty() if rear is None else e.model([rear], False, 0, MENU))
            ms.models.append(e.empty())
            ms.models.append(e.empty() if pool is None else e.model([[pool]], False, 0, MENU))
            ground = sp(spec, "menu_ground_shadow")
            if ground is None:
                gs_ = sp(spec, "ground_shadow")
                ground = next((g for g in gs_ if g is not None), None) if gs_ is not None else None
            if mgd:
                ms.models.append(e.model([mgd], False, 0, MENU))
            else:
                ms.models.append(e.empty() if ground is None else e.shadow_model([ground], False))
            self.place_extras(ms, e, v)
        else:
            nb = sp(spec, "night_body")
            lods = nb if v == NIGHT and nb else (sp(spec, "body") or [])
            if not lods:
                raise ValueError("the spec has no body LODs")
            sd, sh, gd, gsh = sp(spec, "shadow_draws"), sp(spec, "shadow"), sp(spec, "ground_draws"), sp(spec, "ground_shadow")
            ls, sls, gls = sp(spec, "lod_share"), sp(spec, "shadow_lod_share"), sp(spec, "ground_lod_share")
            n_lod = max(len(lods), len(sd) if sd is not None else (len(sh) if sh is not None else 0),
                        len(gd) if gd is not None else (len(gsh) if gsh is not None else 0),
                        len(ls or []), len(sls or []), len(gls or []))
            n_lod = min(max(n_lod, 1), LOD_LEVELS)
            divs = sp(spec, "texture_lod_divisors")
            per_lod = []
            for k in range(n_lod):
                div = max(1, int(divs[k])) if divs is not None and k < len(divs) else 1
                draws = list(lods[k]) if share_of(ls, k) < 0 and k < len(lods) and lods[k] is not None else []
                if share_of(sls, k) < 0 and sd is not None and k < len(sd) and sd[k] is not None:
                    draws += sd[k]
                if share_of(gls, k) < 0 and gd is not None and k < len(gd) and gd[k] is not None:
                    draws += gd[k]
                for x in self.extras(v):
                    xl = x.get("lods") or []
                    if (k < len(xl) and xl[k] is not None) if x.get("lod_select") else k == 0:
                        if x.get("lod_select"):
                            draws += xl[k]
                        else:
                            for l in xl:
                                draws += l or []
                self.set_colours = self.colours_for("night" if v == NIGHT else "day", k)
                per_lod.append(self.tex_set(ms, mats, self.used_mats(draws), div, "LOD%d" % k))
            e.per_lod_tex = per_lod
            pool_set = -1
            if pool is not None:
                pool_set = len(ms.texture_sets)
                self.set_colours = self.colours_for("night" if v == NIGHT else "day", pool_set)
                e.pool_tex = self.tex_set(ms, mats, self.used_mats([pool]), 1, "pool")
            e.share = ls
            e.levels = levels(spec, "night_body", levels(spec, "body", LOD_LEVELS)) if v == NIGHT else levels(spec, "body", LOD_LEVELS)
            ms.models.append(e.model(lods, True, -1, v))
            e.share = sls
            e.levels = levels(spec, "shadow", LOD_LEVELS)
            if sd:
                ms.models.append(e.model(sd, True, -1, v))
            elif sh:
                ms.models.append(e.shadow_model(sh, True))
            else:
                ms.models.append(e.empty())
            e.share = None
            e.levels = LOD_LEVELS
            ms.models += [e.empty(), e.empty(), e.empty()]
            if pool is not None:
                e.tex_index = e.pool_tex
                ms.models.append(e.model([[pool]], False, pool_set, v))
            else:
                ms.models.append(e.empty())
            e.share = gls
            e.levels = levels(spec, "ground", LOD_LEVELS)
            if gd:
                ms.models.append(e.model(gd, True, -1, v))
            elif gsh:
                ms.models.append(e.shadow_model(gsh, True))
            else:
                ms.models.append(e.empty())
            e.share = None
            e.levels = LOD_LEVELS
            self.place_extras(ms, e, v)
        key = {MENU: "menu", NIGHT: "night"}.get(v, "day")
        mc = sp(spec, "model_counts")
        if isinstance(mc, dict) and key in mc:
            cnt = int(mc[key])
            if 0 < cnt < len(ms.models):
                del ms.models[cnt:]
                del e.bounds[cnt:]
        self.variation_tables(ms, e, key)
        ms.boundings = [self._bounding(mn, mx) for mn, mx in e.bounds]
        data = ms.serialize()
        tris = sum(s.num_triangles for s in ms.shapes)
        rep = "(%d models, %d shapes, %s tris, %d tex sets)" % (len(ms.models), len(ms.shapes), format(tris, ","), len(ms.texture_sets))
        return data, rep

    # ---- everything ---------------------------------------------------------------------------------------
    def build_all(self, out_dir, code):
        spec = self.spec
        if sp(spec, "auto_shadows") and sp(spec, "body"):
            pts = body_points(spec["body"][0])
            if sp(spec, "shadow_draws") is None and sp(spec, "shadow") is None:
                hull = auto_hull(pts)
                if hull is not None:
                    spec["shadow"] = [hull]
                    spec["shadow_lod_share"] = [-1, 0, 0, 0]
                    log("  auto real-time shadow hull: %d verts / %d tris (PD's 16-column skirt)" % (len(hull["vertices"]), len(hull["triangles"])))
            if sp(spec, "ground_draws") is None and sp(spec, "ground_shadow") is None:
                spec["ground_shadow"] = [auto_ground(pts, 32), auto_ground(pts, 22), auto_ground(pts, 18)]
                log("  auto ground shadow: rings of 32/22/18 (soft edge: alpha 77 -> 0)")
        target = sp(spec, "target")
        target = target.lower() if target else None
        if target not in (None, "race", "race_all", "menu"):
            raise ValueError("target '%s': race | race_all | menu" % spec.get("target"))
        gtci = self.build_info()
        gttr = self.build_tire()
        mtf = sp(spec, "menu_tire_file")
        menu_gttr = open(car_path(self.base, mtf), "rb").read() if mtf else gttr
        rim_rep = mr = ""
        gttw = None
        if target != "menu":
            gttw, rim_rep = self.build_rim(sp(spec, "rim"), sp(spec, "rear_rim"))
        menu_gttw = None
        if target != "race":
            own = bool(sp(spec, "menu_rim"))
            menu_gttw, mr = self.build_rim(sp(spec, "menu_rim") if own else sp(spec, "rim"),
                                           sp(spec, "menu_rear_rim") if own else sp(spec, "rear_rim"), menu=True)
        if gttw is None:
            rim_rep = mr
        log("  GTCI %s B | GTTR %s B | GTTW %s B  %s" % (format(len(gtci), ","), format(len(gttr), ","),
                                                        format(len(gttw if gttw is not None else menu_gttw), ","), rim_rep))
        outputs = []
        variants = {"race": [DAY], "race_all": [DAY, NIGHT], "menu": [MENU]}.get(target, [DAY, NIGHT, MENU])
        for v in variants:
            body, rep = self.build_body(v)
            vinfo = sp(spec, "night_info_file") if v == NIGHT else sp(spec, "menu_info_file") if v == MENU else None
            vgtci = open(car_path(self.base, vinfo), "rb").read() if vinfo else gtci
            data = container(vgtci, body, menu_gttr if v == MENU else gttr, menu_gttw if v == MENU else gttw)
            log("  %-5s body %s B -> container %s B  %s" % (("Day", "Night", "Menu")[v], format(len(body), ","),
                                                           format(len(data), ","), rep))
            rel = {DAY: "cars/day", NIGHT: "cars/night"}.get(v, "menu/cars")
            outputs.append(("%s/%s" % (rel, code), data, MENU_CAP if v == MENU else RACE_CAP))
            if v == NIGHT:
                outputs.append(("cars/eve/%s" % code, data, RACE_CAP))
        if target is None:
            outputs.append(("wheel/%s" % code, gttw, 1 << 62))
        if target in (None, "menu"):
            outputs.append(("menu/wheel/%s" % code, menu_gttw, 1 << 62))
        over = False
        for rel, data, cap in outputs:
            p = os.path.join(out_dir, *rel.split("/"))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(data)
            if len(data) > cap:
                over = True
                log("  OVER THE ENGINE CAP: %s is %s B > %s B" % (rel, format(len(data), ","), format(cap, ",")))
        log("  wrote %d files under %s" % (len(outputs), out_dir))
        return 2 if over else 0


def build_car(spec_path, out_dir, code=None):
    spec = load_spec(spec_path)
    base = os.path.dirname(os.path.abspath(spec_path))
    code = code or spec.get("name") or os.path.splitext(os.path.basename(spec_path))[0]
    log("GT3 car '%s' from scratch (%d materials, %d race LODs)" % (code, len(sp(spec, "materials") or []), len(sp(spec, "body") or [])))
    return CarBuilder(spec, base).build_all(out_dir, code)

# Nenkai's research is the core of this human & machine made tool.
