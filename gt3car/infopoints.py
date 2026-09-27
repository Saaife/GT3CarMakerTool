"""The car info from authored points, and the two shadows generated from the body (GT4TrackKit Gt3CarInfo.cs).
Car info is handled in PDTools' CarInfo JSON form (dicts), float32 throughout."""

import copy
import math

from .f32 import f32, fma, bits_f32, v_add, v_sub, v_scale, v_dot, v_cross, sqrt as fsqrt, div as fdiv
from .carinfo import CAMERA_NAMES, TIRE_KEYS, jfloat

# PD's per-slot camera values (stored form: position negated) - x, y, z, pitch, yaw, fov, type
_CAMS = [
    (0, -0.91, -0.13, 0, 0, 1.4352127, 13), (0, -1.5, -6, 0, 0, 1.4352127, 10),
    (0, -1.5, -6, 0, 0, 1.4352127, 0), (0.83, -0.78, 0.40, -9, 191, 1.4352127, 15),
    (-0.83, -0.78, 0.40, -9, -191, 1.4352127, 15), (0, -0.15, 1.89, 0, 0, 1.6899495, 13),
    (-0.89, -1.25, 3.68, 0, 202, 1.5011548, 3), (0, -1.75, -0.71, 28, 0, 1.2124356, 7),
    (0, -1.68, 0.17, 8, 180, 1.5722257, 7), (0, -0.15, -1.87, 0, 180, 1.6899495, 13),
    (0, -0.91, -0.13, 2.96, -90, 1.2124356, 13), (0, -0.91, -0.13, 2.96, 90, 1.2124356, 13),
    (0.76, -0.73, -3.70, 0, 12, 1.2124356, 3), (1.09, -1.59, 4.04, 0, 147, 1.2124356, 3),
    (1.29, -0.15, 0.18, -5, 19, 1.3446875, 3), (-1.29, -0.15, 0.18, -5, -19, 1.3446875, 3),
    (1.30, -0.15, 0, -8, 163, 1.3446875, 3), (-1.30, -0.15, 0, -8, -163, 1.3446875, 3),
    (1.96, -1.76, -3.80, 10, 25, 1.2124356, 3), (1.01, -1.80, 3.35, 25, -211, 1.2124356, 3),
]


def _default_cameras():
    return [{"Position": {"X": f32(x), "Y": f32(y), "Z": f32(z)}, "Pitch": f32(p), "Yaw": f32(yw), "Roll": 0.0,
             "FoV": f32(fov), "Unk": t} for x, y, z, p, yw, fov, t in _CAMS]


def _tires(v):
    return dict(zip(TIRE_KEYS, v))


def info_defaults():
    """PD's modal GTCI (no lights, no exhausts: those come from the author's points)."""
    return {
        "BrakeParameters": [
            {"BrakeCaliperTextureIndex": 14, "BrakeDiscTextureIndex": 2, "BrakeTextureSize": f32(0.14),
             "BrakeOffsetFromCenter": f32(-0.06), "BrakeTextureOrientationDeg": -22.0},
            {"BrakeCaliperTextureIndex": 14, "BrakeDiscTextureIndex": 2, "BrakeTextureSize": f32(0.14),
             "BrakeOffsetFromCenter": f32(-0.06), "BrakeTextureOrientationDeg": -156.0}],
        "DefaultTireIndex": 1, "FrontLights": [], "NightBrakeLights": [], "NightBrakeLightFlares": [],
        "CollisionParticles": [], "Exhausts": [],
        "OnboardCameras": dict(zip(CAMERA_NAMES, _default_cameras())),
        "Tires": {"FrontTires": _tires([1, 2, 3, 3, 3, 3, 1, 5]), "RearTires": _tires([1, 2, 4, 4, 4, 4, 1, 5])},
    }


def _v(d, comps):
    d = d or {}
    return [jfloat(d.get(k, 0.0)) for k in comps]


def _light_norm(l):
    ud = l.get("UnkData")
    return {"UnkData": [jfloat(x) for x in ud] if ud is not None else [0.0] * 8,
            "Intensities": dict(zip("XYZW", _v(l.get("Intensities"), "XYZW"))),
            "Position": dict(zip("XYZW", _v(l.get("Position"), "XYZW"))),
            "UnkMin": jfloat(l.get("UnkMin", 0.0)), "UnkMax": jfloat(l.get("UnkMax", 0.0)),
            "UnkVec": dict(zip("XYZW", _v(l.get("UnkVec"), "XYZW"))),
            "FlareColor": dict(zip("RGBA", _v(l.get("FlareColor"), "RGBA"))), "Unk": int(l.get("Unk", 0))}


def _default_light(kind):
    rear = kind != 0
    fc = {0: (1.0, f32(0.8333334), 0.5, bits_f32(16)), 1: (1.0, 0.0, 0.0, bits_f32(4))}.get(kind, (f32(0.2), 0.0, 0.0, bits_f32(4)))
    l = {"UnkData": [0.0] * 8, "Intensities": {"X": 0.0, "Y": 0.0, "Z": 0.0, "W": 0.0},
         "Position": {"X": 0.0, "Y": 0.0, "Z": 0.0, "W": 0.0}, "UnkMin": 1.0, "UnkMax": 1.0,
         "UnkVec": {"X": 1.0, "Y": 0.0, "Z": 0.0, "W": 0.0}, "FlareColor": dict(zip("RGBA", fc)), "Unk": 1}
    _set_rows(l, [[-1, 0, 0], [0, 1, 0], [0, 0, -1]] if rear else [[1, 0, 0], [0, 1, 0], [0, 0, 1]])
    return l


def _set_rows(l, r):
    u = l["UnkData"] if l.get("UnkData") is not None and len(l["UnkData"]) == 8 else [0.0] * 8
    r = [[jfloat(x) for x in row] for row in r]
    l["UnkData"] = [r[0][0], r[0][1], r[0][2], u[3], r[1][0], r[1][1], r[1][2], u[7]]
    w = l["Intensities"]["W"]
    l["Intensities"] = {"X": r[2][0], "Y": r[2][1], "Z": r[2][2], "W": w}


def _apply_lights(old, pts, kind):
    if pts is None:
        return old
    defk = f32(-0.2) if kind == 0 else 0.0
    res = []
    for i, p in enumerate(pts):
        l = copy.deepcopy(_light_norm(old[i])) if i < len(old) else _default_light(kind)
        it, uv, ps = l["Intensities"], l["UnkVec"], l["Position"]
        axis = (it["X"], it["Y"], it["Z"])
        if p.get("flare_offset") is not None:
            k = jfloat(p["flare_offset"])
        elif i < len(old):
            k = v_dot(v_sub((uv["Y"], uv["Z"], uv["W"]), (ps["X"], ps["Y"], ps["Z"])), axis)
        else:
            k = defk
        rows = p.get("rows")
        if rows is not None and len(rows) == 3:
            _set_rows(l, rows)
            axis = (jfloat(rows[2][0]), jfloat(rows[2][1]), jfloat(rows[2][2]))
        pp = p.get("pos")
        pos = (jfloat(pp[0]), jfloat(pp[1]), jfloat(pp[2])) if pp is not None and len(pp) >= 3 else (ps["X"], ps["Y"], ps["Z"])
        l["Position"] = {"X": pos[0], "Y": pos[1], "Z": pos[2], "W": 1.0}
        old_min, vec_x = l["UnkMin"], l["UnkVec"]["X"]
        if p.get("size") is not None:
            s = jfloat(p["size"])
            if s != old_min:
                if l["UnkMax"] == old_min:
                    l["UnkMax"] = s
                if vec_x == old_min:
                    vec_x = s
                l["UnkMin"] = s
        fp = p.get("flare")
        flare = (jfloat(fp[0]), jfloat(fp[1]), jfloat(fp[2])) if fp is not None and len(fp) >= 3 else v_add(pos, v_scale(axis, k))
        l["UnkVec"] = {"X": vec_x, "Y": flare[0], "Z": flare[1], "W": flare[2]}
        col, cb = p.get("color"), p.get("color_bits")
        fc = l["FlareColor"]
        if col is not None and len(col) >= 3:
            l["FlareColor"] = {"R": jfloat(col[0]), "G": jfloat(col[1]), "B": jfloat(col[2]),
                               "A": bits_f32(int(cb)) if cb is not None else fc["A"]}
        elif cb is not None:
            l["FlareColor"] = {"R": fc["R"], "G": fc["G"], "B": fc["B"], "A": bits_f32(int(cb))}
        res.append(l)
    return res


def apply_info_points(ci, ip):
    """CarApplyInfoPoints on a CarInfo JSON dict (modified in place)."""
    ci["FrontLights"] = _apply_lights(ci.get("FrontLights") or [], ip.get("headlights"), 0)
    ci["NightBrakeLights"] = _apply_lights(ci.get("NightBrakeLights") or [], ip.get("brakelights"), 1)
    ci["NightBrakeLightFlares"] = _apply_lights(ci.get("NightBrakeLightFlares") or [], ip.get("brakeflares"), 2)
    if ip.get("exhausts") is not None:
        ci["Exhausts"] = [{"Position": dict(zip("XYZ", (jfloat(e[0]), jfloat(e[1]), jfloat(e[2]))))} for e in ip["exhausts"]]
    if ip.get("sparks") is not None:
        ci["CollisionParticles"] = [{"Position": dict(zip("XYZ", (jfloat(e[0]), jfloat(e[1]), jfloat(e[2]))))} for e in ip["sparks"]]
    cams_in = ip.get("cameras")
    if cams_in:
        from .carinfo import _cameras
        cams = [copy.deepcopy(c) for c in _cameras(ci.get("OnboardCameras"))]
        if not cams:
            cams = _default_cameras()
        for c in cams_in:
            slot = int(c.get("slot", 0))
            if slot < 0 or slot >= len(cams):
                continue
            d = cams[slot]
            sp, p = c.get("stored_pos"), c.get("pos")
            if sp is not None and len(sp) >= 3:
                d["Position"] = {"X": jfloat(sp[0]), "Y": jfloat(sp[1]), "Z": jfloat(sp[2])}
            elif p is not None and len(p) >= 3:
                d["Position"] = {"X": -jfloat(p[0]), "Y": -jfloat(p[1]), "Z": -jfloat(p[2])}
            for key, name in (("pitch", "Pitch"), ("yaw", "Yaw"), ("roll", "Roll"), ("fov", "FoV")):
                if c.get(key) is not None:
                    d[name] = jfloat(c[key])
            if c.get("type") is not None:
                d["Unk"] = int(c["type"])
        ci["OnboardCameras"] = {n: (cams[i] if i < len(cams) else None) for i, n in enumerate(CAMERA_NAMES)}
    br = ip.get("brakes")
    if br is not None and len(br) == 2:
        ci["BrakeParameters"] = [{"BrakeCaliperTextureIndex": int(b.get("caliper_texture", 0)),
                                  "BrakeDiscTextureIndex": int(b.get("disc_texture", 0)),
                                  "BrakeTextureSize": jfloat(b.get("size", 0.0)),
                                  "BrakeOffsetFromCenter": jfloat(b.get("offset_from_center", 0.0)),
                                  "BrakeTextureOrientationDeg": jfloat(b.get("orientation_deg", 0.0))} for b in br]
    if ip.get("default_tire_index") is not None:
        ci["DefaultTireIndex"] = int(ip["default_tire_index"])
    tires = ci.setdefault("Tires", {})
    if ip.get("front_tires") is not None and len(ip["front_tires"]) == 8:
        tires["FrontTires"] = _tires([int(x) for x in ip["front_tires"]])
    if ip.get("rear_tires") is not None and len(ip["rear_tires"]) == 8:
        tires["RearTires"] = _tires([int(x) for x in ip["rear_tires"]])


# ---- auto shadows ---------------------------------------------------------------------------------------
def body_points(draws):
    pts = []

    def walk(ds):
        for d in ds or []:
            if d is None:
                continue
            m = d.get("mesh")
            if m is not None and m.get("vertices") is not None:
                for v in m["vertices"]:
                    pts.append((f32(float(v[0])), f32(float(v[1])), f32(float(v[2]))))
            walk(d.get("lamp_off"))
            walk(d.get("lamp_on"))
    walk(draws)
    return pts


def _cr(o, a, b):
    return f32(f32(f32(a[0] - o[0]) * f32(b[1] - o[1])) - f32(f32(a[1] - o[1]) * f32(b[0] - o[0])))


def _dist(a, b):
    dx, dy = f32(a[0] - b[0]), f32(a[1] - b[1])
    return fsqrt(f32(f32(dx * dx) + f32(dy * dy)))


def _lerp(a, b, t):
    # .NET 10 Vector2.Lerp = MultiplyAddEstimate(x, 1 - amount, y * amount): a FUSED multiply-add on x64
    om = f32(1.0 - t)
    return (fma(a[0], om, f32(b[0] * t)), fma(a[1], om, f32(b[1] * t)))


def footprint(pts, n):
    seen = {}
    for p in pts:
        seen.setdefault((p[0], p[2]), (p[0], p[2]))
    p2 = sorted(seen.values(), key=lambda q: (q[0], q[1]))
    if len(p2) < 3:
        return []
    hull = []
    for pas in (p2, list(reversed(p2))):
        start = len(hull)
        for p in pas:
            while len(hull) >= start + 2 and _cr(hull[-2], hull[-1], p) <= 0:
                hull.pop()
            hull.append(p)
        hull.pop()
    cx = f32(f32(min(q[0] for q in p2) + max(q[0] for q in p2)) / 2.0)
    cz = f32(f32(min(q[1] for q in p2) + max(q[1] for q in p2)) / 2.0)
    ring = hull + [hull[0]]
    ln = [0.0] * len(ring)
    for i in range(1, len(ring)):
        ln[i] = f32(ln[i - 1] + _dist(ring[i - 1], ring[i]))
    total, t0 = ln[-1], 0.0
    for i in range(1, len(ring)):
        a, b = f32(ring[i - 1][0] - cx), f32(ring[i][0] - cx)
        if (a <= 0) != (b <= 0) and f32(f32(ring[i - 1][1] + ring[i][1]) / 2.0) < cz:
            t0 = f32(ln[i - 1] + f32(f32(ln[i] - ln[i - 1]) * fdiv(a, f32(a - b))))
            break

    def at(t):
        t = f32(math.fmod(t, total))
        s = 1
        while s < len(ring) - 1 and ln[s] < t:
            s += 1
        f = fdiv(f32(t - ln[s - 1]), max(f32(1e-6), f32(ln[s] - ln[s - 1])))
        return _lerp(ring[s - 1], ring[s], f)
    return [at(f32(t0 + f32(f32(total * k) / n))) for k in range(n)]


def _tri(v, a, b, c, want):
    nrm = v_cross(v_sub(v[b], v[a]), v_sub(v[c], v[a]))
    return [a, b, c] if v_dot(nrm, want) >= 0 else [a, c, b]


def _cap(tris, va, first, n):
    s = [0, 1]
    lo, hi = 2, n - 1
    while len(s) < n:
        s.append(hi)
        hi -= 1
        if len(s) < n:
            s.append(lo)
            lo += 1
    for k in range(len(s) - 2):
        tris.append(_tri(va, first + s[k], first + s[k + 1], first + s[k + 2], (0.0, 1.0, 0.0)))


def _centre(ring, n):
    sx = sy = 0.0
    for p in ring:
        sx, sy = f32(sx + p[0]), f32(sy + p[1])
    return f32(sx / n), f32(sy / n)


def auto_ground(body, n):
    outer = footprint(body, n)
    if len(outer) < 3:
        return None
    c = _centre(outer, len(outer))
    k = f32(0.93)
    v, col = [], []
    for p in outer:
        q = (f32(c[0] + f32(f32(p[0] - c[0]) * k)), f32(c[1] + f32(f32(p[1] - c[1]) * k)))
        v.append((q[0], 0.0, q[1]))
        col.append([0, 0, 0, 77])
    for p in outer:
        v.append((p[0], 0.0, p[1]))
        col.append([0, 0, 0, 0])
    tris = []
    _cap(tris, v, 0, n)
    for i in range(n):
        j = (i + 1) % n
        tris.append(_tri(v, i, n + i, j, (0.0, 1.0, 0.0)))
        tris.append(_tri(v, n + i, n + j, j, (0.0, 1.0, 0.0)))
    return {"vertices": [list(p) for p in v], "colors": col, "triangles": tris}


def auto_hull(body):
    N = 16
    ring = footprint(body, N)
    if len(ring) < 3:
        return None
    c = _centre(ring, N)
    max_y = max(p[1] for p in body)
    v, col = [], []
    for i in range(N):
        v.append((ring[i][0], f32(0.002), ring[i][1]))
        col.append([0, 0, 0, 64])
    heights = []
    for i in range(N):
        sl = [p[1] for p in body if abs(f32(p[2] - ring[i][1])) < f32(0.15)]
        heights.append(max(sl) if sl else max_y)
    for i in range(N):
        mid = max(f32(0.01), f32(heights[i] - f32(0.156)))
        v.append((ring[i][0], mid, ring[i][1]))
        a = f32(f32(51.2) - f32(24.0 * mid))
        col.append([0, 0, 0, int(min(64, max(0, round(a))))])
    for i in range(N):
        v.append((ring[i][0], max(heights[i], f32(0.02)), ring[i][1]))
        col.append([0, 0, 0, 0])
    tris = []
    _cap(tris, v, 0, N)
    for i in range(N):
        j = (i + 1) % N
        mid2 = (f32(f32(ring[i][0] + ring[j][0]) / 2.0), f32(f32(ring[i][1] + ring[j][1]) / 2.0))
        inward = (f32(c[0] - mid2[0]), 0.0, f32(c[1] - mid2[1]))
        for lo, hi in ((0, N), (N, 2 * N)):
            tris.append(_tri(v, lo + i, hi + i, lo + j, inward))
            tris.append(_tri(v, hi + i, hi + j, lo + j, inward))
    return {"vertices": [list(p) for p in v], "colors": col, "triangles": tris}

# Nenkai's research is the core of this human & machine made tool.
