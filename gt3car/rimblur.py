"""Race WHEEL FACE texture sets (the spinning-wheel picture), read from GT3's engine (PAL core):

A race rim draws its face disc with texture 511 + pglExternalTexIndex(1): the shape takes external texture slot 1,
and nothing in the file fills that slot. Every frame the wheel code (0x223860 -> 0x2243a0) fills it from the rim's
OWN texture sets:
  * set 2 (a pre-blurred face) when the wheel turns more than 3 degrees a frame (+-0.05236 rad, 0x352340/44),
  * otherwise set 1: texture 0 as it is (parked), or texture 1 drawn rotated a few times into a render target
    (slow spin blur); the brake disc + caliper pictures are drawn just before the rim and show through the
    face's alpha.
With variation texture sets the picked table is variation [level + 3 * wheel model] (3 distance levels x 2 models).
0x2243a0 reads sets 1/2 WITHOUT checking the count: a rim with only set 0 leaves slot 1 holding whatever the last
wheel put there - the "colourful, lagging, brakeless" wheels of the earlier test builds.

PD census (285 race rims): 279 use the face slot and ALL carry 3 sets x 6 variations - set 1 = the face twice
(128x128 or 64x64 PSMT4), set 2 = the blurred face at half size; variation levels 1/2 = half / quarter size;
variation set 0 = the model's own set 0. The 6 rims without the slot have one set and no variations."""

import math
import struct

import numpy as np

from . import gs
from .imagesharp import resize as bicubic
from .modelset import ModelSet1
from .texbuild import TextureSetBuilder, TextureConfig, pow2_up


def pd_blur_sets(gttw_bytes):
    """PD's rim model set from an imported car's race GTTW (only its texture sets are used), or None."""
    size = struct.unpack_from("<I", gttw_bytes, 0xC)[0]
    ms = ModelSet1.read(gttw_bytes[0x20:size])
    if len(ms.texture_sets) < 3 or len(ms.variation_texsets) < 6:
        return None
    return ms


def face_set(ms, model):
    vs = ms.variation_texsets
    sets = vs[3 * model] if vs and 3 * model < len(vs) else ms.texture_sets
    return sets[1] if len(sets) > 1 else None


def _same(a, b):
    return a is not None and b is not None and a.shape == b.shape and np.array_equal(a, b)


def pd_unchanged(pd, model, face, colours):
    """True when the face picture (and each colour's picture) is still exactly PD's decoded face for that model."""
    ts = face_set(pd, model)
    if ts is None or face is None:
        return False
    try:
        if not _same(ts.image(0, 0), face):
            return False
        for c, img in enumerate(colours or []):
            if c > 0 and img is not None and not _same(ts.image(0, c if c < len(ts.clut_patch_sets) else 0), img):
                return False
    except Exception:
        return False
    return True


def rot_blur(img, degrees=90.0, samples=48):
    """The face smeared around its centre (the wheel axis): alpha-weighted average of `samples` rotations over
    `degrees` - close to PD's set 2 look (spokes gone, rings and hub left)."""
    h, w = img.shape[:2]
    f = img.astype(np.float64)
    a = f[..., 3:4] / 255.0
    pm = np.concatenate([f[..., :3] * a, f[..., 3:4]], axis=2)
    cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    dy, dx = yy - cy, xx - cx
    acc = np.zeros_like(pm)
    for k in range(samples):
        t = math.radians(-degrees / 2 + degrees * k / (samples - 1))
        sx = np.clip(cx + dx * math.cos(t) - dy * math.sin(t), 0, w - 1)
        sy = np.clip(cy + dx * math.sin(t) + dy * math.cos(t), 0, h - 1)
        x0, y0 = np.floor(sx).astype(int), np.floor(sy).astype(int)
        x1, y1 = np.minimum(x0 + 1, w - 1), np.minimum(y0 + 1, h - 1)
        fx, fy = (sx - x0)[..., None], (sy - y0)[..., None]
        acc += (pm[y0, x0] * (1 - fx) * (1 - fy) + pm[y0, x1] * fx * (1 - fy)
                + pm[y1, x0] * (1 - fx) * fy + pm[y1, x1] * fx * fy)
    acc /= samples
    alpha = acc[..., 3:4]
    rgb = np.where(alpha > 1e-6, acc[..., :3] / np.maximum(alpha / 255.0, 1e-6), 0.0)
    out = np.concatenate([rgb, alpha], axis=2)
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def _square(img, n):
    return img if img.shape[:2] == (n, n) else bicubic(img, n, n)


def _set(entries, colour_count):
    """entries = [(rgba, [colour variant rgba or None per colour])]; PSMT4, region clamp (PD's face sets)."""
    b = TextureSetBuilder(max(1, colour_count))
    cfg = TextureConfig(fmt=gs.PSMT4, is_texture_map=True)
    for img, cols in entries:
        local = b.add_image(img, cfg, resize=bicubic)
        for c, v in enumerate(cols or []):
            if c > 0 and c < colour_count and v is not None:
                b.set_colour_variant(c, local, v)
    return b.build(resize=bicubic)


def face_levels(face, colours, colour_count):
    """[(set 1, set 2)] for distance levels 0-2 (PD's dominant layout: 128 -> 64 -> 32, blur at half each)."""
    n = min(128, max(16, pow2_up(max(face.shape[0], face.shape[1]))))
    cols = [None if (c == 0 or img is None) else img for c, img in enumerate(colours or [])]
    full = _square(face, n)
    fcols = [None if v is None else _square(v, n) for v in cols]
    blur = rot_blur(full)
    bcols = [None if v is None else rot_blur(v) for v in fcols]
    out = []
    for lvl in range(3):
        m = max(8, n >> lvl)
        s1 = _set([(_square(full, m), [None if v is None else _square(v, m) for v in fcols])] * 2, colour_count)
        mb = max(8, m >> 1)
        s2 = _set([(_square(blur, mb), [None if v is None else _square(v, mb) for v in bcols])], colour_count)
        out.append((s1, s2))
    return out, n


def attach(ms, pd, front, rear, colour_count, log=print):
    """Give the race rim model set `ms` (set 0 already built) its spinning-wheel sets.
    front / rear = (face rgba, [colour rgba...], label) or None. pd = PD's rim model set (import) or None."""
    s0 = ms.texture_sets[0]
    fimg, fcols, flabel = front
    keep = pd is not None and pd_unchanged(pd, 0, fimg, fcols) and (
        rear is None or face_set(pd, 1) is face_set(pd, 0) or pd_unchanged(pd, 1, rear[0], rear[1]))
    if keep and all(len(ts.clut_patch_sets) == len(s0.clut_patch_sets)
                    for v in pd.variation_texsets for ts in v[1:]):
        ms.texture_sets = [s0] + list(pd.texture_sets[1:])
        ms.variation_texsets = [[s0] + list(v[1:]) for v in pd.variation_texsets]
        log("  rim face: PD's spinning-wheel pictures kept (face unchanged)")
        return
    lv, n = face_levels(fimg, fcols, colour_count)
    if rear is not None and not (_same(rear[0], fimg)):
        rv, _ = face_levels(rear[0], rear[1], colour_count)
        log("  rim face: spinning-wheel pictures made from '%s' (front) and '%s' (rear), %dx%d + blur"
            % (flabel, rear[2], n, n))
    else:
        rv = lv
        log("  rim face: spinning-wheel pictures made from '%s', %dx%d + blur" % (flabel, n, n))
    ms.texture_sets = [s0, lv[0][0], lv[0][1]]
    ms.variation_texsets = [list(ms.texture_sets), [s0, *lv[1]], [s0, *lv[2]],
                            [s0, *rv[0]], [s0, *rv[1]], [s0, *rv[2]]]

# Nenkai's research is the core of this human & machine made tool.
