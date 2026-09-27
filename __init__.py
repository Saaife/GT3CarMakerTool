bl_info = {
    "name": "GT3 Car Maker (from scratch)",
    "author": "GT4TrackKit",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "3D View > Sidebar (N) > GT3 Car; File > Export > GT3 Car; File > Import > GT3 Car (editable)",
    "description": "Build Gran Turismo 3 cars: drop your parts into the car's collections (Body, Wheel, Steering, Mudflaps ...) "
                   "or name them (_Glass, _Chrome, _Rim ...). "
                   "One car = one file (+ its night / eve car). Round-trips PD's own cars draw for draw; imports live "
                   "in the .blend. PNG / TGA / BMP textures, own brake art. Pure Python: no external tool. See README.md",
    "category": "Import-Export",
}

# The add-on UI lives in addon.py; the car toolkit (decompile / build / the paint-colour database) is the bundled
# pure-Python package gt3car (stdlib + numpy, which Blender ships). Outside Blender:  python -m gt3_car_maker <command>
# Every feature and how it is emitted: README.md


def register():
    from . import addon
    addon.register()


def unregister():
    from . import addon
    addon.unregister()

# Nenkai's research is the core of this human & machine made tool.
