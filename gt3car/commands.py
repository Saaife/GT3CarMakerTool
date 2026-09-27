"""ModelSet render commands (PDTools ModelSetupPS2Command): read and write, same grammar and quirks."""

import struct

END = 0
CALL_SHAPE_BYTE, CALL_SHAPE_USHORT = 4, 5
CALL_MODEL_CALLBACK, LOD_SELECT, JUMP_BYTE, JUMP_USHORT, BBOX_RENDER, ENABLE_RENDERING = 6, 7, 8, 9, 10, 11
PUSH_MATRIX, POP_MATRIX, MULT_MATRIX = 12, 13, 16
ENABLE_ALPHA_TEST, DISABLE_ALPHA_TEST, ALPHA_FUNC = 26, 27, 28
ENABLE_DEST_ALPHA_TEST, DISABLE_DEST_ALPHA_TEST, DEST_ALPHA_FUNC = 29, 30, 31
BLEND_FUNC, SET_FOG_COLOR, STORE_FOG_COLOR, COPY_FOG_COLOR, COLOR_MASK = 32, 33, 34, 35, 36
DISABLE_DEPTH_MASK, ENABLE_DEPTH_MASK = 37, 38
EXTERNAL_TEX_INDEX, EXTERNAL_MAT_INDEX, SET_TEX_TABLE_BYTE, SET_TEX_TABLE_USHORT = 41, 42, 43, 44
ENABLE_CULL_FACE, DISABLE_CULL_FACE = 45, 46
GT3_2_1UI, GT3_2_4F, SHAPE_TWEEN_RATIO, OP53, GT3_3_1UI, GT3_3_4F = 50, 51, 52, 53, 54, 55
VM_BRANCH = 77

NAMES = {
    0: "End", 2: "RenderModel_Byte", 3: "RenderModel_UShort", 4: "pgluCallShape_Byte", 5: "pgluCallShape_UShort",
    6: "CallModelCallback", 7: "LODSelect", 8: "Jump_Byte", 9: "Jump_UShort", 10: "BBoxRender", 11: "pglEnableRendering",
    12: "pglPushMatrix", 13: "pglPopMatrix", 14: "pglMatrixMode", 15: "pglLoadMatrix", 16: "pglMultMatrix",
    17: "pglTranslate", 18: "pglScale", 19: "pglRotate", 20: "pglRotateX", 21: "pglRotateY", 22: "pglRotateZ",
    23: "pglEnableDepthTest", 24: "pglDisableDepthTest", 25: "pglDepthFunc", 26: "pglEnableAlphaTest",
    27: "pglDisableAlphaTest", 28: "pglAlphaFunc", 29: "pglEnableDestinationAlphaTest",
    30: "pglDisableDestinationAlphaTest", 31: "pglSetDestinationAlphaFunc", 32: "pglBlendFunc", 33: "pglSetFogColor",
    34: "pglStoreFogColor", 35: "pglCopyFogColor", 36: "pglColorMask", 37: "pglDisableDepthMask", 38: "pglEnableDepthMask",
    39: "pglDepthBias", 40: "pglGT3_Unk40", 41: "pglExternalTexIndex", 42: "pglExternalMatIndex",
    43: "pgluSetTexTable_Byte", 44: "pgluSetTexTable_UShort", 45: "pglEnableCullFace", 46: "pglDisableCullFace",
    47: "pglAlphaFail", 49: "pglCylinderMapHint", 50: "pglGT3_2_1ui", 51: "pglGT3_2_4f", 52: "ModelSet_setShapeTweenRatio",
    53: "pgl_53", 54: "pglGT3_3_1ui", 55: "pglGT3_3_4f", 64: "pglCullFace", 65: "Unk_65", 66: "Unk_66", 67: "CallVM",
    68: "VM_MultMatrix", 69: "VM_pglTranslate", 70: "VM_pglScale", 72: "VM_pglRotate", 73: "VM_pglRotateX",
    74: "VM_pglRotateY", 75: "VM_pglRotateZ", 76: "VM_pgluShapeTweenRatio", 77: "VM_Branch", 78: "pglCullFace_2",
    79: "pglCullFace_1", 80: "pgluTexTableFromExternalTexSetByte", 81: "pgluTexTableFromExternalTexSetUShort",
    82: "pglVariableColorScale", 83: "pglVariableColorOffset", 84: "VM_pglVariableColorScale",
    85: "VM_pglVariableColorOffset", 86: "pglTexGenf_WithCurrentFacingParameters", 87: "pglTexGenf_Default",
    88: "pglDisable19_14", 89: "VMCallback_Byte", 90: "VMCallback_UShort",
}

# payload formats of the plain commands (struct codes, little-endian)
_FMT = {
    2: "B", 3: "H", 4: "B", 5: "H", 8: "B", 9: "H", 11: "", 12: "", 13: "", 14: "B", 15: "16f", 16: "16f",
    17: "3f", 18: "3f", 19: "4f", 20: "f", 21: "f", 22: "f", 23: "", 24: "", 25: "B", 26: "", 27: "", 28: "BB",
    29: "", 30: "", 31: "B", 32: "BB", 33: "I", 34: "", 35: "", 36: "I", 37: "", 38: "", 39: "f", 40: "4f",
    41: "B", 42: "B", 43: "B", 44: "H", 45: "", 46: "", 47: "B", 49: "3f", 50: "f", 51: "4f", 52: "f", 53: "HH",
    54: "f", 55: "4f", 64: "B", 65: "", 66: "", 67: "", 68: "H", 69: "3H", 70: "3H", 72: "H", 73: "H", 74: "H",
    75: "H", 76: "H", 78: "", 79: "", 80: "B", 81: "H", 82: "4f", 83: "4f", 84: "4H", 85: "4H", 86: "", 87: "",
    88: "", 89: "B", 90: "B",   # PDTools maps 90 onto the BYTE callback class
}
_ST = {op: struct.Struct("<" + f) for op, f in _FMT.items()}


class Cmd:
    """A plain command: op + args tuple."""
    __slots__ = ("op", "args")

    def __init__(self, op, *args):
        self.op = op
        self.args = tuple(args)

    def write(self, w):
        w.u8(self.op)
        if self.args or _FMT[self.op]:
            w.write(_ST[self.op].pack(*self.args))

    def __repr__(self):
        return "%s%s" % (NAMES.get(self.op, self.op), self.args if self.args else "")


class VMBranch:
    __slots__ = ("op", "reg", "targets")

    def __init__(self, reg, targets):
        self.op, self.reg, self.targets = VM_BRANCH, reg, list(targets)

    def write(self, w):
        w.u8(self.op)
        w.u16(self.reg)
        w.u8(len(self.targets))
        w.many("h", self.targets)


class BBoxRender:
    __slots__ = ("op", "bbox", "commands")

    def __init__(self, bbox, commands):
        self.op, self.bbox, self.commands = BBOX_RENDER, [tuple(p) for p in bbox], commands

    def write(self, w):
        w.u8(self.op)
        w.u8(len(self.bbox))
        for p in self.bbox:
            w.f32(p[0])
            w.f32(p[1])
            w.f32(p[2])
        jpos = w.pos
        w.i16(0)
        for c in self.commands:
            c.write(w)
        end = w.pos
        w.pos = jpos
        w.u16((end - jpos) & 0xFFFF)
        w.pos = end


class LODSelect:
    __slots__ = ("op", "unk", "unk2", "branches")

    def __init__(self, unk=(0.0, 0.0, 0.0), unk2=3.0, branches=None):
        self.op, self.unk, self.unk2 = LOD_SELECT, tuple(unk), unk2
        self.branches = branches if branches is not None else []

    def write(self, w):
        w.u8(self.op)
        for v in self.unk:
            w.f32(v)
        w.f32(self.unk2)
        w.u8(len(self.branches))
        table = w.pos
        data = w.pos + 2 * len(self.branches)
        jumps = []
        for i, br in enumerate(self.branches):
            entry = table + 2 * i
            w.pos = entry
            w.u16(data - entry)
            w.pos = data
            for c in br:
                c.write(w)
            if i != len(self.branches) - 1:
                w.u8(JUMP_USHORT)
                jumps.append(w.pos)
                w.i16(0)
            data = w.pos
        for j in jumps:
            w.pos = j
            w.u16(data - j)
        w.pos = data


class CallModelCallback:
    __slots__ = ("op", "param", "default", "branches")

    def __init__(self, param, branches=None, default=None):
        self.op, self.param = CALL_MODEL_CALLBACK, param
        self.default = default if default is not None else []
        self.branches = branches if branches is not None else []

    def write(self, w):
        w.u8(self.op)
        w.u16(self.param)
        w.u8(len(self.branches))
        table = w.pos
        w.pos = w.pos + 2 * len(self.branches)
        for c in self.default:
            c.write(w)
        w.u8(JUMP_USHORT)
        skip = w.pos
        w.i16(0)
        data = w.pos
        jumps = []
        for i, br in enumerate(self.branches):
            off = table + 2 * i
            w.pos = off
            w.u16(data - off)
            w.pos = data
            for c in br:
                c.write(w)
            if i != len(self.branches) - 1:
                w.u8(JUMP_USHORT)
                jumps.append(w.pos)
                w.i16(0)
            data = w.pos
        for j in jumps:
            w.pos = j
            w.u16(data - j)
        w.pos = skip
        w.u16(data - skip)
        w.pos = data


# ---- reading ---------------------------------------------------------------------------------------
def read_command(r, op):
    """Read the payload of opcode op (already consumed) at r.pos."""
    if op == BBOX_RENDER:
        n = r.u8()
        bbox = [(r.f32(), r.f32(), r.f32()) for _ in range(n)]
        start = r.pos
        jump = r.u16()
        cmds = []
        while r.pos < start + jump:
            o = r.u8()
            if o == END:
                break
            c = read_command(r, o)
            cmds.append(c)
            if o == ENABLE_RENDERING:
                break
        return BBoxRender(bbox, cmds)
    if op == LOD_SELECT:
        unk = (r.f32(), r.f32(), r.f32())
        unk2 = r.f32()
        n = r.u8()
        table = r.pos
        offs = r.many("H", n)
        branches = []
        for i in range(n):
            start = table + 2 * i
            r.pos = start + offs[i]
            end = start + 2 + (len(r.b) if i == n - 1 else offs[i + 1])
            cmds = []
            while r.pos < end:
                o = r.u8()
                if o in (END, ENABLE_RENDERING):
                    r.pos -= 1
                    break
                c = read_command(r, o)
                if o not in (JUMP_BYTE, JUMP_USHORT):
                    cmds.append(c)
            branches.append(cmds)
        return LODSelect(unk, unk2, branches)
    if op == CALL_MODEL_CALLBACK:
        param = r.u16()
        n = r.u8()
        table = r.pos
        offs = r.many("H", n)
        default, branches = [], []
        if n == 0 and r.pos + 3 <= len(r.b) and r.b[r.pos] == JUMP_USHORT and r.b[r.pos + 1] == 2 and r.b[r.pos + 2] == 0:
            # our writer (PDTools') ends a no-branch callback with a no-op Jump_UShort(+2); PD's files have none. Part
            # of the callback: inside a tail-lamp branch it must not be taken for the branch's closing jump.
            r.pos += 3
        if n > 0:
            end_all = 0
            while r.pos < table + offs[0]:
                o = r.u8()
                if o == END:
                    break
                c = read_command(r, o)
                if o == JUMP_USHORT:
                    end_all = r.pos - 2 + c.args[0]
                    break
                if o == JUMP_BYTE:
                    end_all = r.pos - 1 + c.args[0]
                    break
                default.append(c)
            for i in range(n):
                start = table + 2 * i
                r.pos = start + offs[i]
                end = end_all if i == n - 1 else start + 2 + offs[i + 1]
                cmds = []
                while r.pos < end:
                    o = r.u8()
                    if o == END:
                        break
                    c = read_command(r, o)
                    if o in (JUMP_USHORT, JUMP_BYTE):
                        break
                    cmds.append(c)
                branches.append(cmds)
        return CallModelCallback(param, branches, default)
    if op == VM_BRANCH:
        reg = r.u16()
        n = r.u8()
        return VMBranch(reg, r.many("h", n))
    st = _ST.get(op)
    if st is None:
        raise ValueError("Unexpected opcode %d" % op)
    if st.size == 0:
        return Cmd(op)
    v = st.unpack_from(r.b, r.pos)
    r.pos += st.size
    return Cmd(op, *v)


def read_model(r):
    cmds = []
    while True:
        o = r.u8()
        if o == END:
            break
        cmds.append(read_command(r, o))
    return cmds


def write_model(w, cmds):
    for c in cmds:
        c.write(w)
    w.u8(0)


# ---- builders for the commands the car emitter uses -------------------------------------------------
def call_shape(index):
    return Cmd(CALL_SHAPE_BYTE, index) if index <= 0xFF else Cmd(CALL_SHAPE_USHORT, index)


def blend_func(a, b, c, d, fix):
    return Cmd(BLEND_FUNC, ((d & 3) << 6 | (c & 3) << 4 | (b & 3) << 2 | (a & 3)) & 0xFF, fix & 0xFF)

# Nenkai's research is the core of this human & machine made tool.
