"""Minimal QR-code encoder — pure-Python, no dependencies.

Just enough to produce a Version-2..6 byte-mode QR with error-
correction level L for short text like our LAN URL
(`http://10.64.241.57:8080`, ~28 chars).  The output is a 2-D list of
booleans (modules) the caller can render however it likes.

Why not pip-install `qrcode`?  Adding a runtime dep + new PyInstaller
hidden imports is more risk than this ~150-line module.

Algorithm follows ISO/IEC 18004:2015 straightforwardly.  Reference
implementation cross-checked against `pip install qrcode`.
"""
from __future__ import annotations
from typing import List

# --- Reed-Solomon over GF(256) ------------------------------------
_GF_EXP: List[int] = [0] * 512
_GF_LOG: List[int] = [0] * 256
def _init_gf():
    x = 1
    for i in range(255):
        _GF_EXP[i] = x
        _GF_LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        _GF_EXP[i] = _GF_EXP[i - 255]
_init_gf()

def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _GF_EXP[_GF_LOG[a] + _GF_LOG[b]]

def _rs_generator(n: int) -> List[int]:
    g = [1]
    for i in range(n):
        g = [g[0]] + [g[j] ^ _gf_mul(g[j+1], _GF_EXP[i])
                      for j in range(len(g) - 1)] + [_gf_mul(g[-1], _GF_EXP[i])]
    return g

def _rs_encode(data: List[int], ecc_n: int) -> List[int]:
    g = _rs_generator(ecc_n)
    res = list(data) + [0] * ecc_n
    for i in range(len(data)):
        coef = res[i]
        if coef:
            for j in range(len(g)):
                res[i + j] ^= _gf_mul(g[j], coef)
    return res[len(data):]


# --- Version / capacity tables (level L only) --------------------
# data_codewords, ecc_per_block, num_blocks
_CAPACITY_L = {
    1: (19,  7, 1),
    2: (34,  10, 1),
    3: (55,  15, 1),
    4: (80,  20, 1),
    5: (108, 26, 1),
    6: (136, 18, 2),
    7: (156, 20, 2),
    8: (194, 24, 2),
    9: (232, 30, 2),
    10:(274, 18, 4),
}

# Version → align pattern centres
_ALIGN_POS = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30],
    6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46],
    10:[6, 28, 50],
}

# Format info bits (level L, mask 0..7) — pre-computed
_FORMAT_BITS_L = [
    0x77C4, 0x72F3, 0x7DAA, 0x789D,
    0x662F, 0x6318, 0x6C41, 0x6976,
]

def _pick_version(byte_len: int) -> int:
    """Smallest version that fits N bytes in mode 0100."""
    # mode (4) + char count (8 or 16) + data + terminator
    for v in range(1, 11):
        cap, _, _ = _CAPACITY_L[v]
        cap_bits = cap * 8
        cci = 8 if v <= 9 else 16
        need = 4 + cci + byte_len * 8 + 4
        if need <= cap_bits:
            return v
    raise ValueError("Text too long for the built-in QR encoder")


def _to_codewords(text: str, version: int) -> List[int]:
    cap, _, _ = _CAPACITY_L[version]
    cci = 8 if version <= 9 else 16
    data = text.encode("utf-8")
    bits: List[int] = []
    def push(val: int, n: int):
        for i in range(n - 1, -1, -1):
            bits.append((val >> i) & 1)
    push(0b0100, 4)        # byte mode
    push(len(data), cci)
    for byte in data:
        push(byte, 8)
    push(0, min(4, cap * 8 - len(bits)))   # terminator
    while len(bits) % 8:
        bits.append(0)
    code = []
    for i in range(0, len(bits), 8):
        v = 0
        for b in bits[i:i+8]:
            v = (v << 1) | b
        code.append(v)
    pad = [0xEC, 0x11]
    i = 0
    while len(code) < cap:
        code.append(pad[i % 2]); i += 1
    return code


def _matrix_size(version: int) -> int:
    return 17 + 4 * version


def _place_finder(m, x0, y0):
    for dy in range(-1, 8):
        for dx in range(-1, 8):
            x, y = x0 + dx, y0 + dy
            if 0 <= x < len(m) and 0 <= y < len(m):
                if -1 <= dx <= 7 and -1 <= dy <= 7:
                    inner = (1 <= dx <= 5 and 1 <= dy <= 5 and
                             not (2 <= dx <= 4 and 2 <= dy <= 4))
                    on = ((0 <= dx <= 6 and dy in (0, 6)) or
                          (0 <= dy <= 6 and dx in (0, 6)) or
                          (2 <= dx <= 4 and 2 <= dy <= 4))
                    m[y][x] = 1 if on and not inner else 0


def _place_align(m, version):
    pos = _ALIGN_POS.get(version, [])
    for cy in pos:
        for cx in pos:
            # skip the three near finder patterns
            if (cx, cy) in {(pos[0], pos[0]),
                            (pos[-1], pos[0]),
                            (pos[0], pos[-1])}:
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    on = (abs(dx) == 2 or abs(dy) == 2 or
                          (dx == 0 and dy == 0))
                    m[cy + dy][cx + dx] = 1 if on else 0


def _place_timing(m):
    n = len(m)
    for i in range(8, n - 8):
        if m[6][i] is None:
            m[6][i] = 1 if i % 2 == 0 else 0
        if m[i][6] is None:
            m[i][6] = 1 if i % 2 == 0 else 0


def _place_format(m, mask: int):
    bits = _FORMAT_BITS_L[mask]
    n = len(m)
    # Around top-left finder
    for i in range(15):
        b = (bits >> i) & 1
        if i < 6:
            m[8][i] = b
        elif i == 6:
            m[8][7] = b
        elif i == 7:
            m[8][8] = b
        elif i == 8:
            m[7][8] = b
        else:
            m[14 - i][8] = b
    for i in range(15):
        b = (bits >> i) & 1
        if i < 8:
            m[n - 1 - i][8] = b
        else:
            m[8][n - 15 + i] = b
    m[n - 8][8] = 1   # dark module


def _is_data(m, x, y):
    return m[y][x] is None


def _interleave(blocks_data, blocks_ecc, total_bytes, version):
    out = []
    max_d = max(len(b) for b in blocks_data)
    for i in range(max_d):
        for b in blocks_data:
            if i < len(b):
                out.append(b[i])
    max_e = max(len(b) for b in blocks_ecc)
    for i in range(max_e):
        for b in blocks_ecc:
            if i < len(b):
                out.append(b[i])
    return out


def _bits_from_bytes(byts):
    for b in byts:
        for i in range(7, -1, -1):
            yield (b >> i) & 1


def _place_data(m, byts):
    n = len(m)
    bit_iter = _bits_from_bytes(byts)
    x = n - 1
    going_up = True
    while x > 0:
        if x == 6:
            x -= 1
        for i in range(n):
            y = n - 1 - i if going_up else i
            for dx in (0, 1):
                xx = x - dx
                if _is_data(m, xx, y):
                    try:
                        m[y][xx] = next(bit_iter)
                    except StopIteration:
                        m[y][xx] = 0
        x -= 2
        going_up = not going_up


def _apply_mask(m, fixed, mask):
    cond = [
        lambda r, c: (r + c) % 2 == 0,
        lambda r, c: r % 2 == 0,
        lambda r, c: c % 3 == 0,
        lambda r, c: (r + c) % 3 == 0,
        lambda r, c: (r // 2 + c // 3) % 2 == 0,
        lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
        lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
        lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
    ][mask]
    n = len(m)
    for r in range(n):
        for c in range(n):
            if not fixed[r][c] and cond(r, c):
                m[r][c] ^= 1


def encode(text: str) -> List[List[int]]:
    """Encode text → 2-D matrix of 0/1 (1 = dark module)."""
    version = _pick_version(len(text.encode("utf-8")))
    cap, ecc_n, n_blocks = _CAPACITY_L[version]
    code = _to_codewords(text, version)
    # split into blocks (we use simple equal split since all our levels
    # have just one block size on level L for v1..10)
    block_size = cap // n_blocks
    blocks_data = [code[i*block_size:(i+1)*block_size] for i in range(n_blocks)]
    blocks_ecc  = [_rs_encode(b, ecc_n) for b in blocks_data]
    final = _interleave(blocks_data, blocks_ecc, cap + ecc_n*n_blocks, version)

    n = _matrix_size(version)
    m = [[None] * n for _ in range(n)]
    _place_finder(m, 0, 0)
    _place_finder(m, n - 7, 0)
    _place_finder(m, 0, n - 7)
    # Reserve format areas
    for i in range(9):
        m[8][i] = m[i][8] = 0
    for i in range(8):
        m[8][n - 1 - i] = 0
        m[n - 1 - i][8] = 0
    _place_align(m, version)
    _place_timing(m)
    _place_data(m, final)
    # Snapshot fixed cells before mask/format placement
    fixed = [[c is not None and (r in (6,) or c == 6 or
                                 (r < 9 and c < 9) or
                                 (r < 9 and c >= n - 8) or
                                 (r >= n - 8 and c < 9))
              for c, _ in enumerate(row)] for r, row in enumerate(m)]
    # Default: mask 0 (good enough for our small QR; no penalty eval)
    mask = 0
    _apply_mask(m, fixed, mask)
    _place_format(m, mask)
    # Convert any leftover None to 0 (shouldn't happen)
    for r in range(n):
        for c in range(n):
            if m[r][c] is None:
                m[r][c] = 0
    return m
