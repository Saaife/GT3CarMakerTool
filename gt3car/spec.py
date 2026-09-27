"""The car spec (car.json, format gt3-car/1): loading with the C# defaults, draw kinds, PD's render recipes
and the lazy GS-state transitions (GT4TrackKit Gt3CarSpec.cs)."""

import decimal
import json
import math
import struct

from .f32 import f32
from . import commands as C

# ---- JSON --------------------------------------------------------------------------------------------
def _parse_int(s):
    return -0.0 if s == "-0" else int(s)


_NAMED = {"NaN": math.nan, "Infinity": math.inf, "-Infinity": -math.inf}


_D, _Q = struct.Struct("<d"), struct.Struct("<Q")
_LOW29, _HALF29 = (1 << 29) - 1, 1 << 28


def _parse_float(s):
    """A JSON number as the C# float reader sees it: the decimal text rounded ONCE to float32. Going through a double
    first rounds a second time, and a double that lands exactly on a float32 midpoint (Blender's 1 - v UVs do, all the
    time) would then tie-to-even where the decimal text itself lies above or below the midpoint."""
    d = float(s)
    bits = _Q.unpack(_D.pack(d))[0]
    if (bits & _LOW29) != _HALF29 or d != d or abs(d) < 1.1754943508222875e-38 or abs(d) > 3.4028234663852886e38:
        return f32(d)
    x = decimal.Decimal(s)
    exact = decimal.Decimal(d)
    if x == exact:
        return f32(d)                                   # a true tie: to even, as both readers do
    mag = bits & ~(1 << 63)
    lo_mag = _D.unpack(_Q.pack(mag & ~_LOW29))[0]
    hi_mag = _D.unpack(_Q.pack((mag & ~_LOW29) + (1 << 29)))[0]
    up = abs(x) > abs(exact)
    r = hi_mag if up else lo_mag
    return -r if d < 0 else r


def load_json(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f, parse_int=_parse_int, parse_float=_parse_float, parse_constant=lambda s: _NAMED[s])


_KEEP_TOP = ("info", "color_sets", "color_tables", "lod_levels", "model_counts")   # data keys, looked up case-sensitively


def _lower_keys(o, top=False):
    """The spec deserializer is case-insensitive (PropertyNameCaseInsensitive); PDTools' CarInfo JSON and the
    spec's string-keyed dictionaries are not."""
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            lk = k.lower() if isinstance(k, str) else k
            out[lk] = v if top and lk in _KEEP_TOP else _lower_keys(v)
        return out
    if isinstance(o, list):
        return [_lower_keys(v) for v in o]
    return o


def load_spec(path):
    return _lower_keys(load_json(path), top=True)


def fnum(v, default=0.0):
    """A C# float field."""
    if v is None:
        return default
    if isinstance(v, str):
        return _NAMED[v]
    return f32(float(v))


def inum(v, default=0):
    if v is None:
        return default
    return int(v)


def farr(a):
    return None if a is None else [fnum(x) for x in a]


# ---- defaults (the C# property initialisers) -----------------------------------------------------------
DRAW_DEFAULTS = {"kind": "opaque", "mat": -1, "lit": True, "double_sided": False, "reflect_strength": 128,
                 "reflect_tint": True, "env_texture": False, "stamp": False, "external_tex": -1}
MAT_DEFAULTS = {"wrap": "region", "psm": 4, "preset": "plain", "power": 0.0, "flags": 0}
MORPH_DEFAULTS = {"driver": "flutter1", "ratio": 0.0, "mult": 1, "target": []}


def dget(d, key, defaults):
    """A value-type property: missing or null = its initialiser."""
    v = d.get(key)
    return defaults.get(key) if v is None else v


def spec_get(spec, key, default=None):
    """A reference property: missing = initialiser, explicit null = null."""
    return spec[key] if key in spec else default


SPEC_DEFAULTS = {"materials": [], "body": [], "tire_header": [2, 0, 8], "texture_lod_divisors": [1, 2, 4, 4],
                 "auto_shadows": True, "colors": 1}


def sp(spec, key):
    if key in spec:
        v = spec[key]
        if v is None and key in ("auto_shadows", "colors"):
            return SPEC_DEFAULTS[key]
        return v
    return SPEC_DEFAULTS.get(key)


# ---- kinds ---------------------------------------------------------------------------------------------
(OPAQUE, CUTOUT, CUTOUT_BLEND, NOALPHA, SOFT, SOFT_Z, MASK, DEPTH, DEPTH_CUT, REFLECT, REFLECT_MASKED,
 ADDITIVE, GLINT, BLEND, SHADOW, LAMP) = range(16)

_KIND = {"opaque": OPAQUE, "o": OPAQUE, "cutout": CUTOUT, "c": CUTOUT, "cutout_blend": CUTOUT_BLEND,
         "noalpha": NOALPHA, "n": NOALPHA, "soft": SOFT, "s": SOFT, "soft_z": SOFT_Z, "sz": SOFT_Z,
         "mask": MASK, "m": MASK, "depth": DEPTH, "z": DEPTH, "depth_cut": DEPTH_CUT, "reflect": REFLECT,
         "r": REFLECT, "reflect_masked": REFLECT_MASKED, "r*": REFLECT_MASKED, "additive": ADDITIVE,
         "glint": GLINT, "blend": BLEND, "shadow": SHADOW, "lamp": LAMP}
KIND_NAMES = {OPAQUE: "opaque", CUTOUT: "cutout", CUTOUT_BLEND: "cutout_blend", NOALPHA: "noalpha", SOFT: "soft",
              SOFT_Z: "soft_z", MASK: "mask", DEPTH: "depth", DEPTH_CUT: "depth_cut", REFLECT: "reflect",
              REFLECT_MASKED: "reflect_masked", ADDITIVE: "additive", GLINT: "glint", BLEND: "blend",
              SHADOW: "shadow", LAMP: "lamp"}


def parse_kind(k):
    k = (k if k is not None else "opaque").lower()
    if k not in _KIND:
        raise ValueError("unknown draw kind '%s'" % k)
    return _KIND[k]


# ---- GS state --------------------------------------------------------------------------------------------
NEVER, ALWAYS, LESS, LEQUAL, EQUAL, GEQUAL, GREATER, NOTEQUAL = range(8)
EQUAL_ZERO, EQUAL_ONE = 2, 5
MASK_RGB, MASK_ALPHA, MASK_NOTHING = 0xFF000000, 0x00FFFFFF, 0xFFFFFFFF

_FIELDS = ("alpha_test", "af", "af_ref", "blend", "depth_write", "color_mask", "dest_test", "dest_func",
           "fog_black", "ext_tint", "cull", "ext_tex")


class GsState:
    __slots__ = _FIELDS

    def __init__(self, **kw):
        for f in _FIELDS:
            setattr(self, f, kw.get(f))

    def clone(self):
        s = GsState()
        for f in _FIELDS:
            setattr(s, f, getattr(self, f))
        return s

    @staticmethod
    def defaults():
        return GsState(alpha_test=True, af=GREATER, af_ref=32, blend=(0, 1, 0, 1, 0), depth_write=True, color_mask=0,
                       dest_test=False, dest_func=EQUAL_ONE, fog_black=False, ext_tint=False, cull=True, ext_tex=0)


def target_state(k):
    if k == OPAQUE:
        return GsState(alpha_test=True, af=GREATER, af_ref=32, dest_test=False, depth_write=True, color_mask=0, fog_black=False, ext_tint=False)
    if k == CUTOUT:
        return GsState(alpha_test=True, af=GREATER, af_ref=0, dest_test=False, depth_write=True, color_mask=0, fog_black=False, ext_tint=False)
    if k == CUTOUT_BLEND:
        return GsState(alpha_test=True, af=GREATER, af_ref=127, dest_test=False, blend=(0, 1, 0, 1, 0), depth_write=True, color_mask=0, fog_black=False, ext_tint=False)
    if k == NOALPHA:
        return GsState(alpha_test=False, dest_test=False, depth_write=True, color_mask=0, fog_black=False, ext_tint=False)
    if k == SOFT:
        return GsState(alpha_test=False, dest_test=False, blend=(0, 1, 0, 1, 0), depth_write=False, color_mask=MASK_RGB, fog_black=False, ext_tint=False)
    if k == SOFT_Z:
        return GsState(alpha_test=False, dest_test=False, blend=(0, 1, 0, 1, 0), depth_write=True, color_mask=MASK_RGB, fog_black=False, ext_tint=False)
    if k == MASK:
        return GsState(alpha_test=False, dest_test=False, depth_write=False, color_mask=MASK_ALPHA, fog_black=False, ext_tint=False)
    if k == DEPTH:
        return GsState(alpha_test=False, dest_test=False, depth_write=True, color_mask=MASK_NOTHING, fog_black=False, ext_tint=False)
    if k == DEPTH_CUT:
        return GsState(alpha_test=True, af=GREATER, af_ref=0, dest_test=False, depth_write=True, color_mask=MASK_NOTHING, fog_black=False, ext_tint=False)
    if k == REFLECT:
        return GsState(alpha_test=False, dest_test=False, blend=(0, 2, 1, 1, 0), depth_write=False, color_mask=0, fog_black=True, ext_tint=True)
    if k == REFLECT_MASKED:
        return GsState(alpha_test=False, dest_test=True, dest_func=EQUAL_ZERO, blend=(0, 2, 1, 1, 0), depth_write=False, color_mask=0, fog_black=True, ext_tint=True)
    if k == ADDITIVE:
        return GsState(alpha_test=False, dest_test=False, blend=(0, 2, 0, 1, 128), depth_write=False, color_mask=MASK_RGB, fog_black=True)
    if k == SHADOW:
        return GsState(alpha_test=False, dest_test=False, blend=(0, 1, 0, 1, 0), depth_write=False, color_mask=MASK_RGB, fog_black=False)
    if k == BLEND:
        return GsState(alpha_test=False, dest_test=False, blend=(0, 1, 0, 1, 0), depth_write=False, color_mask=0, fog_black=False, ext_tint=False)
    if k == GLINT:
        return GsState(alpha_test=False, dest_test=False, blend=(0, 2, 1, 1, 128), depth_write=False, color_mask=0, fog_black=True)
    return GsState()


def transition(o, cur, tgt):
    """CarTransition: emit only what moves cur to tgt, in PD's order."""
    if tgt.alpha_test is not None and cur.alpha_test != tgt.alpha_test:
        o.append(C.Cmd(C.ENABLE_ALPHA_TEST if tgt.alpha_test else C.DISABLE_ALPHA_TEST))
        cur.alpha_test = tgt.alpha_test
    if tgt.af is not None and tgt.af_ref is not None and (cur.af != tgt.af or cur.af_ref != tgt.af_ref):
        o.append(C.Cmd(C.ALPHA_FUNC, tgt.af & 0xFF, tgt.af_ref & 0xFF))
        cur.af, cur.af_ref = tgt.af, tgt.af_ref
    if tgt.dest_test is not None and cur.dest_test != tgt.dest_test:
        o.append(C.Cmd(C.ENABLE_DEST_ALPHA_TEST if tgt.dest_test else C.DISABLE_DEST_ALPHA_TEST))
        cur.dest_test = tgt.dest_test
    if tgt.dest_func is not None and cur.dest_func != tgt.dest_func:
        o.append(C.Cmd(C.DEST_ALPHA_FUNC, tgt.dest_func & 0xFF))
        cur.dest_func = tgt.dest_func
    if tgt.blend is not None and cur.blend != tgt.blend:
        o.append(C.blend_func(*tgt.blend))
        cur.blend = tgt.blend
    if tgt.depth_write is not None and cur.depth_write != tgt.depth_write:
        o.append(C.Cmd(C.ENABLE_DEPTH_MASK if tgt.depth_write else C.DISABLE_DEPTH_MASK))
        cur.depth_write = tgt.depth_write
    if tgt.color_mask is not None and cur.color_mask != tgt.color_mask:
        o.append(C.Cmd(C.COLOR_MASK, tgt.color_mask & 0xFFFFFFFF))
        cur.color_mask = tgt.color_mask
    if tgt.fog_black is not None and cur.fog_black != tgt.fog_black:
        o.append(C.Cmd(C.SET_FOG_COLOR, 0) if tgt.fog_black else C.Cmd(C.COPY_FOG_COLOR))
        cur.fog_black = tgt.fog_black
    if tgt.ext_tint is not None and cur.ext_tint != tgt.ext_tint:
        o.append(C.Cmd(C.GT3_2_4F, f32(0.8), f32(0.8), f32(0.8), 0.0) if tgt.ext_tint else C.Cmd(C.GT3_2_1UI, 0.0))
        cur.ext_tint = tgt.ext_tint
    if tgt.cull is not None and cur.cull != tgt.cull:
        o.append(C.Cmd(C.ENABLE_CULL_FACE if tgt.cull else C.DISABLE_CULL_FACE))
        cur.cull = tgt.cull
    if tgt.ext_tex is not None and cur.ext_tex != tgt.ext_tex:
        o.append(C.Cmd(C.EXTERNAL_TEX_INDEX, tgt.ext_tex & 0xFF))
        cur.ext_tex = tgt.ext_tex


def shape_flags(kind, lit, ext, untextured):
    """CarShapeFlags: Unk1 = the VU program, Unk3 bit 0 = no texture of its own."""
    if ext:
        return 2, 0x01
    if kind in (SHADOW, ADDITIVE):
        u1 = 5
    elif kind == GLINT:
        u1 = 4
    elif kind in (MASK, DEPTH, DEPTH_CUT):
        u1 = 0
    else:
        u1 = 1 if lit else 0
    return u1, 0x01 if untextured else 0x00


def morph_param(driver):
    return {"flutter1": 1, "flap_fl": 1, "flutter2": 2, "flap_fr": 2, "flutter3": 3, "flap_rl": 3,
            "flutter4": 4, "flap_rr": 4, "steering": 5}.get((driver or "").lower(), 0)

# Nenkai's research is the core of this human & machine made tool.
