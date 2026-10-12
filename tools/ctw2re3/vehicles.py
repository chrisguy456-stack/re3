# SPDX-License-Identifier: GPL-3.0-only
"""CTW's cars as GTA 3 vehicle models.

The vehicle table is game.pak resource 1994 (CTW-Native port/game/vehicle.cpp, Vehicle::init): u32 count, then
0x138-byte records: +0 u16 type (0 car), +2 u16 model resource, +4 u32 paint mask, +8 name, +0x28/+0x2A/+0x2C s16 half
width, half length, half height (1/4096), +0x38 wheel radius, +0x3A front axle y, +0x3C rear axle y, +0x94 four door
node ids (255 = none), +0xC4 rear lamp xyz and +0xD0 headlamp xyz (s32), +0xDC four seat xyz (s32), +0x110 boot node.

The models are cModels like the city's (+y forward, z up, the ground at z 0). Each paint job is its own set of batches:
batch mask bit p is paint p; 25 is the burnt wreck, 26 the default. Wheels are part of the body, so the ones under the
wheel arches are cut out and GTA 3's own wheels go in their place, where they can turn.

A GTA 3 car model has named frames: the body ("chassis_hi", "chassis_vlo"), wheel_lf_dummy etc. where the game puts
the wheels, door_lf_dummy etc. that it swings open, bonnet_dummy and boot_dummy, and positions for the seat and lights.
"""
import math
import struct

import gtaformats
import rwwriter
from ctwdata import FX, Model

INFO_RES = 1994
RECORD = 0x138
WRECK, DEFAULT_PAINT = 25, 26
WHEEL_MODEL = 250       # GTA 3's wheel_rim
TEXTURE_PREFIX = 'v'    # vehicle textures are their own copies, in one dictionary

# Name, GTA 3 handling line, traffic class and how often it shows up (0: never in traffic). The handling lines are
# GTA 3's own, picked for a car of the same kind; its engine sounds follow the same pick.
TRAFFIC = {
    'admiral': ('SENTINEL', 'richfamily', 10), 'blista': ('BLISTA', 'poorfamily', 10),
    'dukes': ('STALLION', 'poorfamily', 8), 'buccaneer': ('MANANA', 'poorfamily', 8),
    'cabbie': ('CABBIE', 'taxi', 10), 'hellenbach': ('KURUMA', 'richfamily', 8),
    'banshee': ('BANSHEE', 'executive', 6), 'comet': ('CHEETAH', 'executive', 6),
    'coquette': ('CHEETAH', 'executive', 6), 'cognoscenti': ('SENTINEL', 'executive', 8),
    'chavos': ('MANANA', 'poorfamily', 10), 'cavcade': ('LANDSTAL', 'richfamily', 8),
    'ext': ('LANDSTAL', 'richfamily', 8), 'bobcat': ('BOBCAT', 'worker', 8), 'yankee': ('YANKEE', 'worker', 6),
    'burrito': ('RUMPO', 'worker', 8), 'flatbed': ('FLATBED', 'big', 4), 'tanker': ('LINERUN', 'big', 3),
    'infernus': ('INFERNUS', 'executive', 4), 'patriot': ('PATRIOT', 'richfamily', 6),
    'sabregt': ('STALLION', 'poorfamily', 6), 'stallion': ('STALLION', 'poorfamily', 6),
    'sultan': ('KURUMA', 'richfamily', 8), 'peyote': ('MANANA', 'poorfamily', 6), 'ingot': ('KURUMA', 'poorfamily', 8),
    'perren': ('PEREN', 'poorfamily', 8), 'limo': ('STRETCH', 'executive', 3), 'bus': ('BUS', 'big', 3),
    'alphamail': ('PONY', 'worker', 4),
    # in the game, but not driving around on their own
    'police': ('POLICE', 'special', 0), 'swat': ('ENFORCER', 'special', 0), 'ambulance': ('AMBULAN', 'special', 0),
    'firetruck': ('FIRETRUK', 'special', 0), 'fbirancher': ('FBICAR', 'special', 0),
    'recovery': ('FLATBED', 'special', 0), 'hearse': ('SENTINEL', 'special', 0),
}
HANDLING_BY_PREFIX = (('burrito', 'RUMPO'), ('gang_burr', 'RUMPO'), ('yankee', 'YANKEE'), ('streetracer', 'KURUMA'),
                      ('gang_banshee', 'BANSHEE'), ('gang_comet', 'CHEETAH'), ('gang_coquette', 'CHEETAH'))

ARCH_MARGIN = 1.15      # how far around a wheel's radius body triangles count as that wheel


class VehicleInfo:
    def __init__(self, d, i):
        r = 4 + i * RECORD
        self.index = i
        self.type, self.model, self.paints = struct.unpack_from('<HHI', d, r)
        self.name = d[r + 8:r + 40].split(b'\0')[0].decode('latin-1')
        hw, hl, hh = struct.unpack_from('<3h', d, r + 0x28)
        self.half = (hw / FX, abs(hl) / FX, hh / FX)
        radius, front, rear = struct.unpack_from('<3h', d, r + 0x38)
        self.radius, self.front_axle, self.rear_axle = radius / FX, front / FX, rear / FX
        self.doors = tuple(d[r + 0x94:r + 0x98])
        self.boot = d[r + 0x110]
        self.rear_lamp = tuple(v / FX for v in struct.unpack_from('<3i', d, r + 0xC4))
        self.head_lamp = tuple(v / FX for v in struct.unpack_from('<3i', d, r + 0xD0))
        self.seat = tuple(v / FX for v in struct.unpack_from('<3i', d, r + 0xDC))

    def paint(self):
        """The paint job converted: the first of the car's random ones, or its default."""
        p = self.paints & ((1 << WRECK) - 1)
        return (p & -p).bit_length() - 1 if p else DEFAULT_PAINT


def read_table(pak):
    d = pak.read(INFO_RES)
    if len(d) < 4:
        return []
    n, = struct.unpack_from('<I', d, 0)
    if n == 0 or 4 + n * RECORD > len(d):
        return []
    return [VehicleInfo(d, i) for i in range(n)]


def model_name(info):
    base = ''.join(c for c in info.name.lower().replace('.vehicle', '') if c.isalnum() or c == '_')
    return ('ctw' + base)[:20]


def traffic_of(info):
    key = info.name.lower().replace('.vehicle', '')
    if key in TRAFFIC:
        return TRAFFIC[key]
    for prefix, handling in HANDLING_BY_PREFIX:
        if key.startswith(prefix):
            return handling, 'special', 0
    return 'KURUMA', 'special', 0


def _node_triangles(m, node, paint):
    """The triangles of one node in node space (unrotated: the rotation is applied, the translation is not)."""
    R, _ = m.world[node]
    s = m.scale
    out = []
    for mb in m.batches:
        if mb['node'] != node or not mb['mask'] & (1 << paint):
            continue
        vs = m.verts[mb['first']:mb['first'] + mb['count']]
        for i in range(2, len(vs)):
            tri = [vs[i - 2], vs[i - 1], vs[i]]
            if i & 1:
                tri[1], tri[2] = tri[2], tri[1]
            if tri[0][:3] == tri[1][:3] or tri[1][:3] == tri[2][:3] or tri[0][:3] == tri[2][:3]:
                continue
            corners = []
            for v in tri:
                p = (v[0] * s, v[1] * s, v[2] * s)
                n = (v[3] / 32767.0, v[4] / 32767.0, v[5] / 32767.0)
                corners.append((tuple(sum(R[a][j] * p[j] for j in range(3)) for a in range(3)),
                                tuple(sum(R[a][j] * n[j] for j in range(3)) for a in range(3)),
                                (v[6] / 2048.0, v[7] / 2048.0)))
            out.append((mb, corners))
    return out


def wheel_spots(info, body):
    """Where the wheels go: (x, y) of the four wheel centres, from the axles and the body's width."""
    xs = [abs(p[0]) for _, tri in body for p, _, _ in tri]
    half = max(xs) if xs else info.half[0]
    x = max(half - 0.25 * info.radius - 0.1, 0.2)
    return {'lf': (-x, info.front_axle), 'rf': (x, info.front_axle),
            'lb': (-x, info.rear_axle), 'rb': (x, info.rear_axle)}, half


def in_wheel(p, spots, info, half):
    """Whether a point lies in a wheel's space: near a wheel centre along y, low down and near the sides."""
    r = info.radius * ARCH_MARGIN
    if p[2] > 2 * r:
        return False
    if abs(p[0]) < half - 0.9 * info.radius:
        return False
    return any(abs(p[1] - y) <= r for _, y in spots.values())


def _mesh(tris, texname_of):
    m = rwwriter.Mesh()
    for mb, corners in tris:
        alpha = mb['alpha'] if mb['flags'] & 0x08 else 255
        mat = m.material(texname_of(mb, alpha), (255, 255, 255, alpha))
        ids = [m.vertex(p, n, uv, (255, 255, 255, 255)) for p, n, uv in corners]
        m.tris.append((ids[0], ids[1], ids[2], mat))
    return m


def build(info, model_bytes, texsrc):
    """(dff, col, texture names used) for one car."""
    m = Model(model_bytes)
    paint = info.paint()
    texnames = set()

    def texname_of(mb, alpha):
        tid = mb['texture']
        name = TEXTURE_PREFIX + ('t' if alpha < 255 and texsrc.get(tid).alpha else 'o') + str(tid)
        texnames.add(name)
        return name

    doors = {}
    door_nodes = [n for n in info.doors if n != 255 and 0 < n < len(m.world)]
    for n in door_nodes:
        _, T = m.world[n]
        doors[n] = ('l' if T[0] < 0 else 'r', T[1])
    # front and back doors on each side, by position
    names = {}
    for side in 'lr':
        ns = sorted((n for n, (s, _) in doors.items() if s == side), key=lambda n: -doors[n][1])
        for n, part in zip(ns, ('f', 'r')):
            names[n] = 'door_%s%s' % (side, part)
    parts = {}   # node -> frame name
    body = _node_triangles(m, 0, paint)
    for n in range(1, len(m.world)):
        tris = _node_triangles(m, n, paint)
        if not tris:
            continue
        if n in names:
            parts[n] = names[n]
        else:
            # a lid: the bonnet in front of the middle, the boot behind (by where its geometry is)
            _, T = m.world[n]
            ys = [p[1] + T[1] for _, tri in tris for p, _, _ in tri]
            lid = 'bonnet' if sum(ys) / len(ys) > 0 else 'boot'
            if lid in parts.values():
                # a second piece: fold it into the body where it sits
                body += [(mb, [(tuple(p[i] + T[i] for i in range(3)), nn, uv) for p, nn, uv in tri]) for mb, tri in tris]
                continue
            parts[n] = lid

    spots, half = wheel_spots(info, body)
    kept = [t for t in body if not all(in_wheel(p, spots, info, half) for p, _, _ in t[1])]
    cut = len(body) - len(kept)

    meshes = [_mesh(kept, texname_of)]
    frames = [(model_name(info), -1, (0.0, 0.0, 0.0)), ('chassis_hi', 0, (0.0, 0.0, 0.0)),
              ('chassis_vlo', 0, (0.0, 0.0, 0.0))]
    atomics = [(1, 0), (2, 0)]
    for w, (x, y) in sorted(spots.items()):
        frames.append(('wheel_%s_dummy' % w, 0, (x, y, info.radius)))
    for n, part in sorted(parts.items()):
        _, T = m.world[n]
        meshes.append(_mesh(_node_triangles(m, n, paint), texname_of))
        frames.append((part + '_dummy', 0, T))
        dummy = len(frames) - 1
        # the same geometry shown whole ("_ok") and damaged ("_dam"), which GTA 3 swaps when it is hit
        frames.append((part + '_hi_ok', dummy, (0.0, 0.0, 0.0)))
        atomics.append((len(frames) - 1, len(meshes) - 1))
        frames.append((part + '_hi_dam', dummy, (0.0, 0.0, 0.0)))
        atomics.append((len(frames) - 1, len(meshes) - 1))

    seat = info.seat if any(info.seat) else (-0.4 * half, 0.0, 0.5 * info.half[2])
    frames.append(('ped_frontseat', 0, (abs(seat[0]), seat[1], seat[2])))   # the driver sits at -x
    if any(info.head_lamp):
        frames.append(('headlights', 0, (abs(info.head_lamp[0]), info.head_lamp[1], info.head_lamp[2])))
    if any(info.rear_lamp):
        frames.append(('taillights', 0, (abs(info.rear_lamp[0]), info.rear_lamp[1], info.rear_lamp[2])))

    return rwwriter.write_clump(frames, meshes, atomics), collision(info, meshes[0]), texnames, cut


def collision(info, body):
    """Spheres along the body (what GTA 3's cars bump the world with), a box for other cars, and four lines the
    game turns into the wheels' suspension."""
    lo, hi = body.bounds()
    hw = max(abs(lo[0]), abs(hi[0]), 0.3)
    top = max(hi[2], 0.5)
    r = min(hw * 0.95, top * 0.4)
    z = top - r
    y0, y1 = lo[1] + r, hi[1] - r
    n = max(2, int(math.ceil((y1 - y0) / r)) + 1)
    spheres = [((0.0, y0 + (y1 - y0) * k / (n - 1), z), r, 0) for k in range(n)]
    lines = [((0.0, 0.0, 0.0), (0.0, 0.0, -1.0))] * 4
    box = ((lo[0], lo[1], top * 0.3), (hi[0], hi[1], top), 0)
    bmin, bmax = (lo[0], lo[1], 0.0), (hi[0], hi[1], top)
    return box, spheres, lines, bmin, bmax


def convert(pak, texsrc, first_id):
    """IMG entries, IDE car lines, COL models and a summary for every car in the table."""
    files, cars, cols, report = [], [], [], []
    texnames = set()
    infos = [i for i in read_table(pak) if i.type == 0]
    for info in infos:
        name = model_name(info)
        try:
            dff, (box, spheres, lines, bmin, bmax), used, cut = build(info, pak.read(info.model), texsrc)
        except ValueError as e:
            report.append('%s: %s' % (info.name, e))
            continue
        handling, cls, freq = traffic_of(info)
        cars.append((first_id + len(cars), name, 'ctwveh', handling, name, cls, freq, WHEEL_MODEL,
                     2 * info.radius))
        files.append((name + '.dff', dff))
        cols.append(gtaformats.col1(name, bmin, bmax, boxes=[box], spheres=spheres, lines=lines))
        texnames |= used
        report.append('%s: %s, %s %d, %d wheel triangles cut' % (name, handling, cls, freq, cut))
    return files, cars, cols, texnames, report
