#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Converts the GTA: Chinatown Wars city into GTA 3 map files that re3 can load (build re3 with -DRE3_CTW_PROTOTYPE=ON).

Reads your own copy of the game's data (the data/ folder the CTW-Native Porter writes from the Android 4.4.243 APK:
game.pak, dxt.bin, rom.toc and rom.wad) and writes, under --out (normally your GTA 3 folder):

    models/ctw.img, models/ctw.dir      models (.dff) and texture dictionaries (.txd), per 120 x 120 city block
    data/maps/ctw/ctw.ide, ctw.ipl      the block definitions and their placements
    data/maps/ctw/ctw.col               collision, built from world.bin plus a ground plane from its ground map
    data/ctw.dat                        the level file re3 loads instead of gta3.dat

Nothing from either game is part of this tool; it only runs on files you provide.

    python tools/ctw2re3/ctw2re3.py --ctw-data "C:/Games/GTA Chinatown Wars PC/data" --out "C:/Games/GTA3"
"""
import argparse
import multiprocessing
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ctwdata                      # noqa: E402
import gtaformats                   # noqa: E402
import rwwriter                     # noqa: E402
import textures as ctwtex           # noqa: E402

LIGHT_DIR = (0.35, -0.45, 0.82)     # fake sun for the prelight: CTW lights the city from above
MAX_VERTS = 0xFFFF
MAX_COL_TRIS = 32000                # re3 reads the triangle count as int16
SURF_DEFAULT, SURF_TARMAC = 0, 1
WATER_RGBA = {True: (52, 86, 110, 200), False: (60, 96, 96, 200)}


def model_name(c, r, part=0):
    return 'ctw_%d_%d' % (c, r) + ('' if part == 0 else '_%d' % part)


# ------------------------------------------------------------------------------------------------ collision
def box_tris(cx, cy, cz, hx, hy, hz, angle):
    import math
    c, s = math.cos(angle), math.sin(angle)
    corners = []
    for sz in (-1, 1):
        for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            lx, ly = sx * hx, sy * hy
            corners.append((cx + c * lx - s * ly, cy + s * lx + c * ly, cz + sz * hz))
    q = [(0, 1, 2, 3), (7, 6, 5, 4), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]
    out = []
    for a, b, c_, d in q:
        out.append((corners[a], corners[c_], corners[b]))
        out.append((corners[a], corners[d], corners[c_]))
    return out


def cyl_tris(x, y, z, r, h, sides=8):
    import math
    ring = [(x + r * math.cos(2 * math.pi * i / sides), y + r * math.sin(2 * math.pi * i / sides)) for i in range(sides)]
    out = []
    for i in range(sides):
        a, b = ring[i], ring[(i + 1) % sides]
        out.append(((a[0], a[1], z), (b[0], b[1], z), (b[0], b[1], z + h)))
        out.append(((a[0], a[1], z), (b[0], b[1], z + h), (a[0], a[1], z + h)))
    for i in range(1, sides - 1):
        out.append(((ring[0][0], ring[0][1], z + h), (ring[i][0], ring[i][1], z + h),
                    (ring[i + 1][0], ring[i + 1][1], z + h)))
    return out


def ground_rects(cx, cy, cell):
    """The ground plane of one collision cell as (x0, y0, x1, y1, z) rectangles, split at block edges: runs of one
    ground type along each row, merged with identical runs in the rows above."""
    sq = ctwdata.CELL / ctwdata.GROUND_N
    x0, y0 = ctwdata.CELL_X0 + cx * ctwdata.CELL, ctwdata.CELL_Y0 + cy * ctwdata.CELL
    zs = {0: ctwdata.SEA_Z, 1: ctwdata.LAND_Z, 2: ctwdata.LAKE_Z, 3: ctwdata.SEA_Z}
    per_block = int(round(ctwdata.BLOCK / sq))
    gx0 = int(round((x0 - ctwdata.ORIGIN_X) / sq))   # square index from the block grid origin
    gy0 = int(round((y0 - ctwdata.ORIGIN_Y) / sq))
    out, open_runs = [], {}                          # (start, end, z) -> first row
    for row in range(ctwdata.GROUND_N + 1):
        runs = set()
        if row < ctwdata.GROUND_N:
            types = cell.ground[row] if cell.ground else [0] * ctwdata.GROUND_N
            start = 0
            for col in range(1, ctwdata.GROUND_N + 1):
                if col == ctwdata.GROUND_N or types[col] != types[start] or (gx0 + col) % per_block == 0:
                    runs.add((start, col, zs[types[start]]))
                    start = col
        # a run continues only with an identical run in this row, and never across a block edge
        new_open = {}
        for run, first in open_runs.items():
            if run in runs and (gy0 + row) % per_block != 0:
                new_open[run] = first
                runs.discard(run)
            else:
                out.append((x0 + run[0] * sq, y0 + first * sq, x0 + run[1] * sq, y0 + row * sq, run[2]))
        for run in runs:
            new_open[run] = row
        open_runs = new_open
    return out


def gather_collision(wc, blocks):
    """Distributes every collision shape to the block that holds its centre."""
    per = {b: {'boxes': [], 'tris': []} for b in blocks}

    def put(x, y, kind, item):
        b = per.get(ctwdata.block_of(x, y))
        if b is not None:
            b[kind].append(item)

    for cx in range(100):
        for cy in range(100):
            cell = wc.cell(cx, cy)
            if cell is None:
                continue
            for bx in cell.boxes:
                x, y, z, hx, hy, hz, angle = bx
                if abs(angle) < 1e-6:
                    put(x, y, 'boxes', ((x - hx, y - hy, z - hz), (x + hx, y + hy, z + hz), SURF_DEFAULT))
                else:
                    for t in box_tris(*bx):
                        put(x, y, 'tris', (t, SURF_DEFAULT))
            for x, y, z, r, h in cell.cyls:
                for t in cyl_tris(x, y, z, r, h):
                    put(x, y, 'tris', (t, SURF_DEFAULT))
            for t in cell.tris:
                put(sum(v[0] for v in t) / 3, sum(v[1] for v in t) / 3, 'tris', (t, SURF_DEFAULT))
            for x0, y0, x1, y1, z in ground_rects(cx, cy, cell):
                a, b, c, d = (x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)
                xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
                put(xm, ym, 'tris', ((a, b, c), SURF_TARMAC))
                put(xm, ym, 'tris', ((a, c, d), SURF_TARMAC))
    return per


# ------------------------------------------------------------------------------------------------ blocks
def assign_objects(world):
    """CTW lists an object in every block it overlaps; each one is converted once, in the first block listing it."""
    owner, plan = set(), {}
    for (c, r) in sorted(world.grid, key=lambda b: (b[1], b[0])):
        keys = []
        for item in world.instances(c, r):
            if item[1] not in owner:
                owner.add(item[1])
                keys.append(item[1])
        plan[(c, r)] = set(keys)
    return plan


_worker = {}


def _init_worker(data_dir):
    pak = ctwdata.Pak(os.path.join(data_dir, 'game.pak'))
    dxt_path = os.path.join(data_dir, 'dxt.bin')
    dxt = ctwdata.DxtBin(dxt_path) if os.path.exists(dxt_path) else None
    _worker['world'] = ctwdata.WorldMap(pak)
    _worker['tex'] = ctwtex.TextureSource(pak, dxt)


def prelight(n, lit):
    if not lit:
        return (255, 255, 255, 255)
    d = max(0.0, n[0] * LIGHT_DIR[0] + n[1] * LIGHT_DIR[1] + n[2] * LIGHT_DIR[2])
    v = int(255 * min(1.0, 0.55 + 0.5 * d))
    return (v, v, v, 255)


def build_block(job):
    (c, r), keys, col, zoff = job
    world, texsrc = _worker['world'], _worker['tex']
    ox, oy = ctwdata.block_centre(c, r)
    tris = []    # (material key, [(p, n, uv, colour)] x 3)
    for item in world.instances(c, r):
        if item[1] not in keys:
            continue
        if item[0] == 'water':
            x, y, z, sea = item[2]
            z += zoff
            mk = (None, WATER_RGBA[sea])
            p = [(x - 10 - ox, y - 10 - oy, z), (x + 10 - ox, y - 10 - oy, z), (x + 10 - ox, y + 10 - oy, z),
                 (x - 10 - ox, y + 10 - oy, z)]
            uv = [(0, 0), (1, 0), (1, 1), (0, 1)]
            v = [(p[i], (0, 0, 1), uv[i], (255, 255, 255, 255)) for i in range(4)]
            tris += [(mk, [v[0], v[1], v[2]]), (mk, [v[0], v[2], v[3]])]
            continue
        _, _, m, (M, T) = item
        for mb, tri in m.triangles():
            translucent = bool(mb['flags'] & 0x08)
            alpha = int(round((mb['alpha'] >> 3) / 31 * 255)) if translucent else 255
            # CTW draws opaque batches without blending, ignoring the texture's alpha: those use an 'o' copy of the
            # texture with its alpha thrown away; translucent batches use the texture as it is ('t')
            mk = (('t%d' if translucent else 'o%d') % mb['texture'], (255, 255, 255, alpha))
            lit = bool(mb['flags'] & 0x07)
            out = []
            for p, n, uv in tri:
                wp = tuple(M[a][0] * p[0] + M[a][1] * p[1] + M[a][2] * p[2] + T[a] for a in range(3))
                wn = tuple(M[a][0] * n[0] + M[a][1] * n[1] + M[a][2] * n[2] for a in range(3))
                out.append(((wp[0] - ox, wp[1] - oy, wp[2] + zoff), wn, uv, prelight(wn, lit)))
            tris.append((mk, out))

    # Opaque and translucent surfaces go in separate models, so re3 draws the translucent ones (IDE flag 4) after
    # everything opaque, as CTW does. Each is split further (by material) if it has more vertices than a geometry
    # can index. The first model carries the block's collision.
    parts = []
    for want_translucent in (False, True):
        by_mat = {}
        for mk, t in tris:
            if (mk[1][3] < 255) == want_translucent:
                by_mat.setdefault(mk, []).append(t)
        cur, verts = [], 0
        for mk, ts in by_mat.items():
            while ts:
                room = (MAX_VERTS - 3 - verts) // 3
                if room <= 0:
                    parts.append(cur)
                    cur, verts = [], 0
                    continue
                take, ts = ts[:room], ts[room:]
                cur.append((mk, take))
                verts += 3 * len(take)
        if cur:
            parts.append(cur)
    if not parts:
        parts.append([])

    out = []
    for i, part in enumerate(parts):
        mesh = rwwriter.Mesh()
        for mk, ts in part:
            mat = mesh.material(*mk)
            for t in ts:
                idx = [mesh.vertex(*v) for v in t]
                mesh.tris.append((idx[0], idx[1], idx[2], mat))
        if not mesh.pos:   # an empty block still needs a model for its collision: one invisible triangle
            mat = mesh.material(None, (0, 0, 0, 0))
            a = mesh.vertex((0, 0, zoff), (0, 0, 1), (0, 0), (0, 0, 0, 0))
            b = mesh.vertex((0.01, 0, zoff), (0, 0, 1), (0, 0), (0, 0, 0, 0))
            c_ = mesh.vertex((0, 0.01, zoff), (0, 0, 1), (0, 0), (0, 0, 0, 0))
            mesh.tris.append((a, b, c_, mat))
        name = model_name(c, r, i)
        texnames = sorted({t for t, _ in mesh.materials if t})
        txd = rwwriter.write_txd([(n, texsrc.get(int(n[1:])) if n[0] == 't' else ctwtex.opaque(texsrc.get(int(n[1:]))))
                                  for n in texnames])
        lo, hi = mesh.bounds()
        boxes, cverts, ctris = [], [], []
        if i == 0:
            vindex = {}

            def vid(p):
                k = (round(p[0] - ox, 3), round(p[1] - oy, 3), round(p[2] + zoff, 3))
                if k not in vindex:
                    vindex[k] = len(cverts)
                    cverts.append(k)
                return vindex[k]

            for (bl, bh, surf) in col['boxes']:
                boxes.append(((bl[0] - ox, bl[1] - oy, bl[2] + zoff), (bh[0] - ox, bh[1] - oy, bh[2] + zoff), surf))
            for t, surf in col['tris'][:MAX_COL_TRIS]:
                ctris.append((vid(t[0]), vid(t[1]), vid(t[2]), surf))
            pts = cverts + [p for b in boxes for p in b[:2]]
            if pts:
                lo = tuple(min([lo[k]] + [p[k] for p in pts]) for k in range(3))
                hi = tuple(max([hi[k]] + [p[k] for p in pts]) for k in range(3))
        colm = gtaformats.col1(name, lo, hi, boxes, cverts, ctris)
        translucent = any(cc[3] < 255 for _, cc in mesh.materials)
        out.append(dict(name=name, dff=rwwriter.write_dff(mesh), txd=txd, col=colm, flags=4 if translucent else 0,
                        pos=(ox, oy, 0.0), verts=len(mesh.pos), tris=len(mesh.tris),
                        coltris=len(ctris), dropped=max(0, len(col['tris']) - MAX_COL_TRIS) if i == 0 else 0,
                        maxz=hi[2]))
    return (c, r), out, sorted(texsrc.missing)


# ------------------------------------------------------------------------------------------------ main
def write(path, data):
    with open(path, 'wb' if isinstance(data, bytes) else 'w') as f:
        f.write(data)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--ctw-data', required=True, help='CTW data folder (game.pak, dxt.bin, rom.toc, rom.wad)')
    ap.add_argument('--out', required=True, help='where to write models/ and data/ (normally the GTA 3 folder)')
    ap.add_argument('--first-id', type=int, default=1000, help='first model id (GTA 3 map ids are free)')
    ap.add_argument('--draw-distance', type=int, default=299)
    ap.add_argument('--z-offset', type=float, default=0.0, help='raise the whole city by this much')
    ap.add_argument('--area', help='only blocks c0,r0,c1,r1 (inclusive), for quick tests')
    ap.add_argument('--jobs', type=int, default=0, help='worker processes (default: all cores)')
    a = ap.parse_args(argv)

    t0 = time.time()
    pak = ctwdata.Pak(os.path.join(a.ctw_data, 'game.pak'))
    world = ctwdata.WorldMap(pak)
    blocks = sorted(world.grid, key=lambda b: (b[1], b[0]))
    if a.area:
        c0, r0, c1, r1 = (int(v) for v in a.area.split(','))
        blocks = [b for b in blocks if c0 <= b[0] <= c1 and r0 <= b[1] <= r1]
    print('%d city blocks' % len(blocks))
    plan = assign_objects(world)
    wb = ctwdata.load_world_bin(a.ctw_data)
    if wb is None:
        print('warning: world.bin not found, the city will have no collision')
        col = {b: {'boxes': [], 'tris': []} for b in blocks}
    else:
        col = gather_collision(ctwdata.WorldCollision(wb), blocks)
    print('collision gathered (%.0fs)' % (time.time() - t0))

    models = os.path.join(a.out, 'models')
    maps = os.path.join(a.out, 'data', 'maps', 'ctw')
    os.makedirs(models, exist_ok=True)
    os.makedirs(maps, exist_ok=True)
    img = gtaformats.ImgWriter(os.path.join(models, 'ctw.img'))
    objs, insts, cols, missing = [], [], [], set()
    stats = dict(verts=0, tris=0, coltris=0, dropped=0, parts=0, maxz=0.0)
    jobs = [(b, plan[b], col[b], a.z_offset) for b in blocks]
    procs = a.jobs or multiprocessing.cpu_count()
    with multiprocessing.Pool(procs, _init_worker, (a.ctw_data,)) as pool:
        for n, (b, parts, miss) in enumerate(pool.imap(build_block, jobs, chunksize=4)):
            missing.update(miss)
            for p in parts:
                mid = a.first_id + len(objs)
                objs.append((mid, p['name'], p['name'], a.draw_distance, p['flags']))
                insts.append((mid, p['name']) + p['pos'])
                img.add(p['name'] + '.dff', p['dff'])
                img.add(p['name'] + '.txd', p['txd'])
                cols.append(p['col'])
                for k in ('verts', 'tris', 'coltris', 'dropped'):
                    stats[k] += p[k]
                stats['maxz'] = max(stats['maxz'], p['maxz'])
                stats['parts'] += 1
            if (n + 1) % 50 == 0 or n + 1 == len(jobs):
                print('  %d / %d blocks (%.0fs)' % (n + 1, len(jobs), time.time() - t0))
    img.close(os.path.join(models, 'ctw.dir'))
    write(os.path.join(maps, 'ctw.ide'), gtaformats.ide(objs))
    write(os.path.join(maps, 'ctw.ipl'), gtaformats.ipl(insts))
    write(os.path.join(maps, 'ctw.col'), b''.join(cols))
    write(os.path.join(a.out, 'data', 'ctw.dat'), '\n'.join([
        '# GTA: Chinatown Wars city for re3 (CTW_PROTOTYPE), written by tools/ctw2re3',
        'CDIMAGE MODELS\\CTW.IMG',
        'IDE DATA\\MAPS\\CTW\\CTW.IDE',
        'COLFILE 0 DATA\\MAPS\\CTW\\CTW.COL',
        'IPL DATA\\MAPS\\CTW\\CTW.IPL',
        'SPLASH loadsc0',
        '']))
    print('done in %.0fs: %d models, %d vertices, %d triangles, %d collision triangles'
          % (time.time() - t0, stats['parts'], stats['verts'], stats['tris'], stats['coltris']))
    if stats['dropped']:
        print('warning: %d collision triangles dropped (over %d in a block)' % (stats['dropped'], MAX_COL_TRIS))
    if stats['maxz'] >= 256:
        print('note: the tallest block reaches z %.0f; re3 warns about collision above 256' % stats['maxz'])
    if missing:
        print('warning: %d textures could not be read and are magenta: %s'
              % (len(missing), ', '.join(str(t) for t in sorted(missing)[:20])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
