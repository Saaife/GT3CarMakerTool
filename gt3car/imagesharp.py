"""Exact ports of the two SixLabors.ImageSharp 3.1.6 operations the texture builder relies on, so a texture that
has to be resized or quantized comes out bit-identical to the C# tools:

  resize(rgba, w, h)   Image.Mutate(x => x.Resize(w, h)): BicubicResampler, premultiplied alpha, the
                       ResizeKernelMap (incl. its periodic kernel reuse), and ResizeKernel.ConvolveCore's AVX2 +
                       FMA accumulation order, Rgba32's SIMD byte <-> float conversions.
  wu_quantize(rgba, n) WuQuantizer (no dither): 5-bit RGBA histogram, Wu's variance cuts, tag lookup.

All float math is float32 where the C# is; FMA is emulated exactly (one rounding).

Ported (translated from C# to Python, reduced to these two operations) from SixLabors.ImageSharp 3.1.6,
Copyright (c) Six Labors, https://github.com/SixLabors/ImageSharp - used under the Apache License, Version 2.0
(Six Labors Split License 1.0, open-source use). See LICENSE."""

import math

import numpy as np

_F32 = np.float32
_LOW29 = np.uint64((1 << 29) - 1)
_HALF29 = np.uint64(1 << 28)
_CLR29 = np.uint64(~((1 << 29) - 1) & 0xFFFFFFFFFFFFFFFF)
_STEP29 = np.uint64(1 << 29)
_SIGN = np.uint64(1 << 63)


def fma32(a, b, c):
    """Element-wise float32 fused multiply-add a * b + c with a single rounding."""
    a64, b64, c64 = (np.asarray(v, np.float32).astype(np.float64) for v in (a, b, c))
    p = a64 * b64                                  # exact: 24 x 24 bit product
    s = p + c64
    bb = s - p
    err = (p - (s - bb)) + (c64 - bb)              # TwoSum: s + err == p + c exactly
    r = s.astype(np.float32)
    bits = s.view(np.uint64)
    mid = ((bits & _LOW29) == _HALF29) & (err != 0) & (np.abs(s) >= 1.1754943508222875e-38) & np.isfinite(s)
    if mid.any():
        mag = bits[mid] & ~_SIGN
        lo = (mag & _CLR29).view(np.float64)
        hi = ((mag & _CLR29) + _STEP29).view(np.float64)
        sm = s[mid]
        away = np.sign(err[mid]) == np.sign(sm)
        res = np.where(away, hi, lo)
        r[mid] = np.where(sm < 0, -res, res).astype(np.float32)
    return r


# ---- resize ---------------------------------------------------------------------------------------------
def _bicubic(x):
    x = _F32(abs(_F32(x)))
    if x <= _F32(1):
        return float(_F32(_F32(_F32(_F32(_F32(_F32(1.5) * x) - _F32(2.5)) * x) * x) + _F32(1)))
    if x < _F32(2):
        return float(_F32(_F32(_F32(_F32(_F32(_F32(_F32(-0.5) * x) + _F32(2.5)) * x) - _F32(4)) * x) + _F32(2)))
    return 0.0


_EPS = 1e-8


def _tceil(a):
    rem = math.remainder(a, 1.0)
    if -_EPS < rem < _EPS:
        return float(round(a))
    return float(math.ceil(a))


def _tfloor(a):
    rem = math.remainder(a, 1.0)
    if -_EPS < rem < _EPS:
        return float(round(a))
    return float(math.floor(a))


def kernel_map(dst, src):
    """ResizeKernelMap.Calculate: per destination index (left, float32 weights)."""
    ratio = src / dst
    scale = max(ratio, 1.0)
    radius = int(_tceil(scale * 2.0))
    period = dst // math.gcd(src, dst)
    center0 = (ratio - 1) * 0.5
    first_nonneg = (radius - center0 - 1) / ratio
    corner = int(_tceil(first_nonneg))
    if -_EPS < first_nonneg - corner < _EPS:
        corner += 1

    def build(i):
        center = ((i + .5) * ratio) - .5
        left = max(0, int(_tceil(center - radius)))
        right = min(src - 1, int(_tfloor(center + radius)))
        vals = []
        total = 0.0
        for j in range(left, right + 1):
            v = _bicubic(_F32((j - center) / scale))
            total += v
            vals.append(v)
        if total > 0:
            vals = [v / total for v in vals]
        return left, np.array(vals, np.float32)

    kernels = [None] * dst
    if 2 * (corner + period) < dst:                 # PeriodicKernelMap
        start = corner + period
        for i in range(start):
            kernels[i] = build(i)
        bottom = dst - corner
        for i in range(start, bottom):
            center = ((i + .5) * ratio) - .5
            left = int(_tceil(center - radius))
            kernels[i] = (left, kernels[i - period][1])
        for i in range(corner):
            kernels[bottom + i] = build(bottom + i)
    else:
        for i in range(dst):
            kernels[i] = build(i)
    return kernels


def _convolve(rows, left, w):
    """ResizeKernel.ConvolveCore (AVX2 + FMA): rows (n, length, 4) float32 -> (n, 4); taps rows[:, left + j]."""
    n = rows.shape[0]
    L = len(w)
    acc0 = np.zeros((n, 2, 4), np.float32)
    acc1 = np.zeros((n, 2, 4), np.float32)
    j = 0
    full = L & ~3
    while j < full:
        acc0[:, 0] = fma32(rows[:, left + j], w[j], acc0[:, 0])
        acc0[:, 1] = fma32(rows[:, left + j + 1], w[j + 1], acc0[:, 1])
        acc1[:, 0] = fma32(rows[:, left + j + 2], w[j + 2], acc1[:, 0])
        acc1[:, 1] = fma32(rows[:, left + j + 3], w[j + 3], acc1[:, 1])
        j += 4
    acc0 = (acc0 + acc1).astype(np.float32)
    if (L & 3) >= 2:
        acc0[:, 0] = fma32(rows[:, left + j], w[j], acc0[:, 0])
        acc0[:, 1] = fma32(rows[:, left + j + 1], w[j + 1], acc0[:, 1])
        j += 2
    r = (acc0[:, 0] + acc0[:, 1]).astype(np.float32)
    if L & 1:
        r = fma32(rows[:, left + j], w[j], r)
    return r


_INV255 = _F32(1.0) / _F32(255.0)


def _to_vec(rgba):
    """Rgba32 -> premultiplied Vector4 (ByteToNormalizedFloat: byte * (1/255f); then xyz *= w)."""
    v = (rgba.astype(np.float32) * _INV255).astype(np.float32)
    v[..., :3] = (v[..., :3] * v[..., 3:4]).astype(np.float32)
    return v


def _from_vec(v):
    """Un-premultiply, then NormalizedFloatToByteSaturate per row: the first (w - w % 8) pixels through AVX2
    (x * 255, round half to even, saturate), the rest through the Vector4 fallback (x * 255 + 0.5, clamp, truncate)."""
    v = v.astype(np.float32).copy()
    a = v[..., 3:4]
    nz = np.broadcast_to(a != 0, v[..., :3].shape)
    with np.errstate(divide="ignore", invalid="ignore"):
        q = (v[..., :3] / a).astype(np.float32)
    v[..., :3] = np.where(nz, q, v[..., :3])
    h, w = v.shape[:2]
    out = np.zeros((h, w, 4), np.uint8)
    split = w - (w % 8)
    s = (v * _F32(255)).astype(np.float32)
    if split:
        part = s[:, :split]
        r = np.rint(part)
        r = np.where(np.isfinite(part) & (np.abs(part) < 2147483648.0), r, -2147483648.0)
        out[:, :split] = np.clip(r, 0, 255).astype(np.uint8)
    if split < w:
        part = (s[:, split:] + _F32(0.5)).astype(np.float32)
        part = np.minimum(np.maximum(part, _F32(0)), _F32(255))
        out[:, split:] = part.astype(np.uint8)
    return out


def resize(rgba, w, h):
    """Image.Mutate(x => x.Resize(w, h)) on an Rgba32 image (h0, w0, 4) uint8."""
    h0, w0 = rgba.shape[:2]
    if (w0, h0) == (w, h):
        return rgba.copy()
    hk = kernel_map(w, w0)
    vk = kernel_map(h, h0)
    src = _to_vec(rgba)                                     # (h0, w0, 4)
    first = np.zeros((w, h0, 4), np.float32)                # transposed first pass
    for x, (left, wt) in enumerate(hk):
        first[x] = _convolve(src, left, wt)
    out = np.zeros((h, w, 4), np.float32)
    for y, (top, wt) in enumerate(vk):
        out[y] = _convolve(first, top, wt)
    return _from_vec(out)


# ---- Wu quantizer ---------------------------------------------------------------------------------------------
_IB = 5
_IC = (1 << _IB) + 1          # 33


class _Box:
    __slots__ = ("r0", "r1", "g0", "g1", "b0", "b1", "a0", "a1", "vol")

    def __init__(self):
        self.r0 = self.r1 = self.g0 = self.g1 = self.b0 = self.b1 = self.a0 = self.a1 = self.vol = 0


def _dot4(r, g, b, a):
    """Vector4.Dot on float32 lanes: (x*x + y*y) + (z*z + w*w)."""
    r, g, b, a = (_F32(v) for v in (r, g, b, a))
    return _F32(_F32(_F32(r * r) + _F32(g * g)) + _F32(_F32(b * b) + _F32(a * a)))


class _Wu:
    def __init__(self, rgba, max_colors):
        px = rgba.reshape(-1, 4).astype(np.int64)
        idx = (((px[:, 0] >> 3) + 1) * _IC ** 3 + ((px[:, 1] >> 3) + 1) * _IC ** 2 +
               ((px[:, 2] >> 3) + 1) * _IC + ((px[:, 3] >> 3) + 1))
        n = _IC ** 4
        m = np.zeros((5, n), np.int64)
        for k in range(4):
            m[k] = np.bincount(idx, weights=px[:, k], minlength=n).astype(np.int64)
        m[4] = np.bincount(idx, minlength=n)
        sq = (px * px).sum(1).astype(np.float64)                 # per-pixel Vector4.Dot: exact (< 2^24)
        m2 = np.bincount(idx, weights=sq, minlength=n)
        shape = (_IC, _IC, _IC, _IC)
        m = m.reshape((5,) + shape)
        m2 = m2.reshape(shape)
        for ax in range(4):                                       # Get3DMoments = the 4-D prefix sum (integer-exact)
            m = np.cumsum(m, axis=ax + 1)
            m2 = np.cumsum(m2, axis=ax)
        self.m, self.m2 = m, m2
        self.max_colors = max_colors
        self.tags = np.zeros(shape, np.uint8)

    def _vol(self, c, arr=None):
        """Volume(cube) over the 5 integer moments (arr = m) or Moment2 (arr = m2)."""
        m = self.m if arr is None else arr
        sl = (slice(None),) if arr is None else ()
        tot = 0
        for ri, rs in ((c.r1, 1), (c.r0, -1)):
            for gi, gs in ((c.g1, 1), (c.g0, -1)):
                for bi, bs in ((c.b1, 1), (c.b0, -1)):
                    for ai, as_ in ((c.a1, 1), (c.a0, -1)):
                        tot = tot + (rs * gs * bs * as_) * m[sl + (ri, gi, bi, ai)]
        return tot

    def _top(self, c, d, pos):
        """Top(cube, direction, position) for an array of positions: (5, len(pos)) moments."""
        m = self.m
        pos = np.asarray(pos)
        tot = 0
        corners = []
        if d == 3:
            for gi, gs in ((c.g1, 1), (c.g0, -1)):
                for bi, bs in ((c.b1, 1), (c.b0, -1)):
                    for ai, as_ in ((c.a1, 1), (c.a0, -1)):
                        corners.append((gs * bs * as_, m[:, pos, gi, bi, ai]))
        elif d == 2:
            for ri, rs in ((c.r1, 1), (c.r0, -1)):
                for bi, bs in ((c.b1, 1), (c.b0, -1)):
                    for ai, as_ in ((c.a1, 1), (c.a0, -1)):
                        corners.append((rs * bs * as_, m[:, ri, pos, bi, ai]))
        elif d == 1:
            for ri, rs in ((c.r1, 1), (c.r0, -1)):
                for gi, gs in ((c.g1, 1), (c.g0, -1)):
                    for ai, as_ in ((c.a1, 1), (c.a0, -1)):
                        corners.append((rs * gs * as_, m[:, ri, gi, pos, ai]))
        else:
            for ri, rs in ((c.r1, 1), (c.r0, -1)):
                for gi, gs in ((c.g1, 1), (c.g0, -1)):
                    for bi, bs in ((c.b1, 1), (c.b0, -1)):
                        corners.append((rs * gs * bs, m[:, ri, gi, bi, pos]))
        for s, v in corners:
            tot = tot + s * v
        return tot

    def _maximize(self, c, d, first, last, whole):
        lo = (c.r0, c.g0, c.b0, c.a0)[3 - d]
        bottom = -self._top(c, d, [lo])[:, 0]
        if first >= last:
            return _F32(0), -1
        pos = np.arange(first, last)
        half = bottom[:, None] + self._top(c, d, pos)             # (5, k)
        rest = whole[:, None] - half
        best, cut = _F32(0), -1
        for k in range(len(pos)):
            w1 = half[4, k]
            if w1 == 0:
                continue
            t = _F32(_dot4(half[0, k], half[1, k], half[2, k], half[3, k]) / _F32(w1))
            w2 = rest[4, k]
            if w2 == 0:
                continue
            t = _F32(t + _F32(_dot4(rest[0, k], rest[1, k], rest[2, k], rest[3, k]) / _F32(w2)))
            if t > best:
                best, cut = t, int(pos[k])
        return best, cut

    def _cut(self, s1, s2):
        whole = self._vol(s1)
        mr, cr = self._maximize(s1, 3, s1.r0 + 1, s1.r1, whole)
        mg, cg = self._maximize(s1, 2, s1.g0 + 1, s1.g1, whole)
        mb, cb = self._maximize(s1, 1, s1.b0 + 1, s1.b1, whole)
        ma, ca = self._maximize(s1, 0, s1.a0 + 1, s1.a1, whole)
        if mr >= mg and mr >= mb and mr >= ma:
            d = 3
            if cr < 0:
                return False
        elif mg >= mr and mg >= mb and mg >= ma:
            d = 2
        elif mb >= mr and mb >= mg and mb >= ma:
            d = 1
        else:
            d = 0
        s2.r1, s2.g1, s2.b1, s2.a1 = s1.r1, s1.g1, s1.b1, s1.a1
        if d == 3:
            s2.r0 = s1.r1 = cr
            s2.g0, s2.b0, s2.a0 = s1.g0, s1.b0, s1.a0
        elif d == 2:
            s2.g0 = s1.g1 = cg
            s2.r0, s2.b0, s2.a0 = s1.r0, s1.b0, s1.a0
        elif d == 1:
            s2.b0 = s1.b1 = cb
            s2.r0, s2.g0, s2.a0 = s1.r0, s1.g0, s1.a0
        else:
            s2.a0 = s1.a1 = ca
            s2.r0, s2.g0, s2.b0 = s1.r0, s1.g0, s1.b0
        for s in (s1, s2):
            s.vol = (s.r1 - s.r0) * (s.g1 - s.g0) * (s.b1 - s.b0) * (s.a1 - s.a0)
        return True

    def _variance(self, c):
        v = self._vol(c)
        m2 = float(self._vol(c, self.m2))
        return m2 - float(_F32(_dot4(v[0], v[1], v[2], v[3]) / _F32(v[4])))

    def run(self):
        cubes = [_Box() for _ in range(self.max_colors)]
        c0 = cubes[0]
        c0.r1 = c0.g1 = c0.b1 = c0.a1 = _IC - 1
        vv = [0.0] * self.max_colors
        nxt = 0
        i = 1
        while i < self.max_colors:
            if self._cut(cubes[nxt], cubes[i]):
                vv[nxt] = self._variance(cubes[nxt]) if cubes[nxt].vol > 1 else 0.0
                vv[i] = self._variance(cubes[i]) if cubes[i].vol > 1 else 0.0
            else:
                vv[nxt] = 0.0
                i -= 1
            nxt = 0
            temp = vv[0]
            for k in range(1, i + 1):
                if vv[k] > temp:
                    temp, nxt = vv[k], k
            if temp <= 0.0:
                self.max_colors = i + 1
                break
            i += 1
        pal = []
        for k in range(self.max_colors):
            c = cubes[k]
            self.tags[c.r0 + 1:c.r1 + 1, c.g0 + 1:c.g1 + 1, c.b0 + 1:c.b1 + 1, c.a0 + 1:c.a1 + 1] = k
            v = self._vol(c)
            if v[4] > 0:
                w = _F32(v[4])
                comps = []
                for ch in range(4):
                    x = _F32(_F32(_F32(v[ch]) / w) / _F32(255))
                    x = _F32(_F32(x * _F32(255)) + _F32(0.5))
                    x = min(max(x, _F32(0)), _F32(255))
                    comps.append(int(x))
                pal.append(comps[0] | comps[1] << 8 | comps[2] << 16 | comps[3] << 24)
            else:
                pal.append(0)
        return pal


def wu_quantize(rgba, n):
    """WuQuantizer(MaxColors = n, Dither = null).BuildPaletteAndQuantizeFrame on an Rgba32 image.
    Returns (palette: packed R|G<<8|B<<16|A<<24 list, indices (h, w) uint8)."""
    wu = _Wu(rgba, n)
    pal = wu.run()
    px = rgba.reshape(-1, 4).astype(np.int64) >> 3
    idx = wu.tags[px[:, 0] + 1, px[:, 1] + 1, px[:, 2] + 1, px[:, 3] + 1]
    return pal, idx.reshape(rgba.shape[:2]).astype(np.uint8)
# Nenkai's research is the core of this human & machine made tool.