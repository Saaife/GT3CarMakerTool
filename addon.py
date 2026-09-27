# GT3 CAR MAKER v3 - ONE CAR = ONE FILE
#   Start in the 3D view's sidebar (N) > GT3 Car: "Race car" makes a car collection with LOD0-3 collections, "Menu car"
#   the showroom car - both with PD's full car-info template (4 headlights, 4 brake lights, 5 brake flares with the
#   rear-window third light, 2 exhausts, 20 cameras; hidden extras: logo glow, rear fog, roof light, sparks - unhide).
#   Export writes that ONE file: a race car to data/cars/day/<code> (its stock wheel lives INSIDE it - the engine loads
#   no wheel file for it), a menu car to data/menu/cars/<code> (+ data/menu/wheel/<code>, written for you: the showroom
#   loads it by car code). Race LOD1-3 left empty reuse LOD0; LOD3 stays empty like PD's.
#   COLLECTIONS say what their objects are: LOD0 > Body / Wheel / Rear Wheel / Steering / Mudflaps (menu car also
#   Brake Front / Brake Rear) - drop the pieces in, modifiers and all; a mudflap's corner comes from where it sits.
#   Any word below works as a collection name too. Object NAMES (suffix words, any order, case-insensitive) win:
#     _MainBody       paint / body shell: opaque, lit, + automatic EXT reflection (strength = texture alpha)
#     _Chrome         opaque + double-strength reflection
#     _Glass          PD glass: soft blend with depth, alpha stamp, + reflection
#     _Soft           soft transparency, no reflection (wheel arches, soft decals)
#     _Cutout         alpha-tested holes (grilles, mesh)
#     _Reflect        a hand-made reflection mesh (drawn as the EXT pass only)
#     _TailOff/_TailOn  tail lamp unlit / lit (switches under braking)
#     _Glow           additive (lamp glows)
#     _HeadlightBeam  the white-on-black beam pool on the road
#     _RTShadow       real-time shadow mesh   (none = generated PD-style)
#     _GroundShadow   baked soft ground shadow (none = generated PD-style)
#     _Rim            the wheel - model it where it sits on the car, any size, either side: the export turns it into
#                     PD's unit wheel (radius 1 at the origin, left side) that the game scales and places itself
#     _RearWheel      optional: a different REAR wheel (PD: wider rears, Formula cars 2-4x) - else _Rim is all four
#   SAVE FOLDER decides the car: data/cars/day = the race car, data/menu/cars = the menu car (a race car saved there
#   is built from its LOD0; a menu car saved in cars/day becomes a race car whose LOD1-2 reuse it).
#     _BrakeFront/_BrakeRear  menu car only: the showroom's brake discs behind the rims
#     _MudflapFL/_MudflapFR/_MudflapRL/_MudflapRR (= _Flutter1.._Flutter4), _Steering: an ANIMATED part. Give it a
#                     shape key "GT3 Morph" (+ "GT3 Morph 2" ...) = its key poses (sidebar > Selected part > Add morph
#                     target); the engine plays mesh > key 1 > ... - _Steering follows the steering (PD: left / centre /
#                     right), mudflaps follow a per-wheel spring (PD: mesh = HANGING, one key = swept back + curled)
#     flags: _2S double sided, _Unlit pre-lit vertex colour, _NoReflect no reflection
#     no suffix       an ordinary opaque part
#   HIDDEN objects (eye icon / "Disable in Viewports") are left out; hiding a whole COLLECTION does not.
#   Car info = empties by name: Headlight_0.. (every light that glows at night: fogs, logos, roof lights too),
#   BrakeLight_0.., BrakeFlare_0.., Exhaust_0.., Spark_0.., Cam_<SLOT>.
# PAINT COLOURS (sidebar > Paint colours): the list = what the dealer offers. Row 0 = your textures as they are; click a
#   row to see that colour; "Make colour textures" recolours the paint for every row. Export writes the colours into
#   the car AND the game's colour list (data/database/carcolor.db) so the two always agree.
# Axes: game (x, y, z) -> Blender (-x, z, y); the car faces -Y (Front view), ground at Z = 0.

import bpy
import json
import math
import os
import re
import base64
import contextlib
import io
import shutil
import struct
import tempfile
import traceback

from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty,
                       PointerProperty, StringProperty)
from bpy.types import AddonPreferences, Operator, Panel, PropertyGroup, UIList
from bpy_extras.io_utils import ExportHelper, ImportHelper
from mathutils import Matrix, Vector

# every item carries a FIXED number = its v1 position, so .blend files saved with v1 keep their choices (v1 stored
# these by position; v2 inserted Auto at the top)
_KINDS = [
    ("AUTO", "Auto (from name)", "Use the object's name suffix", "NONE", 99),
    ("opaque", "Opaque", "Alpha test > 32. Texture alpha = reflection strength", "NONE", 0),
    ("cutout", "Cutout", "Alpha test > 0 (grilles, decals with holes)", "NONE", 1),
    ("cutout_blend", "Cutout + blend", "Alpha test > 127 with blending: anti-aliased cut edges (PD rims)", "NONE", 2),
    ("noalpha", "No alpha test", "Solid; alpha is pure reflection strength", "NONE", 3),
    ("soft", "Soft", "PD soft transparency: alpha blend, RGB-only, no depth write", "NONE", 4),
    ("soft_z", "Soft + depth", "Soft transparency that still writes depth (PD glass)", "NONE", 5),
    ("blend", "Blend (all channels)", "Alpha blend writing alpha too, no depth write", "NONE", 6),
    ("mask", "Mask stamp", "Writes ONLY the alpha channel", "NONE", 7),
    ("depth", "Depth only", "Writes only depth", "NONE", 8),
    ("depth_cut", "Depth only (alpha > 0)", "Depth where alpha > 0", "NONE", 9),
    ("reflect", "EXT reflection", "Environment-map pass: Cs*Ad + Cd, fog black", "NONE", 10),
    ("reflect_masked", "EXT reflection (masked)", "EXT pass only where the destination alpha test passes", "NONE", 11),
    ("glint", "Glint (lit sheen)", "Lit additive sheen weighted by the reflection mask", "NONE", 12),
    ("additive", "Additive", "Cs*As + Cd, fog black (glows, headlight pool)", "NONE", 13),
    ("shadow", "Soft shadow", "Untextured black, soft vertex alpha", "NONE", 14),
]
_LAMP = [("AUTO", "Auto (from name)", "", "NONE", 99), ("NONE", "None", "Always drawn", "NONE", 0),
         ("off", "Tail lamp OFF", "Drawn while the brake lamps are off", "NONE", 1), ("on", "Tail lamp ON", "Drawn while braking", "NONE", 2),
         ("off_night", "Tail lamp OFF (night)", "Night body only: running lights", "NONE", 3)]
_REFLECT = [("AUTO", "Auto (from name)", "", "NONE", 99), ("NONE", "None", "", "NONE", 0),
            ("plain", "Reflect", "EXT copy right after this draw, everywhere (weighted by texture alpha)", "NONE", 1),
            ("masked", "Reflect (masked)", "EXT copy only where the destination alpha test passes", "NONE", 2)]
_PRESETS = [("plain", "Plain", "Lit: ambient 1, diffuse 1"), ("paint", "Paint", "Specular 1, power 8"),
            ("chrome", "Chrome", "Specular 0.5, power 20"), ("emissive", "Emissive (lamps)", "Self-lit"),
            ("none", "None", "All zero (EXT / pre-lit)")]
_WRAPS = [("region", "Region clamp", "PD default"), ("repeat", "Repeat", ""), ("clamp", "Clamp", ""),
          ("region_pad", "Region (pad, no stretch)", "PNG is the visible part of a power-of-two buffer")]

# a paint colour's finish = the paint materials' values in that colour. The presets are PD's most used rows over the
# 177 cars that have colours (solid 928 rows, metallic 301, satin 187, pearl 128, gloss 50).
_FINISHES = [("EXACT", "PD values", "The imported car's own per-material values for this colour", "NONE", 0),
             ("solid", "Solid", "Diffuse 1, no specular (PD's most common paint)", "NONE", 1),
             ("metallic", "Metallic", "Diffuse 0.5, specular 1.5, power 16", "NONE", 2),
             ("pearl", "Pearl", "Diffuse 0.5, specular 2, power 16", "NONE", 3),
             ("satin", "Satin", "Diffuse 0.5, no specular, power 16", "NONE", 4),
             ("gloss", "Gloss", "Diffuse 1, specular 1, power 8", "NONE", 5),
             ("KEEP", "Material's own", "Every material keeps its own values", "NONE", 6)]
_FINISH_VALUES = {"solid": ([1, 1, 1, 1], [0, 0, 0, 0], 0), "metallic": ([0.5, 0.5, 0.5, 1], [1.5, 1.5, 1.5, 0], 16),
                  "pearl": ([0.5, 0.5, 0.5, 1], [2, 2, 2, 0], 16), "satin": ([0.5, 0.5, 0.5, 1], [0, 0, 0, 0], 16),
                  "gloss": ([1, 1, 1, 1], [1, 1, 1, 0], 8)}


def _finish_values(finish):
    dif, spec, power = _FINISH_VALUES[finish]
    return {"ambient": [1, 1, 1, 1], "diffuse": dif, "specular": spec, "emissive": [0, 0, 0, 0], "power": power,
            "flags": 0, "unk2": 127, "unk3": 1}


# animated parts (PD: 7 cars) - driver of the morph blend
_ANIM = [("AUTO", "Auto (collection / name)", "From its collection (Steering, Mudflaps - the flap's corner from where "
                                                  "it sits) or its name suffix", "NONE", 99),
         ("NONE", "None (static)", "Not animated", "NONE", 0),
         ("flutter1", "Flutter 1 - front-left mudflap", "Engine callback 1: a per-wheel spring - swings with the car's acceleration (throttle / brake), random kicks grow with that wheel's speed, hard stops at both ends. PD: mesh = hanging, one key = swept back (mudflaps, aerials)", "NONE", 1),
         ("flutter2", "Flutter 2 - front-right mudflap", "Engine callback 2", "NONE", 2),
         ("flutter3", "Flutter 3 - rear-left mudflap", "Engine callback 3", "NONE", 3),
         ("flutter4", "Flutter 4 - rear-right mudflap", "Engine callback 4", "NONE", 4),
         ("steering", "Steering", "Engine callback 5: follows the steering (PD cockpit part)", "NONE", 5),
         ("fixed", "Fixed blend", "A constant blend between the mesh and its morph target", "NONE", 6)]
_MORPH_KEY = "GT3 Morph"

_CAM_NAMES = ["DEFAULT", "CHASE", "UNK_2", "MIRROR_L", "MIRROR_R", "NOSE", "BONNET", "ROOF", "BACK", "TAIL",
              "SIDE_L", "SIDE_R", "FENDER_L", "FENDER_R", "WHEEL_FL", "WHEEL_FR", "WHEEL_RL", "WHEEL_RR", "CAM18", "CAM19"]
# PD-median defaults for a brand-new car (stored camera positions are negated model space)
_CAM_DEFAULTS = [
    ((0, -0.91, -0.13), 0, 0, 1.4352127, 13), ((0, -1.5, -6), 0, 0, 1.4352127, 10), ((0, -1.5, -6), 0, 0, 1.4352127, 0),
    ((0.83, -0.78, 0.40), -9, 191, 1.4352127, 15), ((-0.83, -0.78, 0.40), -9, -191, 1.4352127, 15),
    ((0, -0.15, 1.89), 0, 0, 1.6899495, 13), ((-0.89, -1.25, 3.68), 0, 202, 1.5011548, 3),
    ((0, -1.75, -0.71), 28, 0, 1.2124356, 7), ((0, -1.68, 0.17), 8, 180, 1.5722257, 7),
    ((0, -0.15, -1.87), 0, 180, 1.6899495, 13), ((0, -0.91, -0.13), 2.96, -90, 1.2124356, 13),
    ((0, -0.91, -0.13), 2.96, 90, 1.2124356, 13), ((0.76, -0.73, -3.70), 0, 12, 1.2124356, 3),
    ((1.09, -1.59, 4.04), 0, 147, 1.2124356, 3), ((1.29, -0.15, 0.18), -5, 19, 1.3446875, 3),
    ((-1.29, -0.15, 0.18), -5, -19, 1.3446875, 3), ((1.30, -0.15, 0), -8, 163, 1.3446875, 3),
    ((-1.30, -0.15, 0), -8, -163, 1.3446875, 3), ((1.96, -1.76, -3.80), 10, 25, 1.2124356, 3),
    ((1.01, -1.80, 3.35), 25, -211, 1.2124356, 3),
]

_A = Matrix(((-1, 0, 0), (0, 0, 1), (0, 1, 0)))   # game <-> Blender (its own inverse)
PNG_MAGIC = bytes((0x89, 0x50, 0x4E, 0x47))
TEXTURE_FORMATS = "PNG, TGA or BMP"


def _tex_format(head, name=""):
    """PNG / BMP / TGA from a file's first bytes (TGA has no magic: by its .tga name) - None = not one the car
    builder reads."""
    if head[:4] == PNG_MAGIC:
        return "png"
    if head[:2] == b"BM":
        return "bmp"
    if str(name).lower().endswith(".tga"):
        return "tga"
    return None


def _to_b(p):
    return Vector((-p[0], p[2], p[1]))


def _to_g(v):
    # "+ 0.0" folds the -0.0 the axis negation makes of PD's +0.0 (the centre line) back to +0.0
    return [-v[0] + 0.0, v[2] + 0.0, v[1] + 0.0]


class _Result:
    def __init__(self, returncode, stdout, stderr):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _run(args):
    """The GT3 car commands (gt3-car-decompile / -build, gt3-carcolor / -set), run in-process by the bundled
    pure-Python gt3car package - same verbs, arguments, output and exit codes as the old gt4track.exe."""
    from .gt3car.cli import main
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main([str(x) for x in args])
    except SystemExit as ex:
        rc = ex.code if isinstance(ex.code, int) else 1
        if ex.code and not isinstance(ex.code, int):
            err.write(str(ex.code))
    except Exception as ex:
        rc = 1
        err.write("%s: %s\n%s" % (type(ex).__name__, ex, traceback.format_exc()))
    return _Result(rc or 0, out.getvalue(), err.getvalue())


class GT3CarMakerPreferences(AddonPreferences):
    bl_idname = __package__
    database_dir: StringProperty(name="GT3 database folder", subtype="DIR_PATH", default="",
                                 description="The game's data/database folder (carcolor.db): colour names + chips. "
                                             "Filled in by itself the first time you import or export a car inside "
                                             "a game data folder")

    def draw(self, context):
        col = self.layout.column()
        ok = _db_dir_pref(context) is not None
        row = col.row()
        row.alert = not ok
        row.prop(self, "database_dir")
        if not ok:
            r2 = col.row()
            r2.alert = True
            r2.label(text="No database path!" + (" (no carcolor.db in that folder)" if self.database_dir else ""),
                     icon="ERROR")


def _db_dir_pref(context):
    try:
        p = context.preferences.addons[__package__].preferences.database_dir
    except (KeyError, AttributeError):
        p = ""
    p = bpy.path.abspath(p) if p else ""
    return p if p and os.path.exists(os.path.join(p, "carcolor.db")) else None


def _remember_db(context, db_dir):
    """A game database met on import / export fills an EMPTY preference (never replaces one the user set)."""
    try:
        prefs = context.preferences.addons[__package__].preferences
    except (KeyError, AttributeError):
        return
    if db_dir and not _db_dir_pref(context) and os.path.exists(os.path.join(db_dir, "carcolor.db")):
        prefs.database_dir = db_dir


def _db_colours(db_dir, code):
    """The game's colour list for a car code: [{index, id, name, name_jp, chip}], [] = not listed, None = no DB."""
    if not db_dir or not os.path.exists(os.path.join(db_dir, "carcolor.db")):
        return None
    with tempfile.TemporaryDirectory(prefix="gt3car_") as td:          # gone again when the lookup is done
        out = os.path.join(td, "colours.json")
        res = _run(["gt3-carcolor", db_dir, code, "--json", out])
        if res.returncode not in (0, 2) or not os.path.exists(out):
            return None
        with open(out, encoding="utf-8") as f:
            return json.load(f).get("colours") or []


# side files an imported car carries (tyre GTTR, car info GTCI, PD's spinning-wheel face sets): stored IN the .blend,
# written out per export
_BLOB_KEYS = ("tire_file", "menu_tire_file", "info_file", "night_info_file", "menu_info_file", "rim_blur_file")


def _embed_blob(idb, key, path):
    with open(path, "rb") as f:
        idb[f"gt3_blob_{key}"] = base64.b64encode(f.read()).decode("ascii")


def _blob_file(context, key, work):
    """The car's side file for `key` written into this export's temp folder - or, for a .blend from an older build, the
    path into that import's work folder. None when the car has none."""
    b = _car_prop(context, f"gt3_blob_{key}")
    if b:
        p = os.path.join(work, key + ".bin")
        with open(p, "wb") as f:
            f.write(base64.b64decode(b))
        return p
    return _car_prop(context, f"gt3_{key}") or None


# ------------------------------------------------------------------ names -> what an object is

# role tokens (what the object IS) and style tokens (how a car part draws)
_ROLE_TOKENS = {"rtshadow": "shadow", "groundshadow": "ground", "headlightbeam": "pool", "rim": "rim", "rearwheel": "rear_wheel",
                "brakefront": "rim_front", "brakerear": "rim_rear", "rimfront": "rim_front", "rimrear": "rim_rear",
                "body": "part"}
_ANIM_TOKENS = {"flutter1": "flutter1", "flutter2": "flutter2", "flutter3": "flutter3", "flutter4": "flutter4",
                "mudflapfl": "flutter1", "mudflapfr": "flutter2", "mudflaprl": "flutter3", "mudflaprr": "flutter4",
                "mudflap": "flutter_auto", "steering": "steering"}
_STYLE_TOKENS = {
    "mainbody": {"kind": "opaque", "reflect": "plain"},
    "chrome": {"kind": "opaque", "reflect": "plain", "strength": 255},
    "glass": {"kind": "soft_z", "reflect": "plain", "stamp": True},
    "soft": {"kind": "soft"},
    "cutout": {"kind": "cutout"},
    "reflect": {"kind": "reflect"},
    "glow": {"kind": "additive"},
    "part": {"kind": "opaque"},
}
_LAMP_TOKENS = {"tailoff": "off", "tailon": "on", "tailoffnight": "off_night"}
# role defaults
_ROLE_STYLE = {"shadow": {"kind": "shadow", "lit": False}, "ground": {"kind": "shadow", "lit": False},
               "pool": {"kind": "additive", "lit": False}, "rim": {"kind": "cutout", "reflect": "plain"},
               "rear_wheel": {"kind": "cutout", "reflect": "plain"},
               "rim_front": {"kind": "cutout", "reflect": "plain"}, "rim_rear": {"kind": "cutout", "reflect": "plain"}}


def _tokens(name):
    base = re.sub(r"\.\d{3}$", "", name)
    return [t.lower() for t in base.split("_") if t]


# COLLECTIONS work like name words: everything inside a collection (at any depth below the car) takes its meaning -
# drop a car's pieces into "Body", the wheel into "Wheel", the steering wheel into "Steering", the flaps into "Mudflaps".
# An object's OWN name words still win (Door_Chrome inside Body = chrome body part).
_COLL_ALIASES = {"carbody": "body", "parts": "body", "carparts": "body", "wheel": "rim", "wheels": "rim", "rims": "rim",
                 "frontwheel": "rim", "frontwheels": "rim", "rearwheels": "rearwheel",
                 "brakesfront": "brakefront", "frontbrake": "brakefront", "frontbrakes": "brakefront",
                 "brakesrear": "brakerear", "rearbrake": "brakerear", "rearbrakes": "brakerear",
                 "mudflaps": "mudflap", "flaps": "mudflap", "steeringwheel": "steering",
                 "rtshadows": "rtshadow", "realtimeshadow": "rtshadow", "groundshadows": "groundshadow",
                 "headlightbeams": "headlightbeam", "taillampsoff": "tailoff", "taillampson": "tailon",
                 "lampsoff": "tailoff", "lampson": "tailon", "doublesided": "2s"}


def _known_token(t):
    return (t in _ROLE_TOKENS or t in _ANIM_TOKENS or t in _STYLE_TOKENS or t in _LAMP_TOKENS
            or t in ("2s", "unlit", "noreflect"))


def _coll_words(name):
    """A collection name as name words: the whole name first ("Rear Wheel" = rearwheel), else its words."""
    base = re.sub(r"\.\d{3}$", "", name).lower()
    whole = _COLL_ALIASES.get(re.sub(r"[^a-z0-9]", "", base), re.sub(r"[^a-z0-9]", "", base))
    if _known_token(whole):
        return [whole]
    return [t for t in (_COLL_ALIASES.get(w, w) for w in re.split(r"[^a-z0-9]+", base) if w) if _known_token(t)]


def _coll_chain(o):
    """The collections between the object's car collection and the object, outermost first."""
    for root in (c for c in bpy.data.collections if "gt3_car" in c):
        if o.name not in root.all_objects:
            continue
        parent = {}
        for c in [root] + list(root.children_recursive):
            for ch in c.children:
                parent.setdefault(ch.name, c)
        for c in o.users_collection:
            if c == root:
                return []
            if c.name in parent:
                chain = []
                while c is not None and c != root:
                    chain.append(c)
                    c = parent.get(c.name)
                return chain[::-1]
    return [c for c in o.users_collection]        # no car collection: the whole scene is the car


def _flap_corner(o):
    """A mudflap's corner from where it sits - the car faces -Y, +X is its LEFT: flutter 1-4 = front-left, front-right,
    rear-left, rear-right (PD's order: ty0032's flutter 1 is its front-left flap)."""
    mw = _mw(o)
    pts = [mw @ Vector(c) for c in o.bound_box]
    left = sum(p.x for p in pts) > 0
    front = sum(p.y for p in pts) < 0
    return {(True, True): "flutter1", (False, True): "flutter2", (True, False): "flutter3", (False, False): "flutter4"}[(left, front)]


def _resolve(o):
    """What the object's NAME (then its GT3 Car panel overrides) makes of it."""
    chain = _coll_chain(o)
    ctoks = [t for c in chain for t in _coll_words(c.name)]
    coll = next((c.name for c in reversed(chain) if _coll_words(c.name)), None)
    # collection words first, the object's own name words after them (they win): "_RTShadow", "Glass", "Door_L_Chrome_2S"
    toks = ctoks + _tokens(o.name)
    role, style, lamp, anim = "part", {"kind": "opaque"}, None, None
    flags = set(toks)
    for t in toks:
        if t in _ANIM_TOKENS:
            anim = _ANIM_TOKENS[t]
        elif t in _ROLE_TOKENS:
            role = _ROLE_TOKENS[t]
        elif t in _STYLE_TOKENS:
            style = dict(_STYLE_TOKENS[t])
        elif t in _LAMP_TOKENS:
            lamp = _LAMP_TOKENS[t]
    if anim == "flutter_auto":
        anim = _flap_corner(o)
    r = {"role": role, "kind": "opaque", "reflect": None, "strength": 128, "stamp": False, "lit": True,
         "double": "2s" in flags, "lamp": lamp, "anim": anim, "coll": coll}
    r.update(_ROLE_STYLE.get(role, {}))
    if role == "part" or any(t in _STYLE_TOKENS for t in flags):
        r.update({k: v for k, v in style.items()})
    if "unlit" in flags:
        r["lit"] = False
    if "noreflect" in flags:
        r["reflect"] = None
    # panel overrides (the editable importer sets these to PD's exact values)
    if o.gt3_kind != "AUTO":
        r["kind"] = o.gt3_kind
    if o.gt3_reflect != "AUTO":
        r["reflect"] = None if o.gt3_reflect == "NONE" else o.gt3_reflect
    if o.gt3_lamp != "AUTO":
        r["lamp"] = None if o.gt3_lamp == "NONE" else o.gt3_lamp
    if not o.gt3_lit:
        r["lit"] = False
    if o.gt3_double_sided:
        r["double"] = True
    if o.gt3_stamp:
        r["stamp"] = True
    if o.gt3_reflect_strength != 128:
        r["strength"] = o.gt3_reflect_strength
    if o.gt3_anim != "AUTO":
        r["anim"] = None if o.gt3_anim == "NONE" else o.gt3_anim
    if r["anim"]:                        # PD's animated parts: lit, no reflection copy / stamp (the copy would not move)
        r["lit"], r["reflect"], r["stamp"] = True, None, False
    return r


def _car_type(root):
    return root.gt3_car_type if root is not None else "RACE"


_MORPH_KEY_RE = re.compile(r"^GT3 Morph(?:\s+(\d+))?$")


def _morph_keys(o):
    """An animated part's key poses after the mesh itself, in order: "GT3 Morph", "GT3 Morph 2", "GT3 Morph 3" ...
    (else the last shape key). GT3 plays them as a KEYFRAME CHAIN along the driver: mesh -> key 1 -> key 2 ..., the
    keys evenly spaced over 0..1 (engine: opcode 53 draws segment trunc(ratio * count))."""
    sk = o.data.shape_keys if o.type == "MESH" and o.data is not None else None
    if sk is None or len(sk.key_blocks) < 2:
        return []
    named = []
    for kb in list(sk.key_blocks)[1:]:
        m = _MORPH_KEY_RE.match(kb.name)
        if m:
            named.append((int(m.group(1) or 1), kb))
    if named:
        return [kb for _, kb in sorted(named, key=lambda x: x[0])]
    return [sk.key_blocks[-1]]


def _morph_key(o):
    """The animated part's first target pose (None = not animatable yet)."""
    ks = _morph_keys(o)
    return ks[0] if ks else None


def _morph_key_name(n):
    return _MORPH_KEY if n == 1 else f"{_MORPH_KEY} {n}"


# which car a collection belongs to: ("race", lod) or ("menu", 0)
_LOD_RE = re.compile(r"(?i)(?:^|[^a-z])lod\s*_?([0-3])(?:$|[^0-9])")
_MENU_RE = re.compile(r"(?i)(?:^|[^a-z])menu(?:$|[^a-z])")


def _collection_variants(scene, root=None):
    out = {}

    def walk(c, inherited):
        v = inherited
        if _MENU_RE.search(c.name):
            v = ("menu", 0)
        else:
            m = _LOD_RE.search(c.name)
            if m:
                v = ("race", int(m.group(1)))
        out[c.name] = v
        for ch in c.children:
            walk(ch, v)

    for c in (root.children if root is not None else scene.collection.children):
        walk(c, None)
    return out


def _mw(o):
    """o.matrix_world rebuilt from the object's own transform and its parents. Blender never evaluates objects in a
    viewport-DISABLED collection, so one made by a script and parked there keeps an IDENTITY matrix_world (the new-car
    template's 20 cameras sat in such a collection and all exported at the origin, pointing up)."""
    if o.constraints or o.parent_type != "OBJECT":
        return o.matrix_world
    m = o.matrix_basis
    if o.parent is not None:
        m = _mw(o.parent) @ o.matrix_parent_inverse @ m
    return m


def _layer_coll(context, coll):
    """The view layer's entry for a collection (its Outliner eye)."""
    def find(lc):
        if lc.collection == coll:
            return lc
        for ch in lc.children:
            r = find(ch)
            if r is not None:
                return r
        return None
    return find(context.view_layer.layer_collection)


def _hide_in_view(context, coll, hide=True):
    """Hide a collection with the Outliner EYE - toggleable, and its objects still export (they stay evaluated). The
    old way, collection.hide_viewport ("Disable in Viewports"), has no toggle in the Outliner by default."""
    lc = _layer_coll(context, coll)
    if lc is not None:
        lc.hide_viewport = hide
    else:
        coll.hide_viewport = hide


def _hidden(o):
    """The user hid this object ITSELF (eye icon or "Disable in Viewports") - it stays out of the car. Hiding a whole
    collection does not count: the importer hides LOD1-3 / Menu so they don't overlap LOD0, and those still belong in
    the car. A hidden object is also missing from the depsgraph, so its evaluated mesh could not be trusted anyway."""
    if o.hide_viewport or o.hide_render:
        return True
    try:
        return o.hide_get()
    except RuntimeError:          # not in this view layer (its collection is excluded) - not the user hiding the part
        return False


def _variant_of(o, variants):
    for c in o.users_collection:
        v = variants.get(c.name)
        if v is not None:
            return v
    return ("race", 0)


def _car_roots(context):
    in_scene = {c.name for c in context.scene.collection.children_recursive}
    return [c for c in bpy.data.collections if "gt3_car" in c and c.name in in_scene]


def _car_root(context):
    """The car being worked on: the 'GT3 Car: <code>' collection holding the ACTIVE object (or the only car in the
    scene); None = no car collections, the whole scene is the car."""
    roots = _car_roots(context)
    if not roots:
        return None
    o = context.active_object
    if o is not None:
        for r in roots:
            if o.name in r.all_objects:
                return r
    if len(roots) == 1:
        return roots[0]
    raise RuntimeError(f"{len(roots)} cars in this scene ({', '.join(r['gt3_car'] for r in roots)}): click an object of "
                       "the one to export first")


def _car_prop(context, key, default=None):
    r = _car_root(context)
    if r is not None and key in r:
        return r[key]
    return context.scene.get(key, default)


# ------------------------------------------------------------------ paint colours

class GT3CarPaint(PropertyGroup):
    # .name (built in) = the colour's name in the dealer
    chip: FloatVectorProperty(name="Chip", subtype="COLOR_GAMMA", size=3, min=0.0, max=1.0, default=(0.5, 0.5, 0.5),
                              description="The dealer's colour chip; also the target colour of Generate colour textures")
    finish: EnumProperty(name="Finish", items=_FINISHES, default="solid",
                         description="How the paint materials shine in this colour")
    db_id: IntProperty(default=-1, options={"HIDDEN"})
    db_name: StringProperty(default="", options={"HIDDEN"})
    name_jp: StringProperty(default="", options={"HIDDEN"})


class GT3PaintTexture(PropertyGroup):
    image: PointerProperty(type=bpy.types.Image, name="Texture", description="This material's texture in this colour (empty = unchanged)")
    values: StringProperty(default="", options={"HIDDEN"})      # PD's exact material values in this colour (JSON)


def _paint_owner(context):
    """Where the colour list lives: the car's collection, else the scene."""
    try:
        r = _car_root(context)
    except RuntimeError:
        r = None
    return r if r is not None else context.scene


def _owner_objects(owner):
    return owner.all_objects if isinstance(owner, bpy.types.Collection) else owner.objects


def _car_materials(owner):
    mats = []
    for o in _owner_objects(owner):
        if o.type == "MESH":
            for s in o.material_slots:
                if s.material is not None and s.material not in mats:
                    mats.append(s.material)
    return mats


def _tex_node(m):
    """The material's image texture node - even when its image slot is empty (a removed image must not make the
    material 'textureless': the preview and the export would then lose track of its base)."""
    if m is None or not m.use_nodes or m.node_tree is None:
        return None
    nodes = [n for n in m.node_tree.nodes if n.type == "TEX_IMAGE"]
    return next((n for n in nodes if n.image), nodes[0] if nodes else None)


def _base_image(m, owner):
    """The material's colour-0 texture (the base), whatever colour the viewport is showing. The base is PINNED in
    the material's first colour slot; at row 0 a node image that is neither the pinned base nor one of the colour
    textures means the author swapped the base texture, and that wins."""
    node = _tex_node(m)
    cur = node.image if node is not None else None
    pt = m.gt3_paint_tex
    if len(pt) and pt[0].image is not None:
        shown = int(owner.get("gt3_paint_shown", 0)) if owner is not None else 0
        if (shown == 0 and cur is not None and cur != pt[0].image and cur != m.gt3_paint_src
                and all(cur != e.image for e in list(pt)[1:])):
            return cur
        return pt[0].image
    return cur


def _ensure_paint_tex(m, n, owner=None):
    while len(m.gt3_paint_tex) < n:
        first = len(m.gt3_paint_tex) == 0
        e = m.gt3_paint_tex.add()
        if first:
            e.image = _base_image(m, owner)


def _show_colour(owner, k):
    """Swap every car material's viewport texture to colour k (0 = the base); the base stays pinned in slot 0."""
    k = max(0, min(k, max(0, len(owner.gt3_paints) - 1)))
    for m in _car_materials(owner):
        pt = m.gt3_paint_tex
        node = _tex_node(m)
        if len(pt) == 0 or node is None:
            continue
        base = _base_image(m, owner)                 # read BEFORE the shown row changes
        if base is not None and pt[0].image != base:
            pt[0].image = base
        target = pt[k].image if 0 < k < len(pt) and pt[k].image is not None else pt[0].image
        if target is not None:
            node.image = target
    owner["gt3_paint_shown"] = k


def _paint_show_update(self, context):
    """v2's 'Show colour' number (kept so v2 .blend files load): same as clicking that row."""
    if int(self.get("gt3_paint_shown", 0)) != self.gt3_paint_show:
        _show_colour(self, self.gt3_paint_show)


def _paint_active_update(self, context):
    if len(self.gt3_paints) and int(self.get("gt3_paint_shown", 0)) != self.gt3_paint_active:
        _show_colour(self, self.gt3_paint_active)


class GT3Brake(PropertyGroup):
    """One brake (front or rear): what the car info (GTCI BrakeParameters) points at in data/race/brake.bin."""
    disc: IntProperty(name="Disc", default=2, min=1,
                      description="brake.bin disc number (PD's cars use 1-6; 1 = a plain disc with flag 0)")
    caliper: IntProperty(name="Caliper", default=14, min=1,
                         description="brake.bin caliper number - 1 = NO caliper (the game skips it); PD uses 2-31")
    disc_image: PointerProperty(type=bpy.types.Image, name="Own disc",
                                description="Your disc art (64x64, 256 colours; other sizes are resampled). Exporting into "
                                            "the game's data folder adds it to data/race/brake.bin and sets Disc for you")
    caliper_image: PointerProperty(type=bpy.types.Image, name="Own caliper",
                                   description="Your caliper art (32x32, 16 colours; other sizes are resampled). Added to "
                                               "data/race/brake.bin on export; Caliper is set for you")
    size: FloatProperty(name="Size", default=0.14, description="BrakeTextureSize (PD: 0.10-0.20)")
    offset: FloatProperty(name="Offset", default=-0.06, description="BrakeOffsetFromCenter")
    angle: FloatProperty(name="Angle", default=-22.0, description="BrakeTextureOrientationDeg (the caliper's clock position)")


_BRAKE_KEYS = (("disc", "BrakeDiscTextureIndex"), ("caliper", "BrakeCaliperTextureIndex"), ("size", "BrakeTextureSize"),
               ("offset", "BrakeOffsetFromCenter"), ("angle", "BrakeTextureOrientationDeg"))


def _car_brakes_json(root):
    """The car's BrakeParameters as it came (import) or from its car-info template - None when it has neither."""
    for key in ("gt3_brakes", "gt3_info_json"):
        if root is not None and root.get(key):
            v = json.loads(root[key])
            v = v.get("BrakeParameters") if isinstance(v, dict) else v
            if v:
                return v
    return None


def _paint_props():
    bpy.types.Collection.gt3_car_type = EnumProperty(
        name="Car", items=[("RACE", "Race car", "The in-race car with its LODs: data/cars/day/<code> (one file, its wheel inside)"),
                           ("MENU", "Menu car", "The garage / showroom car: data/menu/cars/<code> (+ its showroom wheel)")],
        default="RACE")
    bpy.types.Scene.gt3_new_code = StringProperty(name="Code", default="custom", description="The car code (file name), e.g. ar0006")
    for T in (bpy.types.Collection, bpy.types.Scene):
        T.gt3_paints = CollectionProperty(type=GT3CarPaint)
        T.gt3_paint_active = IntProperty(default=0, update=_paint_active_update)
        T.gt3_paint_show = IntProperty(name="Show colour", default=0, min=0, update=_paint_show_update,
                                       description="Show the car in this paint colour in the viewport (0 = the base)")
    bpy.types.Collection.gt3_brake_ui = CollectionProperty(type=GT3Brake)
    bpy.types.Collection.gt3_tyre_image = PointerProperty(
        type=bpy.types.Image, name="Tyre picture",
        description="This car's tyre (all four; PD: one 128x64 picture, TOP half = tread, BOTTOM half = sidewall with "
                    "the lettering). Empty = the imported car's own tyre, or a plain one in PD's layout")
    M = bpy.types.Material
    M.gt3_paint_tex = CollectionProperty(type=GT3PaintTexture)
    M.gt3_is_paint = BoolProperty(name="Paint", default=False,
                                  description="Car paint: takes each colour's finish; Generate colour textures recolours it")
    M.gt3_paint_src = PointerProperty(type=bpy.types.Image, name="Paint source",
                                      description="The untouched texture every colour is recoloured from")


def _del_paint_props():
    for T, a in ((bpy.types.Collection, "gt3_car_type"), (bpy.types.Scene, "gt3_new_code"), (bpy.types.Collection, "gt3_brake_ui"),
                 (bpy.types.Collection, "gt3_tyre_image")):
        if hasattr(T, a):
            delattr(T, a)
    for T in (bpy.types.Collection, bpy.types.Scene):
        for a in ("gt3_paints", "gt3_paint_active", "gt3_paint_show"):
            if hasattr(T, a):
                delattr(T, a)
    for a in ("gt3_paint_tex", "gt3_is_paint", "gt3_paint_src"):
        if hasattr(bpy.types.Material, a):
            delattr(bpy.types.Material, a)


_LW = (0.299, 0.587, 0.114)


def _img_px(img):
    import numpy as np
    try:
        img.pixels[0]
    except Exception:
        pass
    a = np.empty(img.size[0] * img.size[1] * 4, dtype=np.float32)
    img.pixels.foreach_get(a)
    return a.reshape(-1, 4)


def _paint_mask(src, variants, tol):
    """Which pixels are PAINT. With colour textures already there (PD's own on an imported car) it is EXACTLY the
    pixels any of them changes - no guessing. Without: the texture's dominant colour (greys skipped when there is
    colour), matched with its brightness taken out, within tol."""
    import numpy as np
    rgb = src[:, :3]
    mask = np.zeros(len(src), bool)
    for v in variants:
        mask |= (np.abs(v[:, :3] - rgb) > 1.5 / 255).any(axis=1)
    if mask.any():
        return mask, "the car's own colour textures"
    vis = src[:, 3] > 0.01
    if not vis.any():
        return mask, "nothing visible"
    q = np.round(rgb[vis] * 31).astype(np.int64)
    keys = q[:, 0] * 1024 + q[:, 1] * 32 + q[:, 2]
    sat = rgb[vis].max(axis=1) - rgb[vis].min(axis=1)
    pool = keys[sat > 0.08] if (sat > 0.08).sum() > 0.05 * len(keys) else keys
    k = int(np.bincount(pool).argmax())
    ref = np.array([k // 1024, (k // 32) % 32, k % 32], np.float32) / 31
    lw = np.array(_LW, np.float32)
    ls = float(ref @ lw)
    if ls > 0.02:
        scale = (rgb @ lw) / ls
        diff = np.linalg.norm(rgb - ref[None, :] * scale[:, None], axis=1)
    else:
        diff = np.linalg.norm(rgb - ref[None, :], axis=1)
    return (diff <= tol) & (src[:, 3] > 0.01), "its dominant colour"


def _recolour(src, mask, ref, target):
    """Paint pixels take `target`, each keeping its brightness relative to the paint's own colour `ref` (shading,
    highlights); everything else - and every alpha - stays as it is."""
    import numpy as np
    out = src.copy()
    lw = np.array(_LW, np.float32)
    tgt = np.asarray(target, np.float32)
    lr, lt = float(ref @ lw), float(tgt @ lw)
    rgb = src[mask, :3]
    if lr > 0.02 and lt > 0.02:
        out[mask, :3] = np.clip(tgt[None, :] * ((rgb @ lw) / lr)[:, None], 0.0, 1.0)
    else:                                      # black paint (to or from): the shading rides as an offset
        out[mask, :3] = np.clip(rgb - ref[None, :] + tgt[None, :], 0.0, 1.0)
    return out


class GT3_UL_paints(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.label(text=f"{index}")
        row.prop(item, "chip", text="")
        row.prop(item, "name", text="", emboss=False)
        row.prop(item, "finish", text="")


class OBJECT_OT_gt3_paint_add(Operator):
    bl_idname = "object.gt3_paint_add"
    bl_label = "Add Paint Colour"
    bl_description = "Add a colour to the car's list (the first one = the base texture's own colour)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        owner = _paint_owner(context)
        p = owner.gt3_paints.add()
        p.name = "Base (your textures)" if len(owner.gt3_paints) == 1 else f"Colour {len(owner.gt3_paints) - 1}"
        p.finish = "solid"
        if len(owner.gt3_paints) == 1:              # row 0 = the texture as it is: its chip = the paint in it
            mats = [m for m in _car_materials(owner) if m.gt3_is_paint]
            p.chip = (0.5, 0.5, 0.5)
        owner.gt3_paint_active = len(owner.gt3_paints) - 1
        return {"FINISHED"}


class OBJECT_OT_gt3_paint_remove(Operator):
    bl_idname = "object.gt3_paint_remove"
    bl_label = "Remove Paint Colour"
    bl_description = "Remove the selected colour (and its textures from every material of the car)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        owner = _paint_owner(context)
        k = owner.gt3_paint_active
        if not 0 <= k < len(owner.gt3_paints):
            return {"CANCELLED"}
        if owner.gt3_paint_show:
            owner.gt3_paint_show = 0
        for m in _car_materials(owner):
            if k < len(m.gt3_paint_tex) and k > 0:
                m.gt3_paint_tex.remove(k)
        owner.gt3_paints.remove(k)
        owner.gt3_paint_active = max(0, k - 1)
        return {"FINISHED"}


class OBJECT_OT_gt3_paint_generate(Operator):
    bl_idname = "object.gt3_paint_generate"
    bl_label = "Make Colour Textures"
    bl_description = ("Recolour the paint of every PAINT material into the colours of the list. Where the paint is comes "
                      "from the car itself: an imported car's own colour textures show exactly which pixels are paint")
    bl_options = {"REGISTER", "UNDO"}
    which: EnumProperty(name="Make", items=[("THIS", "Only the selected colour", "Redo the textures of the colour selected in the list"),
                                            ("ALL", "Every colour", "Redo the textures of every colour in the list (replaces the car's own)")],
                        default="ALL")
    base_too: BoolProperty(name="Also colour 0 (the base texture)", default=False,
                           description="Recolour the base texture itself to colour 0's chip (the original is kept as the source)")
    tolerance: FloatProperty(name="Tolerance", default=0.12, min=0.0, max=1.0,
                             description="Only for paint that has no colour textures yet: how far (brightness taken out) a pixel may "
                                         "be from the texture's dominant colour to count as paint")

    def invoke(self, context, event):
        owner = _paint_owner(context)
        if len(owner.gt3_paints) < 1:
            self.report({"ERROR"}, "Add a colour first")
            return {"CANCELLED"}
        row = owner.gt3_paint_active
        self.which = "THIS" if row > 0 else "ALL"
        self.base_too = row == 0
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        import numpy as np
        owner = _paint_owner(context)
        n = len(owner.gt3_paints)
        row = max(0, min(owner.gt3_paint_active, n - 1))
        rows = [row] if self.which == "THIS" else list(range(1, n))
        if self.base_too and 0 not in rows:
            rows = [0] + rows
        rows = [k for k in rows if k > 0 or self.base_too]
        if not rows:
            self.report({"ERROR"}, "Nothing to make: pick a colour row (or tick 'Also colour 0')")
            return {"CANCELLED"}
        _show_colour(owner, 0)                     # work on the base; the selected row comes back at the end
        mats = [m for m in _car_materials(owner) if m.gt3_is_paint]
        if not mats:                               # nothing marked: the _MainBody objects' materials are the paint
            for o in _owner_objects(owner):
                if o.type == "MESH" and "mainbody" in _tokens(o.name):
                    for s in o.material_slots:
                        if s.material is not None and s.material not in mats:
                            s.material.gt3_is_paint = True
                            mats.append(s.material)
        if not mats:
            self.report({"ERROR"}, "No paint material: select the body and tick 'is car paint' (sidebar > Selected part)")
            return {"CANCELLED"}
        lw = np.array(_LW, np.float32)
        made, notes, hows = 0, [], set()
        for m in mats:
            src_img = m.gt3_paint_src or _base_image(m, owner)
            if src_img is None:
                notes.append(f"{m.name}: no texture")
                continue
            if m.gt3_paint_src is None:
                m.gt3_paint_src = src_img          # every later recolour starts from the untouched original
            src = _img_px(src_img)
            w, h = src_img.size
            if w == 0:
                notes.append(f"{m.name}: texture has no pixels")
                continue
            _ensure_paint_tex(m, n, owner)
            variants = []
            for k in range(1, n):
                im = m.gt3_paint_tex[k].image
                if im is not None and im != src_img and tuple(im.size) == (w, h):
                    variants.append((k, _img_px(im)))
            mask, how = _paint_mask(src, [v for _, v in variants], self.tolerance)
            if not mask.any():
                notes.append(f"{m.name}: no paint found")
                continue
            hows.add(how)
            ref = src[mask, :3].mean(axis=0)
            # chip -> paint brightness from the car's OWN colours (PD's chips are brighter swatches than the texels)
            fs = []
            for k, v in variants:
                lc = float(np.array(owner.gt3_paints[k].chip, np.float32) @ lw)
                if lc > 0.03:
                    fs.append(float(v[mask, :3].mean(axis=0) @ lw) / lc)
            f = float(np.median(fs)) if fs else 1.0
            for k in rows:
                target = np.clip(np.array(owner.gt3_paints[k].chip, np.float32) * f, 0.0, 1.0)
                out = _recolour(src, mask, ref, target)
                name = f"{bpy.path.clean_name(m.name)}_c{k}"
                img = bpy.data.images.get(name)
                if img is None:
                    img = bpy.data.images.new(name, w, h, alpha=True)
                elif tuple(img.size) != (w, h):
                    img.scale(w, h)                  # never remove: a viewport node or another slot may show it
                img.pixels.foreach_set(out.ravel())
                img.pack()                               # lives in the .blend - nothing is written to disk
                m.gt3_paint_tex[k].image = img
                made += 1
            if 0 in rows and _tex_node(m) is not None:
                _tex_node(m).image = m.gt3_paint_tex[0].image     # colour 0 IS the base now: show it
        _show_colour(owner, row)
        msg = f"{made} colour textures ({', '.join(sorted(hows)) or '-'} told where the paint is)"
        self.report({"INFO"} if not notes else {"WARNING"}, msg + (f"; check: {', '.join(notes[:6])}" if notes else ""))
        return {"FINISHED"}


class OBJECT_OT_gt3_paint_from_game(Operator):
    bl_idname = "object.gt3_paint_from_game"
    bl_label = "Colours From Game"
    bl_description = "Take the colour list (names + chips) the game has for a car code - e.g. the car you are replacing"
    bl_options = {"REGISTER", "UNDO"}
    code: StringProperty(name="Car code", default="")

    def invoke(self, context, event):
        owner = _paint_owner(context)
        self.code = owner.get("gt3_car", "") if isinstance(owner, bpy.types.Collection) else ""
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        db = _db_dir_pref(context)
        if not db:
            self.report({"ERROR"}, "Set the GT3 database folder (data/database) in the add-on preferences")
            return {"CANCELLED"}
        cols = _db_colours(db, self.code)
        if not cols:
            self.report({"ERROR"}, f"{self.code}: not in the game's colour list")
            return {"CANCELLED"}
        owner = _paint_owner(context)
        for k, c in enumerate(cols):
            p = owner.gt3_paints[k] if k < len(owner.gt3_paints) else owner.gt3_paints.add()
            _set_paint_from_db(p, c, keep_finish=k < len(owner.gt3_paints))
        self.report({"INFO"}, f"{len(cols)} colours from {self.code}" +
                    (f" (the list still has {len(owner.gt3_paints)} - remove the extra ones to match)" if len(owner.gt3_paints) > len(cols) else ""))
        return {"FINISHED"}


def _set_paint_from_db(p, c, keep_finish=False):
    p.name = c.get("name") or "-"
    p.db_name = p.name
    p.db_id = int(c["id"]) if c.get("id") is not None else -1
    p.name_jp = c.get("name_jp") or ""
    ch = c.get("chip") or [128, 128, 128]
    p.chip = [x / 255.0 for x in ch[:3]]
    if not keep_finish:
        p.finish = "metallic" if "metallic" in p.name.lower() else "solid"


class MATERIAL_OT_gt3_paint_slots(Operator):
    bl_idname = "material.gt3_paint_slots"
    bl_label = "Colour Textures"
    bl_description = "Give this material a texture slot per paint colour of the car"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        owner = _paint_owner(context)
        m = context.material
        _ensure_paint_tex(m, max(2, len(owner.gt3_paints)))
        return {"FINISHED"}


class OBJECT_OT_gt3_paint_slot(Operator):
    bl_idname = "object.gt3_paint_slot"
    bl_label = "Add Colour Texture"
    bl_description = "Give this paint material a texture slot for every colour"
    bl_options = {"REGISTER", "UNDO"}
    material: StringProperty()

    def execute(self, context):
        m = bpy.data.materials.get(self.material)
        if m is None:
            return {"CANCELLED"}
        m.gt3_is_paint = True
        owner = _paint_owner(context)
        _ensure_paint_tex(m, max(2, len(owner.gt3_paints)), owner)
        return {"FINISHED"}


# ------------------------------------------------------------------ panels

def _obj_props():
    T = bpy.types.Object
    T.gt3_kind = EnumProperty(name="Kind", items=_KINDS, default="AUTO")
    T.gt3_reflect = EnumProperty(name="Reflection", items=_REFLECT, default="AUTO")
    T.gt3_lamp = EnumProperty(name="Tail lamp", items=_LAMP, default="AUTO")
    T.gt3_lit = BoolProperty(name="Lit", default=True, description="Runtime-lit (normals); off = pre-lit vertex colour")
    T.gt3_double_sided = BoolProperty(name="Double sided", default=False)
    T.gt3_reflect_strength = IntProperty(name="Reflection strength", default=128, min=0, max=255,
                                         description="EXT vertex grey: 128 normal, 255 double, 64 half")
    T.gt3_reflect_tint = BoolProperty(name="Tint 0.8", default=True, description="PD tints 5462 of 5835 EXT draws by 0.8")
    T.gt3_stamp = BoolProperty(name="Glass stamp", default=False, description="Alpha-only stamp of the same mesh after it")
    T.gt3_order = IntProperty(name="Draw order", default=-1, description="Position in the draw list (-1 = automatic)")
    T.gt3_anim = EnumProperty(name="Animation", items=_ANIM, default="AUTO",
                              description="What moves this part: it plays the mesh > shape key 'GT3 Morph' > 'GT3 Morph 2' ... "
                                          "(Selected part > Add next key pose)")
    T.gt3_anim_ratio = FloatProperty(name="Blend", default=0.0, min=0.0, max=1.0, description="Fixed blend: 0 = the mesh, 1 = the target")
    T.gt3_anim_mult = IntProperty(name="Range", default=0, min=0, max=4,
                                  description="Opcode 53 operand: 0 = automatic (PD: 2 for steering parts, 1 otherwise)")


def _mat_props():
    T = bpy.types.Material
    T.gt3_preset = EnumProperty(name="Preset", items=_PRESETS, default="plain")
    T.gt3_wrap = EnumProperty(name="Wrap", items=_WRAPS, default="region",
                              description="Region (pad): the UVs map onto your picture as it is; the export fits them "
                                          "into the game's power-of-two buffer")
    T.gt3_psm = EnumProperty(name="Format", items=[("4", "PSMT4 (16 col)", ""), ("8", "PSMT8 (256 col)", "")], default="4")
    T.gt3_night_texture = StringProperty(name="Night texture file", subtype="FILE_PATH",
                                         description="Optional: lit-lamp art swapped in for the night / eve car "
                                                     "(a file - the Night image below wins when both are set)")
    T.gt3_night_image = PointerProperty(name="Night image", type=bpy.types.Image,
                                        description="Optional: this material's texture on the NIGHT / EVE car (PD: the "
                                                    "same art with the lamps lit). Same size as the day texture")


class OBJECT_PT_gt3_car(Panel):
    bl_label = "GT3 Car"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == "MESH"

    def draw(self, context):
        o = context.object
        r = _resolve(o)
        try:
            root = _car_root(context)
        except RuntimeError:
            root = None
        v = _variant_of(o, _collection_variants(context.scene, root))
        box = self.layout.box()
        menu = v[0] == "menu" or _car_type(root) == "MENU"
        box.label(text=f"{'Menu car' if menu else f'Race LOD{v[1]}'}  |  {r['role']}", icon="AUTO")
        refl = f" + reflection ({r['reflect']}, {r['strength']})" if r["reflect"] else ""
        box.label(text=f"{r['kind']}{refl}{'  stamp' if r['stamp'] else ''}{'  2-sided' if r['double'] else ''}"
                       f"{'  unlit' if not r['lit'] else ''}{('  tail lamp ' + r['lamp']) if r['lamp'] else ''}")
        if _hidden(o):
            box.label(text="Hidden - this object is NOT exported", icon="HIDE_ON")
        if "gt3_follows" in o:
            box.label(text=f"Follows edits of: {o['gt3_follows'][:60]}", icon="LINKED")
        col = self.layout.column()
        col.label(text="Overrides (Auto = from its collection / name):")
        col.prop(o, "gt3_kind")
        col.prop(o, "gt3_reflect")
        if r["reflect"] or r["kind"] in ("reflect", "reflect_masked"):
            row = col.row()
            row.prop(o, "gt3_reflect_strength")
            row.prop(o, "gt3_reflect_tint")
        col.prop(o, "gt3_lamp")
        row = col.row()
        row.prop(o, "gt3_lit")
        row.prop(o, "gt3_double_sided")
        row.prop(o, "gt3_stamp")
        col.prop(o, "gt3_order")
        _draw_anim(col, o, r)


def _draw_anim(col, o, r):
    box = col.box()
    box.prop(o, "gt3_anim")
    if o.gt3_anim == "AUTO" and r.get("anim"):
        box.label(text="-> " + next((x[1] for x in _ANIM if x[0] == r["anim"]), r["anim"]), icon="ANIM")
    if r.get("anim"):
        if _morph_key(o) is None:
            box.label(text="Needs a morph target (its extreme pose)", icon="ERROR")
            box.operator(OBJECT_OT_gt3_add_morph.bl_idname, icon="SHAPEKEY_DATA")
        else:
            ks = _morph_keys(o)
            if len(ks) == 1:
                box.label(text=f"Target: shape key '{ks[0].name}' - edit it to the extreme pose", icon="SHAPEKEY_DATA")
            else:
                box.label(text=f"{len(ks)} key poses, played in order: mesh > " + " > ".join(k.name for k in ks),
                          icon="SHAPEKEY_DATA")
            box.label(text="At rest / straight ahead the part shows the MIDDLE of the chain", icon="INFO")
            if str(r["anim"]).startswith("flutter"):
                box.label(text="PD mudflap: mesh = HANGING, one key = swept BACK and curled", icon="INFO")
                box.label(text="(it rests half-way; the mesh pose is the hard stop it swings back to)")
            for k in ks:
                box.prop(k, "value", text=f"Preview {k.name}")
            box.operator(OBJECT_OT_gt3_add_morph.bl_idname, text="Add next key pose", icon="ADD")
        if r["anim"] == "fixed":
            box.prop(o, "gt3_anim_ratio")


class OBJECT_OT_gt3_add_morph(Operator):
    bl_idname = "object.gt3_add_morph"
    bl_label = "Add Morph Target"
    bl_description = ("Give the active part its next key pose ('GT3 Morph', then 'GT3 Morph 2' ...) - edit it into the pose. "
                      "The engine plays mesh > key 1 > key 2 ... along the driver (steering, a flap's flutter, a fixed value)")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == "MESH"

    def execute(self, context):
        o = context.object
        if o.data.shape_keys is None:
            o.shape_key_add(name="Basis", from_mix=False)
        ks = _morph_keys(o) if o.data.shape_keys.key_blocks.get(_MORPH_KEY) else []
        name = _morph_key_name(len(ks) + 1)
        prev = ks[-1] if ks else None
        kb = o.shape_key_add(name=name, from_mix=False)
        if prev is not None:                         # start the new pose where the previous one ended
            for i, d in enumerate(prev.data):
                kb.data[i].co = d.co
        for k in list(o.data.shape_keys.key_blocks)[1:]:
            k.value = 0.0
        o.active_shape_key_index = list(o.data.shape_keys.key_blocks).index(kb)
        kb.value = 1.0
        if o.gt3_anim in ("AUTO", "NONE") and not _resolve(o).get("anim"):
            o.gt3_anim = "flutter1"
        self.report({"INFO"}, f"'{name}' added: enter Edit Mode and pose the part (the key is active)")
        return {"FINISHED"}


class MATERIAL_PT_gt3_car(Panel):
    bl_label = "GT3 Car"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "material"

    @classmethod
    def poll(cls, context):
        return context.material is not None

    def draw(self, context):
        m = context.material
        col = self.layout.column()
        col.prop(m, "gt3_preset")
        col.prop(m, "gt3_wrap")
        col.prop(m, "gt3_psm")
        col.prop(m, "gt3_night_image")
        if m.gt3_night_image is None:
            col.prop(m, "gt3_night_texture")
        col.label(text="Texture ALPHA = reflection strength (and > ~0.25 on opaque parts)", icon="INFO")
        box = self.layout.box()
        box.prop(m, "gt3_is_paint", text="Car paint (changes with the paint colour)")
        box.label(text="Paint colours: 3D view sidebar (N) > GT3 Car", icon="INFO")
        if "gt3_pglu" in m:
            col.label(text="Exact PD material values stored (gt3_pglu)", icon="CHECKMARK")


# ------------------------------------------------------------------ import (editable)

_KIND_TOKEN = {"opaque": "Part", "noalpha": "Part", "cutout": "Cutout", "cutout_blend": "Cutout", "soft": "Soft",
               "soft_z": "Soft", "blend": "Soft", "mask": "Mask", "depth": "Depth", "depth_cut": "Depth",
               "reflect": "Reflect", "reflect_masked": "Reflect", "glint": "Glint", "additive": "Glow", "shadow": "Shadow"}


def _pow2_up(x):
    p = 1
    while p < x:
        p *= 2
    return p


def _pad_scale(m):
    """Region (pad): the PNG is the TOP-LEFT w x h of a power-of-two W x H buffer, and the GAME's UVs measure that
    buffer (PD: 375 materials on 96 cars, e.g. 384x128 in 512x128, UVs 0..0.75). In Blender the UVs measure the PNG
    (so every view mode shows it right, and an author maps onto the picture they drew): game u = u * w / W, game
    t = t_png * h / H. Returns (W / w, H / h), or None when nothing needs scaling."""
    if m is None or getattr(m, "gt3_wrap", "") != "region_pad":
        return None
    tex = _tex_node(m)
    img = tex.image if tex is not None else None
    if img is None:
        return None
    w, h = tuple(img.size)
    if w == 0 or h == 0 or (_pow2_up(w), _pow2_up(h)) == (w, h):
        return None
    return _pow2_up(w) / w, _pow2_up(h) / h


def _f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


# how each see-through kind uses texture alpha in the GAME (viewport display only): cutout = hole where alpha is 0,
# cutout+blend = hole below 128, the soft kinds blend. Opaque parts use alpha as reflection strength - never shown.
_ALPHA_CUT = {"cutout": 0.5 / 255.0, "cutout_blend": 0.5}
_ALPHA_BLEND = {"soft", "soft_z", "blend", "additive"}


def _show_alpha(mats_kinds):
    """Viewport only: a material used only by see-through parts shows its texture alpha the way the game uses it."""
    for m, kinds in mats_kinds.items():
        if not kinds or not m.use_nodes:
            continue
        cut = kinds & set(_ALPHA_CUT)
        if not kinds <= set(_ALPHA_CUT) | _ALPHA_BLEND or (cut and kinds - cut):
            continue                                  # an opaque user, or cut + blend mixed: leave it solid
        tex, bsdf = _tex_node(m), next((n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if tex is None or bsdf is None or tex.image is None:
            continue
        nt = m.node_tree
        if cut:
            gt = nt.nodes.new("ShaderNodeMath")
            gt.name = gt.label = "GT3 alpha test"
            gt.operation = "GREATER_THAN"
            gt.inputs[1].default_value = min(_ALPHA_CUT[k] for k in cut)
            gt.location = (tex.location.x + 200, tex.location.y - 250)
            nt.links.new(tex.outputs["Alpha"], gt.inputs[0])
            nt.links.new(gt.outputs["Value"], bsdf.inputs["Alpha"])
        else:
            nt.links.new(tex.outputs["Alpha"], bsdf.inputs["Alpha"])
        if hasattr(m, "surface_render_method"):
            m.surface_render_method = "DITHERED"


def _make_material(spec_mat, work):
    name = spec_mat.get("name") or "gt3_mat"
    m = bpy.data.materials.new(name)          # always fresh: Blender suffixes a clash (.001), the import keeps the real name
    m.use_nodes = True
    nt = m.node_tree
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    tex = spec_mat.get("texture")
    if tex and bsdf is not None:
        path = tex if os.path.isabs(tex) else os.path.join(work, tex)
        if os.path.exists(path):
            node = next((n for n in nt.nodes if n.type == "TEX_IMAGE"), None) or nt.nodes.new("ShaderNodeTexImage")
            node.image = bpy.data.images.load(path, check_existing=True)
            node.interpolation = "Closest"
            nt.links.new(node.outputs["Color"], bsdf.inputs["Base Color"])
    m.gt3_preset = spec_mat.get("preset") or "plain"
    m.gt3_wrap = spec_mat.get("wrap") or "region"
    m.gt3_psm = str(spec_mat.get("psm", 4)) if spec_mat.get("psm", 4) in (4, 8) else "4"
    if spec_mat.get("night_texture"):
        nt_path = spec_mat["night_texture"]
        nt_path = nt_path if os.path.isabs(nt_path) else os.path.join(work, nt_path)
        if os.path.exists(nt_path):
            m.gt3_night_image = bpy.data.images.load(nt_path, check_existing=True)   # packed with the import
    exact = {k: spec_mat[k] for k in ("ambient", "diffuse", "specular", "emissive", "power", "flags") if k in spec_mat}
    if exact:
        m["gt3_pglu"] = json.dumps(exact)
    # paint colours: the texture per colour ([0] = the base) and PD's exact values per colour
    cts, cms = spec_mat.get("color_textures") or [], spec_mat.get("color_materials") or []
    for k in range(max(len(cts), len(cms))):
        e = m.gt3_paint_tex.add()
        if k == 0:
            node = _tex_node(m)
            e.image = node.image if node is not None else None
        elif k < len(cts) and cts[k]:
            cp = cts[k] if os.path.isabs(cts[k]) else os.path.join(work, cts[k])
            if os.path.exists(cp):
                e.image = bpy.data.images.load(cp, check_existing=True)
        if k < len(cms) and cms[k] is not None:
            e.values = json.dumps(cms[k])
    m.gt3_is_paint = any(cts[1:])
    return m


def _build_object(draw, mats, coll, name):
    mesh = draw["mesh"]
    verts = mesh["vertices"]
    morph = draw.get("morph")
    tgts = morph["target"] if morph and len(morph.get("target") or []) == len(verts) else None
    tnrm = morph.get("target_normals") if tgts and morph.get("target_normals") and len(morph["target_normals"]) == len(verts) else None
    # a keyframe chain (PD's steering parts): key poses 2..N
    xk = [k for k in (morph.get("extra_keys") or []) if k and len(k) == len(verts)] if tgts else []
    xn = list(morph.get("extra_key_normals") or []) if xk else []
    xn = [n if n and len(n) == len(verts) else None for n in xn][:len(xk)] + [None] * max(0, len(xk) - len(xn))
    bx, bxn = [[] for _ in xk], [[] for _ in xk]
    # weld by position so the mesh edits as one surface; UV / normal / colour live on the corners
    # (an animated part also by its target: two points that part ways while moving stay two points)
    pos_index, bverts, remap, btgt, btn = {}, [], [], [], []
    for i, v in enumerate(verts):
        k = (v[0], v[1], v[2]) + (tuple(tgts[i]) if tgts else ()) + tuple(c for key in xk for c in key[i])
        if k not in pos_index:
            pos_index[k] = len(bverts)
            bverts.append(_to_b(v))
            if tgts:
                btgt.append(_to_b(tgts[i]))
                btn.append(_to_b(tnrm[i]) if tnrm else None)
            for j, key in enumerate(xk):
                bx[j].append(_to_b(key[i]))
                bxn[j].append(_to_b(xn[j][i]) if xn[j] else None)
        remap.append(pos_index[k])
    tris = [t for t in mesh["triangles"] if len({remap[t[0]], remap[t[1]], remap[t[2]]}) == 3]
    # Blender keeps ONE face per vertex set: PD's back-to-back pairs (double-sided surfaces: the same three corners
    # wound both ways) would lose a triangle - give the repeat its own coincident vertices
    faces, seen = [], set()
    for t in tris:
        f = [remap[i] for i in t]
        key = frozenset(f)
        if key in seen:
            f = []
            for i in t:
                f.append(len(bverts))
                bverts.append(_to_b(verts[i]))
                if tgts:
                    btgt.append(_to_b(tgts[i]))
                    btn.append(_to_b(tnrm[i]) if tnrm else None)
                for j, xkey in enumerate(xk):
                    bx[j].append(_to_b(xkey[i]))
                    bxn[j].append(_to_b(xn[j][i]) if xn[j] else None)
        seen.add(key)
        faces.append(f)
    me = bpy.data.meshes.new(name)
    me.from_pydata(bverts, [], faces)
    me.update()
    uvs, nrm, cols = mesh.get("uvs"), mesh.get("normals"), mesh.get("colors")
    # create EVERY layer first, then look each up by name: adding an attribute reallocates the corner data and
    # leaves earlier Python references pointing at the wrong layer (UVs landed in the colour attribute)
    tm = mesh.get("tri_materials")
    tri_ids = [i for i, t in enumerate(mesh["triangles"]) if len({remap[t[0]], remap[t[1]], remap[t[2]]}) == 3]
    tri_pad = [(_pad_scale(mats[tm[ti]]) if tm and 0 <= tm[ti] < len(mats) else None) for ti in tri_ids]
    pad_any = uvs and any(tri_pad)
    if uvs:
        me.uv_layers.new(name="UVMap")
    if cols:
        me.color_attributes.new(name="GT3Color", type="FLOAT_COLOR", domain="CORNER")
    if pad_any:          # PD's own UVs, kept for an exact export - two plain FLOATs (a FLOAT2 corner attribute IS a UV map)
        me.attributes.new(name="gt3_pd_u", type="FLOAT", domain="CORNER")
        me.attributes.new(name="gt3_pd_v", type="FLOAT", domain="CORNER")
    uvl = me.uv_layers.get("UVMap") if uvs else None
    ca = me.color_attributes.get("GT3Color") if cols else None
    pdu, pdv = (me.attributes.get("gt3_pd_u"), me.attributes.get("gt3_pd_v")) if pad_any else (None, None)
    loop_normals = []
    for pi, t in enumerate(tris):
        poly = me.polygons[pi]
        pad = tri_pad[pi] if pad_any else None
        for c, li in zip(t, poly.loop_indices):
            if uvs:
                if pad is not None:          # Region (pad): shown over the PNG, PD's own values kept for the export
                    # from the float32 values Blender keeps, so the export's "untouched?" test recomputes the same
                    uvl.data[li].uv = (_f32(uvs[c][0]) * pad[0], 1.0 - _f32(uvs[c][1]) * pad[1])
                    pdu.data[li].value, pdv.data[li].value = uvs[c][0], uvs[c][1]
                else:
                    uvl.data[li].uv = (uvs[c][0], 1.0 - uvs[c][1])
            if cols:
                ca.data[li].color = [x / 128.0 for x in cols[c]]   # PS2 scale: 128 = 1.0 (neutral)
            if nrm:
                loop_normals.append(_to_b(nrm[c]).normalized() if Vector(nrm[c]).length > 1e-9 else Vector((0, 0, 1)))
    if tm:
        keep = tri_ids
        slots = {}
        for pi, ti in enumerate(keep):
            mi = tm[ti]
            if mi not in slots:
                slots[mi] = len(me.materials)
                me.materials.append(mats[mi])
            me.polygons[pi].material_index = slots[mi]
    if nrm and loop_normals:
        # custom normals only count on SMOOTH faces - flat ones hand back per-face normals and split every corner
        for poly in me.polygons:
            poly.use_smooth = True
        try:
            me.normals_split_custom_set(loop_normals)
        except Exception as ex:
            print("GT3 Car Maker: custom normals not set:", ex)
    o = bpy.data.objects.new(name, me)
    coll.objects.link(o)
    o["gt3_uid"] = _UID[0] + len(_MADE) + 1      # unique across the FILE (several imported cars)
    if tgts:
        # PD's animated part: the target pose as a shape key, the driver + operands as the part's settings
        o.shape_key_add(name="Basis", from_mix=False)
        kb = o.shape_key_add(name=_MORPH_KEY, from_mix=False)
        for i, co in enumerate(btgt):
            kb.data[i].co = co
        if tnrm:
            at = me.attributes.new("gt3_morph_normal", "FLOAT_VECTOR", "POINT")
            for i, n in enumerate(btn):
                at.data[i].vector = n if n is not None else (0, 0, 1)
        for j in range(len(xk)):
            kbj = o.shape_key_add(name=_morph_key_name(j + 2), from_mix=False)
            for i, co in enumerate(bx[j]):
                kbj.data[i].co = co
            if xn[j]:
                at = me.attributes.new(f"gt3_morph_normal_{j + 2}", "FLOAT_VECTOR", "POINT")
                for i, n in enumerate(bxn[j]):
                    at.data[i].vector = n if n is not None else (0, 0, 1)
        o.gt3_anim = morph.get("driver", "flutter1") if morph.get("driver") in {x[0] for x in _ANIM} else "fixed"
        o.gt3_anim_ratio = float(morph.get("ratio", 0.0))
        o.gt3_anim_mult = int(morph.get("mult", 0))
        if morph.get("matrix"):
            o["gt3_anim_matrix"] = json.dumps(morph["matrix"])
    _MADE.append(o)
    # PD's exact state as explicit overrides, so the round trip is exact whatever the name says
    kind = draw.get("kind", "opaque")
    o.gt3_kind = kind if kind in {it[0] for it in _KINDS} else "opaque"
    o.gt3_reflect = "NONE"
    o.gt3_lamp = "NONE"
    o.gt3_lit = bool(draw.get("lit", True))
    o.gt3_double_sided = bool(draw.get("double_sided", False))
    o.gt3_reflect_tint = bool(draw.get("reflect_tint", True))
    for k in ("unk1", "unk3", "external_tex", "alpha_ref"):
        if draw.get(k) is not None and not (k == "external_tex" and draw[k] < 0):
            o[f"gt3_{k}"] = draw[k]
    if draw.get("env_texture"):
        o["gt3_env_texture"] = True
    return o


_MADE = []                    # objects the running import created (uid order)
_UID = [0]                    # uid base of the running import: past every uid already in the file
_COPY_KINDS = {"reflect", "reflect_masked", "mask", "depth", "depth_cut", "glint"}


def _pos_key(v):
    return (round(v[0], 5), round(v[1], 5), round(v[2], 5))


class _PartIndex:
    """The parts of one LOD, looked up by TRIANGLE (sorted corner positions) and by position."""
    def __init__(self, objs):
        self.tri, self.pos = {}, {}
        for o in objs:
            if o.gt3_kind in _COPY_KINDS:
                continue
            vs = o.data.vertices
            for poly in o.data.polygons:
                if len(poly.vertices) != 3:
                    continue
                keyed = [(_pos_key(vs[i].co), i) for i in poly.vertices]
                self.tri.setdefault(tuple(sorted(k for k, _ in keyed)), (o["gt3_uid"], {k: i for k, i in keyed}, o.name))
            for v in vs:
                self.pos.setdefault(_pos_key(v.co), (o["gt3_uid"], v.index, o.name))

    def resolve(self, tris_positions):
        """[(k0, k1, k2), ...] corner position keys per triangle -> {position key: (uid, vertex, name)}."""
        out = {}
        for keys in tris_positions:
            hit = self.tri.get(tuple(sorted(keys)))
            if hit:
                uid, vmap, name = hit
                for k in keys:
                    out.setdefault(k, (uid, vmap[k], name))
        for keys in tris_positions:
            for k in keys:
                if k not in out and k in self.pos:
                    out[k] = self.pos[k]
        return out


def _link_followers(objs):
    """PD draws its reflection pass, glass stamp, depth pass and glint as COPIES sitting on a part's vertices (all 285
    cars: copies are a part, a piece of one, or pieces of several). Each copy remembers, per vertex, the part-vertex
    it sits on, so an edit to the part carries into the copy at export (untouched vertices stay bit-exact)."""
    parts = _PartIndex(objs)
    linked = 0
    for o in objs:
        if o.gt3_kind not in _COPY_KINDS:
            continue
        me = o.data
        vk = [_pos_key(v.co) for v in me.vertices]
        src = parts.resolve([tuple(vk[i] for i in poly.vertices) for poly in me.polygons if len(poly.vertices) == 3])
        uids, vs, orig, names = [], [], [], set()
        for v in me.vertices:
            s = src.get(vk[v.index])
            uids.append(s[0] if s else -1)
            vs.append(s[1] if s else -1)
            orig.extend(v.co)
            if s:
                names.add(s[2])
        if not names:
            continue
        # create every attribute first, then fetch by name (adding one reallocates the others)
        for nm, typ in (("gt3_src_uid", "INT"), ("gt3_src_v", "INT"), ("gt3_src_orig", "FLOAT_VECTOR")):
            me.attributes.new(nm, typ, "POINT")
        me.attributes["gt3_src_uid"].data.foreach_set("value", uids)
        me.attributes["gt3_src_v"].data.foreach_set("value", vs)
        me.attributes["gt3_src_orig"].data.foreach_set("vector", orig)
        o["gt3_follows"] = ", ".join(sorted(names))
        linked += 1
    return linked


def _follow_night(night_lods, lod_objs):
    """The night body rides along as JSON; give each of its vertices the day part-vertex it sits on (same LOD)."""
    linked = 0
    for k, lod in enumerate(night_lods or []):
        parts = _PartIndex(lod_objs.get(k, []))

        def walk(draws):
            nonlocal linked
            for d in draws or []:
                if not d:
                    continue
                if d.get("kind") == "lamp":
                    for key in ("lamp_off", "lamp_on", "lamp_off_night"):
                        walk(d.get(key))
                    continue
                m = d.get("mesh")
                if not m:
                    continue
                vk = [_pos_key(_to_b(q)) for q in m["vertices"]]
                src = parts.resolve([tuple(vk[i] for i in tri) for tri in m["triangles"]])
                hits = [src.get(k) for k in vk]
                if any(hits):
                    d["gt3_follow"] = {"uid": [h[0] if h else -1 for h in hits], "v": [h[1] if h else -1 for h in hits]}
                    linked += 1
        walk(lod)
    return linked


def _import_draws(draws, mats, coll, prefix, role_token=None):
    n = 0
    for i, d in enumerate(draws or []):
        if d is None:
            continue
        if d.get("kind") == "lamp":
            for branch, key, tok in (("off", "lamp_off", "TailOff"), ("on", "lamp_on", "TailOn"),
                                     ("off_night", "lamp_off_night", "TailOffNight")):
                for j, bd in enumerate(d.get(key) or []):
                    o = _build_object(bd, mats, coll, f"{prefix}{i:02d}_{j:02d}_{tok}")
                    o.gt3_order, o.gt3_lamp, o["gt3_lamp_order"] = i, branch, j
                    n += 1
            continue
        if d.get("mesh") is None:
            continue
        tok = role_token or _KIND_TOKEN.get(d.get("kind"), "Part")
        o = _build_object(d, mats, coll, f"{prefix}{i:02d}_{tok}")
        o.gt3_order = i
        n += 1
    return n


def _light_empty(coll, name, l):
    e = bpy.data.objects.new(name, None)
    e.empty_display_type = "SPHERE"
    e.empty_display_size = 0.06
    p = l["Position"]
    e.location = _to_b((p["X"], p["Y"], p["Z"]))
    u, it = l["UnkData"], l["Intensities"]
    g = Matrix(((u[0], u[1], u[2]), (u[4], u[5], u[6]), (it["X"], it["Y"], it["Z"])))
    e.rotation_mode = "QUATERNION"
    e.rotation_quaternion = (_A @ g @ _A).to_quaternion()
    v = l["UnkVec"]
    axis = Vector((it["X"], it["Y"], it["Z"]))
    e["gt3_flare_offset"] = (Vector((v["Y"], v["Z"], v["W"])) - Vector((p["X"], p["Y"], p["Z"]))).dot(axis)
    e["gt3_size"] = l["UnkMin"]
    # PD's exact numbers: an empty the author doesn't touch exports these instead of re-derived (1-ulp) floats
    e["gt3_rows"] = [u[0], u[1], u[2], u[4], u[5], u[6], it["X"], it["Y"], it["Z"]]
    e["gt3_pos"] = [p["X"], p["Y"], p["Z"]]
    e["gt3_flare"] = [v["Y"], v["Z"], v["W"]]
    c = l["FlareColor"]
    e["gt3_color"] = [c["R"], c["G"], c["B"]]
    e["gt3_color_bits"] = struct.unpack("<i", struct.pack("<f", c["A"]))[0] if isinstance(c["A"], (int, float)) else 0
    coll.objects.link(e)
    return e


def _camera_dir(pitch, yaw):
    t, f = math.radians(yaw), math.radians(pitch)
    return Vector((math.sin(t) * math.cos(f), -math.sin(f), -math.cos(t) * math.cos(f)))


def _cam_empty(coll, slot, pos_model, pitch, yaw, roll, fov, ctype):
    e = bpy.data.objects.new(f"Cam_{_CAM_NAMES[slot]}", None)
    e.empty_display_type = "SINGLE_ARROW"
    e.empty_display_size = 0.35
    e.location = _to_b(pos_model)
    d = _to_b(_camera_dir(pitch, yaw))
    e.rotation_mode = "QUATERNION"
    e.rotation_quaternion = d.normalized().to_track_quat("Z", "Y")
    e["gt3_slot"], e["gt3_pitch"], e["gt3_yaw"], e["gt3_roll"], e["gt3_fov"], e["gt3_type"] = slot, pitch, yaw, roll, fov, ctype
    coll.objects.link(e)
    return e


def _new_coll(parent, name):
    c = bpy.data.collections.new(name)
    parent.children.link(c)
    return c


def _info_empties(root, info):
    ic = _new_coll(root, "Car Info")
    sub = {}

    def grp(label):
        if label not in sub:
            sub[label] = _new_coll(ic, label)
        return sub[label]

    for key, prefix in (("FrontLights", "Headlight"), ("NightBrakeLights", "BrakeLight"), ("NightBrakeLightFlares", "BrakeFlare")):
        for i, l in enumerate(info.get(key) or []):
            _light_empty(grp("Light Points"), f"{prefix}_{i}", l)
    for i, x in enumerate(info.get("Exhausts") or []):
        p = x["Position"]
        e = bpy.data.objects.new(f"Exhaust_{i}", None)
        e.empty_display_type, e.empty_display_size = "SINGLE_ARROW", 0.25
        e.location = _to_b((p["X"], p["Y"], p["Z"]))
        e.rotation_mode = "QUATERNION"
        e.rotation_quaternion = _to_b((0, 0, 1)).to_track_quat("Z", "Y")
        grp("Exhausts").objects.link(e)
    for i, x in enumerate(info.get("CollisionParticles") or []):
        p = x["Position"]
        e = bpy.data.objects.new(f"Spark_{i}", None)
        e.empty_display_type, e.empty_display_size = "PLAIN_AXES", 0.05
        e.location = _to_b((p["X"], p["Y"], p["Z"]))
        grp("Sparks").objects.link(e)
    cams = info.get("OnboardCameras") or {}
    for slot, (nm, c) in enumerate(cams.items()):
        if c is None or slot >= len(_CAM_NAMES):
            continue
        p = c["Position"]
        e = _cam_empty(grp("Cameras"), slot, (-p["X"], -p["Y"], -p["Z"]), c["Pitch"], c["Yaw"], c["Roll"], c["FoV"], c["Unk"])
        e["gt3_stored_pos"] = [p["X"], p["Y"], p["Z"]]     # the file's own (negated) numbers, signed zeros included
    root["gt3_brakes"] = json.dumps(info.get("BrakeParameters"))
    root["gt3_default_tire_index"] = info.get("DefaultTireIndex", 1)
    root["gt3_tires"] = json.dumps(info.get("Tires"))


def _names_for_json(obj, mats_by_index):
    """Draw JSON carried verbatim (PD's night body, exotic model slots): material indices -> NAMES, so the exporter
    can re-index them against whatever material list it builds."""
    if isinstance(obj, list):
        return [_names_for_json(x, mats_by_index) for x in obj]
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "tri_materials" and isinstance(v, list):
                out[k] = [mats_by_index[i] if 0 <= i < len(mats_by_index) else None for i in v]
            elif k == "mat" and isinstance(v, int):
                out[k] = mats_by_index[v] if 0 <= v < len(mats_by_index) else None
            else:
                out[k] = _names_for_json(v, mats_by_index)
        return out
    return obj


def _indices_for_json(obj, mat_index):
    if isinstance(obj, list):
        return [_indices_for_json(x, mat_index) for x in obj]
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "tri_materials" and isinstance(v, list):
                out[k] = [mat_index(bpy.data.materials.get(n)) if n else -1 for n in v]
            elif k == "mat" and (v is None or isinstance(v, str)):
                out[k] = mat_index(bpy.data.materials.get(v)) if v else -1
            else:
                out[k] = _indices_for_json(v, mat_index)
        return out
    return obj


class IMPORT_OT_gt3_car_editable(Operator, ImportHelper):
    bl_idname = "import_scene.gt3_car_editable"
    bl_label = "Import GT3 Car (editable)"
    bl_description = ("Take a PD GT3 car (cars/day/<code>; the menu car comes from the same disc folder) apart into two "
                      "editable cars - the race car (LOD0-3) and the menu car - each exported back as one file")
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ""
    filter_glob: StringProperty(default="*", options={"HIDDEN"})
    work_dir: StringProperty(name="Also keep the files in", subtype="DIR_PATH", default="",
                             description="Optional: a folder that KEEPS the decompiled textures + car.json. Empty = "
                                         "nothing is left on disk - the car lives in the .blend (textures packed)")
    race: BoolProperty(name="Race car", default=True, description="The in-race car (LOD0-3) - its own car in the scene")
    menu: BoolProperty(name="Menu car", default=True, description="The showroom car - its own car in the scene")
    lods: BoolProperty(name="All LODs", default=True)

    def execute(self, context):
        code = os.path.basename(self.filepath)
        if not self.work_dir:                            # the default: nothing stays on disk
            with tempfile.TemporaryDirectory(prefix="gt3car_import_") as work:
                return self._import(context, work, None)
        # a FRESH folder per import: re-decompiling into a folder whose textures an open scene already shows made
        # Blender keep the OLD pixels under the same file names (another car's numbering = another texture)
        base_work = os.path.normpath(os.path.join(bpy.path.abspath(self.work_dir), code))
        work, n2 = base_work, 2
        while os.path.exists(os.path.join(work, "car.json")):
            work, n2 = f"{base_work}_{n2}", n2 + 1
        os.makedirs(work, exist_ok=True)
        return self._import(context, work, work)

    def _import(self, context, work, kept):
        car = self.filepath
        code = os.path.basename(car)
        d = os.path.dirname(car)
        root_dir = os.path.dirname(os.path.dirname(d)) if os.path.basename(os.path.dirname(d)) == "cars" else None
        args = ["gt3-car-decompile", root_dir, code, work] if root_dir else ["gt3-car-decompile", car, work]
        res = _run(args)
        sp = os.path.join(work, "car.json")
        if res.returncode != 0 or not os.path.exists(sp):
            self.report({"ERROR"}, f"decompile failed: {(res.stderr or res.stdout).strip()[-400:]}")
            return {"CANCELLED"}
        with open(sp, encoding="utf-8") as f:
            # PD stores -0.0 in places (light rows); JSON writes it "-0", which plain json.load turns into int 0
            spec = json.load(f, parse_int=lambda s: -0.0 if s == "-0" else int(s))

        mats = [_make_material(m, work) for m in spec.get("materials", [])]
        names = [m.name for m in mats]
        _MADE.clear()
        _UID[0] = max([int(o.get("gt3_uid", 0)) for o in bpy.data.objects] + [0])
        # the colour list: PD's exact per-texture-set layout + the game's names / chips for the code
        ncol = max(1, int(spec.get("colors") or 1))
        db = os.path.join(root_dir, "database") if root_dir and os.path.exists(os.path.join(root_dir, "database", "carcolor.db")) else _db_dir_pref(context)
        _remember_db(context, db)
        game = _db_colours(db, code) or []

        def new_root(kind):
            r = bpy.data.collections.new(f"GT3 {'Menu ' if kind == 'MENU' else ''}Car: {code}")
            context.scene.collection.children.link(r)
            r["gt3_car"] = code
            r.gt3_car_type = kind
            r["gt3_auto_shadows"] = bool(spec.get("auto_shadows", True))
            # PD's odd LODSelect branch counts (10 race models use 3) and model counts (special cars) - exported as they came;
            # a Blender menu wheel is always one level, so the 5 special cars' menu-wheel LODSelects are not carried
            lv = {k: v for k, v in (spec.get("lod_levels") or {}).items() if not k.startswith("menu_")}
            if lv:
                r["gt3_lod_levels"] = json.dumps(lv)
            if spec.get("model_counts"):
                r["gt3_model_counts"] = json.dumps(spec["model_counts"])
            r["gt3_color_layout"] = json.dumps({"count": ncol, "sets": spec.get("color_sets"), "tables": spec.get("color_tables")})
            for k in range(ncol):
                pp = r.gt3_paints.add()
                if k < len(game):
                    _set_paint_from_db(pp, game[k])
                else:
                    pp.name = f"Colour {k}"
                pp.finish = "EXACT"
            if spec.get("info") is not None:
                r["gt3_info_json"] = json.dumps(spec["info"])
            if spec.get("extra_models"):              # PD's exotic model slots ride along verbatim (per variant)
                r["gt3_extra_models"] = json.dumps(_names_for_json(spec["extra_models"], names))
            return r

        n, followers, night_links, made = 0, 0, 0, []
        if self.race:
            root = new_root("RACE")
            made.append(root)
            for k in ("tire_file", "info_file", "night_info_file", "rim_blur_file"):
                if spec.get(k):
                    _embed_blob(root, k, os.path.join(work, spec[k]))
            nl = 4 if self.lods else 1
            # PD's own empty LODs stay empty on export; any OTHER LOD found empty (its objects deleted) reuses the one below
            root["gt3_pd_empty_lods"] = json.dumps({role: [k for k in range(4) if k >= len(spec.get(key) or []) or not spec[key][k]]
                                                    for key, role in (("body", "part"), ("rim", "rim"), ("rear_rim", "rear_wheel"),
                                                                      ("shadow_draws", "shadow"), ("ground_draws", "ground"))})
            lodc = [_new_coll(root, f"LOD{k}") for k in range(nl)]
            for k in range(nl):
                n += _import_draws(spec["body"][k] if k < len(spec.get("body") or []) else [], mats, lodc[k], f"L{k}_")
                for key, tok in (("rim", "Rim"), ("rear_rim", "RearWheel"), ("shadow_draws", "RTShadow"), ("ground_draws", "GroundShadow")):
                    lods = spec.get(key) or []
                    if k < len(lods):
                        n += _import_draws(lods[k], mats, lodc[k], f"L{k}_{tok.lower()}", role_token=tok)
            if spec.get("headlight_pool"):
                n += _import_draws([spec["headlight_pool"]], mats, lodc[0], "L0_pool", role_token="HeadlightBeam")
            lod_objs = {k: [o for o in _MADE if o.users_collection and o.users_collection[0] == lodc[k]] for k in range(nl)}
            followers += sum(_link_followers(objs) for objs in lod_objs.values())
            if spec.get("night_body"):
                night_links = _follow_night(spec["night_body"], lod_objs)
                root["gt3_night_json"] = json.dumps(_names_for_json(spec["night_body"], names))
            if spec.get("info") is not None:
                _info_empties(root, spec["info"])
            for c in lodc:
                _into_role_collections(c)
            for c in root.children_recursive:        # only LOD0 shows: the others overlap it (the eye toggles them)
                if c.name.startswith(("LOD1", "LOD2", "LOD3")):
                    _hide_in_view(context, c)
        if self.menu and spec.get("menu_body") is not None:
            mroot = new_root("MENU")
            made.append(mroot)
            if spec.get("menu_tire_file") or spec.get("tire_file"):
                _embed_blob(mroot, "menu_tire_file", os.path.join(work, spec.get("menu_tire_file") or spec["tire_file"]))
            if spec.get("menu_info_file"):
                _embed_blob(mroot, "menu_info_file", os.path.join(work, spec["menu_info_file"]))
            elif spec.get("info_file"):
                _embed_blob(mroot, "info_file", os.path.join(work, spec["info_file"]))
            if spec.get("tire_file"):
                _embed_blob(mroot, "tire_file", os.path.join(work, spec["tire_file"]))
            first = len(_MADE)
            n += _import_draws(spec["menu_body"], mats, mroot, "M_")
            for key, tok in (("menu_rim_front", "BrakeFront"), ("menu_rim_rear", "BrakeRear"), ("menu_ground_draws", "GroundShadow")):
                if spec.get(key):
                    n += _import_draws(spec[key], mats, mroot, f"M_{tok.lower()}", role_token=tok)
            if spec.get("headlight_pool"):
                n += _import_draws([spec["headlight_pool"]], mats, mroot, "M_pool", role_token="HeadlightBeam")
            if spec.get("menu_rim"):
                n += _import_draws(spec["menu_rim"][0], mats, mroot, "M_wheel", role_token="Rim")
                if spec.get("menu_rear_rim"):
                    n += _import_draws(spec["menu_rear_rim"][0], mats, mroot, "M_rearwheel", role_token="RearWheel")
            elif spec.get("rim"):
                n += _import_draws(spec["rim"][0], mats, mroot, "M_wheel", role_token="Rim")
                if spec.get("rear_rim"):
                    n += _import_draws(spec["rear_rim"][0], mats, mroot, "M_rearwheel", role_token="RearWheel")
            followers += _link_followers(_MADE[first:])
            _into_role_collections(mroot)
            if self.race:                            # it overlaps the race car: hidden (a hidden COLLECTION still exports)
                _hide_in_view(context, mroot)
        mk = {}
        for o in _MADE:
            if _resolve(o)["role"] in ("shadow", "ground", "pool"):
                o.display_type = "WIRE"            # the game never draws these solid - keep them out of the way
            k = _resolve(o)["kind"]
            for sl in o.material_slots:
                if sl.material is not None:
                    mk.setdefault(sl.material, set()).add(k)
        _show_alpha(mk)
        # materials only the night body / PD's extra slots use (JSON, by name) have no object users: Blender would drop
        # them on save and the night car would lose its textures after a reopen - keep them
        for m in mats:
            if m.users == 0:
                m.use_fake_user = True
        # every texture this import loaded goes INTO the .blend (the temp folder is deleted right after)
        wn = os.path.normcase(os.path.abspath(work))
        packed = 0
        for img in bpy.data.images:
            fp = bpy.path.abspath(img.filepath) if img.filepath else ""
            if fp and not img.packed_file and os.path.normcase(os.path.abspath(fp)).startswith(wn):
                img.pack()
                packed += 1
        context.view_layer.update()
        self.report({"INFO"}, f"GT3 car {code}: {' + '.join('race car' if _car_type(r) == 'RACE' else 'menu car' for r in made)}, "
                              f"{n} objects, {len(mats)} materials, {ncol} paint colour{'s' if ncol > 1 else ''}"
                              f"{'' if game else ' (no names: set the GT3 database folder in the add-on preferences)'}, "
                              f"{followers} copies follow their parts, {night_links} night draws follow the day car, "
                              f"{packed} textures packed into the .blend" + (f" (files also kept in {kept})" if kept else ""))
        return {"FINISHED"}


# ------------------------------------------------------------------ gathering the car

def _corner_normals(me):
    if hasattr(me, "corner_normals"):
        return [cn.vector.copy() for cn in me.corner_normals]
    me.calc_normals_split()
    return [l.normal.copy() for l in me.loops]


class _Sources:
    """uid -> the part's CURRENT world positions + per-vertex normals (averaged corner normals), built on demand."""
    def __init__(self, context, depsgraph):
        root = _car_root(context)                  # only THIS car's parts (older files may repeat uids across cars)
        objs = root.all_objects if root is not None else context.scene.objects
        self.by_uid = {o["gt3_uid"]: o for o in objs if o.type == "MESH" and "gt3_uid" in o}
        self.depsgraph, self.cache = depsgraph, {}

    def get(self, uid):
        if uid in self.cache:
            return self.cache[uid]
        o = self.by_uid.get(uid)
        if o is None:
            self.cache[uid] = None
            return None
        ev = o.evaluated_get(self.depsgraph)
        me = ev.to_mesh()
        mw = _mw(o)
        nmat = mw.to_3x3().inverted_safe().transposed()
        pos = [mw @ v.co for v in me.vertices]
        acc = [Vector((0, 0, 0)) for _ in me.vertices]
        for l, cn in zip(me.loops, _corner_normals(me)):
            acc[l.vertex_index] += cn
        nrm = [(nmat @ a).normalized() if a.length > 1e-9 else Vector((0, 0, 1)) for a in acc]
        ev.to_mesh_clear()
        self.cache[uid] = (pos, nrm)
        return self.cache[uid]


_FOLLOW_EPS = 1e-4       # 0.1 mm: below this a part-vertex counts as unmoved (keeps untouched cars bit-exact)


def _follow_overrides(o, sources):
    me = o.data
    if sources is None or "gt3_src_uid" not in me.attributes:
        return {}
    n = len(me.vertices)
    uids, vs, orig = [0] * n, [0] * n, [0.0] * (3 * n)
    me.attributes["gt3_src_uid"].data.foreach_get("value", uids)
    me.attributes["gt3_src_v"].data.foreach_get("value", vs)
    me.attributes["gt3_src_orig"].data.foreach_get("vector", orig)
    ov = {}
    for i in range(n):
        if uids[i] < 0:
            continue
        s = sources.get(uids[i])
        if s is None or vs[i] >= len(s[0]):
            continue
        cur = s[0][vs[i]]
        if (cur - Vector(orig[3 * i:3 * i + 3])).length > _FOLLOW_EPS:
            ov[i] = (cur, s[1][vs[i]])
    return ov


def _follow_json(draws, sources):
    """Night-body JSON: vertices whose day part-vertex moved take its new position + normal."""
    moved = 0
    for d in draws or []:
        if not d:
            continue
        if d.get("kind") == "lamp":
            for key in ("lamp_off", "lamp_on", "lamp_off_night"):
                moved += _follow_json(d.get(key), sources)
            continue
        f = d.pop("gt3_follow", None)
        m = d.get("mesh")
        if not f or not m:
            continue
        for i, (uid, sv) in enumerate(zip(f["uid"], f["v"])):
            if uid < 0:
                continue
            s = sources.get(uid)
            if s is None or sv >= len(s[0]):
                continue
            cur = s[0][sv]
            if (cur - _to_b(m["vertices"][i])).length > _FOLLOW_EPS:
                m["vertices"][i] = _to_g(cur)
                if m.get("normals"):
                    m["normals"][i] = _to_g(s[1][sv])
                moved += 1
    return moved


def _export_mesh(o, depsgraph, mat_index, with_normals=True, sources=None, morph_keys=None):
    ov = _follow_overrides(o, sources)
    ev = o.evaluated_get(depsgraph)
    me = ev.to_mesh()
    me.calc_loop_triangles()
    keys = list(morph_keys or [])
    if keys and len(me.vertices) != len(o.data.vertices):
        ev.to_mesh_clear()
        raise RuntimeError(f"'{o.name}': an animated part can't use modifiers that add or remove vertices - apply them first")
    # per key: its imported normals (PD's exact ones) when the attribute is there, else the builder recomputes them
    kna = [o.data.attributes.get("gt3_morph_normal" if j == 0 else f"gt3_morph_normal_{j + 1}") for j in range(len(keys))]
    kpos, knrm = [[] for _ in keys], [[] for _ in keys]
    mw = _mw(o)
    nmat = mw.to_3x3().inverted_safe().transposed()
    cn = _corner_normals(me)
    uvl = me.uv_layers.active.data if me.uv_layers.active else None
    ca = me.color_attributes.get("GT3Color") or (me.color_attributes.active_color if me.color_attributes else None)
    pdu, pdv = me.attributes.get("gt3_pd_u"), me.attributes.get("gt3_pd_v")
    pdu, pdv = (pdu, pdv) if pdu is not None and pdv is not None and pdu.domain == pdv.domain == "CORNER" else (None, None)
    verts, normals, uvs, cols, tris, tmat = [], [], [], [], [], []
    index = {}

    def corner(li, vi, pad=None):
        if vi in ov:                                   # a copy vertex whose part-vertex was edited
            p, n = _to_g(ov[vi][0]), _to_g(ov[vi][1])
        else:
            p = _to_g(mw @ me.vertices[vi].co)
            n = _to_g((nmat @ cn[li]).normalized())
        uv = [uvl[li].uv[0], 1.0 - uvl[li].uv[1]] if uvl else None
        if uv is not None and pad is not None:        # Region (pad): Blender UVs measure the PNG, the game's the buffer
            U, V = uvl[li].uv[0], uvl[li].uv[1]
            pd = (pdu.data[li].value, pdv.data[li].value) if pdu is not None else None
            if pd is not None and _f32(pd[0] * pad[0]) == U and _f32(1.0 - pd[1] * pad[1]) == V:
                uv = [pd[0], pd[1]]                   # untouched: PD's exact numbers
            else:
                uv = [U / pad[0], (1.0 - V) / pad[1]]
        col = None
        if ca is not None:
            src = ca.data[li].color if ca.domain == "CORNER" else ca.data[vi].color
            col = [max(0, min(255, round(x * 128.0))) for x in src]
        tg = tuple(tuple(_to_g(mw @ kb.data[vi].co)) for kb in keys)
        key = (tuple(p), tuple(round(x, 5) for x in n) if with_normals else None, tuple(uv) if uv else None,
               tuple(col) if col else None, tg)
        if key not in index:
            index[key] = len(verts)
            verts.append(p)
            normals.append(n)
            for j in range(len(keys)):
                kpos[j].append(list(tg[j]))
                knrm[j].append(_to_g((nmat @ Vector(kna[j].data[vi].vector)).normalized()) if kna[j] is not None else None)
            if uv is not None:
                uvs.append(uv)
            if col is not None:
                cols.append(col)
        return index[key]

    pads = {}
    # an object imported by an older build keeps its Region (pad) UVs in the game's buffer measure (no stored PD values):
    # exported as they are, exactly like before - re-import it to get the picture-relative view
    legacy = "gt3_uid" in o and pdu is None
    for lt in me.loop_triangles:
        m = me.materials[lt.material_index] if lt.material_index < len(me.materials) else None
        if lt.material_index not in pads:
            pads[lt.material_index] = None if legacy else _pad_scale(m.original if m is not None else None)
        tri = [corner(li, vi, pads[lt.material_index]) for li, vi in zip(lt.loops, lt.vertices)]
        if mw.determinant() < 0:
            tri = [tri[0], tri[2], tri[1]]
        tris.append(tri)
        tmat.append(mat_index(m))
    ev.to_mesh_clear()
    mesh = {"vertices": verts, "triangles": tris, "tri_materials": tmat}
    if with_normals:
        mesh["normals"] = normals
    if uvs:
        mesh["uvs"] = uvs
    if cols:
        mesh["colors"] = cols
    if keys:
        mesh["keys"] = kpos
        mesh["key_normals"] = [kn if kn and all(x is not None for x in kn) else None for kn in knrm]
    return mesh


def _draw_of(o, r, depsgraph, mat_index, sources=None):
    kind = r["kind"]
    # normals travel only where the VU uses them: lit shapes, the glint, EXT (env-map coordinates)
    normals = kind in ("reflect", "reflect_masked", "glint") or bool(r["reflect"]) or (
        r["lit"] and kind not in ("shadow", "mask", "depth", "depth_cut", "additive"))
    if "gt3_unk1" in o:
        u1 = int(o["gt3_unk1"])
        normals = normals or ((u1 ^ (u1 >> 2)) & 1) == 1
    ks = []
    if r.get("anim"):
        ks = _morph_keys(o)
        if not ks:
            raise RuntimeError(f"'{o.name}' is animated ({r['anim']}) but has no morph target: select it and use "
                               "'Add morph target' in the sidebar (N) > GT3 Car > Selected part")
        normals = True
    d = {"name": o.name, "kind": kind, "lit": r["lit"], "double_sided": r["double"],
         "reflect_tint": o.gt3_reflect_tint,
         # an untextured see-through part YOU made gets a tint texture; PD's own untextured soft parts (6 cars) stay as PD made them
         "mesh": _export_mesh(o, depsgraph, getattr(mat_index, "tint", mat_index)
                              if kind in ("soft", "soft_z", "blend") and "gt3_uid" not in o else mat_index,
                              normals, sources, ks)}
    if ks:
        m = d["mesh"]
        kp, kn = m.pop("keys"), m.pop("key_normals")
        # the chain length IS the key count (opcode 53 count): 1 key = mesh <-> key, 3 keys = PD's steering L / centre / R
        d["morph"] = {"driver": r["anim"], "ratio": float(o.gt3_anim_ratio), "mult": len(kp), "target": kp[0]}
        if kn[0] is not None:
            d["morph"]["target_normals"] = kn[0]
        if len(kp) > 1:
            d["morph"]["extra_keys"] = kp[1:]
            if all(x is not None for x in kn[1:]):
                d["morph"]["extra_key_normals"] = kn[1:]
        if "gt3_anim_matrix" in o:
            d["morph"]["matrix"] = json.loads(o["gt3_anim_matrix"])
    if r["reflect"]:
        d["reflect"] = r["reflect"]
        d["reflect_strength"] = r["strength"]
    elif kind in ("reflect", "reflect_masked"):
        d["reflect_strength"] = r["strength"]
    if r["stamp"]:
        d["stamp"] = True
    for k in ("unk1", "unk3", "external_tex", "alpha_ref"):
        if f"gt3_{k}" in o:
            d[k] = int(o[f"gt3_{k}"])
    if o.get("gt3_env_texture"):
        d["env_texture"] = True
    tm = d["mesh"]["tri_materials"]
    d["mat"] = tm[0] if tm else -1
    return d


# automatic draw order (PD's grammar): solid parts, cut-outs, hand-made reflections, glass, soft, glows
_ORDER_GROUP = {"opaque": 0, "noalpha": 0, "glint": 0, "cutout": 1, "cutout_blend": 1, "reflect": 2, "reflect_masked": 2,
                "depth": 3, "depth_cut": 3, "soft_z": 3, "mask": 3, "soft": 4, "blend": 4, "additive": 5, "shadow": 5}


def _draw_list(objs, depsgraph, mat_index, sources=None):
    """Objects -> ordered draws. Tail lamps: explicit orders (import) group per order; automatic ones become ONE
    lamp callback after the solid parts."""
    items = []
    for o in objs:
        r = _resolve(o)
        g = _ORDER_GROUP.get(r["kind"], 0)
        if r["lamp"] and o.gt3_order < 0:
            key = (1000 + 0 * 100 + 99, "")
        else:
            key = (o.gt3_order if o.gt3_order >= 0 else 1000 + g * 100, o.name)
        items.append((key, o, r))
    items.sort(key=lambda x: x[0])
    out, lamps = [], {}
    for key, o, r in items:
        if r["lamp"]:
            lk = key[0]
            if lk not in lamps:
                lamps[lk] = {"kind": "lamp", "name": "IsTailLampActive", "lamp_off": [], "lamp_on": []}
                out.append(lamps[lk])
            branch = {"off": "lamp_off", "on": "lamp_on", "off_night": "lamp_off_night"}[r["lamp"]]
            lamps[lk].setdefault(branch, []).append((o.get("gt3_lamp_order", 0), o.name, _draw_of(o, r, depsgraph, mat_index, sources)))
        else:
            out.append(_draw_of(o, r, depsgraph, mat_index, sources))
    for d in out:
        if d.get("kind") == "lamp":
            for branch in ("lamp_off", "lamp_on", "lamp_off_night"):
                if branch in d:
                    d[branch] = [x[2] for x in sorted(d[branch], key=lambda x: (x[0], x[1]))]
    return out


def _gather(context, depsgraph, mat_index):
    """Sort the car's mesh objects into (variant, lod, role) buckets (only the active car's, when there are several)."""
    root = _car_root(context)
    variants = _collection_variants(context.scene, root)
    buckets = {}
    for o in (root.all_objects if root is not None else context.scene.objects):
        if o.type != "MESH" or _hidden(o):
            continue
        if o.name.startswith("_gt3_"):
            continue
        v = _variant_of(o, variants)
        role = _resolve(o)["role"]
        buckets.setdefault((v[0], v[1], role), []).append(o)
    return buckets


def _light_points(objs, prefix):
    es = sorted([o for o in objs if o.type == "EMPTY" and re.match(re.escape(prefix) + r"_\d+", o.name)],
                key=lambda o: int(re.match(re.escape(prefix) + r"_(\d+)", o.name).group(1)))
    pts = []
    for e in es:
        mw = _mw(e)
        g = _A @ mw.to_3x3().normalized() @ _A
        rows = [list(g[0]), list(g[1]), list(g[2])]
        pos = _to_g(mw.translation)
        same_rows = same_pos = False
        if "gt3_rows" in e:
            r0 = list(e["gt3_rows"])
            orig = [r0[0:3], r0[3:6], r0[6:9]]
            if max(abs(rows[i][j] - orig[i][j]) for i in range(3) for j in range(3)) < 1e-5:
                rows, same_rows = orig, True
        if "gt3_pos" in e and max(abs(a - b) for a, b in zip(pos, e["gt3_pos"])) < 1e-5:
            pos, same_pos = list(e["gt3_pos"]), True
        p = {"pos": pos, "rows": rows}
        if same_rows and same_pos and "gt3_flare" in e:
            p["flare"] = list(e["gt3_flare"])
        for k, key in (("flare_offset", "gt3_flare_offset"), ("size", "gt3_size"), ("color_bits", "gt3_color_bits")):
            if key in e:
                p[k] = e[key]
        if "gt3_color" in e:
            p["color"] = list(e["gt3_color"])
        pts.append(p)
    return pts


def _info_points(context):
    root = _car_root(context)
    objs = [o for o in (root.all_objects if root is not None else context.scene.objects) if o.type == "EMPTY" and not _hidden(o)]
    ip = {"headlights": _light_points(objs, "Headlight"), "brakelights": _light_points(objs, "BrakeLight"),
          "brakeflares": _light_points(objs, "BrakeFlare")}
    ex = sorted([o for o in objs if re.match(r"Exhaust_\d+", o.name)], key=lambda o: o.name)
    ip["exhausts"] = [_to_g(_mw(o).translation) for o in ex]
    sp = sorted([o for o in objs if re.match(r"Spark_\d+", o.name)], key=lambda o: o.name)
    ip["sparks"] = [_to_g(_mw(o).translation) for o in sp]
    cams = []
    for o in objs:
        if not o.name.startswith("Cam_"):
            continue
        nm = o.name[4:].split(".")[0]
        slot = int(o.get("gt3_slot", _CAM_NAMES.index(nm) if nm in _CAM_NAMES else -1))
        if slot < 0:
            continue
        mw = _mw(o)
        d = _A @ (mw.to_3x3().normalized() @ Vector((0, 0, 1)))
        pitch = math.degrees(math.asin(max(-1.0, min(1.0, -d.y))))
        yaw = math.degrees(math.atan2(d.x, -d.z))
        if "gt3_pitch" in o and abs(pitch - o["gt3_pitch"]) < 0.05:
            pitch = o["gt3_pitch"]
        if "gt3_yaw" in o and abs(((yaw - o["gt3_yaw"]) + 180) % 360 - 180) < 0.05:
            yaw = o["gt3_yaw"]
        c = {"slot": slot, "pos": _to_g(mw.translation), "pitch": pitch, "yaw": yaw}
        if "gt3_stored_pos" in o and max(abs(a + b) for a, b in zip(c["pos"], o["gt3_stored_pos"])) < 1e-5:
            c["stored_pos"] = list(o["gt3_stored_pos"])
        for k, key in (("roll", "gt3_roll"), ("fov", "gt3_fov"), ("type", "gt3_type")):
            if key in o:
                c[k] = o[key]
        cams.append(c)
    ip["cameras"] = sorted(cams, key=lambda c: c["slot"])
    bx = _brakes_for_export(_car_root(context))
    if bx is not None:
        ip["brakes"] = bx
    b = None if bx is not None else _car_prop(context, "gt3_brakes")
    if b:
        b = json.loads(b)
        if b:
            ip["brakes"] = [{"caliper_texture": x["BrakeCaliperTextureIndex"], "disc_texture": x["BrakeDiscTextureIndex"],
                             "size": x["BrakeTextureSize"], "offset_from_center": x["BrakeOffsetFromCenter"],
                             "orientation_deg": x["BrakeTextureOrientationDeg"]} for x in b]
    dt = _car_prop(context, "gt3_default_tire_index")
    if dt is not None:
        ip["default_tire_index"] = int(dt)
    t = _car_prop(context, "gt3_tires")
    if t:
        t = json.loads(t)
        if t:
            ip["front_tires"] = list(t["FrontTires"].values())
            ip["rear_tires"] = list(t["RearTires"].values())
    return ip


def _lin_to_srgb8(c):
    c = max(0.0, min(1.0, float(c)))
    s = 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055
    return int(round(s * 255))


def _material_rgba(m):
    """The colour Blender shows for a material without an image: Principled BSDF Base Color + Alpha (else the
    viewport colour), as 8-bit sRGB + alpha."""
    col, alpha = tuple(m.diffuse_color[:3]), m.diffuse_color[3]
    if m.use_nodes and m.node_tree is not None:
        bsdf = next((n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is not None:
            col = tuple(bsdf.inputs["Base Color"].default_value[:3])
            alpha = bsdf.inputs["Alpha"].default_value
    return [_lin_to_srgb8(c) for c in col] + [int(round(max(0.0, min(1.0, alpha)) * 255))]


def _image_path(img, work, mat_name=""):
    """The texture file the builder reads for this image (PNG, TGA or BMP). A file on disk is used as it is, a packed
    file is written out byte for byte; painted or generated images are written out pixel-for-pixel as PNG (never
    through the render colour transform). Other formats (JPG ...) and images with no pixels (their file moved or
    deleted) are clear errors, not crashes."""
    if img is None:
        return None
    p = bpy.path.abspath(img.filepath, library=img.library) if img.filepath else ""
    if p and os.path.exists(p) and not img.packed_file and not img.is_dirty:
        with open(p, "rb") as f:
            fmt = _tex_format(f.read(8), p)
        if fmt is None:
            raise RuntimeError(f"material '{mat_name}': '{os.path.basename(p)}' is not a PNG, TGA or BMP - GT3 Car Maker "
                               f"reads {TEXTURE_FORMATS} only (Image Editor > Image > Save As)")
        return p
    os.makedirs(os.path.join(work, "Textures"), exist_ok=True)
    if img.packed_file and not img.is_dirty:
        fmt = _tex_format(bytes(img.packed_file.data[:8]), p or img.name)
        if fmt is not None:
            out = os.path.join(work, "Textures", bpy.path.clean_name(img.name) + "." + fmt)
            with open(out, "wb") as f:
                f.write(img.packed_file.data)          # the packed file itself, byte for byte
            return out
        if img.source == "FILE":
            raise RuntimeError(f"material '{mat_name}': packed image '{img.name}' is not a PNG, TGA or BMP - GT3 Car "
                               f"Maker reads {TEXTURE_FORMATS} only (unpack it, Save As one of those, pack again)")
    out = os.path.join(work, "Textures", bpy.path.clean_name(img.name) + ".png")
    try:
        img.pixels[0]                              # loads the buffer if Blender hasn't yet
    except Exception:
        pass
    w, h = img.size
    if not img.has_data or w == 0 or h == 0:
        raise RuntimeError(f"material '{mat_name}': image '{img.name}' has no pixels - its file is missing "
                           f"({p or 'no file path'}). Re-link it (Image Editor > Image > Replace) or pack it (Image > Pack).")
    px = [0.0] * (w * h * 4)
    img.pixels.foreach_get(px)
    tmp = bpy.data.images.new("_gt3_export_tmp", w, h, alpha=True)
    try:
        tmp.pixels.foreach_set(px)
        tmp.filepath_raw = out
        tmp.file_format = "PNG"
        tmp.save()
    finally:
        bpy.data.images.remove(tmp)
    return out


def _default_tyre_png(work):
    """A tyre in PD's layout when the car has none of its own: every PD tyre (285 of 285) is ONE 128x64 picture, the
    TOP half the tread, the BOTTOM half the sidewall (where the brand lettering sits)."""
    import numpy as np
    from .gt3car.png import write_png
    path = os.path.join(work, "Textures", "tyre_default.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    y, x = np.mgrid[0:64, 0:128]
    v = np.full((64, 128), 46, np.int32)                       # tread rubber
    v[(y < 38) & ((x % 8) < 2)] = 30                           # tread blocks
    v[(y < 38) & (np.isin(y, (11, 12, 24, 25)))] = 30          # circumferential grooves
    v[(y >= 38) & (y < 41)] = 34                               # shoulder
    v[(y >= 41) & (y < 59)] = 52                               # sidewall
    v[(y >= 47) & (y < 50)] = 58                               # sidewall rib
    v[y >= 59] = 36                                            # bead
    img = np.zeros((64, 128, 4), np.uint8)
    img[..., 0], img[..., 1], img[..., 2], img[..., 3] = v, v, v + 3, 255
    write_png(path, img)
    return path


def _tire_header(path):
    """(strip mode, flags, 8.0) from a GTTR file: PD's cars use (2, 0, 8) 226x, (1, 1, 8) 52x, (1, 3, 8) 7x."""
    with open(path, "rb") as f:
        h = f.read(0x1C)
    return [struct.unpack_from("<I", h, 0x10)[0], struct.unpack_from("<I", h, 0x14)[0], struct.unpack_from("<f", h, 0x18)[0]]


def _apply_tyre(spec, root, work):
    """The car's OWN tyre picture (Brakes & tyres panel) wins over the imported / template tyre; the imported car's
    tyre header is kept."""
    img = getattr(root, "gt3_tyre_image", None) if root is not None else None
    if img is None:
        return None
    header = None
    for k in ("tire_file", "menu_tire_file"):
        if spec.get(k):
            header = header or _tire_header(spec[k])
            del spec[k]
    spec["tire_texture"] = _image_path(img, work, "tyre")
    spec["tire_header"] = header or [2, 0, 8.0]
    return f"tyre: '{img.name}'"


def _rim_transform(lods):
    """PD's rim = a UNIT LEFT wheel at the origin (every PD rim: radius ~1.0, axle X, outer face ~x 0, depth +x); the
    engine scales and places it. A rim modelled where it sits on the car - any size, either side - is mapped into
    that. Returns (fn, note) or (None, None) when it already is PD-shaped (imported rims stay bit-exact)."""
    vs = [v for d in (lods[0] if lods else []) if d and d.get("mesh") for v in d["mesh"]["vertices"]]
    if not vs:
        return None, None
    xs, ys, zs = [v[0] for v in vs], [v[1] for v in vs], [v[2] for v in vs]
    cy, cz = (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2
    rad = max(max(ys) - min(ys), max(zs) - min(zs)) / 2     # the outline's half size (PD rims: 0.957 - 1.043)
    if rad < 1e-6 or (abs(cy) < 0.05 and abs(cz) < 0.05 and 0.85 < rad < 1.15 and abs(min(xs)) < 0.2):
        return None, None                                   # already PD's unit wheel (showroom rims run to x 0.84)
    right = min(xs) > 0.1                                   # wholly on the car's right (game +X): mirror it
    face = -max(xs) if right else min(xs)                   # the outer face, once it is a left wheel
    s = 1.0 / rad

    def fn(v):
        x = -v[0] if right else v[0]
        return [(x - face) * s, (v[1] - cy) * s, (v[2] - cz) * s]
    note = (f"wheel moved to the origin and scaled x{s:.2f}" + (" and mirrored to a left wheel" if right else "")
            + " (PD's unit wheel - the game places it)")
    return (fn, right), note


_SEE_THROUGH_KINDS = {"cutout", "cutout_blend", "soft", "soft_z", "blend"}


def _rim_facing_note(draw_lists, label="rim", race=True):
    """Checked in PD's unit-wheel space (after _rim_transform) against PD's 608 wheels (2026-09-25 census):
    - EVERY race rim (303/303) has an OPAQUE disc (or square) facing OUTBOARD behind its spokes - area 3.0-3.6 (radius
      ~1), 0.0-0.24 inboard of the face. Without it the spoke holes show straight through the wheel (the road, the car's
      underside, the far side) and the wheel looks hollow / inside-out from some angles. Showroom wheels are full 3D
      models and mostly have none (241/305), so this is checked for race wheels only.
    - Face-on triangles point OUTBOARD (-X) on every PD wheel; none point into the car.
    Returns a warning or None."""
    backing = face_in = face_out = 0
    see = False
    seen = set()
    for d in (draw_lists[0] if draw_lists else []) or []:
        m = d.get("mesh") if d else None
        if not m or id(m) in seen or d.get("kind") in _COPY_KINDS:
            continue
        seen.add(id(m))
        opaque = d.get("kind") not in _SEE_THROUGH_KINDS
        see = see or not opaque
        vs = m["vertices"]
        area = 0.0
        for t in m["triangles"]:
            a, b, c = (Vector(vs[i]) for i in t)
            n = (b - a).cross(c - a)
            ln = n.length
            if ln < 1e-12 or abs(n.x) <= 0.7 * ln:
                continue
            if n.x < 0:
                face_out += 1
                area += ln / 2
            else:
                face_in += 1
        if opaque:
            backing = max(backing, area)
    bad = []
    if race and see and backing < 2.0:
        bad.append("its see-through spokes have NO opaque disc behind them - every PD race rim has one: a disc facing "
                   "outboard, radius ~1, a little inboard of the face, Draw kind 'No alpha test' (a _Rim part is Cutout by "
                   "default - set it in Selected part); without it the holes show straight through the wheel")
    if face_in > face_out:
        bad.append(f"{face_in} of {face_in + face_out} face triangles point INTO the car (PD's point outboard) - flip them")
    return f"WARNING {label}: " + "; ".join(bad) if bad else None


def _apply_rim_transform(draw_lists, tf):
    fn, mirror = tf
    seen = set()
    for dl in draw_lists:
        for d in dl or []:
            if not d or id(d["mesh"]) in seen or not d.get("mesh"):
                continue
            seen.add(id(d["mesh"]))
            m = d["mesh"]
            m["vertices"] = [fn(v) for v in m["vertices"]]
            mo = d.get("morph")
            if mo:
                mo["target"] = [fn(v) for v in mo.get("target") or []]
                if mo.get("extra_keys"):
                    mo["extra_keys"] = [[fn(v) for v in k] for k in mo["extra_keys"]]
            if mirror:
                if m.get("normals"):
                    m["normals"] = [[-n[0], n[1], n[2]] for n in m["normals"]]
                m["triangles"] = [[t[0], t[2], t[1]] for t in m["triangles"]]


def _build_spec(context, work, fill_lods=True, colours=None, as_type=None):
    """The whole car from the scene: returns (spec, notes). colours = the paint-colour count to build (None = the
    car's own list). as_type RACE / MENU = build it as that (the save folder decides; None = the car's own type)."""
    parked = []
    for o in _owner_objects(_paint_owner(context)):
        for kb in (_morph_keys(o) if o.type == "MESH" else []):
            if kb.value != 0.0:
                parked.append((kb, kb.value))
                kb.value = 0.0
    try:
        return _build_spec_inner(context, work, fill_lods, colours, as_type)
    finally:
        for kb, v in parked:
            kb.value = v


def _build_spec_inner(context, work, fill_lods, colours, as_type=None):
    context.view_layer.update()          # matrix_world of freshly moved/imported empties is stale until this
    depsgraph = context.evaluated_depsgraph_get()
    mats, mat_ix = [], {}
    owner = _paint_owner(context)
    paints = list(owner.gt3_paints)
    ncol = colours or max(1, len(paints))
    mismatch = {}                         # material -> colours whose texture is not the base's size

    def mat_index(m):
        if m is None:
            return -1
        m = m.original          # the evaluated mesh hands out depsgraph COPIES: stale until the next evaluation
        if m.name in mat_ix:
            return mat_ix[m.name]
        img = _base_image(m, owner)
        sm = {"name": m.name, "texture": _image_path(img, work, m.name), "wrap": m.gt3_wrap, "psm": int(m.gt3_psm),
              "preset": m.gt3_preset}
        pt = m.gt3_paint_tex
        if ncol > 1 and len(pt) > 1:
            cts = [None] + [_image_path(pt[k].image, work, f"{m.name} colour {k}") if k < len(pt) and pt[k].image is not None
                            and pt[k].image != img else None for k in range(1, ncol)]
            if any(cts):
                sm["color_textures"] = cts
                if img is not None:
                    bad = [k for k in range(1, min(ncol, len(pt))) if pt[k].image is not None and pt[k].image != img
                           and tuple(pt[k].image.size) != tuple(img.size) and pt[k].image.size[0] > 0]
                    if bad:
                        mismatch[m.name] = bad
        cms = []
        for k in range(ncol):
            fin = paints[k].finish if k < len(paints) else "EXACT"
            v = None
            if fin == "EXACT" and k < len(pt) and pt[k].values:
                v = json.loads(pt[k].values)
            elif fin in _FINISH_VALUES and m.gt3_is_paint:
                v = _finish_values(fin)
            cms.append(v)
        if any(v is not None for v in cms):
            sm["color_materials"] = cms
        if m.gt3_night_image is not None:
            sm["night_texture"] = _image_path(m.gt3_night_image, work, m.name)
        elif m.gt3_night_texture:
            sm["night_texture"] = bpy.path.abspath(m.gt3_night_texture)
        if "gt3_pglu" in m:
            sm.update(json.loads(m["gt3_pglu"]))
        mat_ix[m.name] = len(mats)
        mats.append(sm)
        return mat_ix[m.name]

    tints = {}

    def tint_index(m):
        """A SEE-THROUGH part (glass / soft / blend) whose material has no image: PD's glass is always textured (331 of
        331 soft_z draws) - it gets an 8x8 texture of the material's Base Color + Alpha, exactly as Blender shows them
        (Alpha 1.0 = solid). Other parts keep the material untextured."""
        if m is None:
            return mat_index(m)
        m = m.original
        if _base_image(m, owner) is not None:
            return mat_index(m)
        if m.name not in tints:
            from .gt3car.png import write_png
            import numpy as np
            rgba = _material_rgba(m)
            path = os.path.join(work, "Textures", bpy.path.clean_name(m.name) + "_tint.png")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            write_png(path, np.tile(np.array(rgba, np.uint8), (8, 8, 1)))
            sm = dict(mats[mat_index(m)])
            sm["name"], sm["texture"] = m.name + " tint", path
            tints[m.name] = len(mats)
            mats.append(sm)
        return tints[m.name]

    mat_index.tint = tint_index
    b = _gather(context, depsgraph, mat_index)
    notes = []

    def _mismatch_note():
        if mismatch:
            names = ", ".join(f"{k} (colour{'s' if len(v) > 1 else ''} {v[0]}{'-' + str(v[-1]) if len(v) > 1 else ''})"
                              for k, v in list(mismatch.items())[:4])
            notes.append(f"{len(mismatch)} paint material(s) have colour textures of a different size than their base - "
                         f"resized, but check them: {names}{' ...' if len(mismatch) > 4 else ''} (redo: Make colour textures)")
    skipped = sum(1 for o in _owner_objects(owner) if o.type in {"MESH", "EMPTY"} and not o.name.startswith("_gt3_") and _hidden(o))
    if skipped:
        notes.append(f"{skipped} hidden object{'s' if skipped > 1 else ''} left out")
    sources = _Sources(context, depsgraph)

    def _draw_list_s(objs, dg, mi):
        return _draw_list(objs, dg, mi, sources)

    reused = []
    root = _car_root(context)
    ctype = _car_type(root)
    as_type = as_type or ctype
    conv = as_type != ctype                # a race car saved as the menu car, or the other way round
    pd_empty = None if conv else json.loads(_car_prop(context, "gt3_pd_empty_lods") or "null")

    def lods(role, fill_to=None, share_key=None):
        """Draw lists per LOD. A LOD with nothing of its own REUSES the nearest lower one (same shapes + textures,
        zero extra bytes) up to fill_to; beyond that it stays empty (PD: LOD3 draws nothing). An imported PD car:
        every LOD PD filled is kept filled (reused when its objects were deleted), PD's empty ones stay empty."""
        res = [_draw_list_s(b.get(("race", k, role), []), depsgraph, mat_index) for k in range(4)]
        share = [-1] * 4
        pe = pd_empty.get(role) if pd_empty else None
        if fill_lods and share_key and (fill_to or pe is not None):
            for k in range(1, 4):
                if res[k] or (k in pe if pe is not None else k >= fill_to):
                    continue
                j = k - 1
                while j > 0 and not res[j]:
                    j = share[j] if share[j] >= 0 else j - 1
                if res[j]:
                    share[k] = j
        while res and not res[-1] and share[len(res) - 1] < 0:
            res.pop()
        if share_key and any(s >= 0 for s in share):
            spec[share_key] = share[:max(len(res), max(k for k in range(4) if share[k] >= 0) + 1)]
            reused.append(f"{role} LOD{','.join(str(k) for k in range(4) if share[k] >= 0)} reuse LOD{min(s for s in share if s >= 0)}")
        return res

    spec = {}
    spec.update({"format": "gt3-car/1", "name": _car_prop(context, "gt3_car", "car"),
                 # converted: what the other car had is not there - generate what is missing (never for PD's own)
                 "auto_shadows": True if conv else bool(_car_prop(context, "gt3_auto_shadows", True)),
                 "target": "menu" if as_type == "MENU" else "race"})
    no_parts = ("No car parts found: put the car's pieces into its Body collection (or name them e.g. "
                "Shell_MainBody, Windows_Glass)"
                + ("" if ctype == "MENU" else " (in LOD0, or outside the LOD collections)"))
    if conv and as_type == "MENU":
        notes.append("saved into menu/cars: built as the MENU car from LOD0 (one level - the showroom never switches LOD)")
    elif conv:
        brakes = sum(len(v) for (vv, k, role), v in b.items() if role in ("rim_front", "rim_rear"))
        merged = {}
        for (vv, k, role), v in b.items():
            if role not in ("rim_front", "rim_rear"):
                merged.setdefault(("race", 0, role), []).extend(v)
        b = merged
        notes.append("saved into cars/day: built as the RACE car from the menu car (LOD1-2 reuse it)"
                     + (f"; {brakes} brake-disc object(s) left out (race cars have none)" if brakes else ""))
    no_rim = "No wheel: name the rim object '..._Rim' (model it where it sits on the car - the export does the rest)"

    if as_type == "MENU":
        # the showroom car: every object of a menu car, whatever collection it sits in; a race car's LOD0
        by = {}
        for (v, k, role), objs in b.items():
            if ctype == "MENU" or (v == "race" and k == 0):
                by.setdefault(role, []).extend(objs)
        parts = _draw_list_s(by.get("part", []), depsgraph, mat_index)
        if not parts:
            raise RuntimeError(no_parts)
        rim = _draw_list_s(by.get("rim", []), depsgraph, mat_index)
        if not rim:
            raise RuntimeError(no_rim)
        spec["body"] = [parts]
        spec["rim"] = [rim]
        spec["menu_rim"] = [rim]
        tf, note = _rim_transform([rim])
        if tf:
            _apply_rim_transform([rim], tf)
            notes.append(note)
        notes += [w for w in [_rim_facing_note([rim], race=False)] if w]
        rear = _draw_list_s(by.get("rear_wheel", []), depsgraph, mat_index)
        if rear:
            spec["rear_rim"] = [rear]
            spec["menu_rear_rim"] = [rear]
            tf, note = _rim_transform([rear])
            if tf:
                _apply_rim_transform([rear], tf)
                notes.append("rear " + note)
            notes += [w for w in [_rim_facing_note([rear], "rear wheel", race=False)] if w]
        for role, key in (("rim_front", "menu_rim_front"), ("rim_rear", "menu_rim_rear"), ("ground", "menu_ground_draws")):
            if by.get(role):
                spec[key] = _draw_list_s(by[role], depsgraph, mat_index)
        if not by.get("ground"):
            notes.append("ground shadow generated")
        if by.get("pool"):
            spec["headlight_pool"] = _draw_list_s(by["pool"][:1], depsgraph, mat_index)[0]
            spec["headlight_pool"]["kind"] = "additive"
    else:
        spec["body"] = lods("part", fill_to=3, share_key="lod_share")
        if not spec["body"]:
            raise RuntimeError(no_parts)
        rim = lods("rim", fill_to=3, share_key="rim_lod_share")
        if not rim:
            raise RuntimeError(no_rim)
        spec["rim"] = rim
        tf, note = _rim_transform(rim)
        if tf:
            _apply_rim_transform(rim, tf)
            notes.append(note)
        notes += [w for w in [_rim_facing_note(rim)] if w]
        rear = lods("rear_wheel", fill_to=3, share_key="rear_rim_lod_share")
        if rear and rear[0]:
            spec["rear_rim"] = rear
            tf, note = _rim_transform(rear)
            if tf:
                _apply_rim_transform(rear, tf)
                notes.append("rear " + note)
            notes += [w for w in [_rim_facing_note(rear, "rear wheel")] if w]
        for role, key, fill, sk in (("shadow", "shadow_draws", 4, "shadow_lod_share"), ("ground", "ground_draws", 3, "ground_lod_share")):
            v = lods(role, fill_to=fill, share_key=sk)
            if v and any(v):
                spec[key] = v
            else:
                notes.append(f"{'real-time' if role == 'shadow' else 'ground'} shadow generated")
        pool = [o for k in range(4) for o in b.get(("race", k, "pool"), [])]
        if pool:
            spec["headlight_pool"] = _draw_list_s(pool[:1], depsgraph, mat_index)[0]
            spec["headlight_pool"]["kind"] = "additive"
        menu_left = sum(len(v) for (vv, k, role), v in b.items() if vv == "menu")
        if menu_left:
            notes.append(f"{menu_left} object(s) in a 'Menu' collection left out - a menu car is its own car now "
                         "(sidebar > GT3 Car > New car > Menu car)")
        # carried verbatim from an imported PD car (the night car is written only by the full-set tools now)
        nj = _car_prop(context, "gt3_night_json")
        if nj:
            night = json.loads(nj)
            _follow_json_all = sum(_follow_json(lod, sources) for lod in night)
            spec["night_body"] = _indices_for_json(night, mat_index)
    ej = None if conv else _car_prop(context, "gt3_extra_models")    # PD's extra slots belong to THAT car
    if ej:
        spec["extra_models"] = _indices_for_json(json.loads(ej), mat_index)
    notes += _light_warnings(context)
    if tints:
        notes.append(f"see-through part(s) without a texture tinted from their material's Base Color + Alpha: "
                     f"{', '.join(sorted(tints))}")
    for k in _BLOB_KEYS:
        v = _blob_file(context, k, work)
        if v:
            spec[k] = v
    if "tire_file" not in spec:
        tt = _car_prop(context, "gt3_tire_texture")
        spec["tire_texture"] = bpy.path.abspath(tt) if tt else _default_tyre_png(work)
    ij = _car_prop(context, "gt3_info_json")
    if ij:
        spec["info"] = json.loads(ij)
    ip = _info_points(context)
    if any(ip.get(k) for k in ("headlights", "brakelights", "brakeflares", "exhausts", "sparks", "cameras")):
        spec["info_points"] = ip             # no car-info empties at all = keep the imported / default GTCI as it is
    spec["materials"] = mats
    spec["colors"] = ncol
    for k in ("lod_levels", "model_counts"):              # PD's own structure, as imported
        v = None if conv else _car_prop(context, f"gt3_{k}")
        if v:
            spec[k] = json.loads(v)
    lay = None if conv else _car_prop(context, "gt3_color_layout")   # PD's per-set layout fits only its own sets
    if lay:
        lay = json.loads(lay)
        if lay.get("count") == ncol:           # the imported car's own count: PD's exact per-set layout
            if lay.get("sets"):
                spec["color_sets"] = lay["sets"]
            if lay.get("tables"):
                spec["color_tables"] = lay["tables"]
    spec["texture_lod_divisors"] = [1, 1, 1, 1] if ij else [1, 2, 4, 4]
    notes += reused
    _mismatch_note()
    return spec, notes


# ------------------------------------------------------------------ export: one car = one file

def _folder_type(path):
    """What the game reads from the folder a car is saved in: <data>/menu/cars -> MENU, <data>/cars/day|night|eve
    -> RACE, anywhere else None."""
    d = os.path.dirname(os.path.abspath(bpy.path.abspath(path)))
    a, b = os.path.basename(d).lower(), os.path.basename(os.path.dirname(d)).lower()
    if a == "cars" and b == "menu":
        return "MENU"
    if a in ("day", "night", "eve") and b == "cars":
        return "RACE"
    return None


def _data_root(path):
    """The game's data folder of a car saved in <data>/menu/cars or <data>/cars/day|night|eve - else None. Taken
    from the save folder itself: searching upward for cars/ + menu/ once picked a stray copy of that tree."""
    if _folder_type(path) is None:
        return None
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(bpy.path.abspath(path)))))


def _data_root_pref(context):
    """data/ from the add-on's database folder (data/database), for the save dialog's first folder."""
    db = _db_dir_pref(context)
    root = os.path.dirname(db) if db else None
    return root if root and os.path.isdir(os.path.join(root, "cars")) else None


def _guess(path_from_car, *rel):
    root = _data_root(path_from_car)
    return os.path.join(root, *rel) if root else os.path.dirname(path_from_car)


class EXPORT_OT_gt3_car(Operator, ExportHelper):
    bl_idname = "export_scene.gt3_car_scratch"     # own id: gt3_track_tool's old body-swap exporter is export_scene.gt3_car
    bl_label = "Export GT3 Car"
    bl_description = ("Save this car as ONE file. The folder decides what it is: data/cars/day/<code> = the race car, "
                      "data/menu/cars/<code> = the menu car (a race car saved there is built from its LOD0)")
    filename_ext = ""
    filter_glob: StringProperty(default="*", options={"HIDDEN"})
    fill_lods: BoolProperty(name="Fill missing LODs", default=True,
                            description="Race car, only LOD0 modelled: LOD1-2 reuse it (LOD3 stays empty, as PD's)")
    colour_list: BoolProperty(name="Update the game's colour list", default=True,
                              description="Saving into the game's data folder: write the car's colour names + chips into "
                                          "data/database/carcolor.db (first time keeps .bak copies). Off = the car is padded "
                                          "to the game's colour count instead")

    def invoke(self, context, event):
        try:
            root = _car_root(context)
        except RuntimeError as ex:
            self.report({"ERROR"}, str(ex))
            return {"CANCELLED"}
        code = (root.get("gt3_car") if root is not None else None) or bpy.path.display_name_from_filepath(bpy.data.filepath) or "car"
        data = _data_root_pref(context)
        rel = ("menu", "cars") if _car_type(root) == "MENU" else ("cars", "day")
        self.filepath = os.path.join(data, *rel, code) if data else code
        return ExportHelper.invoke(self, context, event)

    def draw(self, context):
        try:
            own = _car_type(_car_root(context))
        except RuntimeError:
            own = "RACE"
        ft = _folder_type(self.filepath)
        menu = (ft or own) == "MENU"
        col = self.layout.column()
        col.label(text=("MENU car -> data/menu/cars/<code>" if menu else "RACE car -> data/cars/day/<code>"), icon="AUTO")
        col.label(text=("(+ its showroom wheel, written for you)" if menu else "(one file - its wheel is inside)"))
        if ft and ft != own:
            col.label(text=f"this folder makes it the {'menu' if menu else 'race'} car", icon="INFO")
        if not menu:
            col.prop(self, "fill_lods")
        col.prop(self, "colour_list")

    def execute(self, context):
        try:
            return self._export(context)
        except RuntimeError as ex:
            self.report({"ERROR"}, str(ex))
            return {"CANCELLED"}

    def _export(self, context):
        with tempfile.TemporaryDirectory(prefix="gt3car_export_") as work:     # nothing is left behind
            return self._export_in(context, work)

    def _export_in(self, context, work):
        name = os.path.basename(self.filepath)
        root = _car_root(context)
        as_type = _folder_type(self.filepath) or _car_type(root)      # the folder decides; elsewhere the car's own type
        menu = as_type == "MENU"
        # colours: the car's list vs the game's (data/database next to data/cars) - the two must agree
        owner = _paint_owner(context)
        paints = list(owner.gt3_paints)
        data = _data_root(self.filepath)
        db_dir = os.path.join(data, "database") if data and os.path.exists(os.path.join(data, "database", "carcolor.db")) else None
        _remember_db(context, db_dir)
        pre_notes = []
        _apply_custom_brakes(root, data, work, pre_notes)
        game = _db_colours(db_dir, name) if db_dir else None
        n_game = len(game) if game else 0
        write_list = bool(paints) and self.colour_list and db_dir is not None
        ncol = len(paints) if write_list else max(len(paints), n_game, 1)
        spec, notes = _build_spec(context, work, self.fill_lods, colours=ncol, as_type=as_type)
        notes = pre_notes + notes
        if not paints and n_game > 1:
            notes.append(f"the game lists {n_game} colours for {name}: all show the car's own paint (add a colour list to vary them)")
        elif paints and not write_list and n_game and n_game != len(paints):
            notes.append(f"the game lists {n_game} colours for {name}, the car has {len(paints)}"
                         + (f": colours {len(paints)}-{n_game - 1} repeat the base" if n_game > len(paints) else ""))
        for k in ("menu_tire_file", "menu_info_file"):           # a menu car's own GTTR / GTCI
            v = _blob_file(context, k, work)
            if v:
                spec[k] = v
        tn = _apply_tyre(spec, root, work)
        if tn:
            notes.append(tn)
        sp = os.path.join(work, "car_export.json")
        with open(sp, "w", encoding="utf-8") as f:
            json.dump(spec, f)
        if not menu:
            spec["target"] = "race_all"          # day + night + eve (PD: eve = the night file, 282 of 287 cars)
        with open(sp, "w", encoding="utf-8") as f:
            json.dump(spec, f)
        out = os.path.join(work, "build")
        res = _run(["gt3-car-build", sp, out, name])
        log = (res.stdout or "") + (res.stderr or "")
        print(log)
        notes += [ln.strip() for ln in log.splitlines() if ln.strip().startswith("WARNING rim face")]
        if res.returncode != 0:
            over = [ln.strip() for ln in log.splitlines() if "OVER THE ENGINE CAP" in ln]
            if over:
                raise RuntimeError("The car is too big for the game: " + "; ".join(over)
                                   + " - lower texture sizes / polygon counts (the Blender console has the full build log)")
            raise RuntimeError(f"build failed ({res.returncode}): {log.strip()[-500:]}")
        built = os.path.join(out, "menu", "cars", name) if menu else os.path.join(out, "cars", "day", name)
        saved = []
        if not menu and data:
            # the game loads cars/day, cars/night and cars/eve: all three are written, whichever one was chosen
            for v in ("day", "night", "eve"):
                dst = os.path.join(data, "cars", v, name)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(os.path.join(out, "cars", v, name), dst)
                saved.append(dst)
        else:
            shutil.copyfile(built, self.filepath)
            saved.append(self.filepath)
        if menu:
            # the showroom loads menu/wheel/<code> for a stock wheel (engine code) - written beside the car
            wheel = os.path.join(out, "menu", "wheel", name)
            if data:
                wd = os.path.join(data, "menu", "wheel")
                os.makedirs(wd, exist_ok=True)
                shutil.copyfile(wheel, os.path.join(wd, name))
                saved.append(os.path.join(wd, name))
            else:
                notes.append("saved outside the game's data folder: the showroom also needs data/menu/wheel/<code> - "
                             "export into data/menu/cars to have it written")
        elif _folder_type(self.filepath) is None:
            notes.append("saved outside the game's data folder: this is the DAY car - export into data/cars/day to have "
                         "the night and eve cars written beside it")
        if write_list:
            cl = [{"name": p.name, "chip": [round(c * 255) for c in p.chip],
                   "id": p.db_id if p.db_id >= 0 else None,
                   "name_jp": p.name_jp if p.name_jp and p.name == p.db_name else None} for p in paints]
            cp = os.path.join(work, "colours_export.json")
            with open(cp, "w", encoding="utf-8") as f:
                json.dump(cl, f)
            r2 = _run(["gt3-carcolor-set", db_dir, name, cp])
            if r2.returncode != 0:
                raise RuntimeError(f"colour list not written: {((r2.stderr or '') + (r2.stdout or '')).strip()[-300:]}")
            notes.append((r2.stdout or "").strip().split(" -> ")[0])
        self.report({"WARNING"} if any(str(n).startswith("WARNING") for n in notes) else {"INFO"},
                    f"GT3 {'menu' if menu else 'race'} car saved: {', '.join(saved)}"
                              + (f" ({'; '.join(notes)})" if notes else ""))
        return {"FINISHED"}


# ------------------------------------------------------------------ the car-info template (PD's medians)

# game coords (x right, y up, z back); PD medians over the cars with that many lights (gt3-car-gtci-dump)
_TPL_LIGHTS = [
    ("Headlight_0", (-0.592, 0.483, -1.868), (0.8, 0.75, 0.8), False, ""),
    ("Headlight_1", (-0.496, 0.457, -1.926), (1.0, 0.833, 0.5), False, ""),
    ("Headlight_2", (0.496, 0.457, -1.926), (1.0, 0.833, 0.5), False, ""),
    ("Headlight_3", (0.592, 0.483, -1.868), (0.8, 0.75, 0.8), False, ""),
    ("Headlight_4", (0.0, 0.50, -2.05), (0.9, 0.9, 1.0), True, "logo glow"),
    ("Headlight_5", (0.0, 0.30, 2.10), (0.6, 0.1, 0.06), True, "rear fog"),
    ("Headlight_6", (0.0, 1.13, 0.13), (0.32, 0.34, 0.4), True, "roof light"),
    ("BrakeLight_0", (-0.637, 0.67, 2.121), (1.0, 0.0, 0.0), False, ""),
    ("BrakeLight_1", (-0.443, 0.659, 2.142), (1.0, 0.0, 0.0), False, ""),
    ("BrakeLight_2", (0.443, 0.659, 2.142), (1.0, 0.0, 0.0), False, ""),
    ("BrakeLight_3", (0.637, 0.67, 2.121), (1.0, 0.0, 0.0), False, ""),
    ("BrakeFlare_0", (-0.625, 0.605, 2.082), (0.2, 0.0, 0.0), False, ""),
    ("BrakeFlare_1", (-0.429, 0.605, 2.084), (0.2, 0.0, 0.0), False, ""),
    ("BrakeFlare_2", (0.426, 0.605, 2.084), (0.2, 0.0, 0.0), False, ""),
    ("BrakeFlare_3", (0.625, 0.605, 2.082), (0.2, 0.0, 0.0), False, ""),
    ("BrakeFlare_4", (0.0, 0.791, 2.111), (0.2, 0.0, 0.0), False, "third brake light"),
]


_REAR_Z = 0.6        # game z (back = +): PD's rear night glows sit at 1.26-1.94, its roof lights at 0.13-0.21


def _light_facing(e):
    """'front' / 'rear': where a light's glare can be seen from (its axis = the rotation's third row, as exported)."""
    g = _A @ _mw(e).to_3x3().normalized() @ _A
    return "front" if g[2][2] >= 0 else "rear"


def _light_warnings(context):
    """A light that faces INTO the car: at the front but facing rear (or at the back but facing front) - its glare only
    shows from the wrong side. PD's own lights (imported, untouched) are never flagged."""
    root = _car_root(context)
    out = []
    for e in (root.all_objects if root is not None else context.scene.objects):
        if e.type != "EMPTY" or _hidden(e) or "gt3_rows" in e or not re.match(r"(Headlight|BrakeLight|BrakeFlare)_\d+", e.name):
            continue
        z = _to_g(_mw(e).translation)[2]
        f = _light_facing(e)
        if (z < -_REAR_Z and f == "rear") or (z > _REAR_Z and f == "front"):
            out.append(f"WARNING {e.name}: at the {'front' if z < 0 else 'rear'} but facing {f.upper()} - its glare only "
                       f"shows from {'behind' if f == 'rear' else 'the front'} (select it: Selected part > Face "
                       f"{'front' if z < 0 else 'rear'})")
    return out


def _tpl_light(coll, name, pos, colour, rear, hidden, note):
    e = bpy.data.objects.new(name, None)
    e.empty_display_type, e.empty_display_size = "SPHERE", 0.06
    e.location = _to_b(pos)
    if rear:
        e.rotation_euler = (0, 0, math.pi)                   # PD brake lamps: yaw 180
    e["gt3_color"] = list(colour)
    if note:
        e["gt3_note"] = note
        e.show_name = True
    coll.objects.link(e)
    if hidden:
        e.hide_set(True)
    return e


def _template_info(root, context):
    ic = _new_coll(root, "Car Info")
    lp, ex, sp, cc = (_new_coll(ic, n) for n in ("Lights", "Exhausts", "Sparks", "Cameras"))
    for name, pos, colour, hidden, note in _TPL_LIGHTS:
        _tpl_light(lp, name, pos, colour, not name.startswith("Headlight") or pos[2] > _REAR_Z, hidden, note)
    for i, x in enumerate((-0.523, 0.53)):
        e = bpy.data.objects.new(f"Exhaust_{i}", None)
        e.empty_display_type, e.empty_display_size = "SINGLE_ARROW", 0.25
        e.location = _to_b((x, 0.148, 2.064))
        ex.objects.link(e)
    for i, (x, z) in enumerate(((0.515, 1.839), (-0.57, 1.526))):
        e = bpy.data.objects.new(f"Spark_{i}", None)
        e.empty_display_type, e.empty_display_size = "PLAIN_AXES", 0.05
        e.location = _to_b((x, 0.0, z))
        sp.objects.link(e)
        e.hide_set(True)                                      # 22 of 285 PD cars: sparks when the floor scrapes
    for slot, (pp, pitch, yaw, fov, ctype) in enumerate(_CAM_DEFAULTS):
        _cam_empty(cc, slot, (-pp[0], -pp[1], -pp[2]), pitch, yaw, 0.0, fov, ctype)
    _hide_in_view(context, cc)                                # 20 cameras: out of the way until wanted (eye toggles)


_TEMPLATE_COLLS = ("Body", "Wheel", "Rear Wheel", "Steering", "Mudflaps")
# the importer sorts PD's parts into the same collections (in this order)
_ROLE_COLL = {"rim": "Wheel", "rear_wheel": "Rear Wheel", "rim_front": "Brake Front", "rim_rear": "Brake Rear",
              "shadow": "RT Shadow", "ground": "Ground Shadow", "pool": "Headlight Beam"}
_COLL_ORDER = ("Body", "Wheel", "Rear Wheel", "Steering", "Mudflaps", "Brake Front", "Brake Rear", "RT Shadow",
               "Ground Shadow", "Headlight Beam")


def _into_role_collections(parent):
    """An imported car's parts moved from their LOD / car collection into Body, Wheel, Steering, Mudflaps ... - the same
    layout a new car has, so it is clear what is what. (Draw order is per object, so the export does not change.)"""
    groups = {}
    for o in list(parent.objects):
        if o.type != "MESH":
            continue
        r = _resolve(o)
        a = str(r["anim"] or "")
        name = "Steering" if a == "steering" else "Mudflaps" if a.startswith("flutter") else _ROLE_COLL.get(r["role"], "Body")
        groups.setdefault(name, []).append(o)
    for name in _COLL_ORDER:
        if name in groups:
            c = _new_coll(parent, name)
            for o in groups[name]:
                c.objects.link(o)
                parent.objects.unlink(o)


class OBJECT_OT_gt3_new_car(Operator):
    bl_idname = "object.gt3_new_car"
    bl_label = "New GT3 Car"
    bl_description = ("A new car: its collection (+ LOD0-3 for a race car) and PD's full car-info template - 4 headlights, "
                      "4 brake lights, 5 brake flares (rear-window third light), 2 exhausts, 20 cameras; hidden extras: "
                      "logo glow, rear fog, roof light, sparks (unhide to use)")
    bl_options = {"REGISTER", "UNDO"}
    kind: EnumProperty(items=[("RACE", "Race car", "LOD0-3 collections"), ("MENU", "Menu car", "The showroom car")], default="RACE")
    code: StringProperty(name="Car code", default="")

    def execute(self, context):
        code = self.code or context.scene.gt3_new_code or "custom"
        root = bpy.data.collections.new(f"GT3 {'Menu ' if self.kind == 'MENU' else ''}Car: {code}")
        context.scene.collection.children.link(root)
        root["gt3_car"] = code
        root.gt3_car_type = self.kind
        root["gt3_auto_shadows"] = True
        if self.kind == "RACE":
            lods = [_new_coll(root, f"LOD{k}") for k in range(4)]
            for name in _TEMPLATE_COLLS:
                _new_coll(lods[0], name)
        else:
            for name in _TEMPLATE_COLLS + ("Brake Front", "Brake Rear"):
                _new_coll(root, name)
        _template_info(root, context)
        self.report({"INFO"}, f"GT3 {'menu' if self.kind == 'MENU' else 'race'} car '{code}': drop your parts into "
                              f"{'LOD0 > ' if self.kind == 'RACE' else ''}Body / Wheel / Steering / Mudflaps (or name them "
                              "..._MainBody, ..._Glass, ..._Rim)")
        return {"FINISHED"}


class OBJECT_OT_gt3_tyre_get(Operator):
    bl_idname = "object.gt3_tyre_get"
    bl_label = "Get a Tyre Picture"
    bl_description = ("Load a tyre as an editable picture into 'Tyre picture': this car's own (imported) tyre, or any car's "
                      "from the game's data folder. PD layout: 128x64, TOP half = tread, BOTTOM half = sidewall")
    bl_options = {"REGISTER", "UNDO"}
    code: StringProperty(name="Car code", default="", description="Empty = this car's own tyre")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        import numpy as np
        from .gt3car.carinfo import CarFileParts
        from .gt3car.tex1 import TextureSet1
        root = _car_root(context)
        if root is None:
            self.report({"ERROR"}, "No GT3 car here")
            return {"CANCELLED"}
        data = None
        if self.code:
            db = _db_dir_pref(context)
            f = os.path.join(os.path.dirname(db), "cars", "day", self.code) if db else ""
            if not f or not os.path.exists(f):
                self.report({"ERROR"}, f"{self.code}: not found in the game's data folder (set the database path first)")
                return {"CANCELLED"}
            data = CarFileParts.load(f).gttr
        else:
            b = root.get("gt3_blob_tire_file") or root.get("gt3_blob_menu_tire_file")
            if not b:
                self.report({"ERROR"}, "This car has no tyre of its own yet - type a game car's code")
                return {"CANCELLED"}
            data = base64.b64decode(b)
        ts = TextureSet1.read(data, struct.unpack_from("<I", data, 0x1C)[0])
        px = np.asarray(ts.image(0), np.float32)[::-1] / 255.0          # PNG rows top-down -> Blender bottom-up
        h, w = px.shape[:2]
        img = bpy.data.images.new(f"tyre_{self.code or root.get('gt3_car', 'car')}", w, h, alpha=True)
        img.pixels.foreach_set(px.ravel())
        img.pack()
        root.gt3_tyre_image = img
        self.report({"INFO"}, f"Tyre picture '{img.name}' ({w}x{h}): top half = tread, bottom half = sidewall")
        return {"FINISHED"}


class OBJECT_OT_gt3_fix_hidden(Operator):
    bl_idname = "object.gt3_fix_hidden"
    bl_label = "Make Hidden Collections Toggleable"
    bl_description = ("Older imports hid LOD1-3 / the menu car / the cameras with 'Disable in Viewports', which the "
                      "Outliner shows no toggle for. This hides them with the eye instead, so you can switch them on/off")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        n = 0
        for root in _car_roots(context):
            for c in [root] + list(root.children_recursive):
                if c.hide_viewport:
                    c.hide_viewport = False
                    _hide_in_view(context, c)
                    n += 1
        self.report({"INFO"}, f"{n} collection(s): now hidden with the Outliner eye - click it to show them")
        return {"FINISHED"}


class OBJECT_OT_gt3_rim_face(Operator):
    bl_idname = "object.gt3_rim_face"
    bl_label = "Wheel spinning face"
    bl_description = ("RACE wheel: draw this disc as PD's SPINNING FACE - the game textures it every frame with a still "
                      "picture (parked) or a motion-blurred one (moving), both made from its texture on export; the "
                      "brake pictures show through its see-through parts. Off = a plain textured part")
    bl_options = {"REGISTER", "UNDO"}
    on: BoolProperty(default=True)

    def execute(self, context):
        n = 0
        for o in context.selected_objects or [context.object]:
            if o is None or o.type != "MESH" or _resolve(o)["role"] not in ("rim", "rear_wheel"):
                continue
            if self.on:
                o["gt3_external_tex"] = 1
            elif "gt3_external_tex" in o:
                del o["gt3_external_tex"]
            n += 1
        self.report({"INFO"}, f"{n} wheel part(s): {'spinning face' if self.on else 'plain part'}")
        return {"FINISHED"}


def _has_picture(o):
    return any(sl.material is not None and _base_image(sl.material, None) is not None for sl in o.material_slots)


class OBJECT_OT_gt3_light_face(Operator):
    bl_idname = "object.gt3_light_face"
    bl_label = "Light Facing"
    bl_description = "Point this light's glare to the front or to the rear of the car"
    bl_options = {"REGISTER", "UNDO"}
    face: EnumProperty(items=[("FRONT", "Face front", ""), ("REAR", "Face rear", "")], default="FRONT")

    def execute(self, context):
        e = context.object
        if e is None or e.type != "EMPTY":
            return {"CANCELLED"}
        e.rotation_mode = "XYZ"
        e.rotation_euler = (0.0, 0.0, 0.0 if self.face == "FRONT" else math.pi)
        for k in ("gt3_rows", "gt3_flare"):                   # the imported exact values no longer apply
            if k in e:
                del e[k]
        return {"FINISHED"}


class OBJECT_OT_gt3_add_point(Operator):
    bl_idname = "object.gt3_add_point"
    bl_label = "Add Car-Info Point"
    bl_description = "Add the next light / exhaust point of this kind at the 3D cursor"
    bl_options = {"REGISTER", "UNDO"}
    prefix: StringProperty(default="Headlight")

    def execute(self, context):
        root = _car_root(context)
        if root is None:
            self.report({"ERROR"}, "Make a car first (sidebar > GT3 Car > New car)")
            return {"CANCELLED"}
        used = {int(m.group(1)) for o in root.all_objects for m in [re.match(re.escape(self.prefix) + r"_(\d+)", o.name)] if m}
        i = 0
        while i in used:
            i += 1
        coll = next((c for c in root.children_recursive if c.name.startswith("Lights" if "Light" in self.prefix or "Flare" in self.prefix else "Exhausts")), root)
        pos = _to_g(context.scene.cursor.location)
        if self.prefix == "Exhaust":
            e = bpy.data.objects.new(f"Exhaust_{i}", None)
            e.empty_display_type, e.empty_display_size = "SINGLE_ARROW", 0.25
            e.location = context.scene.cursor.location
            coll.objects.link(e)
        else:
            colour = {"Headlight": (1.0, 0.833, 0.5), "BrakeLight": (1.0, 0.0, 0.0), "BrakeFlare": (0.2, 0.0, 0.0)}[self.prefix]
            e = _tpl_light(coll, f"{self.prefix}_{i}", pos, colour, self.prefix != "Headlight" or pos[2] > _REAR_Z, False, "")
        for o in context.selected_objects:
            o.select_set(False)
        e.select_set(True)
        context.view_layer.objects.active = e
        return {"FINISHED"}


# ------------------------------------------------------------------ the sidebar (N) > GT3 Car

class VIEW3D_PT_gt3_car(Panel):
    bl_label = "GT3 Car"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GT3 Car"

    def draw(self, context):
        lay = self.layout
        try:
            root, err = _car_root(context), None
        except RuntimeError as ex:
            root, err = None, str(ex)
        if _db_dir_pref(context) is None:
            row = lay.row()
            row.alert = True
            row.label(text="No database path!", icon="ERROR")
            row.operator("preferences.addon_show", text="", icon="PREFERENCES").module = __package__
        if any(c.hide_viewport for r in _car_roots(context) for c in [r] + list(r.children_recursive)):
            lay.operator(OBJECT_OT_gt3_fix_hidden.bl_idname, icon="HIDE_OFF")
        if err:
            lay.label(text=err, icon="ERROR")
            return
        if root is None:
            lay.label(text="No GT3 car in this scene yet", icon="INFO")
            _draw_new_import(lay, context)
            return
        box = lay.box()
        row = box.row()
        row.label(text=root.get("gt3_car", "?"), icon="AUTO")
        row.prop(root, "gt3_car_type", expand=True)
        box.prop(root, '["gt3_car"]', text="Code")
        objs = [o for o in root.all_objects if o.type == "MESH" and not _hidden(o)]
        rs = [_resolve(o) for o in objs]
        rims = sum(1 for r in rs if r["role"] in ("rim", "rear_wheel"))
        anims = sum(1 for r in rs if r["anim"])
        col = box.column(align=True)
        col.label(text=f"{len(objs)} parts, {rims} wheel part{'s' if rims != 1 else ''}"
                       + (f", {anims} animated" if anims else ""), icon="MESH_DATA")
        if not rims:
            col.label(text="No wheel yet: put the rim in the 'Wheel' collection", icon="ERROR")
        box.operator(EXPORT_OT_gt3_car.bl_idname, text="Export this car...", icon="EXPORT")


def _draw_new_import(lay, context):
    box = lay.box()
    box.label(text="New car", icon="ADD")
    box.prop(context.scene, "gt3_new_code")
    row = box.row(align=True)
    row.operator(OBJECT_OT_gt3_new_car.bl_idname, text="Race car").kind = "RACE"
    row.operator(OBJECT_OT_gt3_new_car.bl_idname, text="Menu car").kind = "MENU"
    lay.operator(IMPORT_OT_gt3_car_editable.bl_idname, text="Import a PD car...", icon="IMPORT")


class VIEW3D_PT_gt3_new(Panel):
    bl_label = "New car / Import"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GT3 Car"
    bl_parent_id = "VIEW3D_PT_gt3_car"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 9

    @classmethod
    def poll(cls, context):
        try:
            return _car_root(context) is not None
        except RuntimeError:
            return True

    def draw(self, context):
        _draw_new_import(self.layout, context)


class VIEW3D_PT_gt3_part(Panel):
    bl_label = "Selected part"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GT3 Car"
    bl_parent_id = "VIEW3D_PT_gt3_car"
    bl_order = 1

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def draw(self, context):
        o = context.object
        lay = self.layout
        if o.type == "EMPTY":
            lay.label(text=o.name + (f"  ({o['gt3_note']})" if "gt3_note" in o else ""), icon="LIGHT")
            if re.match(r"(Headlight|BrakeLight|BrakeFlare)_\d+", o.name):
                f = _light_facing(o)
                lay.label(text=f"Glare shows from the {f.upper()} of the car", icon="LIGHT_SPOT")
                row = lay.row(align=True)
                row.operator(OBJECT_OT_gt3_light_face.bl_idname, text="Face front").face = "FRONT"
                row.operator(OBJECT_OT_gt3_light_face.bl_idname, text="Face rear").face = "REAR"
            if _hidden(o):
                lay.label(text="Hidden - not exported (unhide to use it)", icon="HIDE_ON")
            return
        if o.type != "MESH":
            return
        r = _resolve(o)
        box = lay.box()
        role = {"part": "car part", "rim": "WHEEL", "rear_wheel": "REAR WHEEL", "rim_front": "front brake disc", "rim_rear": "rear brake disc",
                "shadow": "real-time shadow", "ground": "ground shadow", "pool": "headlight beam"}.get(r["role"], r["role"])
        box.label(text=f"{o.name}: {role}", icon="OBJECT_DATA")
        if r.get("coll"):
            box.label(text=f"from its collection '{r['coll']}'", icon="OUTLINER_COLLECTION")
        refl = " + reflection" if r["reflect"] else ""
        box.label(text=f"{r['kind']}{refl}{'  glass stamp' if r['stamp'] else ''}{'  2-sided' if r['double'] else ''}"
                       f"{'  unlit' if not r['lit'] else ''}{('  tail lamp ' + r['lamp']) if r['lamp'] else ''}")
        if _hidden(o):
            box.label(text="Hidden - NOT exported", icon="HIDE_ON")
        if r["role"] in ("rim", "rear_wheel"):
            box.label(text="Model it where it sits - the export makes it PD's unit wheel", icon="INFO")
            if r["role"] == "rim":
                box.label(text="All four corners, unless a Rear Wheel is there")
            if _car_type(_car_root(context)) == "RACE":
                if o.get("gt3_external_tex") == 1:
                    box.label(text="SPINNING FACE: still + motion-blur pictures", icon="FORCE_VORTEX")
                    box.label(text="are made from its texture; brakes show through")
                    if not _has_picture(o):
                        box.alert = True
                        box.label(text="No picture: exported as a plain part", icon="ERROR")
                    box.operator(OBJECT_OT_gt3_rim_face.bl_idname, text="Make plain part").on = False
                else:
                    box.operator(OBJECT_OT_gt3_rim_face.bl_idname, text="Make spinning face (PD blur)",
                                 icon="FORCE_VORTEX").on = True
        if r["role"] in ("rim_front", "rim_rear") and _car_type(_car_root(context)) == "RACE":
            box.label(text="Race car: the game draws brakes as flat pictures -", icon="INFO")
            box.label(text="this object's TEXTURE becomes the brake picture")
        _draw_anim(lay, o, r)
        if o.active_material is not None:
            lay.prop(o.active_material, "gt3_is_paint", text=f"'{o.active_material.name}' is car paint")


class VIEW3D_PT_gt3_colours(Panel):
    bl_label = "Paint colours"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GT3 Car"
    bl_parent_id = "VIEW3D_PT_gt3_car"
    bl_order = 2

    def draw(self, context):
        lay = self.layout
        owner = _paint_owner(context)
        n = len(owner.gt3_paints)
        if n == 0:
            lay.label(text="No list: the car keeps the game's colours,", icon="INFO")
            lay.label(text="all showing your own textures. + to start one.")
        row = lay.row()
        row.template_list("GT3_UL_paints", "", owner, "gt3_paints", owner, "gt3_paint_active", rows=3)
        side = row.column(align=True)
        side.operator(OBJECT_OT_gt3_paint_add.bl_idname, text="", icon="ADD")
        side.operator(OBJECT_OT_gt3_paint_remove.bl_idname, text="", icon="REMOVE")
        if n == 0:
            lay.operator(OBJECT_OT_gt3_paint_from_game.bl_idname, text="Copy a game car's list...", icon="IMPORT")
            return
        k = max(0, min(owner.gt3_paint_active, n - 1))
        p = owner.gt3_paints[k]
        box = lay.box()
        box.label(text=f"Colour {k}" + ("  = your textures as they are" if k == 0 else "  (showing in the viewport)"), icon="COLOR")
        row = box.row(align=True)
        row.prop(p, "chip", text="")
        row.prop(p, "name", text="")
        box.prop(p, "finish")
        mats = [m for m in _car_materials(owner) if m.gt3_is_paint]
        if not mats:
            box.label(text="No paint material yet: select the body and", icon="ERROR")
            box.label(text="tick 'is car paint' under Selected part")
        for m in mats:
            row = box.row()
            split = row.split(factor=0.4)
            split.label(text=m.name)
            if k == 0:
                img = _base_image(m, owner)
                split.label(text=img.name if img else "(no texture)")
            elif k < len(m.gt3_paint_tex):
                split.template_ID(m.gt3_paint_tex[k], "image", open="image.open")
            else:
                op = split.operator(OBJECT_OT_gt3_paint_slot.bl_idname, text="+ texture", icon="ADD")
                op.material = m.name
        if n > 1:
            lay.operator(OBJECT_OT_gt3_paint_generate.bl_idname, text="Make colour textures from the chips", icon="BRUSH_DATA")
        lay.operator(OBJECT_OT_gt3_paint_from_game.bl_idname, text="Copy a game car's list...", icon="IMPORT")


class OBJECT_OT_gt3_brakes_edit(Operator):
    bl_idname = "object.gt3_brakes_edit"
    bl_label = "Edit Brakes"
    bl_description = ("Show this car's two brakes (front, rear) as editable numbers - taken from the car as it came "
                      "(or its template) - and let you give them your own disc / caliper art")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        root = _car_root(context)
        if root is None:
            self.report({"ERROR"}, "No GT3 car here")
            return {"CANCELLED"}
        _init_brake_ui(root)
        return {"FINISHED"}


def _init_brake_ui(root):
    src = _car_brakes_json(root) or []
    root.gt3_brake_ui.clear()
    for k in range(2):
        it = root.gt3_brake_ui.add()
        it.name = ("Front", "Rear")[k]
        b = src[k] if k < len(src) else None
        if b:
            for attr, key in _BRAKE_KEYS:
                setattr(it, attr, b[key])


def _brakes_for_export(root):
    """The Brakes box as GTCI BrakeParameters (None = the box was never opened: the car's own values stay as they are)."""
    ui = list(root.gt3_brake_ui) if root is not None else []
    if len(ui) != 2:
        return None
    return [{"caliper_texture": it.caliper, "disc_texture": it.disc, "size": it.size,
             "offset_from_center": it.offset, "orientation_deg": it.angle} for it in ui]


def _brake_mesh_art(root, notes):
    """RACE car: the game draws brakes itself, as flat pictures (disc + caliper) from data/race/brake.bin - it has no
    brake meshes. A mesh in 'Brake Front' / 'Brake Rear' (or named _BrakeFront / _BrakeRear) gives its TEXTURE as that
    axle's picture ('caliper' in its name = the caliper picture); the mesh itself is not used."""
    if root is None or _car_type(root) != "RACE":
        return
    found = []
    for o in root.all_objects:
        if o.type != "MESH" or _hidden(o):
            continue
        role = _resolve(o)["role"]
        if role not in ("rim_front", "rim_rear"):
            continue
        img = next((_base_image(sl.material, root) for sl in o.material_slots if sl.material is not None
                    and _base_image(sl.material, root) is not None), None)
        if img is None:
            notes.append(f"'{o.name}' (brake) has no texture - ignored")
            continue
        found.append((0 if role == "rim_front" else 1, "caliper" if "caliper" in o.name.lower() else "disc", img, o.name))
    if not found:
        return
    if len(root.gt3_brake_ui) != 2:
        _init_brake_ui(root)
    for k, kind, img, name in found:
        it = root.gt3_brake_ui[k]
        if getattr(it, kind + "_image") is None:
            setattr(it, kind + "_image", img)
            notes.append(f"race brakes are pictures: '{name}' gave its texture as the {it.name.lower()} {kind}")


def _apply_custom_brakes(root, data, work, notes):
    """Own disc / caliper art -> data/race/brake.bin (added once, reused on later exports) -> the numbers in the box."""
    _brake_mesh_art(root, notes)
    ui = list(root.gt3_brake_ui) if root is not None else []
    want = [(it, kind, getattr(it, kind + "_image")) for it in ui for kind in ("disc", "caliper") if getattr(it, kind + "_image")]
    if not want:
        return
    bb = os.path.join(data, "race", "brake.bin") if data else None
    if not bb or not os.path.exists(bb):
        notes.append("WARNING own brake pictures skipped: they go into data/race/brake.bin - export into the game's data "
                     "folder" + (f" ({bb} is missing)" if bb else ""))
        return
    from .gt3car.brakes import add_to_file
    from .gt3car.imgio import read_image
    imgs = {kind: [read_image(_image_path(img, work, f"{it.name} {kind}")) for it, k2, img in want if k2 == kind]
            for kind in ("disc", "caliper")}
    ds, cs = add_to_file(bb, imgs["disc"], imgs["caliper"])
    it_d, it_c = iter(ds), iter(cs)
    for it, kind, _ in want:
        setattr(it, kind, next(it_d) if kind == "disc" else next(it_c))
    notes.append("brakes: " + ", ".join(f"{it.name.lower()} {kind} {getattr(it, kind)}" for it, kind, _ in want)
                 + " in data/race/brake.bin")


class VIEW3D_PT_gt3_info(Panel):
    bl_label = "Car info (lights, exhausts, cameras)"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GT3 Car"
    bl_parent_id = "VIEW3D_PT_gt3_car"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 4

    def draw(self, context):
        lay = self.layout
        try:
            root = _car_root(context)
        except RuntimeError:
            root = None
        objs = [o for o in (root.all_objects if root is not None else context.scene.objects) if o.type == "EMPTY"]
        for prefix, label in (("Headlight", "Night glows (headlights, fogs, logos)"), ("BrakeLight", "Brake lights"),
                              ("BrakeFlare", "Brake flares"), ("Exhaust", "Exhausts"), ("Spark", "Sparks"), ("Cam_", "Cameras")):
            live = sum(1 for o in objs if o.name.startswith(prefix) and not _hidden(o))
            hid = sum(1 for o in objs if o.name.startswith(prefix) and _hidden(o))
            row = lay.row()
            row.label(text=f"{label}: {live}" + (f"  (+{hid} hidden)" if hid else ""))
            if prefix in ("Headlight", "BrakeLight", "BrakeFlare", "Exhaust"):
                op = row.operator(OBJECT_OT_gt3_add_point.bl_idname, text="", icon="ADD")
                op.prefix = prefix
        lay.label(text="Adds at the 3D cursor. Hidden points are not exported.", icon="INFO")
        lay.label(text="Select a light to see / set which way it faces.")


class VIEW3D_PT_gt3_wheels(Panel):
    bl_label = "Brakes & tyres"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GT3 Car"
    bl_parent_id = "VIEW3D_PT_gt3_car"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 3

    @classmethod
    def poll(cls, context):
        try:
            return _car_root(context) is not None
        except RuntimeError:
            return False

    def draw(self, context):
        lay = self.layout
        root = _car_root(context)
        box = lay.box()
        box.label(text="Tyres (all four)", icon="MESH_TORUS")
        box.template_ID(root, "gt3_tyre_image", open="image.open")
        box.label(text="128x64: TOP half = tread, BOTTOM half = sidewall")
        if root.gt3_tyre_image is None:
            box.label(text=("Empty = the imported car's own tyre" if root.get("gt3_blob_tire_file")
                            else "Empty = a plain tyre in PD's layout"), icon="INFO")
        box.operator(OBJECT_OT_gt3_tyre_get.bl_idname, icon="IMAGE_DATA")
        box = lay.box()
        box.label(text="Brakes (pictures from data/race/brake.bin)", icon="SHADING_TEXTURE")
        if _car_type(root) == "MENU":
            box.label(text="Menu car: its brake discs are MESHES -", icon="INFO")
            box.label(text="put them in 'Brake Front' / 'Brake Rear'")
            return
        box.label(text="Race car: the game draws each brake as a flat")
        box.label(text="disc + caliper picture (no meshes)")
        if len(root.gt3_brake_ui) != 2:
            src = _car_brakes_json(root)
            if src:
                for k, bb in enumerate(src[:2]):
                    box.label(text=f"{('Front', 'Rear')[k]}: disc {bb['BrakeDiscTextureIndex']}, caliper "
                                   f"{bb['BrakeCaliperTextureIndex']}")
            box.operator(OBJECT_OT_gt3_brakes_edit.bl_idname, icon="GREASEPENCIL")
            return
        for it in root.gt3_brake_ui:
            col = box.column(align=True)
            col.label(text=it.name, icon="DOT")
            row = col.row(align=True)
            row.prop(it, "disc")
            row.prop(it, "caliper")
            row = col.row(align=True)
            row.prop(it, "size")
            row.prop(it, "offset")
            row.prop(it, "angle")
            col.prop(it, "disc_image")
            col.prop(it, "caliper_image")
        box.label(text="Caliper 1 = none. Own pictures (or a textured mesh", icon="INFO")
        box.label(text="in 'Brake Front'/'Rear'): export into the game folder")


def _menu_import(self, context):
    self.layout.operator(IMPORT_OT_gt3_car_editable.bl_idname, text="GT3 Car (editable)")


def _menu_export(self, context):
    self.layout.operator(EXPORT_OT_gt3_car.bl_idname, text="GT3 Car")


def _menu_add(self, context):
    self.layout.operator(OBJECT_OT_gt3_new_car.bl_idname, text="New GT3 Car", icon="AUTO").kind = "RACE"


_classes = (GT3CarMakerPreferences, OBJECT_PT_gt3_car, MATERIAL_PT_gt3_car, GT3_UL_paints,
            OBJECT_OT_gt3_paint_add, OBJECT_OT_gt3_paint_remove, OBJECT_OT_gt3_paint_generate, OBJECT_OT_gt3_paint_from_game,
            OBJECT_OT_gt3_paint_slot, OBJECT_OT_gt3_add_morph, OBJECT_OT_gt3_add_point, IMPORT_OT_gt3_car_editable,
            EXPORT_OT_gt3_car, OBJECT_OT_gt3_new_car, OBJECT_OT_gt3_brakes_edit, OBJECT_OT_gt3_fix_hidden,
            OBJECT_OT_gt3_light_face, OBJECT_OT_gt3_rim_face,
            OBJECT_OT_gt3_tyre_get,
            VIEW3D_PT_gt3_car, VIEW3D_PT_gt3_part, VIEW3D_PT_gt3_colours, VIEW3D_PT_gt3_wheels, VIEW3D_PT_gt3_info,
            VIEW3D_PT_gt3_new)
_groups = (GT3CarPaint, GT3PaintTexture, GT3Brake)


def register():
    for c in _groups:
        bpy.utils.register_class(c)
    _obj_props()
    _mat_props()
    _paint_props()
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_file_import.append(_menu_import)
    bpy.types.TOPBAR_MT_file_export.append(_menu_export)
    bpy.types.VIEW3D_MT_add.append(_menu_add)


def unregister():
    bpy.types.VIEW3D_MT_add.remove(_menu_add)
    bpy.types.TOPBAR_MT_file_export.remove(_menu_export)
    bpy.types.TOPBAR_MT_file_import.remove(_menu_import)
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
    _del_paint_props()
    for c in reversed(_groups):
        bpy.utils.unregister_class(c)

# Nenkai's research is the core of this human & machine made tool.
