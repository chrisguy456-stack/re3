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
                            os.path.join(self.out, 'data', 'maps', 'ctw', 'ctw.col')], capture_output=True, text=True)
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
