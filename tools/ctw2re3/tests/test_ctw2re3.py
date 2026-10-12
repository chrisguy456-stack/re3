# SPDX-License-Identifier: GPL-3.0-only
"""End-to-end test on made-up data: python -m unittest discover -s tools/ctw2re3/tests -v"""
import os
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)
import ctw2re3        # noqa: E402
import ctwdata        # noqa: E402
import textures as ctwtex  # noqa: E402
import make_fixture   # noqa: E402


def read_img(out):
    d = open(os.path.join(out, 'models', 'ctw.dir'), 'rb').read()
    img = open(os.path.join(out, 'models', 'ctw.img'), 'rb').read()
    files = {}
    for i in range(0, len(d), 32):
        off, size = struct.unpack_from('<II', d, i)
        name = d[i + 8:i + 32].split(b'\0')[0].decode()
        files[name] = img[off * 2048:(off + size) * 2048]
    return files


def dff_positions(dff):
    """Vertex positions of the single geometry, found through the chunk tree."""
    def chunks(b, p, end):
        while p + 12 <= end:
            cid, size, _ = struct.unpack_from('<III', b, p)
            yield cid, p + 12, p + 12 + size
            p += 12 + size

    for cid, s, e in chunks(dff, 0, len(dff)):
        for cid2, s2, e2 in chunks(dff, s, e):
            if cid2 != 0x1A:
                continue
            for cid3, s3, e3 in chunks(dff, s2, e2):
                if cid3 != 0x0F:
                    continue
                for cid4, s4, e4 in chunks(dff, s3, e3):
                    if cid4 != 0x01:
                        continue
                    flags, nt, nv, _ = struct.unpack_from('<iiii', dff, s4)
                    p = s4 + 16 + nv * 4 + nv * 8 + nt * 8 + 24
                    return [struct.unpack_from('<3f', dff, p + 12 * i) for i in range(nv)]
    return []


class ConvertFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.data = os.path.join(cls.tmp.name, 'ctw')
        cls.out = os.path.join(cls.tmp.name, 'gta3')
        make_fixture.main(cls.data)
        ctw2re3.main(['--ctw-data', cls.data, '--out', cls.out, '--jobs', '1'])
        cls.files = read_img(cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_one_model_and_txd_per_block(self):
        # block (10, 5) has translucent surfaces (a see-through material, the sea), which get a model of their own
        self.assertEqual(sorted(self.files), ['ctw_10_5.dff', 'ctw_10_5.txd', 'ctw_10_5_1.dff', 'ctw_10_5_1.txd',
                                              'ctw_11_5.dff', 'ctw_11_5.txd'])
        ipl = open(os.path.join(self.out, 'data', 'maps', 'ctw', 'ctw.ipl')).read()
        self.assertIn('1000, ctw_10_5, -2250.0000, -1830.0000, 0.0000', ipl)

    def test_translucent_model_drawn_last(self):
        ide = open(os.path.join(self.out, 'data', 'maps', 'ctw', 'ctw.ide')).read()
        self.assertIn('1000, ctw_10_5, ctw_10_5, 1, 299, 0', ide)
        self.assertIn('1001, ctw_10_5_1, ctw_10_5_1, 1, 299, 4', ide)

    def test_opaque_copy_of_alpha_texture(self):
        # DXT5 block whose colour block is in DXT1's three-colour order (colour0 < colour1): the opaque copy must
        # decode to the same four colours with no transparency
        c0, c1 = 0x001F, 0xF800
        bits = 0
        for i in range(16):
            bits |= (i % 4) << (2 * i)
        dxt5 = bytes(8) + struct.pack('<HHI', c0, c1, bits)
        t = ctwtex.opaque(ctwtex.Texture(4, 4, 'dxt5', dxt5, True))
        self.assertEqual((t.kind, t.alpha, len(t.data)), ('dxt1', False, 8))
        n0, n1, nbits = struct.unpack('<HHI', t.data)
        self.assertGreater(n0, n1)
        ends = {0: c0, 1: c1, 2: '2/3 c0', 3: '2/3 c1'}
        new_ends = {0: n0, 1: n1, 2: '2/3 ' + ('c0' if n0 == c0 else 'c1'), 3: '2/3 ' + ('c1' if n1 == c1 else 'c0')}
        for i in range(16):
            self.assertEqual(new_ends[(nbits >> (2 * i)) & 3], ends[(bits >> (2 * i)) & 3])

    def test_shared_object_converted_once(self):
        # the 8-unit cube listed by both blocks is only in the first one (5 materials there, 2 in the second)
        p10 = dff_positions(self.files['ctw_10_5.dff']) + dff_positions(self.files['ctw_10_5_1.dff'])
        p11 = dff_positions(self.files['ctw_11_5.dff'])
        self.assertEqual(len(p10), 44)
        self.assertEqual(len(p11), 20)

    def test_positions_are_block_local(self):
        bx, by = ctwdata.block_centre(10, 5)
        p = dff_positions(self.files['ctw_10_5.dff']) + dff_positions(self.files['ctw_10_5_1.dff'])
        # cube a: 10 units, centred 20 west of the block centre, standing on z 0
        cube = [v for v in p if -25.01 <= v[0] <= -14.99 and -5.01 <= v[1] <= 5.01]
        self.assertEqual(len(cube), 20)
        self.assertAlmostEqual(min(v[2] for v in cube), 0.0, places=3)
        self.assertAlmostEqual(max(v[2] for v in cube), 10.0, places=3)
        # the sea tile: 20 x 20 at z -7.5
        sea = [v for v in p if abs(v[2] + 7.5) < 1e-3]
        self.assertEqual(len(sea), 4)

    def test_ground_rects_cover_cells(self):
        cell = ctwdata.CollisionCell()
        cell.ground = [[(1 if (r + c) % 7 else 2) for c in range(40)] for r in range(40)]
        for cx, cy in ((26, 12), (27, 13)):
            rects = ctw2re3.ground_rects(cx, cy, cell)
            area = sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1, z in rects)
            self.assertAlmostEqual(area, 50 * 50)
            for x0, y0, x1, y1, z in rects:   # no rectangle crosses a block edge
                self.assertEqual(ctwdata.block_of(x0 + 1e-3, y0 + 1e-3), ctwdata.block_of(x1 - 1e-3, y1 - 1e-3))

    def test_dxt_decode_and_additive_copy(self):
        # one DXT1 block: colour0 white, colour1 black, left half index 0 (white), right half index 1 (black)
        bits = 0
        for i in range(16):
            bits |= (0 if (i & 3) < 2 else 1) << (2 * i)
        t = ctwtex.Texture(4, 4, 'dxt1', struct.pack('<HHI', 0xFFFF, 0x0000, bits), False)
        px = ctwtex.decode(t)
        self.assertEqual(px[0:4], bytes((255, 255, 255, 255)))
        self.assertEqual(px[12:16], bytes((0, 0, 0, 255)))
        a = ctwtex.additive(t)
        self.assertTrue(a.alpha)
        self.assertEqual(a.data[0:4], bytes((255, 255, 255, 255)))   # bright: kept, opaque
        self.assertEqual(a.data[12:16], bytes((0, 0, 0, 0)))         # black: adds nothing, so fully see-through

    def test_librw_reads_everything(self):
        exe = os.environ.get('RWCHECK')
        if not exe:
            self.skipTest('set RWCHECK to a built tests/rwcheck.cpp to check the files with librw')
        r = subprocess.run([exe, os.path.join(self.out, 'models', 'ctw.img'), os.path.join(self.out, 'models', 'ctw.dir'),
                            os.path.join(self.out, 'data', 'maps', 'ctw', 'ctw.col'),
                            os.path.join(self.out, 'models', 'ctw_shared.txd')], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == '__main__':
    unittest.main()


class RenderLists(unittest.TestCase):
    """render_tables.bin decides which translucent batches CTW draws additively."""

    def convert(self, list0):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        data, out = os.path.join(tmp.name, 'ctw'), os.path.join(tmp.name, 'gta3')
        make_fixture.main(data)
        with open(os.path.join(data, 'render_tables.bin'), 'wb') as f:   # the fixture's instances use list index 0
            f.write(b'CTWREND1' + struct.pack('<9i', list0, *range(1, 9)) + bytes(24 + 64 + 32))
        ctw2re3.main(['--ctw-data', data, '--out', out, '--jobs', '1'])
        return b''.join(read_img(out).values())

    def test_additive_list(self):
        files = self.convert(4)
        self.assertIn(b'a5\0', files)
        self.assertNotIn(b't5\0', files)

    def test_blended_list(self):
        files = self.convert(1)
        self.assertIn(b't5\0', files)
        self.assertNotIn(b'a5\0', files)


class Facades(unittest.TestCase):
    """CTW maps most building sides onto black; those walls get the building's own facade tile."""

    def setUp(self):
        px = bytearray()
        for y in range(32):
            for x in range(32):
                px += bytes((0, 0, 0, 255)) if x < 16 else bytes((200, 150, 100, 255))
        self.src = ctwtex.TextureSource(None)
        self.src.cache[9] = ctwtex.Texture(32, 32, 'rgba', bytes(px), False)

    @staticmethod
    def wall(x0, x1, z0, z1, u0, u1, v0, v1):
        """Two triangles of a wall in the plane y = 0 from x0 to x1, z0 to z1, textured u0..u1, v0..v1."""
        n = (0.0, -1.0, 0.0)
        a, b, c, d = ((x0, 0.0, z0), n, (u0, v1)), ((x1, 0.0, z0), n, (u1, v1)), ((x1, 0.0, z1), n, (u1, v0)), \
            ((x0, 0.0, z1), n, (u0, v0))
        mb = {'texture': 9, 'flags': 0x01}
        return [(mb, [a, b, c]), (mb, [a, c, d])]

    def test_dark_walls_get_the_facade(self):
        tris = self.wall(0, 4, 0, 8, 0.5, 1.0, 0.0, 1.0) + self.wall(4, 10, 0, 6, 0.0, 0.4, 0.0, 1.0)
        cands, dark = ctw2re3.facade_tile(self.src, tris)
        self.assertEqual(dark, {2, 3})
        key, mu, mv = ctw2re3.pick_tile(self.src, cands)
        self.assertEqual(key, (9, 16, 0, 32, 32))
        self.assertAlmostEqual(mu, 4.0, places=3)   # the 16-texel tile spans the 4 m wide wall once
        self.assertAlmostEqual(mv, 8.0, places=3)
        uvs = ctw2re3.wall_uvs(tris[2][1], mu, mv)
        self.assertAlmostEqual(abs(uvs[1][0] - uvs[0][0]), 1.5, places=3)   # 6 m of wall: 1.5 repeats
        self.assertAlmostEqual(uvs[2][1] - uvs[1][1], -0.75, places=3)     # 6 m up: 3/4 of a repeat

    def test_tile_texture(self):
        t = ctw2re3.texture_variant(self.src, 'f9_16_0_32_32')
        self.assertEqual((t.w, t.h, t.kind), (16, 32, 'rgba'))
        self.assertEqual(t.data[:4], bytes((200, 150, 100, 255)))


class SharedTextures(unittest.TestCase):
    """Textures enough blocks use are written once, to models/ctw_shared.txd, instead of into every block."""

    def test_shared_dictionary(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        data, out = os.path.join(tmp.name, 'ctw'), os.path.join(tmp.name, 'gta3')
        make_fixture.main(data)
        ctw2re3.main(['--ctw-data', data, '--out', out, '--jobs', '1', '--share-min', '2'])
        files = read_img(out)
        shared = open(os.path.join(out, 'models', 'ctw_shared.txd'), 'rb').read()
        # the cube both blocks list is converted once, but the roof texture (4) and wall texture (6) of the
        # fixture's objects appear in both blocks: those go in the shared dictionary and in no block's own
        self.assertIn(b'o6\0', shared)
        self.assertNotIn(b'o6\0', files['ctw_10_5.txd'] + files['ctw_11_5.txd'])
        self.assertIn(b'o6\0', files['ctw_11_5.dff'])   # the models still name it
        exe = os.environ.get('RWCHECK')
        if exe:
            r = subprocess.run([exe, os.path.join(out, 'models', 'ctw.img'), os.path.join(out, 'models', 'ctw.dir'),
                                '-', os.path.join(out, 'models', 'ctw_shared.txd')], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('[o6 a255]', r.stdout)      # found through the shared dictionary


class Paths(unittest.TestCase):
    setUpClass = ConvertFixture.__dict__['setUpClass']
    tearDownClass = ConvertFixture.__dict__['tearDownClass']

    def read_paths(self):
        d = open(os.path.join(self.out, 'data', 'maps', 'ctw', 'ctw_paths.bin'), 'rb').read()
        self.assertEqual(d[:8], b'CTWPATH1')
        nc, np_, nl = struct.unpack_from('<3I', d, 8)
        pos = [struct.unpack_from('<3f', d, 20 + 12 * i) for i in range(nc + np_)]
        p = 20 + 12 * (nc + np_)
        links = [struct.unpack_from('<2I4B', d, p + 12 * i) for i in range(nl)]
        return nc, pos, links

    def test_roads(self):
        nc, pos, links = self.read_paths()
        x0, y0 = pos[0][0] - 20, pos[0][1] + 10
        # the straight two-way stretch loses its middle node; the restricted and boat nodes are left out
        self.assertEqual(nc, 3)
        self.assertEqual([p[:2] for p in pos[:3]], [(x0 + 20, y0 - 10), (x0 + 20, y0 + 20), (x0 + 20, y0 + 40)])
        car = [l[:4] for l in links if l[0] < nc]
        # two-way: a lane each way; one-way from node 1 to node 2: lanes "from a to b" only
        self.assertEqual(car, [(0, 1, 1, 1), (1, 2, 0, 1)])

    def test_pavements_join_across_cells_and_cross_the_road(self):
        nc, pos, links = self.read_paths()
        ped = [l for l in links if l[0] >= nc]
        self.assertEqual(len(pos) - nc, 5)    # the edge node of both cells is one node
        self.assertEqual(len(ped), 4)
        crossing = [l for l in ped if l[4] & 1]
        self.assertEqual(len(crossing), 1)
        a, b = pos[crossing[0][0]], pos[crossing[0][1]]
        self.assertAlmostEqual(min(a[0], b[0]) + 10, pos[0][0])
