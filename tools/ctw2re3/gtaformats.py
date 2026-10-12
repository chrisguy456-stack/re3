# SPDX-License-Identifier: GPL-3.0-only
"""GTA 3 data formats: COL1 collision, the v1 IMG archive (.img + .dir) and IDE / IPL text files."""
import math
import struct

SECTOR = 2048


def col1(name, bmin, bmax, boxes=(), verts=(), tris=()):
    """One COL1 model. boxes: (min, max, surface); tris: (a, b, c, surface). Coordinates are model-local."""
    centre = tuple((bmin[i] + bmax[i]) / 2 for i in range(3))
    radius = math.sqrt(sum((bmax[i] - bmin[i]) ** 2 for i in range(3))) / 2
    d = struct.pack('<f3f3f3f', radius, *centre, *bmin, *bmax)
    d += struct.pack('<I', 0)                  # spheres
    d += struct.pack('<I', 0)                  # lines
    d += struct.pack('<I', len(boxes))
    d += b''.join(struct.pack('<3f3fBBxx', *lo, *hi, surf, 0) for lo, hi, surf in boxes)
    d += struct.pack('<I', len(verts))
    d += b''.join(struct.pack('<3f', *v) for v in verts)
    d += struct.pack('<I', len(tris))
    d += b''.join(struct.pack('<3iBBxx', a, b, c, surf, 0) for a, b, c, surf in tris)
    body = name.encode('ascii')[:21].ljust(22, b'\0') + struct.pack('<H', 0) + d
    return b'COLL' + struct.pack('<I', len(body)) + body


class ImgWriter:
    """GTA 3 IMG v1: a headerless .img of 2048-byte sectors and a .dir of {u32 offset, u32 size (sectors),
    char name[24]} entries."""

    def __init__(self, img_path):
        self.img = open(img_path, 'wb')
        self.dir = []
        self.sector = 0

    def add(self, name, data):
        if len(name) > 23:
            raise ValueError('IMG entry name too long: ' + name)
        n = (len(data) + SECTOR - 1) // SECTOR
        self.img.write(data + b'\0' * (n * SECTOR - len(data)))
        self.dir.append(struct.pack('<II', self.sector, n) + name.encode('ascii').ljust(24, b'\0'))
        self.sector += n

    def close(self, dir_path):
        self.img.close()
        with open(dir_path, 'wb') as f:
            f.write(b''.join(self.dir))


def ide(objects):
    """objects: (id, model, txd, draw distance, flags)."""
    lines = ['# GTA: Chinatown Wars city, converted by tools/ctw2re3', 'objs']
    lines += ['%d, %s, %s, 1, %d, %d' % o for o in objects]
    lines += ['end', '']
    return '\n'.join(lines)


def ipl(instances):
    """instances: (id, model, x, y, z). No rotation: the blocks are placed by translation only."""
    lines = ['# GTA: Chinatown Wars city, converted by tools/ctw2re3', 'inst']
    lines += ['%d, %s, %.4f, %.4f, %.4f, 1, 1, 1, 0, 0, 0, 1' % i for i in instances]
    lines += ['end', '']
    return '\n'.join(lines)
