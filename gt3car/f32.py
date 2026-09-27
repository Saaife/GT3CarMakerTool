"""Single-precision helpers: the C# tools compute in float32, so byte-identical output needs every
arithmetic step rounded to float32 (one IEEE op in double then rounded = the correctly rounded float
op, since double has > 2*24+2 bits)."""

import math
import struct

_F = struct.Struct("<f")
_I = struct.Struct("<i")
_U = struct.Struct("<I")
NAN_BITS = 0xFFC00000          # .NET float.NaN


def f32(x):
    """Round a Python float to the nearest float32 (overflow -> inf, like a C# (float) cast)."""
    try:
        return _F.unpack(_F.pack(x))[0]
    except OverflowError:
        return math.copysign(math.inf, x)


def f32_bits(x):
    """BitConverter.SingleToInt32Bits((float)x) as a signed int."""
    if x != x:
        return _I.unpack(_U.pack(NAN_BITS))[0]
    try:
        return _I.unpack(_F.pack(x))[0]
    except OverflowError:
        return _I.unpack(_F.pack(math.copysign(math.inf, x)))[0]


def bits_f32(i):
    return _F.unpack(_U.pack(i & 0xFFFFFFFF))[0]


# element-wise float32 arithmetic on Python floats
def add(a, b):
    return f32(a + b)


def sub(a, b):
    return f32(a - b)


def mul(a, b):
    return f32(a * b)


def div(a, b):
    if b == 0:
        if a == 0 or a != a:
            return math.nan
        return math.copysign(math.inf, a) * (math.copysign(1.0, b))
    return f32(a / b)


def sqrt(a):
    return f32(math.sqrt(a)) if a >= 0 else math.nan


# Vector3 as tuples (float32-valued floats)
def v_add(a, b):
    return (f32(a[0] + b[0]), f32(a[1] + b[1]), f32(a[2] + b[2]))


def v_sub(a, b):
    return (f32(a[0] - b[0]), f32(a[1] - b[1]), f32(a[2] - b[2]))


def v_scale(a, s):
    return (f32(a[0] * s), f32(a[1] * s), f32(a[2] * s))


def v_div(a, s):
    return (div(a[0], s), div(a[1], s), div(a[2], s))


def v_dot(a, b):
    return f32(f32(f32(a[0] * b[0]) + f32(a[1] * b[1])) + f32(a[2] * b[2]))


def v_cross(a, b):
    return (f32(f32(a[1] * b[2]) - f32(a[2] * b[1])),
            f32(f32(a[2] * b[0]) - f32(a[0] * b[2])),
            f32(f32(a[0] * b[1]) - f32(a[1] * b[0])))


def v_len(a):
    return sqrt(v_dot(a, a))


def v_len_sq(a):
    return v_dot(a, a)


def v_normalize(a):
    return v_div(a, v_len(a))


def v_min(a, b):
    return tuple(_fmin(x, y) for x, y in zip(a, b))


def v_max(a, b):
    return tuple(_fmax(x, y) for x, y in zip(a, b))


def _fmin(x, y):
    # System.Numerics Min (.NET 9+ = IEEE 754:2019 minimum): NaN propagates, -0 < +0
    if x != x:
        return x
    if y != y:
        return y
    if x < y:
        return x
    if y < x:
        return y
    return x if math.copysign(1.0, x) < 0 else y


def _fmax(x, y):
    if x != x:
        return x
    if y != y:
        return y
    if x > y:
        return x
    if y > x:
        return y
    return x if math.copysign(1.0, x) > 0 else y


_LOW29, _HALF29 = (1 << 29) - 1, 1 << 28
_D, _Q = struct.Struct("<d"), struct.Struct("<Q")


def round_exact(x):
    """A Fraction rounded ONCE to float32 (nearest, ties to even)."""
    from fractions import Fraction
    d = float(x)                                          # correctly rounded to double
    bits = _Q.unpack(_D.pack(d))[0]
    if (bits & _LOW29) != _HALF29 or d != d or abs(d) < 1.1754943508222875e-38:
        return f32(d)
    exact = Fraction(d)
    if x == exact:
        return f32(d)
    mag = bits & ~(1 << 63)
    lo = _D.unpack(_Q.pack(mag & ~_LOW29))[0]
    hi = _D.unpack(_Q.pack((mag & ~_LOW29) + (1 << 29)))[0]
    r = hi if abs(x) > abs(exact) else lo
    return -r if d < 0 else r


def fma(a, b, c):
    """float32 fused multiply-add: a * b + c with a single rounding (x86 vfmadd on float lanes)."""
    from fractions import Fraction
    if a != a or b != b or c != c or math.isinf(a) or math.isinf(b) or math.isinf(c):
        return f32(a * b + c)
    return round_exact(Fraction(a) * Fraction(b) + Fraction(c))

# Nenkai's research is the core of this human & machine made tool.
