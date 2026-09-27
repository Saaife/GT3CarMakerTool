"""GT3's dealer colour list: database/carcolor.db ('GT2K') + carcolor.sdb ('STDB') (port of Gt3CarColorDb.cs).

carcolor.db:  u32 'GT2K', u32 0, u32 cars, u32 listsOff, u32 coloursOff, u32 fileSize
  cars @0x18      {u64 key, u32 colour count, u32 offset of its id list from listsOff (first = 4)}, sorted by key;
                  key = (sum of the code's chars << 7 * length) + the chars packed 7 bits each
  lists @listsOff u32 total ids, u32 colour ids ... (one run per car, PD's own order, contiguous)
  colours         u32 count, {u32 id, u32 name string, u32 Japanese name string, u8 R, G, B, A} sorted by id
carcolor.sdb: 'STDB', u32 count, u32 flag, u32 file size, u32 offsets[count], {u16 byte length, bytes}"""

import os
import shutil
import struct


def key(code):
    packed = s = 0
    for ch in code.encode("ascii"):
        packed = ((packed << 7) | ch) & 0xFFFFFFFFFFFFFFFF
        s += ch
    return ((s << (7 * len(code))) + packed) & 0xFFFFFFFFFFFFFFFF


class Car:
    __slots__ = ("key", "ids", "order")

    def __init__(self, k, ids=None, order=0):
        self.key, self.ids, self.order = k, ids if ids is not None else [], order


class Colour:
    __slots__ = ("id", "name", "name_jp", "r", "g", "b", "a")

    def __init__(self, id, name, name_jp, r, g, b, a=0):
        self.id, self.name, self.name_jp, self.r, self.g, self.b, self.a = id, name, name_jp, r, g, b, a


class ColorDb:
    def __init__(self):
        self.cars = []
        self.colours = {}          # id -> Colour (written sorted by id)
        self.strings = []          # raw bytes after the u16 length (NUL + padding included)
        self._db_zero = 0
        self._sdb_flag = 0

    @classmethod
    def load(cls, dir_):
        d = open(os.path.join(dir_, "carcolor.db"), "rb").read()
        s = open(os.path.join(dir_, "carcolor.sdb"), "rb").read()
        if d[:4] != b"GT2K":
            raise ValueError("carcolor.db: no GT2K magic")
        if s[:4] != b"STDB":
            raise ValueError("carcolor.sdb: no STDB magic")
        db = cls()
        db._db_zero = struct.unpack_from("<I", d, 4)[0]
        db._sdb_flag = struct.unpack_from("<I", s, 8)[0]
        n_cars, lists, cols = struct.unpack_from("<iii", d, 8)
        by_off = []
        for i in range(n_cars):
            o = 0x18 + 16 * i
            k, n, off = struct.unpack_from("<QiI", d, o)
            car = Car(k, list(struct.unpack_from("<%dI" % n, d, lists + off)))
            db.cars.append(car)
            by_off.append((off, car))
        for order, (_, car) in enumerate(sorted(by_off, key=lambda x: x[0])):
            car.order = order
        n_col = struct.unpack_from("<i", d, cols)[0]
        for i in range(n_col):
            o = cols + 4 + 16 * i
            cid, nm, jp = struct.unpack_from("<III", d, o)
            db.colours[cid] = Colour(cid, nm, jp, d[o + 12], d[o + 13], d[o + 14], d[o + 15])
        n_str = struct.unpack_from("<i", s, 4)[0]
        for i in range(n_str):
            o = struct.unpack_from("<i", s, 16 + 4 * i)[0]
            ln = struct.unpack_from("<H", s, o)[0]
            db.strings.append(s[o + 2:o + 2 + ln])
        return db

    def write(self):
        cars = sorted(self.cars, key=lambda c: c.key)
        lists_off = 0x18 + 16 * len(cars)
        total = sum(len(c.ids) for c in cars)
        cols_off = lists_off + 4 + 4 * total
        d = bytearray(cols_off + 4 + 16 * len(self.colours))
        d[0:4] = b"GT2K"
        struct.pack_into("<IiiiI", d, 4, self._db_zero, len(cars), lists_off, cols_off, len(d))
        offset_of = {}
        at = 4
        for car in sorted(cars, key=lambda c: c.order):
            offset_of[id(car)] = at
            for cid in car.ids:
                struct.pack_into("<I", d, lists_off + at, cid)
                at += 4
        struct.pack_into("<i", d, lists_off, total)
        for i, car in enumerate(cars):
            struct.pack_into("<QiI", d, 0x18 + 16 * i, car.key, len(car.ids), offset_of[id(car)])
        struct.pack_into("<i", d, cols_off, len(self.colours))
        for i, c in enumerate(self.colours[k] for k in sorted(self.colours)):
            struct.pack_into("<IIIBBBB", d, cols_off + 4 + 16 * i, c.id, c.name, c.name_jp, c.r, c.g, c.b, c.a)
        s = bytearray(b"STDB" + struct.pack("<iII", len(self.strings), self._sdb_flag, 0))
        at = 16 + 4 * len(self.strings)
        for st in self.strings:
            s += struct.pack("<i", at)
            at += 2 + len(st)
        for st in self.strings:
            s += struct.pack("<H", len(st) & 0xFFFF) + st
        struct.pack_into("<i", s, 12, len(s))
        return bytes(d), bytes(s)

    def find(self, code):
        k = key(code)
        return next((c for c in self.cars if c.key == k), None)

    @staticmethod
    def text(raw, japanese=False):
        n = raw.find(b"\0")
        if n < 0:
            n = len(raw)
        return raw[:n].hex().upper() if japanese else raw[:n].decode("latin-1")

    def name(self, i):
        return self.text(self.strings[i]) if i < len(self.strings) else "<string %d?>" % i

    def name_jp_hex(self, i):
        return self.text(self.strings[i], True) if i < len(self.strings) else ""

    def add_string(self, text):
        n = len(text) + 1
        if n & 1:
            n += 1
        raw = text + bytes(n - len(text))
        for i, s in enumerate(self.strings):
            if s == raw:
                return i
        self.strings.append(raw)
        return len(self.strings) - 1


def car_colours(db_dir, code):
    """The dealer colour list of a car: {"code", "colours": [{index, id, name, name_jp, chip}]} or None."""
    db = ColorDb.load(db_dir)
    car = db.find(code)
    if car is None:
        return None
    cols = []
    for k, cid in enumerate(car.ids):
        c = db.colours.get(cid)
        if c is not None:
            cols.append({"index": k, "id": cid, "name": db.name(c.name),
                         "name_jp": None if c.name_jp == c.name else db.name_jp_hex(c.name_jp), "chip": [c.r, c.g, c.b]})
        else:
            cols.append({"index": k, "id": cid, "name": "<colour %d missing>" % cid, "name_jp": None, "chip": [0, 0, 0]})
    return {"code": code, "colours": cols}


def set_car_colours(db_dir, code, colours, out_dir=None, log=print):
    """Give a car its own colour list (paint-colour order). A colour identical to an existing one (name + chip)
    reuses its id; anything else becomes a NEW entry. Unknown codes are added. Returns 0."""
    out_dir = out_dir or db_dir
    if len(code) > 8:
        raise ValueError("car code '%s' is longer than 8 characters" % code)
    db = ColorDb.load(db_dir)
    if isinstance(colours, dict):
        colours = colours.get("colours")
    if not colours:
        raise ValueError("no colours given")
    ids, reused, added = [], 0, 0
    for c in colours:
        chip = c.get("chip") or []
        r, g, b = (min(255, max(0, int(chip[i]) if i < len(chip) else 0)) for i in range(3))
        name = (c.get("name") or "").strip() or "-"
        name_bytes = name.encode("latin-1", "replace")
        jp = c.get("name_jp")
        jp_bytes = bytes.fromhex(jp) if jp else None

        def same(e):
            return (e.r == r and e.g == g and e.b == b and db.name(e.name) == name
                    and (e.name_jp == e.name if jp_bytes is None else db.name_jp_hex(e.name_jp) == jp))
        want = c.get("id")
        if want is not None and int(want) in db.colours and same(db.colours[int(want)]):
            ids.append(int(want))
            reused += 1
            continue
        hit = next((e for k, e in sorted(db.colours.items()) if same(e)), None)
        if hit is not None:
            ids.append(hit.id)
            reused += 1
            continue
        ni = db.add_string(name_bytes)
        ji = ni if jp_bytes is None else db.add_string(jp_bytes)
        cid = 1 if not db.colours else max(db.colours) + 1
        db.colours[cid] = Colour(cid, ni, ji, r, g, b)
        ids.append(cid)
        added += 1
    car = db.find(code)
    if car is None:
        car = Car(key(code), order=0 if not db.cars else max(x.order for x in db.cars) + 1)
        db.cars.append(car)
    before = len(car.ids)
    car.ids = ids
    d, s = db.write()
    if (os.path.abspath(out_dir) == os.path.abspath(db_dir)
            and d == open(os.path.join(db_dir, "carcolor.db"), "rb").read()
            and s == open(os.path.join(db_dir, "carcolor.sdb"), "rb").read()):
        log("%s: %d colours, unchanged -> %s" % (code, len(ids), out_dir))
        return 0
    os.makedirs(out_dir, exist_ok=True)
    for nm, data in (("carcolor.db", d), ("carcolor.sdb", s)):
        target = os.path.join(out_dir, nm)
        if os.path.exists(target) and not os.path.exists(target + ".bak"):
            shutil.copyfile(target, target + ".bak")
        with open(target, "wb") as f:
            f.write(data)
    log("%s: %d -> %d colours (%d existing, %d new) -> %s" % (code, before, len(ids), reused, added, out_dir))
    return 0

# Nenkai's research is the core of this human & machine made tool.
