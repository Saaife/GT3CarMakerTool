"""Little-endian binary reader / writer with the stream semantics the PDTools serializers rely on:
a writer whose position can move past the end (the gap is zero-filled on the next write) and an
align() that optionally grows the buffer (Syroot BinaryStream.Align(n, grow))."""

import struct

_U8, _U16, _I16, _U32, _I32, _F32 = (struct.Struct(f) for f in ("<B", "<H", "<h", "<I", "<i", "<f"))


class Reader:
    __slots__ = ("b", "pos")

    def __init__(self, data, pos=0):
        self.b = data if isinstance(data, (bytes, bytearray, memoryview)) else bytes(data)
        self.pos = pos

    def __len__(self):
        return len(self.b)

    def u8(self):
        v = self.b[self.pos]
        self.pos += 1
        return v

    def u16(self):
        v = _U16.unpack_from(self.b, self.pos)[0]
        self.pos += 2
        return v

    def i16(self):
        v = _I16.unpack_from(self.b, self.pos)[0]
        self.pos += 2
        return v

    def u32(self):
        v = _U32.unpack_from(self.b, self.pos)[0]
        self.pos += 4
        return v

    def i32(self):
        v = _I32.unpack_from(self.b, self.pos)[0]
        self.pos += 4
        return v

    def f32(self):
        v = _F32.unpack_from(self.b, self.pos)[0]
        self.pos += 4
        return v

    def many(self, fmt, n):
        """n values of struct code fmt ('I', 'i', 'H', 'h', 'f', 'B')."""
        s = struct.calcsize(fmt) * n
        v = struct.unpack_from("<%d%s" % (n, fmt), self.b, self.pos)
        self.pos += s
        return list(v)

    def bytes(self, n):
        if self.pos + n > len(self.b):
            raise EOFError("read past the end (%d + %d > %d)" % (self.pos, n, len(self.b)))
        v = bytes(self.b[self.pos:self.pos + n])
        self.pos += n
        return v

    def align(self, a):
        self.pos += (-self.pos) % a


class Writer:
    __slots__ = ("b", "pos")

    def __init__(self):
        self.b = bytearray()
        self.pos = 0

    def __len__(self):
        return len(self.b)

    def getvalue(self):
        return bytes(self.b)

    def _put(self, data):
        end = self.pos + len(data)
        if end > len(self.b):
            if self.pos > len(self.b):
                self.b.extend(bytes(self.pos - len(self.b)))
            self.b[self.pos:] = data
        else:
            self.b[self.pos:end] = data
        self.pos = end

    def write(self, data):
        self._put(data)

    def u8(self, v):
        self._put(_U8.pack(v & 0xFF))

    def u16(self, v):
        self._put(_U16.pack(v & 0xFFFF))

    def i16(self, v):
        self._put(_I16.pack(v))

    def u32(self, v):
        self._put(_U32.pack(v & 0xFFFFFFFF))

    def i32(self, v):
        self._put(_I32.pack(v))

    def f32(self, v):
        self._put(_F32.pack(v))

    def f32bits(self, bits):
        self._put(_U32.pack(bits & 0xFFFFFFFF))

    def many(self, fmt, vals):
        self._put(struct.pack("<%d%s" % (len(vals), fmt), *vals))

    def align(self, a, grow=True):
        self.pos += (-self.pos) % a
        if grow and self.pos > len(self.b):
            self.b.extend(bytes(self.pos - len(self.b)))

    def set_length(self, n):
        if n < len(self.b):
            del self.b[n:]
        else:
            self.b.extend(bytes(n - len(self.b)))


def align_up(x, a):
    return (x + a - 1) // a * a

# Nenkai's research is the core of this human & machine made tool.
