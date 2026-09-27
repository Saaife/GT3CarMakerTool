"""Command line, same verbs and arguments as the old gt4track.exe GT3 car commands:

  gt3-car-decompile <root> <code> <outDir>     (root = a folder with cars/day, cars/night, menu/cars)
  gt3-car-decompile <car file> <outDir>        (one container; day body only)
  gt3-car-build <car.json> <outDir> [code]
  gt3-carcolor <database dir> [code] [--json <out.json>]
  gt3-carcolor-set <database dir> <code> <colours.json> [--out <dir>]
  gt3-brake <brake.bin> [--png <dir>]          the shared brake textures + the GTCI number of each
  gt3-brake-add <brake.bin> [--disc <img>]... [--caliper <img>]... [--like <caliper>] [--out <file>]
"""

import json
import os
import sys


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if not a or a[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    verb = a[0].lower()
    if verb == "gt3-car-decompile":
        from .decompile import decompile_car
        if len(a) >= 3 and os.path.isfile(a[1]):
            decompile_car(a[1], None, a[2])
        elif len(a) >= 4:
            decompile_car(a[1], a[2], a[3])
        else:
            raise SystemExit("usage: gt3-car-decompile <root> <code> <outDir> | <car file> <outDir>")
        return 0
    if verb == "gt3-car-build":
        from .build import build_car
        if len(a) < 3:
            raise SystemExit("usage: gt3-car-build <car.json> <outDir> [code]")
        return build_car(a[1], a[2], a[3] if len(a) > 3 else None)
    if verb == "gt3-carcolor":
        from .colordb import ColorDb, car_colours
        rest, out = [], None
        i = 2
        while i < len(a):
            if a[i] == "--json" and i + 1 < len(a):
                out = a[i + 1]
                i += 2
                continue
            rest.append(a[i])
            i += 1
        if not rest:
            db = ColorDb.load(a[1])
            print("%d cars, %d list entries, %d colours, %d strings"
                  % (len(db.cars), sum(len(c.ids) for c in db.cars), len(db.colours), len(db.strings)))
            return 0
        code = rest[0]
        dto = car_colours(a[1], code)
        if dto is None:
            print("%s: not in the colour database" % code)
            if out:
                with open(out, "w", encoding="utf-8") as f:
                    json.dump({"code": code, "colours": []}, f)
            return 2
        print("%s: %d colours" % (code, len(dto["colours"])))
        for c in dto["colours"]:
            print("  %2d  id %4d  chip (%d,%d,%d)  %s" % (c["index"], c["id"], c["chip"][0], c["chip"][1], c["chip"][2], c["name"]))
        if out:
            with open(out, "w", encoding="utf-8") as f:
                json.dump(dto, f, indent=2)
        return 0
    if verb == "gt3-carcolor-set":
        from .colordb import set_car_colours
        if len(a) < 4:
            raise SystemExit("usage: gt3-carcolor-set <database dir> <code> <colours.json> [--out <dir>]")
        out = None
        if "--out" in a[4:]:
            k = a.index("--out", 4)
            out = a[k + 1] if k + 1 < len(a) else None
        with open(a[3], encoding="utf-8-sig") as f:
            want = json.load(f)
        return set_car_colours(a[1], a[2], want, out)
    if verb == "gt3-brake":
        from .brakes import BrakeFile
        from .png import write_png
        if len(a) < 2:
            raise SystemExit("usage: gt3-brake <brake.bin> [--png <dir>]")
        b = BrakeFile.load(a[1])
        png = a[a.index("--png") + 1] if "--png" in a[2:] and a.index("--png") + 1 < len(a) else None
        print("%s: %d textures, %d GS blocks | discs = GTCI disc number, calipers = GTCI caliper number (1 = none)"
              % (a[1], len(b.texset.textures), b.texset.total_block_size))
        for n, t, flag in b.discs():
            print("  disc    %2d  (texture %2d, flag %d)" % (n, t, flag))
        for n, t, xyz in b.calipers():
            print("  caliper %2d  (texture %2d, %.2f %.2f %.2f)" % (n, t, xyz[0], xyz[1], xyz[2]))
        if png:
            os.makedirs(png, exist_ok=True)
            write_png(os.path.join(png, "glow.png"), b.image(b.glow))
            for n, t, _ in b.discs():
                write_png(os.path.join(png, "disc_%02d.png" % n), b.image(t))
            for n, t, _ in b.calipers():
                write_png(os.path.join(png, "caliper_%02d.png" % n), b.image(t))
            print("  pictures -> %s" % png)
        return 0
    if verb == "gt3-brake-add":
        from .brakes import add_to_file, load_images
        discs, cals, like, out = [], [], None, None
        i = 2
        while i < len(a):
            if a[i] in ("--disc", "--caliper", "--like", "--out") and i + 1 < len(a):
                {"--disc": discs, "--caliper": cals}.get(a[i], []).append(a[i + 1]) if a[i] in ("--disc", "--caliper") else None
                like = int(a[i + 1]) if a[i] == "--like" else like
                out = a[i + 1] if a[i] == "--out" else out
                i += 2
                continue
            raise SystemExit("usage: gt3-brake-add <brake.bin> [--disc <img>]... [--caliper <img>]... [--like <caliper>] [--out <file>]")
        if len(a) < 2 or not (discs or cals):
            raise SystemExit("usage: gt3-brake-add <brake.bin> [--disc <img>]... [--caliper <img>]... [--like <caliper>] [--out <file>]")
        ds, cs = add_to_file(a[1], load_images(discs), load_images(cals), like, out)
        for p_, n in zip(discs, ds):
            print("  disc    %2d  <- %s" % (n, p_))
        for p_, n in zip(cals, cs):
            print("  caliper %2d  <- %s" % (n, p_))
        print("  -> %s (set the car's GTCI BrakeDiscTextureIndex / BrakeCaliperTextureIndex to these)" % (out or a[1]))
        return 0
    raise SystemExit("unknown command '%s' (gt3-car-decompile, gt3-car-build, gt3-carcolor, gt3-carcolor-set, "
                     "gt3-brake, gt3-brake-add)" % a[0])


if __name__ == "__main__":
    sys.exit(main())

# Nenkai's research is the core of this human & machine made tool.
