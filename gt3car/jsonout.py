"""Compact JSON writer matching the C# spec files: float32 values in their shortest round-trip form,
named float literals as strings ("NaN"), dict keys in insertion order."""

import math
import struct

_F = struct.Struct("<f")
_cache = {}


def fmt_f32(x):
    s = _cache.get(x)
    if s is not None and (x != 0.0 or math.copysign(1.0, x) == math.copysign(1.0, float(s))):
        return s
    if x != x:
        return '"NaN"'
    if x in (math.inf, -math.inf):
        return '"Infinity"' if x > 0 else '"-Infinity"'
    if x == 0.0:
        return "-0" if math.copysign(1.0, x) < 0 else "0"
    for p in (6, 7, 8, 9):
        s = "%.*g" % (p, x)
        try:
            if _F.unpack(_F.pack(float(s)))[0] == x:
                break
        except OverflowError:
            continue
    else:
        s = repr(x)
    if len(_cache) < 1_000_000:
        _cache[x] = s
    return s


def dumps(o):
    out = []
    _w(o, out)
    return "".join(out)


def _w(o, out):
    if o is None:
        out.append("null")
    elif o is True:
        out.append("true")
    elif o is False:
        out.append("false")
    elif isinstance(o, int):
        out.append(str(o))
    elif isinstance(o, float):
        out.append(fmt_f32(o))
    elif isinstance(o, str):
        out.append(_str(o))
    elif isinstance(o, dict):
        out.append("{")
        first = True
        for k, v in o.items():
            if not first:
                out.append(",")
            first = False
            out.append(_str(k))
            out.append(":")
            _w(v, out)
        out.append("}")
    elif isinstance(o, (list, tuple)):
        out.append("[")
        for i, v in enumerate(o):
            if i:
                out.append(",")
            _w(v, out)
        out.append("]")
    else:
        raise TypeError("cannot serialize %r" % type(o))


def _str(s):
    import json
    return json.dumps(s, ensure_ascii=False)


def prune(o):
    """Drop None-valued dict entries recursively (JsonIgnoreCondition.WhenWritingNull); nulls in arrays stay."""
    if isinstance(o, dict):
        return {k: prune(v) for k, v in o.items() if v is not None}
    if isinstance(o, list):
        return [prune(v) for v in o]
    return o

# Nenkai's research is the core of this human & machine made tool.
