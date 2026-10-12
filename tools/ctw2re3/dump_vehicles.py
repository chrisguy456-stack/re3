#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Prints CTW's vehicle table (game.pak resource 1994) and the parts of each vehicle model, to plan the vehicle
conversion. Layout from CTW-Native port/game/vehicle.cpp (Vehicle::init) and port/src/gfx/model.h.

    python tools/ctw2re3/dump_vehicles.py "C:/Games/GTA Chinatown Wars PC/data" > vehicles.txt
"""
import os
import struct
import sys

import ctwdata

FX = ctwdata.FX
INFO_RES = 1994
RECORD = 0x138
TYPES = {0: 'car', 1: 'bike', 2: 'boat', 3: 'boat'}


def node_extents(m):
    """Per node: number of batches, palettes used, and the bounding box of its vertices in node space."""
    out = {}
    s = m.scale
    for b in m.batches:
        e = out.setdefault(b['node'], dict(batches=0, mask=0, lo=[1e9] * 3, hi=[-1e9] * 3))
        e['batches'] += 1
        e['mask'] |= b['mask']
        for v in m.verts[b['first']:b['first'] + b['count']]:
            for i in range(3):
                e['lo'][i] = min(e['lo'][i], v[i] * s)
                e['hi'][i] = max(e['hi'][i], v[i] * s)
    return out


def main(argv):
    pak = ctwdata.Pak(os.path.join(argv[1], 'game.pak'))
    d = pak.read(INFO_RES)
    n, = struct.unpack_from('<I', d, 0)
    print('%d vehicles' % n)
    for i in range(n):
        r = 4 + i * RECORD
        vtype, model, pmask = struct.unpack_from('<HHI', d, r)
        name = d[r + 8:r + 40].split(b'\0')[0].decode('latin-1')
        w, l, h, cog = struct.unpack_from('<4h', d, r + 0x28)
        radius, front, rear = struct.unpack_from('<3h', d, r + 0x38)
        mass, = struct.unpack_from('<i', d, r + 0x50)
        flags, = struct.unpack_from('<H', d, r + 0x8E)
        doors = tuple(d[r + 0x94:r + 0x98])
        boot = d[r + 0x110]
        seats = d[r + 0x10C]
        print('\n#%d %s: %s, model %d, palettes %08x, %.2f x %.2f x %.2f, cog %.2f, wheel r %.2f, axles %.2f %.2f, '
              'mass %d, flags %04x, doors %s, boot %d, seats %d'
              % (i, name, TYPES.get(vtype, 'type %d' % vtype), model, pmask, w / FX, l / FX, h / FX, cog / FX,
                 radius / FX, front / FX, rear / FX, mass, flags, doors, boot, seats))
        try:
            m = ctwdata.Model(pak.read(model))
        except ValueError as e:
            print('  model: %s' % e)
            continue
        print('  model scale %.5f, bbox %s, %d nodes, %d batches, %d vertices'
              % (m.scale, ' '.join('%.2f' % v for v in m.bbox), len(m.world), len(m.batches), len(m.verts)))
        ext = node_extents(m)
        for k, (R, T) in enumerate(m.world):
            e = ext.get(k)
            part = ('%d batches, palettes %08x, box %s .. %s' % (
                e['batches'], e['mask'], ' '.join('%.2f' % v for v in e['lo']), ' '.join('%.2f' % v for v in e['hi'])
            )) if e else 'no batches'
            print('  node %d at %s: %s' % (k, ' '.join('%.2f' % v for v in T), part))
        texs = sorted({b['texture'] for b in m.batches})
        print('  textures: %s' % ' '.join(str(t) for t in texs))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
