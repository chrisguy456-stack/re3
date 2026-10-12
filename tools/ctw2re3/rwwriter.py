# SPDX-License-Identifier: GPL-3.0-only
"""Minimal RenderWare stream writers: a one-atomic clump (.dff) and a D3D8 texture dictionary (.txd).

Written as RW 3.4.0.3, which librw (the renderer re3 builds with) reads."""
import math
import struct

RW_VERSION = 0x1003FFFF   # 3.4.0.3

ID_STRUCT, ID_STRING, ID_EXTENSION = 0x01, 0x02, 0x03
ID_TEXTURE, ID_MATERIAL, ID_MATLIST = 0x06, 0x07, 0x08
ID_FRAMELIST, ID_GEOMETRY, ID_CLUMP = 0x0E, 0x0F, 0x10
ID_ATOMIC, ID_TEXTURENATIVE, ID_TEXDICTIONARY, ID_GEOMETRYLIST = 0x14, 0x15, 0x16, 0x1A
ID_BINMESH = 0x50E

# Geometry flags
TRISTRIP, POSITIONS, TEXTURED, PRELIT, NORMALS, LIGHT, MODULATE = 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40

PLATFORM_D3D8 = 8
FILTER_LINEAR, ADDRESS_WRAP = 2, 1
C1555, C565, C4444, C8888 = 0x0100, 0x0200, 0x0300, 0x0500
RASTER_TEXTURE = 4


def chunk(cid, body):
    return struct.pack('<III', cid, len(body), RW_VERSION) + body


def string_chunk(s):
    b = s.encode('ascii') + b'\0'
    b += b'\0' * (-len(b) % 4)
    return chunk(ID_STRING, b)


def empty_extension():
    return chunk(ID_EXTENSION, b'')


class Mesh:
    """Indexed triangles with per-vertex position, normal, uv and prelight colour."""

    def __init__(self):
        self.pos, self.nrm, self.uv, self.col = [], [], [], []
        self.tris = []        # (a, b, c, material)
        self.materials = []   # (texture name or None, (r, g, b, a))
        self._vindex = {}
        self._mindex = {}

    def material(self, texture, rgba):
        key = (texture, rgba)
        if key not in self._mindex:
            self._mindex[key] = len(self.materials)
            self.materials.append(key)
        return self._mindex[key]

    def vertex(self, p, n, uv, col):
        key = (round(p[0], 4), round(p[1], 4), round(p[2], 4), round(n[0], 3), round(n[1], 3), round(n[2], 3),
               round(uv[0], 5), round(uv[1], 5), col)
        i = self._vindex.get(key)
        if i is None:
            i = self._vindex[key] = len(self.pos)
            self.pos.append(key[0:3])
            self.nrm.append(key[3:6])
            self.uv.append(key[6:8])
            self.col.append(col)
        return i

    def bounds(self):
        if not self.pos:
            return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
        return (tuple(min(p[i] for p in self.pos) for i in range(3)),
                tuple(max(p[i] for p in self.pos) for i in range(3)))


def _material(texture, rgba):
    body = struct.pack('<i4Bii', 0, *rgba, 0, 1 if texture else 0) + struct.pack('<3f', 1.0, 1.0, 1.0)
    if texture:
        tex = chunk(ID_STRUCT, struct.pack('<I', FILTER_LINEAR | ADDRESS_WRAP << 8 | ADDRESS_WRAP << 12))
        tex += string_chunk(texture) + string_chunk('') + empty_extension()
        body = chunk(ID_STRUCT, body) + chunk(ID_TEXTURE, tex)
    else:
        body = chunk(ID_STRUCT, body)
    return chunk(ID_MATERIAL, body + empty_extension())


def _geometry(m, prelit=True):
    """prelit: buildings carry their lighting in vertex colours; vehicles are lit by the game instead."""
    nv, nt = len(m.pos), len(m.tris)
    if nv > 0xFFFF:
        raise ValueError('too many vertices for one geometry: %d' % nv)
    flags = POSITIONS | TEXTURED | NORMALS | MODULATE | (1 << 16)   # one UV set
    flags |= PRELIT if prelit else LIGHT
    s = struct.pack('<iiii', flags, nt, nv, 1)
    if prelit:
        s += b''.join(struct.pack('<4B', *c) for c in m.col)
    s += b''.join(struct.pack('<2f', *uv) for uv in m.uv)
    # librw: v[1] = low half of word 0, v[0] = high half; matId = low half of word 1, v[2] = high half
    s += b''.join(struct.pack('<HHHH', b, a, mat, c) for a, b, c, mat in m.tris)
    lo, hi = m.bounds()
    centre = tuple((lo[i] + hi[i]) / 2 for i in range(3))
    radius = math.sqrt(sum((hi[i] - lo[i]) ** 2 for i in range(3))) / 2
    s += struct.pack('<4f', *centre, radius) + struct.pack('<ii', 1, 1)
    s += b''.join(struct.pack('<3f', *p) for p in m.pos)
    s += b''.join(struct.pack('<3f', *n) for n in m.nrm)
    mats = struct.pack('<i', len(m.materials)) + struct.pack('<i', -1) * len(m.materials)
    matlist = chunk(ID_STRUCT, mats) + b''.join(_material(t, c) for t, c in m.materials)
    # Bin mesh PLG: one triangle-list mesh per material
    per = [[] for _ in m.materials]
    for a, b, c, mat in m.tris:
        per[mat] += (a, b, c)
    bm = struct.pack('<III', 0, len(per), 3 * nt)
    for mat, idx in enumerate(per):
        bm += struct.pack('<II', len(idx), mat) + struct.pack('<%dI' % len(idx), *idx)
    ext = chunk(ID_EXTENSION, chunk(ID_BINMESH, bm))
    return chunk(ID_GEOMETRY, chunk(ID_STRUCT, s) + chunk(ID_MATLIST, matlist) + ext)


def write_dff(m):
    """A clump with one frame, one geometry and one atomic, the shape GTA 3 expects for a simple object."""
    clump = chunk(ID_STRUCT, struct.pack('<iii', 1, 0, 0))
    frames = struct.pack('<i', 1) + struct.pack('<9f3fii', 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, -1, 0)
    clump += chunk(ID_FRAMELIST, chunk(ID_STRUCT, frames) + empty_extension())
    clump += chunk(ID_GEOMETRYLIST, chunk(ID_STRUCT, struct.pack('<i', 1)) + _geometry(m))
    atomic = chunk(ID_STRUCT, struct.pack('<iiii', 0, 0, 5, 0)) + empty_extension()
    clump += chunk(ID_ATOMIC, atomic)
    return chunk(ID_CLUMP, clump + empty_extension())


ID_NODENAME = 0x0253F2FE


def write_clump(frames, meshes, atomics):
    """A clump with named frames, for vehicles. frames: (name, parent index or -1, position), all unrotated;
    meshes: game-lit geometries; atomics: (frame index, mesh index), several atomics may share a mesh."""
    clump = chunk(ID_STRUCT, struct.pack('<iii', len(atomics), 0, 0))
    fl = struct.pack('<i', len(frames))
    for _, parent, pos in frames:
        fl += struct.pack('<9f3fii', 1, 0, 0, 0, 1, 0, 0, 0, 1, *pos, parent, 0)
    exts = b''.join(chunk(ID_EXTENSION, chunk(ID_NODENAME, name.encode('ascii'))) for name, _, _ in frames)
    clump += chunk(ID_FRAMELIST, chunk(ID_STRUCT, fl) + exts)
    clump += chunk(ID_GEOMETRYLIST, chunk(ID_STRUCT, struct.pack('<i', len(meshes))) +
                   b''.join(_geometry(m, prelit=False) for m in meshes))
    for frame, mesh in atomics:
        clump += chunk(ID_ATOMIC, chunk(ID_STRUCT, struct.pack('<iiii', frame, mesh, 5, 0)) + empty_extension())
    return chunk(ID_CLUMP, clump + empty_extension())


def _native_texture(name, t):
    """D3D8 texture native, as GTA 3 PC's own .txd files store them."""
    head = struct.pack('<II', PLATFORM_D3D8, FILTER_LINEAR | ADDRESS_WRAP << 8 | ADDRESS_WRAP << 12)
    head += name.encode('ascii')[:31].ljust(32, b'\0') + b'\0' * 32
    if t.kind == 'rgba':
        bgra = bytearray(t.data)
        bgra[0::4], bgra[2::4] = t.data[2::4], t.data[0::4]   # D3DFMT_A8R8G8B8 is B, G, R, A in memory
        fmt = C8888
        head += struct.pack('<IiHHBBBB', fmt, 1 if t.alpha else 0, t.w, t.h, 32, 1, RASTER_TEXTURE, 0)
        pixels = bytes(bgra)
    else:
        compression = {'dxt1': 1, 'dxt3': 3, 'dxt5': 5}[t.kind]
        fmt = (C1555 if t.alpha else C565) if t.kind == 'dxt1' else C4444
        head += struct.pack('<IiHHBBBB', fmt, 1 if t.alpha else 0, t.w, t.h, 16, 1, RASTER_TEXTURE, compression)
        pixels = t.data
    body = chunk(ID_STRUCT, head + struct.pack('<I', len(pixels)) + pixels)
    return chunk(ID_TEXTURENATIVE, body + empty_extension())


def write_txd(textures):
    """textures: list of (name, textures.Texture)."""
    body = chunk(ID_STRUCT, struct.pack('<HH', len(textures), 0))
    body += b''.join(_native_texture(n, t) for n, t in textures)
    return chunk(ID_TEXDICTIONARY, body + empty_extension())
