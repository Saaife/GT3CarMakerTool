"""gt3-car-decompile: a PD car -> a from-scratch car spec (car.json + Textures/*.png + tire.gttr)
(port of GT4TrackKit Gt3CarDecompile.cs).

Each draw is classified into a kind by simulating the GS state PD's stream sets up before it, its geometry is
decoded (faces CCW-front, strip parity applied) and welded back into a mesh, and its textures are written.
Materials = one per (source, PGLUmaterial, texture set, texture) PD draws with."""

import hashlib
import os
import struct
import sys

from . import commands as C
from . import gs
from .carinfo import CarFileParts, carinfo_read, carinfo_write
from .binio import Writer
from .f32 import f32, f32_bits
from .jsonout import dumps, prune
from .png import write_png
from .spec import (GsState, parse_kind, shape_flags, GREATER, GEQUAL, ALWAYS, EQUAL_ZERO,
                   MASK_RGB, MASK_ALPHA, MASK_NOTHING)

# C# class name [4..8] of each opcode (CarModelSig fingerprints commands by it)
_SIG4 = {2: "Rend", 3: "Rend", 8: "Jump", 9: "Jump", 11: "pglE", 12: "pglP", 13: "pglP", 14: "pglM", 15: "pglL",
         16: "pglM", 17: "pglT", 18: "pglS", 19: "pglR", 20: "pglR", 21: "pglR", 22: "pglR", 23: "pglE", 24: "pglD",
         25: "pglD", 26: "pglE", 27: "pglD", 28: "pglA", 29: "pglE", 30: "pglD", 31: "pglS", 32: "pglB", 33: "pglS",
         34: "pglS", 35: "pglC", 36: "pglC", 37: "pglD", 38: "pglE", 39: "pglD", 40: "pglG", 41: "pglu", 42: "pglu",
         43: "pglu", 44: "pglu", 45: "pglE", 46: "pglD", 47: "pglA", 49: "pglC", 50: "GT3_", 51: "GT3_", 52: "Mode",
         53: "Unk5", 54: "GT3_", 55: "GT3_", 64: "pglC", 65: "UnkN", 66: "UnkN", 67: "Call", 68: "VM_M", 69: "VM_p",
         70: "VM_p", 72: "VM_p", 73: "VM_p", 74: "VM_p", 75: "VM_p", 76: "VM_p", 77: "VM_B", 78: "pglC", 79: "pglC",
         80: "pglu", 81: "pglu", 82: "pglV", 83: "pglV", 84: "VM_p", 85: "VM_p", 86: "pglT", 87: "pglT", 88: "pglE",
         89: "VMCa", 90: "VMCa"}

_CALLBACK_NAMES = {0: "IsTailLampActive", 1: "TweenShapeSpeedRandom_1", 2: "TweenShapeSpeedRandom_2",
                   3: "TweenShapeSpeedRandom_3", 4: "TweenShapeSpeedRandom_4", 5: "SetSteering",
                   6: "SetActiveWingShapeTweenRatio", 7: "GetTimeZone", 8: "RotateZ", 9: "Unk9", 11: "UnkTire11",
                   12: "UnkTire12", 13: "UnkTire13_", 14: "UnkTire14", 15: "RenderTire_1", 16: "RenderTire_2",
                   17: "RenderTire_3", 18: "RenderTire_4", 36: "RenderWheel_1", 37: "RenderWheel_2", 38: "RenderWheel_3",
                   39: "RenderWheel_4", 42: "UnkShapeTweenRatio42", 43: "UnkShapeTweenRatio43",
                   44: "UnkShapeTweenRatio44", 45: "UnkShapeTweenRatio45", 46: "UnkWheelRelated46",
                   47: "UnkWingRelated46", 48: "UnkBikeRelated48", 49: "UnkBikeRelated49", 52: "UnkShapeTweenRatio52",
                   53: "UnkShapeTweenRatio53", 56: "UnkShapeTweenRatio56", 57: "UnkShapeTweenRatio57",
                   60: "UnkShapeTweenRatio60", 62: "UnkShapeTweenRatio62", 63: "UnkShapeTweenRatio63",
                   64: "UnkShapeTweenRatio64", 65: "UnkShapeTweenRatio65", 66: "Unk66"}

log = print


def _find(cmds, op):
    for c in cmds:
        if c.op == op:
            return c
        if c.op == C.BBOX_RENDER:
            hit = _find(c.commands, op)
            if hit is not None:
                return hit
    return None


def levels(ms, m):
    """The LODSelect branch count of model m: 0 = no LODSelect, -1 = no such model."""
    if m >= len(ms.models):
        return -1
    sel = _find(ms.models[m], C.LOD_SELECT)
    return len(sel.branches) if sel is not None else 0


def shape_prim(sh):
    for p in sh.packets:
        for c in p.commands:
            if c.gif is not None and c.gif.pre:
                return c.gif.prim
    return None


def model_sig(ms, m):
    if m >= len(ms.models):
        return ""
    sb = []

    def walk(cmds):
        for c in cmds:
            if c.op == C.BBOX_RENDER:
                walk(c.commands)
            elif c.op == C.LOD_SELECT:
                for br in c.branches:
                    sb.append("|")
                    walk(br)
            elif c.op == C.CALL_MODEL_CALLBACK:
                for br in c.branches:
                    sb.append("/")
                    walk(br)
            elif c.op in (C.CALL_SHAPE_BYTE, C.CALL_SHAPE_USHORT):
                sh = ms.shapes[c.args[0]]
                sb.append("[%d.%d.%d.%d]" % (sh.unk1, sh.unk3, sh.num_triangles,
                                             sum(len(x.data) for p in sh.packets for x in p.commands)))
            else:
                sb.append(_SIG4.get(c.op, "?%d" % c.op))
    walk(ms.models[m])
    return "".join(sb)


def apply_state(c, st):
    op = c.op
    if op == C.ENABLE_ALPHA_TEST:
        st.alpha_test = True
    elif op == C.DISABLE_ALPHA_TEST:
        st.alpha_test = False
    elif op == C.ALPHA_FUNC:
        st.af, st.af_ref = c.args[0], c.args[1]
    elif op == C.BLEND_FUNC:
        b = c.args[0]
        st.blend = (b & 3, (b >> 2) & 3, (b >> 4) & 3, (b >> 6) & 3, c.args[1])
    elif op == C.ENABLE_DEPTH_MASK:
        st.depth_write = True
    elif op == C.DISABLE_DEPTH_MASK:
        st.depth_write = False
    elif op == C.COLOR_MASK:
        st.color_mask = c.args[0]
    elif op == C.ENABLE_DEST_ALPHA_TEST:
        st.dest_test = True
    elif op == C.DISABLE_DEST_ALPHA_TEST:
        st.dest_test = False
    elif op == C.DEST_ALPHA_FUNC:
        st.dest_func = c.args[0]
    elif op == C.SET_FOG_COLOR:
        st.fog_black = True
    elif op == C.COPY_FOG_COLOR:
        st.fog_black = False
    elif op == C.GT3_2_4F:
        st.ext_tint = True
    elif op == C.GT3_2_1UI:
        st.ext_tint = False
    elif op == C.ENABLE_CULL_FACE:
        st.cull = True
    elif op == C.DISABLE_CULL_FACE:
        st.cull = False
    elif op == C.EXTERNAL_TEX_INDEX:
        st.ext_tex = c.args[0]


def _blend_is(st, a, b, c, d):
    return st.blend is not None and st.blend[:4] == (a, b, c, d)


def classify(sh, ext, model, st):
    if ext:
        return ("reflect_masked" if st.dest_test is True and st.dest_func == EQUAL_ZERO else "reflect"), False
    if (sh.unk1 & 4) != 0 and model in (1, 6):
        return "shadow", False
    pr = shape_prim(sh)
    abe = pr is not None and (pr & 0x40) != 0
    if model == 5 or (abe and _blend_is(st, 0, 2, 0, 1)):
        return "additive", False
    if sh.unk1 == 4 or (abe and _blend_is(st, 0, 2, 1, 1)):
        return "glint", False
    cm = st.color_mask if st.color_mask is not None else 0
    if abe and cm == 0 and st.depth_write is False and _blend_is(st, 0, 1, 0, 1):
        return "blend", False
    if cm == MASK_NOTHING:
        cut = st.alpha_test is True and not (st.af == GEQUAL and st.af_ref == 0) and st.af != ALWAYS
        return ("depth_cut" if cut else "depth"), False
    if cm == MASK_ALPHA:
        return "mask", False
    if cm == MASK_RGB:
        return ("soft" if st.depth_write is False else "soft_z"), False
    if cm != 0:
        return "opaque", True
    if st.alpha_test is False or (st.af == GEQUAL and st.af_ref == 0) or st.af == ALWAYS:
        return "noalpha", False
    if abe and _blend_is(st, 0, 1, 0, 1):
        return "cutout_blend", False
    return ("opaque" if st.af == GREATER and st.af_ref == 32 else "cutout"), False


def tex_hash(img):
    h, w = img.shape[:2]
    return "%dx%d:" % (w, h) + hashlib.sha1(img.tobytes()).hexdigest().upper()[:12]


def _fk(x):
    return "n" if x != x else f32_bits(x)


def flatten(draws, path=""):
    res = []
    for d in draws or []:
        if d["kind"] == "lamp":
            res += flatten(d.get("lamp_off"), path + "/off")
            res += flatten(d.get("lamp_on"), path + "/on")
        else:
            res.append((path, d))
    return res


def lod_shares(sel):
    """share[k] = j when LOD branch k's commands are byte-identical to the earlier branch j (j not itself shared)."""
    def ser(br):
        w = Writer()
        for c in br:
            c.write(w)
        return w.getvalue()
    sigs = [ser(br) if br else None for br in sel.branches]
    share = [-1] * len(sigs)
    for k in range(1, len(sigs)):
        if sigs[k] is None:
            continue
        for j in range(k):
            if share[j] < 0 and sigs[j] == sigs[k]:
                share[k] = j
                break
    return share


class Decompiler:
    def __init__(self, out_dir, colour=0):
        self.out_dir, self.colour = out_dir, colour
        self.materials = []
        self.tex_hashes = []
        self.approximated = {}
        self._mat_key = {}
        self._tex = {}

    def decompile(self, code, day, night, menu):
        info = carinfo_read(day.gtci)
        spec = {"format": "gt3-car/1", "name": code}
        spec["texture_lod_divisors"] = [1, 1, 1, 1]
        spec["info"] = info
        shares = {k: [] for k in ("lod_share", "shadow_lod_share", "ground_lod_share", "rim_lod_share", "rear_rim_lod_share")}
        spec["body"] = self.lods(day.body_set, "day", 0, shares["lod_share"])
        spec["shadow_draws"] = self.lods(day.body_set, "day", 1, shares["shadow_lod_share"])
        spec["ground_draws"] = self.lods(day.body_set, "day", 6, shares["ground_lod_share"])
        spec["rim"] = self.lods(day.rim_set, "rim", 0, shares["rim_lod_share"])
        if len(day.rim_set.models) > 1:
            spec["rear_rim"] = self.lods(day.rim_set, "rim", 1, shares["rear_rim_lod_share"])
        for key, share in shares.items():
            if any(j >= 0 for j in share):
                spec[key] = share                    # a tool-made car's LOD reuse (see Decompiler.lods)
        pool = self.lods(day.body_set, "day", 5)
        spec["headlight_pool"] = pool[0][0] if pool and pool[0] else None
        extras = []
        for m in (2, 3, 4, 5):
            if m != 5 or levels(day.body_set, 5) > 0:
                x = self.extra(day.body_set, "day", m, "race")
                if x is not None:
                    extras.append(x)
        if night is not None:
            spec["night_body"] = self.lods(night.body_set, "night", 0)
            for m in (1, 2, 3, 4, 5, 6):
                if (model_sig(night.body_set, m) != model_sig(day.body_set, m)
                        or self.tex_sig(night.body_set, "night", m) != self.tex_sig(day.body_set, "day", m)):
                    x = self.extra(night.body_set, "night", m, "night", force=True)
                    if x is not None:
                        extras.append(x)
        if menu is not None:
            l0 = self.lods(menu.body_set, "menu", 0)
            spec["menu_body"] = l0[0] if l0 else []
            l2 = self.lods(menu.body_set, "menu", 2)
            spec["menu_rim_front"] = l2[0] if l2 else None
            l3 = self.lods(menu.body_set, "menu", 3)
            spec["menu_rim_rear"] = l3[0] if l3 else None
            l6 = self.lods(menu.body_set, "menu", 6)
            spec["menu_ground_draws"] = l6[0] if l6 else None
            spec["menu_rim"] = self.lods(menu.rim_set, "mrim", 0)
            if len(menu.rim_set.models) > 1:
                spec["menu_rear_rim"] = self.lods(menu.rim_set, "mrim", 1)
            for m in (1, 4):
                x = self.extra(menu.body_set, "menu", m, "menu")
                if x is not None:
                    extras.append(x)
            if model_sig(menu.body_set, 5) != model_sig(day.body_set, 5):
                x5 = self.extra(menu.body_set, "menu", 5, "menu", force=True)
                if x5 is not None:
                    extras.append(x5)
        spec["extra_models"] = extras or None
        lv = {}

        def lvl(key, ms, m, norm):
            n = levels(ms, m)
            if n >= 0 and n != norm:
                lv[key] = n
        lvl("body", day.body_set, 0, 4)
        lvl("shadow", day.body_set, 1, 4)
        lvl("ground", day.body_set, 6, 4)
        lvl("rim", day.rim_set, 0, 4)
        lvl("rear_rim", day.rim_set, 1, 4)
        if night is not None:
            lvl("night_body", night.body_set, 0, lv.get("body", 4))
        if menu is not None:
            lvl("menu_rim", menu.rim_set, 0, 0)
            lvl("menu_rear_rim", menu.rim_set, 1, 0)
        spec["lod_levels"] = lv or None
        mc = {}
        for key, parts in (("day", day), ("night", night), ("menu", menu)):
            if parts is not None and len(parts.body_set.models) != 7:
                mc[key] = len(parts.body_set.models)
        spec["model_counts"] = mc or None
        spec["auto_shadows"] = False
        sets, tables = {}, {}

        def rec(key, ms):
            sets[key] = [len(ts.clut_patch_sets) for ts in ms.texture_sets]
            tables[key] = len(ms.variation_materials)
        rec("day", day.body_set)
        rec("rim", day.rim_set)
        if night is not None:
            rec("night", night.body_set)
        if menu is not None:
            rec("menu", menu.body_set)
            rec("mrim", menu.rim_set)
        spec["colors"] = max(1, max(max(sets["day"], default=0), tables["day"]))
        spec["color_sets"] = sets
        spec["color_tables"] = tables
        spec["materials"] = self.materials
        return spec

    def tex_sig(self, ms, src, m):
        out = []
        for l in self.lods(ms, src, m):
            parts = []
            for _, d in flatten(l):
                tm = d["mesh"].get("tri_materials") or []
                parts.append("+".join(self.tex_hashes[i] or "" if i >= 0 else "-" for i in dict.fromkeys(tm)))
            out.append(",".join(parts))
        return ";".join(out)

    def extra(self, ms, src, m, variant, force=False):
        if m >= len(ms.models):
            return {"variant": variant, "model": m, "lod_select": False, "lods": []} if force else None
        lods = self.lods(ms, src, m)
        sel = _find(ms.models[m], C.LOD_SELECT) is not None
        if not force and not sel and all(len(l) == 0 for l in lods):
            return None
        return {"variant": variant, "model": m, "lod_select": sel, "lods": lods}

    def lods(self, ms, src, m, shares=None):
        """The draws of each LOD branch. With `shares` (a list, filled in): a branch whose commands are IDENTICAL to an
        earlier one is the tool's LOD reuse (spec *_lod_share: the builder copies branch j verbatim) - it gets no draws
        of its own and shares[k] = j, so a rebuild emits the same bytes instead of duplicating the shapes. PD's own cars
        never have identical branches (0 of 857 day/night/menu files), so this only ever fires on tool-made cars."""
        if m >= len(ms.models):
            return []
        cmds = ms.models[m]
        sel = _find(cmds, C.LOD_SELECT)
        if sel is not None:
            share = lod_shares(sel) if shares is not None else [-1] * len(sel.branches)
            if shares is not None:
                shares[:] = share
            return [[] if share[k] >= 0 else self.walk(ms, src, m, br, GsState.defaults(), [0])
                    for k, br in enumerate(sel.branches)]
        return [self.walk(ms, src, m, cmds, GsState.defaults(), [0])]

    def walk(self, ms, src, model, cmds, st, tt):
        """tt = [texTable] (passed by reference, like the C#'s ref int)."""
        draws = []
        mstack = []
        matrix = None
        driver = ratio = None
        for c in cmds:
            op = c.op
            if op == C.PUSH_MATRIX:
                mstack.append(matrix)
            elif op == C.POP_MATRIX:
                matrix = mstack.pop() if mstack else None
            elif op == C.MULT_MATRIX:
                matrix = list(c.args)
            elif op == C.CALL_MODEL_CALLBACK and not c.branches and 1 <= c.param <= 5:
                driver = c.param
            elif op == C.SHAPE_TWEEN_RATIO:
                ratio = c.args[0]
            elif op == C.OP53:
                md = self.draw(ms, src, model, c.args[0], st, tt[0], morph=True, chain=c.args[1])
                mo = md["morph"]
                mo["driver"] = ("steering" if driver == 5 else "flutter%d" % driver) if driver is not None else "fixed"
                mo["ratio"] = (ratio if ratio is not None else 0.0) if driver is None else 0.0
                mo["mult"] = c.args[1]
                mo["matrix"] = matrix
                draws.append(md)
                driver = ratio = None
            elif op == C.BBOX_RENDER:
                draws += self.walk(ms, src, model, c.commands, st, tt)
            elif op == C.LOD_SELECT:
                pass
            elif op in (C.SET_TEX_TABLE_BYTE, C.SET_TEX_TABLE_USHORT):
                tt[0] = c.args[0]
            elif op == C.CALL_MODEL_CALLBACK:
                branches = [self.walk(ms, src, model, br, st.clone(), [tt[0]]) for br in c.branches]
                draws.append(_draw_dict(name=_CALLBACK_NAMES.get(c.param, str(c.param)), kind="lamp",
                                        lamp_off=branches[0] if branches else [],
                                        lamp_on=branches[1] if len(branches) > 1 else []))
            elif op in (C.CALL_SHAPE_BYTE, C.CALL_SHAPE_USHORT):
                draws.append(self.draw(ms, src, model, c.args[0], st, tt[0]))
            else:
                apply_state(c, st)
        return draws

    def draw(self, ms, src, model, shape_idx, st, tex_table, morph=False, chain=1):
        sh = ms.shapes[shape_idx]
        sd = sh.decode()
        # a KEYFRAME CHAIN (opcode 53 count > 1): segment k = shape_idx + k, key k -> key k + 1 (engine 0x2262b0)
        more = []
        if morph and chain > 1:
            for k in range(1, chain):
                if shape_idx + k >= len(ms.shapes):
                    break
                sk = ms.shapes[shape_idx + k].decode()
                if len(sk.vertices) != len(sd.vertices) or len(sk.tween) != len(sd.vertices):
                    break
                more.append(sk)
        has511 = any(d.tex == 511 for d in sh.descriptors)
        ext = sh.unk1 == 2
        env_tex = ext and not has511
        kind, approx = classify(sh, ext, model, st)
        if approx:
            k = "m%d U%d at=%s cm=0x%X bf=%s" % (model, sh.unk1, st.alpha_test, st.color_mask or 0, st.blend)
            self.approximated[k] = self.approximated.get(k, 0) + 1
        nv = len(sd.vertices)
        has_n, has_c = len(sd.normals) == nv, len(sd.colors) == nv
        has_uv = not ext and len(sd.uvs) == nv
        morph = morph and len(sd.tween) == nv
        has_tn = morph and len(sd.tween_normals) == nv
        # a race WHEEL FACE (texture 511 + ExternalTexIndex 1): the engine textures it every frame from the rim's
        # texture sets 1/2 (still / spinning blur, 0x2243a0) - its picture is set 1's first texture
        face_ts = self.face_set(ms, model) if has511 and not ext and src == "rim" and st.ext_tex == 1 else None
        face_mat = [-1 if ext and not env_tex else
                    self.material(ms, src, f[3], model, 1, ts_obj=face_ts, tag="face") if face_ts is not None else
                    self.material(ms, src, f[3], tex_table, 0 if f[4] == 511 else f[4])
                    for f in sd.faces]
        remap = [0] * nv
        seen = {}
        verts, nrms, uvs, cols, tgt, tgtn = [], [], [], [], [], []
        xkeys, xnrm = [[] for _ in more], [[] for _ in more]
        for i in range(nv):
            p = sd.vertices[i]
            key = (_fk(p[0]), _fk(p[1]), _fk(p[2]))
            if has_n:
                key += tuple(_fk(x) for x in sd.normals[i])
            if has_uv:
                key += (_fk(sd.uvs[i][0]), _fk(sd.uvs[i][1]))
            if has_c:
                key += tuple(sd.colors[i])
            if morph:
                key += ("T",) + tuple(_fk(x) for x in sd.tween[i])
                for sk in more:
                    key += tuple(_fk(x) for x in sk.tween[i])
            at = seen.get(key)
            if at is None:
                at = seen[key] = len(verts)
                verts.append(list(p))
                if has_n:
                    nrms.append(list(sd.normals[i]))
                if has_uv:
                    uvs.append([sd.uvs[i][0], f32(1.0 - sd.uvs[i][1])])
                if has_c:
                    cols.append(list(sd.colors[i]))
                if morph:
                    tgt.append(list(sd.tween[i]))
                if has_tn:
                    tgtn.append(list(sd.tween_normals[i]))
                for j, sk in enumerate(more):
                    xkeys[j].append(list(sk.tween[i]))
                    if len(sk.tween_normals) == nv:
                        xnrm[j].append(list(sk.tween_normals[i]))
            remap[i] = at
        mesh = {"vertices": verts, "normals": nrms if has_n else None, "uvs": uvs if has_uv else None,
                "colors": cols if has_c else None,
                "triangles": [[remap[f[0] - 1], remap[f[1] - 1], remap[f[2] - 1]] for f in sd.faces],
                "tri_materials": None if ext and not env_tex else face_mat}
        lit = (sh.unk1 & 1) != 0 and (sh.unk1 & 4) == 0
        alpha_ref = st.af_ref if (st.alpha_test is True and st.af == GREATER and st.af_ref not in (32, 0, 127)) else None
        d = _draw_dict(name="%s_m%d_s%d" % (src, model, shape_idx), kind=kind, mesh=mesh, lit=lit,
                       double_sided=st.cull is False,
                       external_tex=(st.ext_tex if st.ext_tex is not None else 0) if has511 and not ext else -1,
                       mat=-1 if (ext and not env_tex) or not face_mat else face_mat[0],
                       env_texture=env_tex, reflect_tint=(not ext) or st.ext_tint is True, alpha_ref=alpha_ref,
                       morph={"driver": "flutter1", "ratio": 0.0, "mult": 1, "matrix": None, "target": tgt,
                              "target_normals": tgtn if has_tn else None,
                              "extra_keys": xkeys or None,
                              "extra_key_normals": xnrm if xkeys and all(len(x) == len(verts) for x in xnrm) else None}
                       if morph else None)
        untextured = all(v.tex == 0 for v in sh.descriptors)
        u1, u3 = shape_flags(parse_kind(kind), lit, ext, untextured)
        if sh.unk1 != u1:
            d["unk1"] = sh.unk1
        if sh.unk3 != u3:
            d["unk3"] = sh.unk3
        d["_probe_shape"] = shape_idx
        return d

    @staticmethod
    def face_set(ms, model):
        """The texture set holding wheel model `model`'s face picture: variation [3 * model] set 1 (PD: 3 distance
        levels x 2 models), or set 1 when there are no variations. None = the rim has no spinning-wheel sets."""
        vs = ms.variation_texsets
        sets = vs[3 * model] if vs and 3 * model < len(vs) else ms.texture_sets
        return sets[1] if len(sets) > 1 else None

    def material(self, ms, src, mat_idx, tset, tex_idx, ts_obj=None, tag=None):
        key = "%s:%d:%d:%d" % (src, mat_idx, tset, tex_idx) + (":" + tag if tag else "")
        if key in self._mat_key:
            return self._mat_key[key]
        m = {"name": key.replace(":", "_"), "texture": None, "night_texture": None, "wrap": "region", "psm": 4,
             "preset": "none", "ambient": None, "diffuse": None, "specular": None, "emissive": None,
             "power": 0.0, "flags": 0, "color_textures": None, "color_materials": None}
        th = None
        if 0 < mat_idx and mat_idx - 1 < len(ms.materials):
            p = ms.materials[mat_idx - 1]
            m["ambient"], m["diffuse"] = list(p.ambient), list(p.diffuse)
            m["specular"], m["emissive"] = list(p.specular), list(p.unk_color)
            m["power"], m["flags"] = p.unk, p.flags
            if ms.variation_materials:
                rows = []
                for row in ms.variation_materials:
                    if mat_idx - 1 < len(row):
                        r = row[mat_idx - 1]
                        rows.append({"ambient": list(r.ambient), "diffuse": list(r.diffuse), "specular": list(r.specular),
                                     "emissive": list(r.unk_color), "power": r.unk, "flags": r.flags,
                                     "unk2": r.unk2, "unk3": r.unk3})
                    else:
                        rows.append(None)
                m["color_materials"] = rows
        ts = ts_obj if ts_obj is not None else ms.texture_sets[tset] if tset < len(ms.texture_sets) else None
        if tex_idx > 0 and ts is not None and tex_idx - 1 < len(ts.textures):
            tk = "%s.%s%d.%d" % (src, tag or "", tset, tex_idx - 1)
            if tk not in self._tex:
                try:
                    img = ts.image(tex_idx - 1, self.colour if self.colour < len(ts.clut_patch_sets) else 0)
                    h = tex_hash(img)
                    # a wheel face is usually the SAME picture as the rim's own texture: share that file, so one edit
                    # in Blender changes both (the build re-makes the spinning-wheel sets from it)
                    face = tag == "face"
                    same = next((v for k, v in self._tex.items() if v[0] and v[1] == h and k.startswith(src + ".")
                                 and (".face" in k) != face), None) if src == "rim" else None
                    rel = same[0] if same else "Textures/%s.png" % tk
                    if self.out_dir is not None and not same:
                        write_png(os.path.join(self.out_dir, rel), img)
                    self._tex[tk] = (rel, h)
                except Exception as ex:
                    print("  texture %s: %s" % (tk, ex), file=sys.stderr)
                    self._tex[tk] = (None, None)
            m["texture"], th = self._tex[tk]
            if self.colour == 0 and len(ts.clut_patch_sets) > 1:
                own = ts.textures[tex_idx - 1]
                ct = [None] * len(ts.clut_patch_sets)
                any_var = False
                for c in range(1, len(ts.clut_patch_sets)):
                    pt = next((x for x in ts.clut_patch_sets[c] if x.tex == tex_idx - 1), None)
                    if pt is None or (pt.cbp == own.cbp and pt.csa == own.csa):
                        continue
                    rel = "Textures/%s.c%d.png" % (tk, c)
                    try:
                        img = ts.image(tex_idx - 1, c)
                        if self.out_dir is not None:
                            write_png(os.path.join(self.out_dir, rel), img)
                        ct[c] = rel
                        any_var = True
                    except Exception as ex:
                        print("  colour texture %s.c%d: %s" % (tk, c, ex), file=sys.stderr)
                if any_var:
                    m["color_textures"] = ct
            tx = ts.textures[tex_idx - 1]
            m["psm"] = 4 if tx.psm in (gs.PSMT4, gs.PSMT4HH, gs.PSMT4HL) else 8
            odd = not _pow2(tx.maxu + 1) or not _pow2(tx.maxv + 1)
            m["wrap"] = "repeat" if tx.wms == gs.REPEAT else "clamp" if tx.wms == gs.CLAMP else "region_pad" if odd else "region"
        self.materials.append(m)
        self.tex_hashes.append(th)
        self._mat_key[key] = len(self.materials) - 1
        return self._mat_key[key]


def _pow2(v):
    return v > 0 and (v & (v - 1)) == 0


_DRAW_ORDER = ("name", "kind", "mat", "mesh", "lit", "double_sided", "reflect", "reflect_strength", "reflect_tint",
               "env_texture", "alpha_ref", "unk1", "unk3", "stamp", "external_tex", "lamp_off", "lamp_on",
               "lamp_off_night", "morph")


def _draw_dict(**kw):
    d = {"name": None, "kind": "opaque", "mat": -1, "mesh": None, "lit": True, "double_sided": False, "reflect": None,
         "reflect_strength": 128, "reflect_tint": True, "env_texture": False, "alpha_ref": None, "unk1": None,
         "unk3": None, "stamp": False, "external_tex": -1, "lamp_off": None, "lamp_on": None,
         "lamp_off_night": None, "morph": None}
    d.update(kw)
    return d


_SPEC_ORDER = ("format", "name", "materials", "body", "night_body", "menu_body", "shadow", "ground_shadow",
               "menu_ground_shadow", "shadow_draws", "ground_draws", "menu_ground_draws", "headlight_pool", "rim",
               "menu_rim", "rear_rim", "menu_rear_rim", "menu_rim_front", "menu_rim_rear", "tire_file", "tire_texture",
               "menu_tire_file", "rim_blur_file", "tire_header", "texture_lod_divisors", "info", "info_points", "auto_shadows",
               "info_file", "night_info_file", "menu_info_file", "extra_models", "colors", "color_sets",
               "color_tables", "target", "lod_share", "rim_lod_share", "rear_rim_lod_share", "lod_levels",
               "model_counts", "shadow_lod_share", "ground_lod_share")


def _clean(o):
    """Drop the private _probe fields and order draw keys like the C# property declarations."""
    if isinstance(o, dict):
        if "kind" in o and "external_tex" in o:
            ordered = {k: o[k] for k in _DRAW_ORDER if k in o}
            return {k: _clean(v) for k, v in ordered.items()}
        return {k: _clean(v) for k, v in o.items() if not k.startswith("_")}
    if isinstance(o, list):
        return [_clean(v) for v in o]
    return o


def spec_json(spec):
    full = dict(spec)
    full.setdefault("tire_header", [2, 0, 8])
    ordered = {k: full.get(k) for k in _SPEC_ORDER}
    ordered["info"] = spec["info"]                      # CarInfo JSON keeps its nulls (PDTools writes them)
    out = prune(_clean({k: v for k, v in ordered.items() if k != "info"}))
    res = {}
    for k in _SPEC_ORDER:
        if k == "info":
            res[k] = spec["info"]
        elif k in out:
            res[k] = out[k]
    return dumps(res)


def decompile_car(root_or_file, code, out_dir):
    """gt3-car-decompile. root_or_file = a folder with cars/day, cars/night, menu/cars (PD's layout) or one
    car container (day body only)."""
    if os.path.isfile(root_or_file):
        day_f, night_f, menu_f = root_or_file, None, None
        code = code or os.path.basename(root_or_file)
    else:
        def p(rel):
            f = os.path.join(root_or_file, *rel.split("/"), code)
            return f if os.path.isfile(f) else None
        day_f = p("cars/day")
        if day_f is None:
            raise FileNotFoundError("no cars/day/%s under %s" % (code, root_or_file))
        night_f, menu_f = p("cars/night"), p("menu/cars")
    os.makedirs(os.path.join(out_dir, "Textures"), exist_ok=True)
    day = CarFileParts.load(day_f)
    night = CarFileParts.load(night_f) if night_f else None
    menu = CarFileParts.load(menu_f) if menu_f else None
    dc = Decompiler(out_dir)
    spec = dc.decompile(code, day, night, menu)
    size = struct.unpack_from("<I", day.gtci, 12)[0]
    ok = False
    try:
        ok = carinfo_write(spec["info"]) == day.gtci[:min(size, len(day.gtci))]
    except Exception:
        ok = False
    if not ok:
        with open(os.path.join(out_dir, "gtci.bin"), "wb") as f:
            f.write(day.gtci[:min(size, len(day.gtci))])
        spec["info_file"] = "gtci.bin"
        log("  GTCI kept raw (gtci.bin): its JSON form is not bit-exact")
    for parts, key in ((night, "night"), (menu, "menu")):
        if parts is None:
            continue
        g = parts.gtci
        gsz, dsz = struct.unpack_from("<I", g, 12)[0], size
        if gsz == dsz and g[:min(gsz, len(g))] == day.gtci[:min(dsz, len(day.gtci))]:
            continue
        with open(os.path.join(out_dir, "gtci_%s.bin" % key), "wb") as f:
            f.write(g[:min(gsz, len(g))])
        spec["night_info_file" if key == "night" else "menu_info_file"] = "gtci_%s.bin" % key
    with open(os.path.join(out_dir, "tire.gttr"), "wb") as f:
        f.write(day.gttr)
    spec["tire_file"] = "tire.gttr"
    if any(d is not None and d.get("external_tex") == 1 for l in (spec["rim"] or []) + (spec.get("rear_rim") or [])
           for d in (l or [])):
        # PD's spinning-wheel texture sets (still face + blur, 3 distance levels x 2 wheel models): the build puts
        # them back untouched while the face picture is unchanged
        with open(os.path.join(out_dir, "rim_blur.gttw"), "wb") as f:
            f.write(day.gttw)
        spec["rim_blur_file"] = "rim_blur.gttw"
    if menu is not None and menu.gttr != day.gttr:
        with open(os.path.join(out_dir, "menu_tire.gttr"), "wb") as f:
            f.write(menu.gttr)
        spec["menu_tire_file"] = "menu_tire.gttr"
    with open(os.path.join(out_dir, "car.json"), "w", encoding="utf-8") as f:
        f.write(spec_json(spec))
    msg = "%s: %d materials | body LODs %s" % (code, len(spec["materials"]), "/".join(str(len(l)) for l in spec["body"]))
    if spec.get("night_body") is not None:
        msg += " | night %s" % "/".join(str(len(l)) for l in spec["night_body"])
    if spec.get("menu_body") is not None:
        msg += " | menu %d + rims %d/%d" % (len(spec["menu_body"]), len(spec.get("menu_rim_front") or []), len(spec.get("menu_rim_rear") or []))
    msg += " | rim LODs %s" % "/".join(str(len(l)) for l in spec["rim"])
    msg += " | shadow %d ground %d pool %s" % (sum(1 for l in spec["shadow_draws"] if l), sum(1 for l in spec["ground_draws"] if l),
                                               "no" if spec.get("headlight_pool") is None else "yes")
    log(msg)
    if dc.approximated:
        log("  approximated states: " + ", ".join("%s x%d" % kv for kv in dc.approximated.items()))
    return spec

# Nenkai's research is the core of this human & machine made tool.
