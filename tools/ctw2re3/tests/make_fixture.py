# SPDX-License-Identifier: GPL-3.0-only
"""Writes a tiny made-up CTW data folder (no game content) in the documented layouts, for testing ctw2re3:
two city blocks sharing one object, a sea tile, textures in four formats, and collision with every shape kind."""
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import ctwdata  # noqa: E402

FX = 4096
SEA_MODEL, LAKE_MODEL = 900, 901
BLOCKS = {(10, 5): 2, (11, 5): 3}          # block -> resource id
TEX_565, TEX_PVR, TEX_DXT, TEX_PNG = 4, 5, 6, 7
CAR_TABLE, CAR_MODEL = 1994, 1995


def cube_model(cx, cy, size, tex_a, tex_b, translucent=False):
    """A 'size' cube centred at (cx, cy, size / 2) in world units: four walls (tex_a, strip) and a roof (tex_b)."""
    scale = 1 / 64.0
    s = size / 2

    def v(x, y, z, nx, ny, nz, u, w):
        return struct.pack('<8h', int(round(x / scale)), int(round(y / scale)), int(round(z / scale)),
                           int(nx * 32767), int(ny * 32767), int(nz * 32767), int(u * 2048), int(w * 2048))

    walls = []
    corners = [(-s, -s, 0, -1), (s, -s, 1, 0), (s, s, 0, 1), (-s, s, -1, 0), (-s, -s, 0, -1)]
    for i in range(4):
        (x0, y0, nx, ny), (x1, y1, _, _) = corners[i], corners[i + 1]
        nrm = ((0, -1), (1, 0), (0, 1), (-1, 0))[i]
        quad = [v(cx + x0, cy + y0, 0, nrm[0], nrm[1], 0, 0, 1), v(cx + x0, cy + y0, size, nrm[0], nrm[1], 0, 0, 0),
                v(cx + x1, cy + y1, 0, nrm[0], nrm[1], 0, 1, 1), v(cx + x1, cy + y1, size, nrm[0], nrm[1], 0, 1, 0)]
        if walls:   # stitch strips with degenerate vertices
            walls += [walls[-1], quad[0]]
        walls += quad
    roof = [v(cx - s, cy - s, size, 0, 0, 1, 0, 0), v(cx + s, cy - s, size, 0, 0, 1, 1, 0),
            v(cx - s, cy + s, size, 0, 0, 1, 0, 1), v(cx + s, cy + s, size, 0, 0, 1, 1, 1)]
    verts = walls + roof
    # positions are model-space; the block matrix adds the block translation, so subtract it in the caller
    head = b'MG' + struct.pack('<HBBH', 0, 2, 2, len(verts)) + b'\0' * 8
    bb = struct.pack('<6i', int((cx - s) * FX), int((cy - s) * FX), 0, int((cx + s) * FX), int((cy + s) * FX),
                     int(size * FX)) + struct.pack('<ff', scale, 1 / scale)
    node = struct.pack('<9h', 4096, 0, 0, 0, 4096, 0, 0, 0, 4096) + struct.pack('<bB', 0, 0) + struct.pack('<3i', 0, 0, 0)
    recs = struct.pack('<HHBBBBI', tex_a, len(walls), 0x08 if translucent else 0x01, 0, 160, 0, 1)
    recs += struct.pack('<HHBBBBI', tex_b, len(roof), 0x01, 1, 255, 0, 1)
    return head + bb + b''.join(verts) + node + recs


def car_model(tex):
    """A box car on four painted-on wheels, with a door on each side (nodes 1 and 2), all in paint 26."""
    scale = 1 / 64.0

    def v(x, y, z, nz=1.0):
        return struct.pack('<8h', int(round(x / scale)), int(round(y / scale)), int(round(z / scale)),
                           0, 0, int(nz * 32767), 0, 0)

    def quad(a, b, c, d):
        return [v(*a), v(*b), v(*c), v(*d)]

    body = quad((-1, -2.5, 1.3), (1, -2.5, 1.3), (-1, 2.5, 1.3), (1, 2.5, 1.3))          # roof
    wheels = []
    for x in (-1.0, 1.0):
        for y in (-1.5, 1.5):
            if wheels:
                wheels += [wheels[-1], v(x, y - 0.3, 0.0)]
            wheels += quad((x, y - 0.3, 0.0), (x, y + 0.3, 0.0), (x, y - 0.3, 0.6), (x, y + 0.3, 0.6))
    door = quad((0, 0, 0.4), (0, -1, 0.4), (0, 0, 1.2), (0, -1, 1.2))                   # hinge at the node
    verts = body + wheels + door + door
    head = b'MG' + struct.pack('<HBBH', 0, 3, 4, len(verts)) + b'\0' * 8
    bb = struct.pack('<6i', -FX, int(-2.5 * FX), 0, FX, int(2.5 * FX), int(1.3 * FX)) + struct.pack('<ff', scale, 1 / scale)
    nodes = b''.join(struct.pack('<9h', 4096, 0, 0, 0, 4096, 0, 0, 0, 4096) + struct.pack('<bB', 0, 0) +
                     struct.pack('<3i', int(x * FX), int(0.8 * FX), 0) for x in (-1, 1))
    paint = 1 << 26
    recs = struct.pack('<HHBBBBI', tex, len(body), 1, 0, 255, 0, paint)
    recs += struct.pack('<HHBBBBI', tex, len(wheels), 1, 0, 255, 0, paint)
    recs += struct.pack('<HHBBBBI', tex, len(door), 1, 1, 255, 0, paint)
    recs += struct.pack('<HHBBBBI', tex, len(door), 1, 2, 255, 0, paint)
    return head + bb + b''.join(verts) + nodes + recs


def car_table(model):
    rec = bytearray(0x138)
    struct.pack_into('<HHI', rec, 0, 0, model, 0x06000000)
    rec[8:8 + 15] = b'Admiral.vehicle'
    struct.pack_into('<3h', rec, 0x28, int(1.0 * FX), int(2.5 * FX), int(0.65 * FX))
    struct.pack_into('<3h', rec, 0x38, int(0.3 * FX), int(1.5 * FX), int(-1.5 * FX))
    rec[0x94:0x98] = bytes((1, 2, 255, 255))
    rec[0x110] = 255
    struct.pack_into('<3i', rec, 0xDC, int(-0.4 * FX), 0, int(0.5 * FX))
    return struct.pack('<I', 1) + bytes(rec)


def build_block(c, r, objects):
    """Lays the block out exactly: header (0x20), offsets (0x28), sub-record at so=8 -> fields at 0x30..0x3B,
    instance table at so + 0x34 = 0x3C, models after."""
    tx, ty = 120 * c - 3480, 120 * r - 2460
    n = len(objects)
    out = bytearray(struct.pack('<9h', 4096, 0, 0, 0, 4096, 0, 0, 0, 4096) + b'\0\0' + struct.pack('<3i', tx * FX, ty * FX, 0))
    out += struct.pack('<4H', 8, 8, 8, 8)
    out += b'\0' * 8                                               # 0x28 .. 0x2F (so + 0x20 .. unused)
    out += struct.pack('<BBHHHHH', 0, 0, n, 0, 0, 0, 0)[0:2]       # so + 0x28: two bytes
    out += struct.pack('<HHHHH', n, 0, 0, 0, 0)                    # so + 0x2A: instances, n20a, groups, lights, tex
    assert len(out) == 0x3C
    table_at = len(out)
    out += b'\0' * (16 * n)
    out += b'\0' * (-len(out) % 16)
    for k, (slot, model, mid, bb) in enumerate(objects):
        off = len(out)
        if model is None:
            data = b'MG' + struct.pack('<HBBH', 0, 1, 0, 0) + b'\0' * 8 + struct.pack('<6i', *bb) + struct.pack('<ff', 1 / 64, 64)
        else:
            data = model[:0x10] + struct.pack('<6i', *bb) + model[0x28:]
        out += data + b'\0' * (-len(data) % 16)
        struct.pack_into('<HBBIII', out, table_at + 16 * k, slot, 0, 0, mid, off, 0)
    return bytes(out)


def pak_bytes(resources):
    """resources: {id: bytes}. Everything in segment 0, one resource per run of pages."""
    count = max(resources) + 1
    table = [0xFFFF] * count
    pages, page = [], 1
    for rid in range(count):
        data = resources.get(rid)
        if data is None:
            continue
        n = (len(data) + 4095) // 4096
        table[rid] = page
        pages.append(data + b'\0' * (n * 4096 - len(data)))
        page += n
    head = struct.pack('<6I', 1, count + 1, count + 1, count + 1, count, page) + struct.pack('<%dH' % count, *table)
    assert len(head) <= 4096
    return head + b'\0' * (4096 - len(head)) + b''.join(pages)


def main(out):
    os.makedirs(out, exist_ok=True)
    c0, r0 = 10, 5
    bx, by = ctwdata.block_centre(c0, r0)
    tx, ty = 120 * c0 - 3480, 120 * r0 - 2460

    def cube(cx, cy, size, ta, tb, tr, translucent=False):
        m = cube_model(cx - tr[0], cy - tr[1], size, ta, tb, translucent)
        s = size / 2
        return m, (int((cx - s) * FX), int((cy - s) * FX), 0, int((cx + s) * FX), int((cy + s) * FX), int(size * FX))

    t0 = (tx, ty)
    t1 = (tx + 120, ty)
    a, abb = cube(bx - 20, by, 10, TEX_565, TEX_DXT, t0)
    shared0, sbb = cube(bx + 60, by + 20, 8, TEX_PVR, TEX_PNG, t0, translucent=True)
    shared1, _ = cube(bx + 60, by + 20, 8, TEX_PVR, TEX_PNG, t1, translucent=True)
    b, bbb = cube(bx + 140, by - 30, 30, TEX_DXT, TEX_565, t1)
    sea_bb = (int((bx - 40) * FX), int((by - 50) * FX), int(-8 * FX), int((bx - 20) * FX), int((by - 30) * FX), int(-7 * FX))
    res = {
        0: b'\0' * 64,
        1: None,
        2: build_block(c0, r0, [(1, a, 50, abb), (2, shared0, 51, sbb), (3, None, SEA_MODEL, sea_bb)]),
        3: build_block(c0 + 1, r0, [(2, shared1, 51, sbb), (4, b, 52, bbb)]),
    }
    hdr = bytearray(8 + 42 * 0x76)
    struct.pack_into('<HH', hdr, 0, SEA_MODEL, LAKE_MODEL)
    for r in range(42):
        for c in range(59):
            struct.pack_into('<H', hdr, 8 + r * 0x76 + c * 2, BLOCKS.get((c, r), 0xFFFF))
    res[1] = bytes(hdr)
    # textures: RGB565 8x8 and PVRTC 4bpp 8x8 in game.pak, DXT1 8x8 in dxt.bin, an 8x8 RGBA PNG in game.pak
    res[TEX_565] = struct.pack('<HHHHI', 8, 8, 0x8363, 0, 128) + struct.pack('<H', 0xF800) * 64
    res[TEX_PVR] = struct.pack('<HHHHI', 8, 8, 0x8C02, 0, 32) + struct.pack('<II', 0x55555555, 0xFFFF801F) * 4
    res[TEX_DXT] = struct.pack('<HHHHI', 8, 8, 0x83F0, 0, 32) + struct.pack('<HHI', 0x07E0, 0x001F, 0) * 4

    def png(w, h, rgba):
        raw = b''.join(b'\0' + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))

        def ch(t, d):
            return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xFFFFFFFF)
        return b'\x89PNG\r\n\x1a\n' + ch(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)) + \
            ch(b'IDAT', zlib.compress(raw)) + ch(b'IEND', b'')
    p = png(8, 8, bytes((0, 0, 255, 128)) * 64)
    res[TEX_PNG] = struct.pack('<HHHHI', 8, 8, 0xBEEF, 0, len(p)) + p
    res[CAR_TABLE] = car_table(CAR_MODEL)
    res[CAR_MODEL] = car_model(TEX_565)
    open(os.path.join(out, 'game.pak'), 'wb').write(pak_bytes(res))

    dxt = bytearray(0x8000)
    struct.pack_into('<I', dxt, TEX_DXT * 4, len(dxt))
    dxt += struct.pack('<HHHHI', 8, 8, 0x83F0, 0, 32) + struct.pack('<HHI', 0x07E0, 0x001F, 0xAAAAAAAA) * 4
    open(os.path.join(out, 'dxt.bin'), 'wb').write(bytes(dxt))

    # world.bin: cells covering the first block get a rotated box, an axis-aligned box, a cylinder, a mesh and a
    # ground map (land with a lake corner); every other cell of the two blocks is plain land
    def path_nodes(nodes):
        """10-byte CTW path nodes from (x, y, z, flags, [links]); returns (links, nodes) bytes."""
        links, out = [], b''
        for x, y, z, flags, to in nodes:
            out += struct.pack('<HHhhbB', len(links), flags | len(to) << 2, int(x * 8), int(y * 8), int(z * 2), 0)
            links += to
        return struct.pack('<%dH' % len(links), *links), out, len(links)

    def pavement(nodes):
        links, packed, nl = path_nodes(nodes)
        off = 0x2C + len(links)
        return bytes(0x20) + struct.pack('<IHHI', off, nl, len(nodes), 0) + links + packed

    def cell_data(cx, cy, rich, walk=None):
        secs = [b''] * 11
        if walk:
            secs[3] = pavement(walk)
        g = bytearray(400)
        for row in range(40):
            for col in range(40):
                t = 2 if (rich and row < 8 and col < 8) else 1
                idx = row * 40 + col
                g[idx >> 2] |= t << ((col & 3) * 2)
        secs[10] = bytes(g)
        if rich:
            x0, y0 = -3500 + cx * 50, -2500 + cy * 50
            boxes = struct.pack('<6ihH', (x0 + 25) * FX, (y0 + 25) * FX, 2 * FX, 3 * FX, 1 * FX, 2 * FX, 0x2000, 0)
            boxes += struct.pack('<6ihH', (x0 + 10) * FX, (y0 + 40) * FX, 1 * FX, 2 * FX, 2 * FX, 1 * FX, 0, 0)
            secs[0] = struct.pack('<I', 2) + boxes
            secs[1] = struct.pack('<I', 1) + struct.pack('<6i', (x0 + 40) * FX, (y0 + 10) * FX, 0, FX // 2, 3 * FX, 0)
            mv = [(x0 + 30, y0 + 30, 0), (x0 + 40, y0 + 30, 0), (x0 + 30, y0 + 40, 4)]
            mesh = struct.pack('<4iHH', 0, 0, 0, 0, 3, 1) + b''.join(struct.pack('<3i', *(c * FX for c in v)) for v in mv)
            mesh += bytes((0, 0, 0, 0) * 3) + bytes((0, 1, 2, 0)) + b'\0' * 24
            secs[2] = struct.pack('<I', 1) + mesh
        d = b''.join(struct.pack('<I', len(s)) + s for s in secs)
        return zlib.compress(d)

    entries = [0] * 10000
    blob = bytearray((len(entries) * 4 + 63) & ~63)
    x_first = int((ctwdata.ORIGIN_X + 120 * c0 - ctwdata.CELL_X0) // 50)
    y_first = int((ctwdata.ORIGIN_Y + 120 * r0 - ctwdata.CELL_Y0) // 50)
    for cx in range(x_first, x_first + 6):
        for cy in range(y_first, y_first + 3):
            # a pavement along y0 + 5 across the first two cells, joined at their shared edge
            x0, y0 = -3500 + cx * 50, -2500 + cy * 50
            walk = None
            if (cx, cy) == (x_first, y_first):
                walk = [(x0 + 10, y0 + 5, 0, 0, [1]), (x0 + 30, y0 + 5, 0, 0, [2]), (x0 + 50, y0 + 5, 0, 0x80, [])]
            elif (cx, cy) == (x_first + 1, y_first):
                walk = [(x0, y0 + 5, 0, 0x80, [1]), (x0 + 20, y0 + 5, 0, 0, [2]), (x0 + 20, y0 + 25, 0, 0, [])]
            z = cell_data(cx, cy, cx == x_first + 1 and cy == y_first + 1, walk)
            off = len(blob)
            csz = (len(z) + 63) // 64
            blob += z + b'\0' * (csz * 64 - len(z))
            entries[cx * 100 + cy] = (off // 64) | (csz << 17) | (4 << 24)
    struct.pack_into('<10000I', blob, 0, *entries)
    world = bytes(blob)

    # ai.bin: a two-way road crossing that pavement at x0 + 20 (r0 - r1, with a node in the middle that goes away),
    # then a one-way street (r1 -> r3), a restricted node and a boat node, which are left out
    x0, y0 = -3500 + x_first * 50, -2500 + y_first * 50
    two = 1 << 5 | 1 << 9
    road = [(x0 + 20, y0 - 10, 0, two, [1]), (x0 + 20, y0 + 5, 0, two, [0, 2]), (x0 + 20, y0 + 20, 0, two, [1, 3]),
            (x0 + 20, y0 + 40, 0, 0, [4, 5]), (x0 + 20, y0 + 60, 0, 1 << 11, []), (x0 + 40, y0 + 40, -6, 0, [])]
    links, packed, nl = path_nodes(road)
    ai = struct.pack('<4I', nl, len(road), 0, 0x10 + len(links)) + links + packed

    wad, toc = b'', b''
    for name, d in sorted((('world.bin', world), ('ai.bin', ai)), key=lambda f: ctwdata.wad_hash(f[0])):
        toc += struct.pack('<III', ctwdata.wad_hash(name), len(wad), len(d))
        wad += d + b'\0' * (-len(d) % 64)
    open(os.path.join(out, 'rom.wad'), 'wb').write(wad)
    open(os.path.join(out, 'rom.toc'), 'wb').write(toc)


if __name__ == '__main__':
    main(sys.argv[1])
