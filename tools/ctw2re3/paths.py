# SPDX-License-Identifier: GPL-3.0-only
"""Car and pedestrian paths: Chinatown Wars' road network (ai.bin) and pavement nodes (world.bin) as one file that
the CTW build of re3 loads into its path finder (CCtwPrototype::LoadPaths).

The layouts follow CTW-Native (https://github.com/official-kryo-to/ctw-native, GPL-3.0): port/game/roads.h,
port/game/aispline.cpp and port/src/world/collision.cpp.

ai.bin: u32 linkCount, u32 nodeCount, u32 0, u32 nodeOffset; u16 links[linkCount] at 0x10; 10-byte nodes at
nodeOffset: u16 firstLink, u16 flags, i16 x, i16 y (1/8 unit), i8 z (1/2 unit), u8. Flags: bits 2-4 link count,
5 two-way (the upper half of the lanes drive the other way), 9 two lanes, 11 restricted. Links are one-way. A node
below z -2 (raw z < -4) is on the water, for boats.

world.bin, section 3 of each 50-unit cell: u32 node offset at 0x20, u16 link count 0x24, u16 node count 0x26,
u16 links from 0x2C, the same 10-byte nodes. Links are local to the cell; node flag 0x80 marks a node on the cell's
edge that continues in the neighbouring cell, where a node sits at the same point.

ctw_paths.bin (little endian):
    char[8] "CTWPATH1"; u32 carNodes, pedNodes, links
    nodes: float x, y, z (car nodes first)
    links: u32 a, b (a < b, both car or both ped nodes); u8 lanes from b to a, lanes from a to b, flags (1: a ped
           link that crosses a road), 0
"""
import math
import struct

from ctwdata import WorldCollision

TWO_WAY, TWO_LANES, RESTRICTED = 1 << 5, 1 << 9, 1 << 11
EDGE_NODE = 0x80

# re3's path arrays in the CTW build (src/core/config.h)
MAX_NODES = 65000
MAX_CONNECTIONS = 200000
MAX_CAR_LINKS = 40000

# Nodes along a straight road are merged as long as the merged link stays within this of them and this long
CAR_TOLERANCE, CAR_MAX_LINK = 0.75, 60.0
PED_TOLERANCE, PED_MAX_LINK = 0.5, 30.0


def _nodes(buf, off, count):
    return [struct.unpack_from('<HHhhbB', buf, off + i * 10) for i in range(count)]


def road_graph(ai_bin):
    """{(a, b): (lanes b->a, lanes a->b)} for a < b, and the node positions; boats' and restricted roads left out."""
    nl, nn, _, off = struct.unpack_from('<4I', ai_bin, 0)
    links = struct.unpack_from('<%dH' % nl, ai_bin, 0x10)
    nodes = _nodes(ai_bin, off, nn)
    pos = [(x / 8.0, y / 8.0, z / 2.0) for _, _, x, y, z, _ in nodes]
    usable = [z >= -4 and not flags & RESTRICTED for _, flags, _, _, z, _ in nodes]
    edges = {}
    for a, (first, flags, _, _, _, _) in enumerate(nodes):
        for k in range(flags >> 2 & 7):
            if first + k >= nl:
                break
            b = links[first + k]
            if b >= nn or b == a or not (usable[a] and usable[b]):
                continue
            fa, fb = flags, nodes[b][1]
            lanes = min(1 + (fa >> 9 & 1), 1 + (fb >> 9 & 1))
            if fa & TWO_WAY and fb & TWO_WAY:
                each = max(1, lanes // 2)
                fwd = back = each
            else:
                fwd, back = lanes, 0
            lo, hi = min(a, b), max(a, b)
            old = edges.get((lo, hi), (0, 0))
            # stored as (lanes hi -> lo, lanes lo -> hi)
            new = (back, fwd) if a == lo else (fwd, back)
            edges[(lo, hi)] = (max(old[0], new[0]), max(old[1], new[1]))
    return pos, edges


def pavement_graph(world_bin):
    """The pavement nodes of every cell joined into one graph: positions and {(a, b): (1, 1)}."""
    wc = WorldCollision(world_bin)
    pos, edges, at_edge = [], {}, {}
    for cx in range(100):
        for cy in range(100):
            s = wc.section(cx, cy, 3)
            if not s or len(s) < 0x2C:
                continue
            off, = struct.unpack_from('<I', s, 0x20)
            nl, nn = struct.unpack_from('<HH', s, 0x24)
            if 0x2C + nl * 2 > len(s) or off + nn * 10 > len(s):
                continue
            links = struct.unpack_from('<%dH' % nl, s, 0x2C)
            nodes = _nodes(s, off, nn)
            ids = []
            for _, flags, x, y, z, _ in nodes:
                if flags & EDGE_NODE and (x, y) in at_edge:
                    ids.append(at_edge[(x, y)])
                    continue
                ids.append(len(pos))
                if flags & EDGE_NODE:
                    at_edge[(x, y)] = len(pos)
                pos.append((x / 8.0, y / 8.0, z / 2.0))
            for i, (first, flags, _, _, _, _) in enumerate(nodes):
                for k in range(flags >> 2 & 7):
                    if first + k >= nl or links[first + k] >= nn:
                        continue
                    a, b = ids[i], ids[links[first + k]]
                    if a != b:
                        edges[(min(a, b), max(a, b))] = (1, 1)
    return pos, edges


def simplify(pos, edges, tolerance, max_link):
    """Drop nodes in the middle of straight runs: a node with two links of the same kind whose neighbours' link
    passes within `tolerance` of it, as long as the new link is at most `max_link` long."""
    adj = {}
    for a, b in edges:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)

    def lanes_from(u, v):
        """(lanes v -> u, lanes u -> v)"""
        e = edges[(min(u, v), max(u, v))]
        return e if u < v else (e[1], e[0])

    changed = True
    while changed:
        changed = False
        for v in list(adj):
            nb = adj.get(v)
            if not nb or len(nb) != 2:
                continue
            u, w = nb
            if w in adj[u]:
                continue
            if lanes_from(u, v) != lanes_from(v, w):
                continue
            (ux, uy, uz), (vx, vy, vz), (wx, wy, wz) = pos[u], pos[v], pos[w]
            dx, dy, dz = wx - ux, wy - uy, wz - uz
            length = math.sqrt(dx * dx + dy * dy + dz * dz)
            if length > max_link or length < 1e-6:
                continue
            t = ((vx - ux) * dx + (vy - uy) * dy + (vz - uz) * dz) / (length * length)
            if not 0.0 < t < 1.0:
                continue
            px, py, pz = ux + t * dx - vx, uy + t * dy - vy, uz + t * dz - vz
            if px * px + py * py + pz * pz > tolerance * tolerance:
                continue
            lanes = lanes_from(u, v)
            del edges[(min(u, v), max(u, v))], edges[(min(v, w), max(v, w))]
            edges[(min(u, w), max(u, w))] = lanes if u < w else (lanes[1], lanes[0])
            adj[u].discard(v), adj[w].discard(v)
            adj[u].add(w), adj[w].add(u)
            del adj[v]
            changed = True
    return compact(pos, edges)


def compact(pos, edges):
    """Renumber so that only linked nodes are left."""
    used = sorted({n for e in edges for n in e})
    new = {old: i for i, old in enumerate(used)}
    return ([pos[n] for n in used],
            {(new[a], new[b]): l for (a, b), l in edges.items()})


def _segments_cross(p, q, r, s):
    def orient(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    return (orient(p, q, r) * orient(p, q, s) < 0) and (orient(r, s, p) * orient(r, s, q) < 0)


def crossings(ped_pos, ped_edges, car_pos, car_edges, cell=50.0):
    """The pedestrian links that cross a road link."""
    grid = {}
    for a, b in car_edges:
        (ax, ay, _), (bx, by, _) = car_pos[a], car_pos[b]
        for gx in range(int(math.floor(min(ax, bx) / cell)), int(math.floor(max(ax, bx) / cell)) + 1):
            for gy in range(int(math.floor(min(ay, by) / cell)), int(math.floor(max(ay, by) / cell)) + 1):
                grid.setdefault((gx, gy), []).append((a, b))
    out = set()
    for a, b in ped_edges:
        p, q = ped_pos[a], ped_pos[b]
        seen = set()
        for gx in range(int(math.floor(min(p[0], q[0]) / cell)), int(math.floor(max(p[0], q[0]) / cell)) + 1):
            for gy in range(int(math.floor(min(p[1], q[1]) / cell)), int(math.floor(max(p[1], q[1]) / cell)) + 1):
                for e in grid.get((gx, gy), ()):
                    if e in seen:
                        continue
                    seen.add(e)
                    if _segments_cross(p, q, car_pos[e[0]], car_pos[e[1]]):
                        out.add((a, b))
                        break
    return out


def build(ai_bin, world_bin, z_offset=0.0):
    """ctw_paths.bin and a summary dict."""
    car_pos, car_edges = road_graph(ai_bin) if ai_bin else ([], {})
    raw_car = (len({n for e in car_edges for n in e}), len(car_edges))
    car_pos, car_edges = simplify(car_pos, car_edges, CAR_TOLERANCE, CAR_MAX_LINK)
    ped_pos, ped_edges = pavement_graph(world_bin) if world_bin else ([], {})
    raw_ped = (len({n for e in ped_edges for n in e}), len(ped_edges))
    ped_pos, ped_edges = simplify(ped_pos, ped_edges, PED_TOLERANCE, PED_MAX_LINK)
    cross = crossings(ped_pos, ped_edges, car_pos, car_edges)

    stats = dict(car_nodes=len(car_pos), car_links=len(car_edges), ped_nodes=len(ped_pos), ped_links=len(ped_edges),
                 raw_car=raw_car, raw_ped=raw_ped, crossings=len(cross))
    # trim to what re3's arrays hold (pavements first, as they are the most numerous)
    while len(car_pos) + len(ped_pos) > MAX_NODES or 2 * (len(car_edges) + len(ped_edges)) > MAX_CONNECTIONS:
        keep = len(ped_pos) * 9 // 10
        ped_edges = {e: l for e, l in ped_edges.items() if e[0] < keep and e[1] < keep}
        ped_pos, ped_edges = compact(ped_pos, ped_edges)
        stats['trimmed'] = True
    if len(car_edges) > MAX_CAR_LINKS:
        raise ValueError('%d road links, more than re3 holds (%d)' % (len(car_edges), MAX_CAR_LINKS))

    nc = len(car_pos)
    out = [b'CTWPATH1', struct.pack('<3I', nc, len(ped_pos), len(car_edges) + len(ped_edges))]
    out += [struct.pack('<3f', x, y, z + z_offset) for x, y, z in car_pos + ped_pos]
    out += [struct.pack('<2I4B', a, b, l[0], l[1], 0, 0) for (a, b), l in sorted(car_edges.items())]
    out += [struct.pack('<2I4B', nc + a, nc + b, 1, 1, 1 if (a, b) in cross else 0, 0)
            for (a, b), l in sorted(ped_edges.items())]
    stats.update(car_nodes=nc, ped_nodes=len(ped_pos), ped_links=len(ped_edges))
    return b''.join(out), stats
