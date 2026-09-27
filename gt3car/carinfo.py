"""GT3 car container parts: GTCI car info (PDTools CarInfo, JSON form = its AsJson), GTTR tyre, GTTW wheel,
and the PD-layout container."""

import math
import struct

from .binio import Reader, Writer, align_up
from .f32 import f32, f32_bits
from .modelset import ModelSet1

GTCI_MAGIC = 0x49435447
GTTR_MAGIC = 0x52545447
GTTW_MAGIC = 0x57545447

CAMERA_NAMES = ["DEFAULT", "CHASE", "UNK_2", "MIRROR_L", "MIRROR_R", "NOSE", "BONNET", "ROOF", "BACK", "TAIL",
                "SIDE_L", "SIDE_R", "FENDER_L", "FENDER_R", "WHEEL_FL", "WHEEL_FR", "WHEEL_RL", "WHEEL_RR",
                "OPTION_1", "OPTION_2"]
TIRE_KEYS = ["NormalTire", "SportsTire", "RacingHardTire", "RacingMediumTire", "RacingSoftTire",
             "RacingSuperSoftTire", "Control_SimulationTire", "DirtTire"]


def jfloat(v):
    """A JSON number field as a float32 value (accepts the "NaN"/"Infinity" strings the C# side writes)."""
    if isinstance(v, str):
        return {"NaN": math.nan, "Infinity": math.inf, "-Infinity": -math.inf}[v]
    return f32(float(v))


def _vec(d, keys):
    d = d or {}
    return [jfloat(d.get(k, 0.0)) for k in keys]


# ---- GTCI ------------------------------------------------------------------------------------------
def carinfo_read(data):
    """GTCI bytes -> the PDTools CarInfo JSON dict."""
    r = Reader(data)
    if r.u32() != GTCI_MAGIC:
        raise ValueError("Not a car info header.")
    r.u32()
    r.u32()
    size = r.u32()
    if len(data) < size:
        raise ValueError("Car info size is smaller than remaining stream length.")
    r.u32()
    brakes = []
    for _ in range(2):
        a, b = r.u32(), r.u32()
        c, d, e = r.f32(), r.f32(), r.f32()
        brakes.append({"BrakeCaliperTextureIndex": a, "BrakeDiscTextureIndex": b, "BrakeTextureSize": c,
                       "BrakeOffsetFromCenter": d, "BrakeTextureOrientationDeg": e})
    info = {"BrakeParameters": brakes, "DefaultTireIndex": r.u32()}
    counts = r.many("I", 13)
    fl_n, fl_o, nb_n, nb_o, nf_n, nf_o, cp_n, cp_o, ex_n, ex_o, cam_n, cam_o, tire_o = counts

    def light(off):
        r.pos = off
        ud = r.many("f", 8)
        f = r.many("f", 18)
        unk = r.u32()
        return {"UnkData": ud, "Intensities": dict(zip("XYZW", f[0:4])), "Position": dict(zip("XYZW", f[4:8])),
                "UnkMin": f[8], "UnkMax": f[9], "UnkVec": dict(zip("XYZW", f[10:14])),
                "FlareColor": dict(zip("RGBA", f[14:18])), "Unk": unk}

    info["FrontLights"] = [light(fl_o + i * 0x80) for i in range(fl_n)]
    info["NightBrakeLights"] = [light(nb_o + i * 0x80) for i in range(nb_n)]
    info["NightBrakeLightFlares"] = [light(nf_o + i * 0x80) for i in range(nf_n)]
    cps = []
    for i in range(cp_n):
        r.pos = cp_o + i * 0x10
        cps.append({"Position": dict(zip("XYZ", r.many("f", 3)))})
    info["CollisionParticles"] = cps
    exs = []
    for i in range(ex_n):
        r.pos = ex_o + i * 0x20
        exs.append({"Position": dict(zip("XYZ", r.many("f", 3)))})
    info["Exhausts"] = exs
    cams = []
    for i in range(cam_n):
        r.pos = cam_o + i * 0x40
        p = r.many("f", 3)
        pi, ya, ro, fov = r.many("f", 4)
        cams.append({"Position": dict(zip("XYZ", p)), "Pitch": pi, "Yaw": ya, "Roll": ro, "FoV": fov, "Unk": r.u32()})
    if 1 <= len(cams) <= 3:
        raise ValueError("PDTools cannot express %d onboard cameras in JSON" % len(cams))
    info["OnboardCameras"] = {n: (cams[i] if i < len(cams) else None) for i, n in enumerate(CAMERA_NAMES)}
    tires = {"FrontTires": dict.fromkeys(TIRE_KEYS, 0), "RearTires": dict.fromkeys(TIRE_KEYS, 0)}
    if tire_o != 0:
        r.pos = tire_o
        tires["FrontTires"] = dict(zip(TIRE_KEYS, r.many("I", 8)))
        tires["RearTires"] = dict(zip(TIRE_KEYS, r.many("I", 8)))
    info["Tires"] = tires
    return info


def _cameras(oc):
    """OnboardCameras JSON -> list (SetCamera semantics: a later slot pads the list with zero cameras)."""
    cams = []
    for i, n in enumerate(CAMERA_NAMES):
        v = (oc or {}).get(n)
        if v is None:
            continue
        while len(cams) < i + 1:
            cams.append({})
        cams[i] = v
    return cams


def carinfo_write(info):
    """CarInfo.Write + the builder's SetLength(max(len, size field)): PD's size = data + 0x40."""
    w = Writer()
    w.pos = 0x94

    def light(l):
        for v in (l.get("UnkData") or [0.0] * 8):
            w.f32bits(f32_bits(jfloat(v)))
        for key, comps in (("Intensities", "XYZW"), ("Position", "XYZW")):
            for v in _vec(l.get(key), comps):
                w.f32bits(f32_bits(v))
        w.f32bits(f32_bits(jfloat(l.get("UnkMin", 0.0))))
        w.f32bits(f32_bits(jfloat(l.get("UnkMax", 0.0))))
        for v in _vec(l.get("UnkVec"), "XYZW") + _vec(l.get("FlareColor"), "RGBA"):
            w.f32bits(f32_bits(v))
        w.u32(int(l.get("Unk", 0)))
        w.pos += 0x14

    offs = []
    for key in ("FrontLights", "NightBrakeLights", "NightBrakeLightFlares"):
        offs.append(w.pos)
        for l in info.get(key) or []:
            light(l)
    cp_o = w.pos
    for c in info.get("CollisionParticles") or []:
        for v in _vec(c.get("Position"), "XYZ"):
            w.f32bits(f32_bits(v))
        w.pos += 4
    ex_o = w.pos
    for e in info.get("Exhausts") or []:
        for v in _vec(e.get("Position"), "XYZ"):
            w.f32bits(f32_bits(v))
        w.pos += 0x14
    cam_o = w.pos
    cams = _cameras(info.get("OnboardCameras"))
    for c in cams:
        for v in _vec(c.get("Position"), "XYZ") + [jfloat(c.get(k, 0.0)) for k in ("Pitch", "Yaw", "Roll", "FoV")]:
            w.f32bits(f32_bits(v))
        w.u32(int(c.get("Unk", 0)))
        w.pos += 0x20
    tire_o = w.pos
    tires = info.get("Tires") or {}
    for side in ("FrontTires", "RearTires"):
        t = tires.get(side) or {}
        for k in TIRE_KEYS:
            w.u32(int(t.get(k, 0)))
    last = w.pos
    w.pos = 0
    w.u32(GTCI_MAGIC)
    w.u32(0)
    w.u32(0)
    w.u32(last + 0x40)
    w.u32(1)
    brakes = info.get("BrakeParameters") or [{}, {}]
    for i in range(2):
        b = brakes[i] if i < len(brakes) else {}
        w.u32(int(b.get("BrakeCaliperTextureIndex", 0)))
        w.u32(int(b.get("BrakeDiscTextureIndex", 0)))
        for k in ("BrakeTextureSize", "BrakeOffsetFromCenter", "BrakeTextureOrientationDeg"):
            w.f32bits(f32_bits(jfloat(b.get(k, 0.0))))
    w.u32(int(info.get("DefaultTireIndex", 0)))
    for key, o in zip(("FrontLights", "NightBrakeLights", "NightBrakeLightFlares"), offs):
        w.u32(len(info.get(key) or []))
        w.u32(o)
    w.u32(len(info.get("CollisionParticles") or []))
    w.u32(cp_o)
    w.u32(len(info.get("Exhausts") or []))
    w.u32(ex_o)
    w.u32(len(cams))
    w.u32(cam_o)
    w.u32(tire_o)
    w.set_length(max(len(w), last + 0x40))
    return w.getvalue()


# ---- GTTR / GTTW -------------------------------------------------------------------------------------
def tire_write(unk_tristrip, tristrip_flags, unk3, texset):
    w = Writer()
    w.pos = 0x20
    texset.serialize(w)
    last = w.pos
    w.pos = 0
    w.u32(GTTR_MAGIC)
    w.u32(0)
    w.u32(0)
    w.u32(last)
    w.u32(unk_tristrip)
    w.u32(tristrip_flags)
    w.f32bits(f32_bits(unk3))
    w.u32(0x20)
    w.pos = last
    return w.getvalue()


def wheel_write(modelset, unk_flags=0):
    w = Writer()
    w.pos = 0x20
    modelset.serialize(w)
    last = w.pos
    w.pos = 0
    w.u32(GTTW_MAGIC)
    w.u32(0)
    w.u32(0)
    w.u32(last)
    w.u32(unk_flags)
    w.pos = 0x1C
    w.u32(0x20)
    w.pos = last
    return w.getvalue()


# ---- container -----------------------------------------------------------------------------------------
class CarFileParts:
    """One PD-layout car container, split (Gt3CarDecompile.CarFileParts)."""

    def __init__(self, data):
        if len(data) < 0x40 or struct.unpack_from("<I", data, 0)[0] != 0:
            raise ValueError("not a GT3 car container")
        _, ci, bo, to, wo, dm = struct.unpack_from("<6i", data, 0)
        self.gtci, self.body, self.gttr, self.gttw = data[ci:bo], data[bo:to], data[to:wo], data[wo:dm]
        self.body_set = ModelSet1.read(self.body)
        self.rim_set = ModelSet1.read(data[wo + 0x20:dm])

    @classmethod
    def load(cls, path):
        with open(path, "rb") as f:
            return cls(f.read())


def container(gtci, body, gttr, gttw):
    w = Writer()
    w.write(bytes(0x40))
    ci = w.pos
    w.write(gtci)
    w.set_length(align_up(0x40 + len(gtci), 0x40))
    w.pos = len(w)
    bo = w.pos
    w.write(body)
    w.align(0x40)
    to = w.pos
    w.write(gttr)
    w.align(0x40)
    wo = w.pos
    w.write(gttw)
    w.align(0x40)
    dm = w.pos
    w.write(b"dummy" + bytes(0x40 - 5))
    b = bytearray(w.getvalue())
    struct.pack_into("<6I", b, 0, 0, ci, bo, to, wo, dm)
    return bytes(b)

# Nenkai's research is the core of this human & machine made tool.
