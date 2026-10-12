# SPDX-License-Identifier: GPL-3.0-only
"""CTW textures -> pixel data a RenderWare D3D8 texture native can hold.

A texture id is looked up in dxt.bin first (S3TC blocks, passed through untouched), then in game.pak, whose
resources start {u16 w, h, glFormat, pad, u32 size} (CTW-Native port/src/gfx/texload.cpp)."""
import struct
import zlib

DXT1_RGB, DXT1_RGBA, DXT3, DXT5 = 0x83F0, 0x83F1, 0x83F2, 0x83F3


class Texture:
    """kind 'dxt1' / 'dxt3' / 'dxt5' with raw blocks, or 'rgba' with w * h * 4 bytes (R, G, B, A)."""

    def __init__(self, w, h, kind, data, alpha):
        self.w, self.h, self.kind, self.data, self.alpha = w, h, kind, data, alpha


def _expand(v, bits):
    return v * 255 // ((1 << bits) - 1)


def _rgba_has_alpha(px):
    return any(a != 255 for a in px[3::4])


def _dxt1_has_alpha(data):
    for o in range(0, len(data) - 7, 8):
        c0, c1, bits = struct.unpack_from('<HHI', data, o)
        if c0 <= c1:
            for i in range(16):
                if (bits >> (2 * i)) & 3 == 3:
                    return True
    return False


def from_dxt(w, h, fmt, data):
    if w % 4 or h % 4:
        return None   # D3D needs whole 4 x 4 blocks; the caller falls back to game.pak
    if fmt == DXT1_RGB:
        return Texture(w, h, 'dxt1', data, False)
    if fmt == DXT1_RGBA:
        return Texture(w, h, 'dxt1', data, _dxt1_has_alpha(data))
    if fmt == DXT3:
        return Texture(w, h, 'dxt3', data, True)
    if fmt == DXT5:
        return Texture(w, h, 'dxt5', data, True)
    return None


# ---------------------------------------------------------------------------------------------------- PVRTC1
def _twiddle(x, y, nx, ny):
    mn, t, bit, dst, shift = min(nx, ny), 0, 1, 1, 0
    while bit < mn:
        if y & bit:
            t |= dst
        if x & bit:
            t |= dst << 1
        bit <<= 1
        dst <<= 2
        shift += 1
    return t | ((max(x, y) >> shift) << (2 * shift))


def _pvr_colours(cw):
    a, b = cw & 0xFFFF, cw >> 16
    if a & 0x8000:
        A = (_expand((a >> 10) & 31, 5), _expand((a >> 5) & 31, 5), _expand((a >> 1) & 15, 4), 255)
    else:
        A = (_expand((a >> 8) & 15, 4), _expand((a >> 4) & 15, 4), _expand((a >> 1) & 7, 3), _expand((a >> 12) & 7, 3))
    if b & 0x8000:
        B = (_expand((b >> 10) & 31, 5), _expand((b >> 5) & 31, 5), _expand(b & 31, 5), 255)
    else:
        B = (_expand((b >> 8) & 15, 4), _expand((b >> 4) & 15, 4), _expand(b & 15, 4), _expand((b >> 12) & 7, 3))
    return A, B


def decode_pvrtc(data, w, h, two_bpp):
    """Port of DecodePVRTC in CTW-Native port/src/gfx/texload.cpp. Returns w * h * 4 RGBA bytes."""
    bw, bh = (8 if two_bpp else 4), 4
    nbx, nby = max(1, w // bw), max(1, h // bh)
    CA, CB, mod, col = [], [], [], []
    for by in range(nby):
        for bx in range(nbx):
            src = _twiddle(bx, by, nbx, nby) * 8
            m, c = struct.unpack_from('<II', data, src)
            mod.append(m)
            col.append(c)
            A, B = _pvr_colours(c)
            CA.append(A)
            CB.append(B)
    rep = (0, 3, 5, 8)
    W = [0] * (w * h)
    if not two_bpp:
        for y in range(h):
            for x in range(w):
                m = mod[(y // 4) * nbx + x // 4]
                W[y * w + x] = rep[(m >> (2 * ((y & 3) * 4 + (x & 3)))) & 3]
    else:
        stored, sub, interp = [-1] * (w * h), [0] * (nbx * nby), [0] * (nbx * nby)
        for by in range(nby):
            for bx in range(nbx):
                k = by * nbx + bx
                m = mod[k]
                if not (col[k] & 1):
                    for py in range(4):
                        for px in range(8):
                            W[(by * 4 + py) * w + bx * 8 + px] = 8 if (m >> (py * 8 + px)) & 1 else 0
                    continue
                interp[k] = 1
                s = 1
                if m & 1:
                    s = 3 if m & (1 << 20) else 2
                    m = m | (1 << 20) if m & (1 << 21) else m & ~(1 << 20)
                m = m | 1 if m & 2 else m & ~1
                sub[k] = s
                n = 0
                for py in range(4):
                    for px in range(8):
                        if ((px ^ py) & 1) == 0:
                            stored[(by * 4 + py) * w + bx * 8 + px] = rep[(m >> (2 * n)) & 3]
                            n += 1

        def at(x, y):
            return stored[((y + h) % h) * w + (x + w) % w]

        for y in range(h):
            for x in range(w):
                k = (y // 4) * nbx + x // 8
                if not interp[k]:
                    continue
                v = stored[y * w + x]
                if v < 0:
                    s = sub[k]
                    if s == 1:
                        v = (at(x, y - 1) + at(x, y + 1) + at(x - 1, y) + at(x + 1, y) + 2) // 4
                    elif s == 2:
                        v = (at(x - 1, y) + at(x + 1, y) + 1) // 2
                    else:
                        v = (at(x, y - 1) + at(x, y + 1) + 1) // 2
                W[y * w + x] = v
    out = bytearray(w * h * 4)
    for y in range(h):
        fy = (y - bh / 2.0) / bh
        y0 = int(fy // 1)
        ty = fy - y0
        r0, r1 = (y0 % nby) * nbx, ((y0 + 1) % nby) * nbx
        for x in range(w):
            fx = (x - bw / 2.0) / bw
            x0 = int(fx // 1)
            tx = fx - x0
            c0, c1 = x0 % nbx, (x0 + 1) % nbx
            k00, k01, k10, k11 = r0 + c0, r0 + c1, r1 + c0, r1 + c1
            wt = W[y * w + x] / 8.0
            o = (y * w + x) * 4
            for c in range(4):
                a = (CA[k00][c] * (1 - tx) + CA[k01][c] * tx) * (1 - ty) + (CA[k10][c] * (1 - tx) + CA[k11][c] * tx) * ty
                b = (CB[k00][c] * (1 - tx) + CB[k01][c] * tx) * (1 - ty) + (CB[k10][c] * (1 - tx) + CB[k11][c] * tx) * ty
                out[o + c] = max(0, min(255, int(a * (1 - wt) + b * wt + 0.5)))
    return bytes(out)


# ---------------------------------------------------------------------------------------------------- PNG
def decode_png(data):
    """8-bit PNG (grey, RGB, palette, grey-alpha, RGBA; not interlaced) -> (w, h, RGBA bytes)."""
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('not a PNG')
    p, idat, plte, trns = 8, b'', None, None
    while p + 8 <= len(data):
        n, typ = struct.unpack_from('>I4s', data, p)
        body = data[p + 8:p + 8 + n]
        if typ == b'IHDR':
            w, h, depth, ctype, _, _, inter = struct.unpack('>IIBBBBB', body)
        elif typ == b'PLTE':
            plte = body
        elif typ == b'tRNS':
            trns = body
        elif typ == b'IDAT':
            idat += body
        elif typ == b'IEND':
            break
        p += 12 + n
    if depth != 8 or inter:
        raise ValueError('unsupported PNG')
    ch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    raw = zlib.decompress(idat)
    stride = w * ch
    rows, prev, q = [], bytearray(stride), 0
    for _ in range(h):
        f = raw[q]
        line = bytearray(raw[q + 1:q + 1 + stride])
        q += 1 + stride
        for i in range(stride):
            a = line[i - ch] if i >= ch else 0
            b = prev[i]
            c = prev[i - ch] if i >= ch else 0
            if f == 1:
                line[i] = (line[i] + a) & 255
            elif f == 2:
                line[i] = (line[i] + b) & 255
            elif f == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 255
            elif f == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(line)
        prev = line
    out = bytearray(w * h * 4)
    for y, line in enumerate(rows):
        for x in range(w):
            o = (y * w + x) * 4
            if ctype == 6:
                out[o:o + 4] = line[x * 4:x * 4 + 4]
            elif ctype == 2:
                out[o:o + 3] = line[x * 3:x * 3 + 3]
                out[o + 3] = 255
            elif ctype == 0:
                out[o:o + 3] = bytes((line[x],)) * 3
                out[o + 3] = 255
            elif ctype == 4:
                out[o:o + 3] = bytes((line[x * 2],)) * 3
                out[o + 3] = line[x * 2 + 1]
            else:
                i = line[x]
                out[o:o + 3] = plte[i * 3:i * 3 + 3]
                out[o + 3] = trns[i] if trns and i < len(trns) else 255
    return w, h, bytes(out)


# ---------------------------------------------------------------------------------------------------- game.pak
def from_pak(raw):
    if len(raw) < 12:
        return None
    w, h, fmt = struct.unpack_from('<HHH', raw, 0)
    size, = struct.unpack_from('<I', raw, 8)
    if not (0 < w <= 4096 and 0 < h <= 4096):
        return None
    px = raw[12:]
    n = w * h
    if fmt == 0xBEEF:
        try:
            w, h, rgba = decode_png(px[:size])
        except (ValueError, KeyError, zlib.error, struct.error, UnboundLocalError):
            return None
        return Texture(w, h, 'rgba', rgba, _rgba_has_alpha(rgba))
    if fmt in (0x83F0, 0x83F1, 0x83F2, 0x83F3):
        return from_dxt(w, h, fmt, px[:min(size, len(px))])
    if fmt in (0x8C00, 0x8C01, 0x8C02, 0x8C03):
        two = bool(fmt & 1)
        need = n * (2 if two else 4) // 8
        if len(px) < need:
            return None
        rgba = decode_pvrtc(px, w, h, two)
        return Texture(w, h, 'rgba', rgba, _rgba_has_alpha(rgba))
    out = bytearray(n * 4)
    if fmt in (0x8033, 0x8034, 0x8363):   # GL_UNSIGNED_SHORT_4_4_4_4 / 5_5_5_1 (RGBA) / 5_6_5 (RGB)
        if len(px) < n * 2:
            return None
        for i, (v,) in enumerate(struct.iter_unpack('<H', px[:n * 2])):
            if fmt == 0x8033:
                c = (_expand(v >> 12, 4), _expand((v >> 8) & 15, 4), _expand((v >> 4) & 15, 4), _expand(v & 15, 4))
            elif fmt == 0x8034:
                c = (_expand(v >> 11, 5), _expand((v >> 6) & 31, 5), _expand((v >> 1) & 31, 5), 255 if v & 1 else 0)
            else:
                c = (_expand(v >> 11, 5), _expand((v >> 5) & 63, 6), _expand(v & 31, 5), 255)
            out[i * 4:i * 4 + 4] = bytes(c)
    elif fmt in (0x1909, 0x190A):         # GL_LUMINANCE / GL_LUMINANCE_ALPHA
        bpp = 2 if fmt == 0x190A else 1
        if len(px) < n * bpp:
            return None
        for i in range(n):
            l = px[i * bpp]
            out[i * 4:i * 4 + 4] = bytes((l, l, l, px[i * 2 + 1] if bpp == 2 else 255))
    else:
        return None
    return Texture(w, h, 'rgba', bytes(out), _rgba_has_alpha(out))


def _opaque_dxt1_block(colour_block):
    """A DXT3/5 colour block (always four-colour) as a DXT1 block that decodes to the same colours: DXT1 reads
    colour0 <= colour1 as three colours plus transparent, so those blocks swap the end points and remap indices."""
    c0, c1, bits = struct.unpack('<HHI', colour_block)
    if c0 > c1:
        return colour_block
    if c0 == c1:
        return struct.pack('<HHI', c0, c1, 0)   # one colour; index 0 everywhere
    remap = (1, 0, 3, 2)                        # 0 <-> 1 swaps ends; 2 (2/3 c0) <-> 3 (2/3 c1)
    nb = 0
    for i in range(16):
        nb |= remap[(bits >> (2 * i)) & 3] << (2 * i)
    return struct.pack('<HHI', c1, c0, nb)


def opaque(t):
    """The texture with its alpha thrown away, for batches CTW draws without blending (render list 0 ignores the
    texture's alpha). DXT3/5 become DXT1 so re3 neither blends nor alpha-tests them."""
    if not t.alpha:
        return t
    if t.kind == 'rgba':
        px = bytearray(t.data)
        px[3::4] = b'\xff' * (len(px) // 4)
        return Texture(t.w, t.h, 'rgba', bytes(px), False)
    if t.kind == 'dxt1':
        return Texture(t.w, t.h, 'dxt1', t.data, False)
    blocks = [_opaque_dxt1_block(t.data[o + 8:o + 16]) for o in range(0, len(t.data) - 15, 16)]
    return Texture(t.w, t.h, 'dxt1', b''.join(blocks), False)


# ---------------------------------------------------------------------------------------------------- S3TC
def _565(c):
    return (_expand(c >> 11, 5), _expand((c >> 5) & 63, 6), _expand(c & 31, 5))


def _colour_block(b, four_colour):
    c0, c1, bits = struct.unpack('<HHI', b)
    p0, p1 = _565(c0), _565(c1)
    if four_colour or c0 > c1:
        pal = [p0 + (255,), p1 + (255,), tuple((2 * a + b_) // 3 for a, b_ in zip(p0, p1)) + (255,),
               tuple((a + 2 * b_) // 3 for a, b_ in zip(p0, p1)) + (255,)]
    else:
        pal = [p0 + (255,), p1 + (255,), tuple((a + b_) // 2 for a, b_ in zip(p0, p1)) + (255,), (0, 0, 0, 0)]
    return [pal[(bits >> (2 * i)) & 3] for i in range(16)]


def decode(t):
    """Any Texture -> w * h * 4 RGBA bytes."""
    if t.kind == 'rgba':
        return t.data
    out = bytearray(t.w * t.h * 4)
    step = 8 if t.kind == 'dxt1' else 16
    nbx = t.w // 4
    for k, o in enumerate(range(0, nbx * (t.h // 4) * step, step)):
        bx, by = k % nbx, k // nbx
        if t.kind == 'dxt1':
            px = _colour_block(t.data[o:o + 8], False)
        else:
            px = _colour_block(t.data[o + 8:o + 16], True)
            if t.kind == 'dxt3':
                a = int.from_bytes(t.data[o:o + 8], 'little')
                alphas = [_expand((a >> (4 * i)) & 15, 4) for i in range(16)]
            else:
                a0, a1 = t.data[o], t.data[o + 1]
                bits = int.from_bytes(t.data[o + 2:o + 8], 'little')
                if a0 > a1:
                    pal = [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
                else:
                    pal = [a0, a1] + [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]
                alphas = [pal[(bits >> (3 * i)) & 7] for i in range(16)]
            px = [p[:3] + (alphas[i],) for i, p in enumerate(px)]
        for i, p in enumerate(px):
            x, y = bx * 4 + (i & 3), by * 4 + (i >> 2)
            q = (y * t.w + x) * 4
            out[q:q + 4] = bytes(p)
    return bytes(out)


def additive(t):
    """For batches CTW blends additively (GL_SRC_ALPHA, GL_ONE: glows, lit windows), which re3's buildings cannot
    do: the texture as normal alpha blending that gives nearly the same picture. Alpha becomes the brightest channel
    and the colour is scaled up to match, so black parts vanish instead of covering what is behind them."""
    px = bytearray(decode(t))
    for q in range(0, len(px), 4):
        r, g, b, a = px[q:q + 4]
        m = max(r, g, b)
        if m == 0:
            px[q:q + 4] = b'\0\0\0\0'
            continue
        px[q:q + 4] = bytes((r * 255 // m, g * 255 // m, b * 255 // m, m * a // 255))
    return Texture(t.w, t.h, 'rgba', bytes(px), True)


def placeholder():
    """4 x 4 magenta: makes a texture the converter could not read easy to spot in game."""
    return Texture(4, 4, 'rgba', bytes((255, 0, 255, 255)) * 16, False)


def crop(t, x0, y0, x1, y1, px=None):
    """Pixels [x0, x1) x [y0, y1) of a texture (px: its decode(), if already done) as a new opaque RGBA texture."""
    px, rows = px or decode(t), []
    for y in range(y0, y1):
        rows.append(px[(y * t.w + x0) * 4:(y * t.w + x1) * 4])
    out = bytearray(b''.join(rows))
    out[3::4] = b'\xff' * (len(out) // 4)
    return Texture(x1 - x0, y1 - y0, 'rgba', bytes(out), False)


DARK = 12    # mean of R, G, B below which a texel counts as black


class TextureSource:
    def __init__(self, pak, dxt=None):
        self.pak, self.dxt = pak, dxt
        self.cache = {}
        self.pixels = {}
        self.missing = set()

    def decoded(self, tid):
        if tid not in self.pixels:
            self.pixels[tid] = decode(self.get(tid))
        return self.pixels[tid]

    def texel(self, tid, u, v):
        """(r, g, b, a) at texture coordinate (u, v), wrapping as the game's textures do."""
        self.decoded(tid)
        t = self.get(tid)
        x, y = int((u % 1.0) * t.w) % t.w, int((v % 1.0) * t.h) % t.h
        q = (y * t.w + x) * 4
        return tuple(self.pixels[tid][q:q + 4])

    def is_dark(self, tid, uvs):
        """Whether a triangle with these texture coordinates shows only black: its corners and centre are."""
        cu, cv = sum(u for u, _ in uvs) / 3.0, sum(v for _, v in uvs) / 3.0
        # corners pulled in a little, so a triangle that ends exactly on a tile's edge samples its own tile
        for u, v in [(cu + 0.9 * (u - cu), cv + 0.9 * (v - cv)) for u, v in uvs] + [(cu, cv)]:
            r, g, b, _ = self.texel(tid, u, v)
            if r + g + b >= 3 * DARK:
                return False
        return True

    def get(self, tid):
        if tid in self.cache:
            return self.cache[tid]
        t = None
        if self.dxt:
            d = self.dxt.get(tid)
            if d:
                t = from_dxt(*d)
        if t is None:
            t = from_pak(self.pak.read(tid))
        if t is None:
            self.missing.add(tid)
            t = placeholder()
        self.cache[tid] = t
        return t
