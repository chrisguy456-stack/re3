# SPDX-License-Identifier: GPL-3.0-only
"""Readers for the GTA: Chinatown Wars data files (Android 4.4.243, as written by the CTW-Native Porter).

The layouts follow the documentation in CTW-Native (https://github.com/official-kryo-to/ctw-native, GPL-3.0):
port/src/os/pak.cpp, port/src/os/wad.cpp, port/src/os/dxtbin.cpp, port/src/gfx/model.h,
port/src/world/worldmap.h and port/src/world/collision.h.

All CTW positions are 20.12 fixed point (1.0 = 4096) unless noted. The world is right-handed and z-up, like GTA 3.
"""
import os
import struct
import zlib

FX = 4096.0


# ---------------------------------------------------------------------------------------------------- game.pak
class Pak:
    """game.pak: u32 version, seg1, seg2, seg3, count, endPage, then a u16 page table at 0x18. The part of the table
    that does not fit the first 4 KB page sits at endPage * 4096 (the file tail)."""

    def __init__(self, path):
        self.f = open(path, 'rb')
        head = self.f.read(0x1000)
        _, self.s1, self.s2, self.s3, self.count, self.end = struct.unpack_from('<6I', head, 0)
        if not 0 < self.count <= 0x10000:
            raise ValueError('%s does not look like game.pak' % path)
        hdr = (self.count * 2 + 0x1017) & ~0xFFF
        if hdr > 0x1000:
            self.f.seek(self.end * 4096)
            head += self.f.read(hdr - 0x1000)
        self.table = list(struct.unpack_from('<%dH' % self.count, head, 0x18)) + [self.end & 0xFFFF]

    def _page(self, i):
        seg = 0 if i < self.s1 else 1 if i < self.s2 else 2 if i < self.s3 else 3
        return self.table[i] + 0x10000 * seg

    def size(self, i):
        if i < 0 or i >= self.count or self.table[i] == 0xFFFF or self.table[i + 1] == 0xFFFF:
            return 0
        return ((self.table[i + 1] - self.table[i]) & 0xFFFF) * 4096

    def read(self, i):
        n = self.size(i)
        if not n:
            return b''
        self.f.seek(self._page(i) * 4096)
        return self.f.read(n)


# ---------------------------------------------------------------------------------------------------- rom.wad
def wad_hash(name):
    h = 0
    for c in name.encode():
        if 0x61 <= c <= 0x7A:
            c -= 0x20
        h = ((h + c) * 0x401) & 0xFFFFFFFF
        h ^= h >> 6
    h = (h * 9) & 0xFFFFFFFF
    return ((h ^ (h >> 11)) * 0x8001) & 0xFFFFFFFF


class Wad:
    """rom.toc ({u32 hash, offset, size} sorted by hash) + rom.wad."""

    def __init__(self, data_dir):
        toc = open(os.path.join(data_dir, 'rom.toc'), 'rb').read()
        self.toc = {h: (o, s) for h, o, s in struct.iter_unpack('<III', toc[:len(toc) // 12 * 12])}
        self.path = os.path.join(data_dir, 'rom.wad')

    def read(self, name):
        e = self.toc.get(wad_hash(name))
        if e is None:
            return None
        with open(self.path, 'rb') as f:
            f.seek(e[0])
            return f.read(e[1])


# ---------------------------------------------------------------------------------------------------- dxt.bin
class DxtBin:
    """dxt.bin: 8192 u32 offsets, then per texture {u16 w, h, glFormat, ...} (12 bytes) and the DXT blocks."""

    def __init__(self, path):
        self.data = open(path, 'rb').read()
        self.offs = struct.unpack_from('<8192I', self.data, 0)

    def get(self, tid):
        if not 0 <= tid < 0x2000 or not self.offs[tid]:
            return None
        o = self.offs[tid]
        w, h, fmt = struct.unpack_from('<HHH', self.data, o)
        size = w * h if fmt == 0x83F3 else w * h // 2   # GetDXTData: DXT5 is 8 bpp, the rest 4 bpp
        if o + 12 + size > len(self.data):
            return None
        return w, h, fmt, self.data[o + 12:o + 12 + size]


# ---------------------------------------------------------------------------------------------------- cModel
class Model:
    """cModel ('MG'): see the header comment of CTW-Native port/src/gfx/model.h."""

    def __init__(self, b, off=0):
        if b[off:off + 2] != b'MG':
            raise ValueError('not a cModel')
        A, B, C, D = struct.unpack_from('<HBBH', b, off + 2)
        need = 16 + B * 32 + D * 16 + C * 12 + A * 192
        if B == 0 or off + need > len(b):
            raise ValueError('truncated cModel')
        self.size = need
        bb = struct.unpack_from('<6i', b, off + 0x10)
        self.bbox = [v / FX for v in bb]
        self.bbox_raw = bb
        self.scale = struct.unpack_from('<f', b, off + 0x28)[0]
        self.verts = list(struct.iter_unpack('<8h', b[off + 0x30:off + 0x30 + D * 16]))
        no = off + 0x30 + D * 16
        nodes = [((1, 0, 0), (0, 1, 0), (0, 0, 1), (0.0, 0.0, 0.0), 0)]
        for k in range(B - 1):
            n = no + 32 * k
            rot = struct.unpack_from('<9h', b, n)
            parent = struct.unpack_from('<b', b, n + 18)[0]
            tr = struct.unpack_from('<3i', b, n + 20)
            r = tuple(tuple(rot[i * 3 + j] / FX for j in range(3)) for i in range(3))
            nodes.append(r + (tuple(t / FX for t in tr), parent))
        self.world = []
        for k, (r0, r1, r2, t, p) in enumerate(nodes):
            R, T = (r0, r1, r2), t
            if 0 < p and p - 1 < k:   # relative to an earlier node (RefreshMatrices walks them in order)
                PR, PT = self.world[p - 1]
                R = tuple(tuple(sum(PR[i][m] * R[m][j] for m in range(3)) for j in range(3)) for i in range(3))
                T = tuple(sum(PR[i][m] * t[m] for m in range(3)) + PT[i] for i in range(3))
            self.world.append((R, T))
        ro = no + 32 * (B - 1)
        self.batches = []
        first = 0
        for k in range(C):
            tex, count, flags, node, alpha, _, mask = struct.unpack_from('<HHBBBBI', b, ro + 12 * k)
            if node >= len(self.world):
                raise ValueError('bad node')
            self.batches.append(dict(texture=tex, count=count, flags=flags, node=node, alpha=alpha, mask=mask,
                                     first=first))
            first += count
        if first != D:
            raise ValueError('batches do not cover the vertices')
        ao = ro + 12 * C
        self.anim_tracks = []
        for a in range(A):
            masks = struct.unpack_from('<32I', b, ao + a * 192)
            frames = struct.unpack_from('<32h', b, ao + a * 192 + 128)
            self.anim_tracks.append((masks, frames))

    def visible_mask(self):
        """The batches a model shows: every batch with a mask for static models; for animated ones (signs, lights)
        the mask of the first animation frame (WorldAnim::step from a fresh track, as CTW-Native draws them)."""
        if not self.anim_tracks:
            return 0xFFFFFFFF
        cur = 0
        for masks, frames in self.anim_tracks:
            slot = 0
            for _ in range(32):
                slot = (slot + 1) & 31
                if frames[slot] >= 0:
                    break
            cur |= masks[slot]
        return cur

    def triangles(self):
        """Yields (batch, [(pos, normal, uv) x 3]) in model space. Vertex data is stitched triangle strips."""
        s = self.scale
        vis = self.visible_mask()
        for mb in self.batches:
            if not mb['mask'] or not (mb['mask'] & vis):
                continue
            R, T = self.world[mb['node']]
            vs = self.verts[mb['first']:mb['first'] + mb['count']]
            for i in range(2, len(vs)):
                tri = [vs[i - 2], vs[i - 1], vs[i]]
                if i & 1:
                    tri[1], tri[2] = tri[2], tri[1]   # strip winding alternates
                if tri[0][:3] == tri[1][:3] or tri[1][:3] == tri[2][:3] or tri[0][:3] == tri[2][:3]:
                    continue
                out = []
                for v in tri:
                    p = (v[0] * s, v[1] * s, v[2] * s)
                    n = (v[3] / 32767.0, v[4] / 32767.0, v[5] / 32767.0)
                    wp = tuple(R[a][0] * p[0] + R[a][1] * p[1] + R[a][2] * p[2] + T[a] for a in range(3))
                    wn = tuple(R[a][0] * n[0] + R[a][1] * n[1] + R[a][2] * n[2] for a in range(3))
                    out.append((wp, wn, (v[6] / 2048.0, v[7] / 2048.0)))
                yield mb, out


# ---------------------------------------------------------------------------------------------------- world map
COLS, ROWS = 59, 42          # streaming blocks, 120 x 120 units each (2 x 2 sectors of 60)
BLOCK = 120.0
ORIGIN_X, ORIGIN_Y = -3510.0, -2490.0


def block_centre(c, r):
    return ORIGIN_X + BLOCK * c + BLOCK / 2, ORIGIN_Y + BLOCK * r + BLOCK / 2


def block_of(x, y):
    return int((x - ORIGIN_X) // BLOCK), int((y - ORIGIN_Y) // BLOCK)


class WorldMap:
    def __init__(self, pak):
        self.pak = pak
        h = pak.read(1)
        if len(h) < 8 + ROWS * 0x76:
            raise ValueError('world map header (resource 1) missing')
        self.sea_model, self.lake_model = struct.unpack_from('<HH', h, 0)
        self.grid = {}
        for r in range(ROWS):
            for c in range(COLS):
                rid = struct.unpack_from('<H', h, 8 + r * 0x76 + c * 2)[0]
                if rid != 0xFFFF:
                    self.grid[(c, r)] = rid

    def instances(self, c, r):
        """Yields ('model', key, Model, matrix, render list index) or ('water', key, (cx, cy, z, sea)) for each
        object block (c, r) lists. The key identifies an object listed by several neighbouring blocks."""
        rid = self.grid.get((c, r))
        if rid is None:
            return
        b = self.pak.read(rid)
        if len(b) < 0x40:
            return
        rot = struct.unpack_from('<9h', b, 0)
        tr = struct.unpack_from('<3i', b, 20)
        M = tuple(tuple(rot[i * 3 + j] / FX for j in range(3)) for i in range(3))
        T = tuple(t / FX for t in tr)
        seen = set()
        for sub in range(4):
            so = struct.unpack_from('<H', b, 0x20 + sub * 2)[0]
            n_inst, n20a, n_groups = struct.unpack_from('<HHH', b, so + 0x2A)
            inst0 = so + 0x34 + n_groups * 16
            for k in range(n_inst):
                io = inst0 + k * 16
                slot, list_idx = struct.unpack_from('<HB', b, io)
                model_id, off = struct.unpack_from('<II', b, io + 4)
                if off + 0x30 > len(b) or b[off:off + 2] != b'MG':
                    continue
                bb = struct.unpack_from('<6i', b, off + 0x10)
                key = (slot,) + bb
                if key in seen:
                    continue
                seen.add(key)
                mid = model_id & 0xFFFF
                if mid in (self.sea_model, self.lake_model):
                    sea = mid == self.sea_model
                    cx = bb[0] + ((bb[3] - bb[0]) >> 1)
                    cy = bb[1] + ((bb[4] - bb[1]) >> 1)
                    yield 'water', key, (cx / FX, cy / FX, -7.5 if sea else -2.5, sea)
                    continue
                try:
                    m = Model(b, off)
                except ValueError:
                    continue
                yield 'model', key, m, (M, T), list_idx


# ---------------------------------------------------------------------------------------------------- render tables
ADDITIVE_LISTS = (4, 6)      # CTW-Native's world renderer: lists 4 and 6 blend GL_SRC_ALPHA, GL_ONE


def load_render_lists(data_dir):
    """renderList[9] from render_tables.bin, which the CTW-Native Porter extracts from the game's binary: maps an
    instance's render list index to the list its translucent batches are drawn in. None if the file is missing."""
    path = os.path.join(data_dir, 'render_tables.bin')
    if not os.path.exists(path):
        return None
    d = open(path, 'rb').read()
    if d[:8] != b'CTWREND1' or len(d) < 8 + 36:
        return None
    return list(struct.unpack_from('<9i', d, 8))


# ---------------------------------------------------------------------------------------------------- world.bin
CELL = 50.0                  # collision cells, from (-3500, -2500), 100 x 100, index x * 100 + y
CELL_X0, CELL_Y0 = -3500.0, -2500.0
GROUND_N = 40                # ground map: 40 x 40 squares of 1.25 units per cell
SEA_Z, LAKE_Z, LAND_Z = -7.5, -2.5, 0.0


class CollisionCell:
    def __init__(self):
        self.boxes = []      # (cx, cy, cz, hx, hy, hz, angle_radians)
        self.cyls = []       # (x, y, zBase, radius, height)
        self.tris = []       # ((x, y, z) x 3)
        self.ground = None   # 40 x 40 list of 0 sea, 1 land, 2 lake, row-major [row][col]


class WorldCollision:
    def __init__(self, world_bin):
        self.w = world_bin

    def cell(self, cx, cy):
        if not (0 <= cx < 100 and 0 <= cy < 100):
            return None
        e, = struct.unpack_from('<I', self.w, (cx * 100 + cy) * 4)
        off, csz = (e & 0x1FFFF) * 64, ((e >> 17) & 0x7F) * 64
        if not csz or off + csz > len(self.w):
            return None
        try:
            d = zlib.decompressobj().decompress(self.w[off:off + csz])
        except zlib.error:
            return None
        secs, p = [], 0
        for _ in range(11):
            if p + 4 > len(d):
                secs.append(b'')
                continue
            n, = struct.unpack_from('<I', d, p)
            secs.append(d[p + 4:p + 4 + n])
            p += 4 + n
        C = CollisionCell()

        def count(s):
            return struct.unpack_from('<I', s, 0)[0] if len(s) >= 4 else 0

        k = count(secs[0])
        if k and len(secs[0]) >= 4 + k * 28:
            for i in range(k):
                cx_, cy_, cz, hx, hy, hz, angle = struct.unpack_from('<6ih', secs[0], 4 + i * 28)
                C.boxes.append((cx_ / FX, cy_ / FX, cz / FX, hx / FX, hy / FX, hz / FX, angle * 9.587378e-05))
        k = count(secs[1])
        if k and len(secs[1]) >= 4 + k * 24:
            for i in range(k):
                x, y, z, rad, h = struct.unpack_from('<5i', secs[1], 4 + i * 24)
                C.cyls.append((x / FX, y / FX, z / FX, rad / FX, h / FX))
        k, s, q = count(secs[2]), secs[2], 4
        for _ in range(k):
            if q + 20 > len(s):
                break
            nv, nt = struct.unpack_from('<HH', s, q + 16)
            q += 20
            if q + nv * 12 + nt * 40 > len(s):
                break
            verts = [tuple(c / FX for c in v) for v in struct.iter_unpack('<3i', s[q:q + nv * 12])]
            q += nv * 12
            for t in range(nt):
                a, b_, c = s[q + t * 40 + 12], s[q + t * 40 + 13], s[q + t * 40 + 14]
                if a < nv and b_ < nv and c < nv:
                    C.tris.append((verts[a], verts[b_], verts[c]))
            q += nt * 40
        g = secs[10]
        if len(g) >= 400:
            C.ground = [[(g[(row * 40 + col) >> 2] >> ((col & 3) * 2)) & 3 for col in range(40)] for row in range(40)]
        return C


def load_world_bin(data_dir):
    loose = os.path.join(data_dir, 'world.bin')
    if os.path.exists(loose):
        return open(loose, 'rb').read()
    return Wad(data_dir).read('world.bin')
