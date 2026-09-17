"""Knight Online .tbl file reader.

Supports both Standard (<=1534) and Chaos Expansion (1886+) encryption.

The cipher implementation is a Python port of the C# encryption classes in PENTAGRAM
Table Editor by xmkg (https://github.com/xmkg/ko-table-editor), used under the MIT
licence its README grants. See THIRD_PARTY_NOTICES.md at the repository root.

Encryption layers for Chaos Expansion:
  1. DES-like 16-round Feistel network (block cipher on 8-byte blocks)
  2. XOR stream cipher (key-derived byte stream)
"""

import struct
from dataclasses import dataclass
from pathlib import Path
from enum import IntEnum


class ColType(IntEnum):
    SBYTE = 1
    BYTE = 2
    SHORT = 3
    INT = 5
    UINT = 6
    STRING = 7
    FLOAT = 8


@dataclass
class TblTable:
    name: str
    column_types: list[ColType]
    rows: list[list]

    def find_rows(self, col_index: int, value) -> list[list]:
        return [r for r in self.rows if r[col_index] == value]

    def search(self, col_index: int, text: str) -> list[list]:
        text_lower = text.lower()
        return [r for r in self.rows if isinstance(r[col_index], str) and text_lower in r[col_index].lower()]


# ---------------------------------------------------------------------------
# Chaos Expansion header (16 bytes)
# ---------------------------------------------------------------------------

CHAOS_HEADER = bytes([
    0x4C, 0x26, 0x43, 0x7F, 0x80, 0xF1, 0x57, 0x98,
    0x79, 0xFC, 0xAF, 0x26, 0x86, 0xD6, 0x20, 0x8E,
])

# ---------------------------------------------------------------------------
# DES-like cipher tables
# ---------------------------------------------------------------------------

CHAOS_KEY = bytes([
    0,0,1,1,1,0,0,0,1,1,0,1,1,1,0,1,1,1,1,1,1,0,0,0,0,1,0,1,0,1,1,1,
    1,0,0,0,0,0,1,1,0,0,0,0,1,1,1,1,1,1,0,1,0,1,1,1,1,1,1,1,1,0,0,0,
    1,0,0,1,0,0,0,0,0,0,1,0,0,1,0,0,1,0,0,0,0,1,1,1,1,0,0,1,1,1,1,0,
    0,0,0,1,1,1,1,0,1,0,1,0,1,1,1,1,1,1,1,1,0,1,1,0,0,1,0,1,1,1,0,1,
    0,0,1,1,0,1,0,0,1,1,0,0,0,1,1,1,1,1,1,1,1,1,1,0,0,1,1,1,0,1,0,0,
    0,0,0,0,1,1,1,1,0,1,1,0,1,1,1,0,1,1,0,0,0,0,0,0,1,1,1,0,1,0,0,1,
    0,1,1,0,1,0,1,1,1,0,0,0,0,1,1,1,0,1,1,0,1,1,0,0,0,0,0,0,0,0,1,0,
    1,1,1,1,1,1,0,1,0,1,0,0,1,1,1,1,1,1,0,0,1,0,0,0,1,1,0,1,1,0,0,0,
    1,0,1,1,1,1,1,1,1,0,1,0,1,1,1,0,1,0,0,1,0,1,0,1,1,0,1,1,0,0,0,0,
    1,0,1,1,0,1,0,1,1,0,1,0,1,0,1,1,0,1,1,1,1,0,1,0,1,1,0,0,1,0,0,1,
    0,1,0,0,1,1,1,1,0,1,1,0,0,0,1,1,1,0,1,0,0,1,1,0,0,1,1,1,1,1,1,0,
    1,0,1,0,0,0,1,1,0,1,0,1,1,1,1,0,1,1,0,0,1,0,1,0,0,0,0,1,1,0,0,0,
    0,0,1,1,1,1,0,0,1,0,1,1,0,0,1,1,0,1,0,0,0,1,1,1,1,0,0,1,0,0,0,0,
    1,0,0,0,1,1,0,0,0,1,1,1,1,1,1,0,0,0,1,0,0,0,1,1,0,1,0,1,1,1,0,0,
    0,1,1,1,1,1,1,1,1,1,0,0,1,1,0,1,1,0,0,1,1,1,1,0,1,0,0,1,0,1,0,0,
    1,1,1,0,1,1,0,1,0,1,1,0,0,0,0,1,1,1,1,1,0,1,0,0,0,0,0,1,1,0,0,1,
    0,1,1,0,0,1,1,0,1,1,1,1,1,0,0,1,1,0,0,1,0,1,1,0,1,1,0,0,1,1,1,1,
    1,0,1,1,1,0,0,1,0,0,1,1,1,0,1,1,1,1,0,1,1,0,0,0,0,0,0,0,0,1,0,1,
    1,1,0,1,1,1,1,1,0,0,1,1,0,0,1,1,0,0,1,1,0,0,1,1,1,0,0,0,0,0,1,0,
    0,1,1,0,0,1,0,1,1,0,1,1,0,1,1,0,1,0,1,0,1,1,1,1,1,0,0,1,1,1,1,0,
    1,1,0,0,1,1,0,1,1,0,1,0,1,1,0,1,0,0,1,0,1,0,1,1,1,0,0,0,0,1,0,1,
    0,1,0,1,1,0,1,1,0,1,1,1,0,0,1,0,1,1,0,0,1,1,1,0,1,1,1,1,0,0,1,0,
    0,1,0,0,0,0,1,0,1,1,0,1,0,0,1,1,0,1,1,1,0,1,0,1,0,0,0,0,1,1,1,0,
    1,1,1,1,1,1,0,1,1,1,1,1,0,0,1,1,1,0,1,0,1,0,0,1,0,1,0,0,0,1,1,0,
])

EXPANSION_MATRIX = bytes([
    32,1,2,3,4,5, 4,5,6,7,8,9, 8,9,10,11,12,13, 12,13,14,15,16,17,
    16,17,18,19,20,21, 20,21,22,23,24,25, 24,25,26,27,28,29, 28,29,30,31,32,1,
])

PERMUTATION = bytes([
    16,7,20,21,29,12,28,17, 1,15,23,26,5,18,31,10,
    2,8,24,14,32,27,3,9, 19,13,30,6,22,11,4,25,
])

SBOX1 = [0x10101,0x100,0x1000101,0x1000000,0x10000,0x1010101,0x1010001,1,0x1010000,0x10001,0x10100,0x101,0x1000100,0x1000001,0,0x1010100,0,0x1010101,0x1010100,0x100,0x10101,0x10000,0x1000101,0x1000000,0x10001,0x10100,0x101,0x1010001,0x1000001,0x1000100,0x1010000,1,0x100,0x1000000,0x10101,1,0x1000101,0x10100,0x10000,0x1010001,0x1010101,0x101,0x1000001,0x1010100,0x1010000,0x10001,0x1000100,0,0x1010101,0x101,1,0x10000,0x100,0x1000001,0x1000000,0x1010100,0x1000100,0x1010001,0x1010000,0x10101,0x10001,0,0x10100,0x1000101]
SBOX2 = [0x1010101,0x1000000,1,0x10101,0x10100,0x1010001,0x1010000,0x100,0x1000001,0x1010100,0x10000,0x1000101,0x101,0,0x1000100,0x10001,0x1010000,0x1000101,0x100,0x1010100,0x1010101,0x10000,1,0x10101,0x101,0,0x1000000,0x10001,0x10100,0x1000001,0x1010001,0x1000100,0,0x10101,0x1010100,0x1010001,0x10001,0x100,0x1000101,0x1000000,0x1000100,1,0x101,0x10100,0x1000001,0x1010000,0x10000,0x1010101,0x1000101,1,0x10001,0x1000000,0x1010000,0x1010101,0x100,0x10000,0x1010001,0x10100,0x1010100,0x101,0,0x1000100,0x10101,0x1000001]
SBOX3 = [0x10001,0,0x1000001,0x10101,0x10100,0x1010000,0x1010101,0x1000100,0x1000000,0x1000101,0x101,0x1010100,0x1010001,0x100,0x10000,1,0x1000101,0x1010100,0,0x1000001,0x1010000,0x100,0x10100,0x10001,0x10000,1,0x1000100,0x10101,0x101,0x1010001,0x1010101,0x1000000,0x1000101,0x10100,0x100,0x1000001,1,0x1010101,0x1010000,0,0x1010001,0x1000000,0x10000,0x101,0x1000100,0x10001,0x10101,0x1010100,0x1000000,0x10001,0x1000101,0,0x10100,0x1000001,1,0x1010100,0x100,0x1010101,0x10101,0x1010000,0x1010001,0x1000100,0x10000,0x101]
SBOX4 = [0x1010100,0x1000101,0x10101,0x1010000,0,0x10100,0x1000001,0x10001,0x1000000,0x10000,1,0x1000100,0x1010001,0x101,0x100,0x1010101,0x1000101,1,0x1010001,0x1000100,0x10100,0x1010101,0,0x1010000,0x100,0x1010100,0x10000,0x101,0x1000000,0x10001,0x10101,0x1000001,0x10001,0x10100,0x1000001,0,0x101,0x1010001,0x1010100,0x1000101,0x1010101,0x1000000,0x1010000,0x10101,0x1000100,0x10000,1,0x100,0x1010000,0x1010101,0,0x10100,0x10001,0x1000000,0x1000101,1,0x1000001,0x100,0x1000100,0x1010001,0x101,0x1010100,0x10000,0x10101]
SBOX5 = [0x10000,0x101,0x100,0x1000000,0x1010100,0x10001,0x1010001,0x10100,1,0x1000100,0x1010000,0x1010101,0x1000101,0,0x10101,0x1000001,0x10101,0x1010001,0x10000,0x101,0x100,0x1010100,0x1000101,0x1000000,0x1000100,0,0x1010101,0x10001,0x1010000,0x1000001,1,0x10100,0x100,0x10000,0x1000000,0x1010001,0x10001,0x1000101,0x1010100,1,0x1010101,0x1000001,0x101,0x1000100,0x10100,0x1010000,0,0x10101,0x1010001,1,0x101,0x1010100,0x1000000,0x10101,0x10000,0x1000101,0x10100,0x1010101,0,0x1000001,0x10001,0x100,0x1000100,0x1010000]
SBOX6 = [0x101,0x1000000,0x10001,0x1010101,0x1000001,0x10000,0x10100,1,0,0x1000101,0x1010000,0x100,0x10101,0x1010100,0x1000100,0x1010001,0x10001,0x1010101,0x100,0x10000,0x1010100,0x101,0x1000001,0x1000100,0x10100,0x1000000,0x1000101,0x10101,0,0x1010001,0x1010000,1,0x1000001,0x10101,0x1010101,0x1000100,0x10000,1,0x101,0x1010000,0x1010100,0,0x100,0x10001,0x1000000,0x1000101,0x1010001,0x10100,0x100,0x1010000,0x10000,0x101,0x1000001,0x1000100,0x1010101,0x10001,0x1010001,0x10101,0x1000000,0x1010100,0x10100,0,1,0x1000101]
SBOX7 = [0x100,0x1010001,0x10000,0x10101,0x1010101,0,1,0x1000101,0x1010000,0x101,0x1000001,0x1010100,0x1000100,0x10001,0x10100,0x1000000,0x1000101,0,0x1010001,0x1010100,0x100,0x1000001,0x1000000,0x10001,0x10101,0x1010000,0x1000100,0x101,0x10000,0x1010101,1,0x10100,0x1000000,0x100,0x1010001,0x1000101,0x101,0x1010000,0x1010100,0x10101,0x10001,0x1010101,0x10100,1,0,0x1000100,0x1000001,0x10000,0x10100,0x1010001,0x1000101,1,0x1000000,0x100,0x10001,0x1010100,0x1000001,0x1000100,0,0x1010101,0x10101,0x10000,0x1010000,0x101]
SBOX8 = [0x1000101,0x10000,1,0x100,0x10100,0x1010101,0x1010001,0x1000000,0x10001,0x1000001,0x1010000,0x10101,0x1000100,0,0x101,0x1010100,0x1000000,0x1010101,0x1000101,1,0x10001,0x1010000,0x1010100,0x100,0x101,0x1000100,0x10100,0x1010001,0,0x10101,0x1000001,0x10000,0x1010100,0x1010001,0x100,0x1000000,0x1000001,0x101,0x10101,0x10000,0,0x10100,0x10001,0x1000101,0x1010101,0x1010000,0x1000100,1,0x10000,0x1000000,0x10101,0x1010100,0x100,0x10001,1,0x1000101,0x1010101,0x101,0x1000001,0,0x1010000,0x1000100,0x10100,0x1010001]
SBOXES = [SBOX1, SBOX2, SBOX3, SBOX4, SBOX5, SBOX6, SBOX7, SBOX8]


def _feistel(key: bytes, bits: bytearray, decrypt: bool):
    """16-round DES-like Feistel network."""
    buf = bytearray(48)
    for round_num in range(16):
        key_round = (15 - round_num) if decrypt else round_num
        for i in range(48):
            buf[i] = key[48 * key_round + i] ^ bits[EXPANSION_MATRIX[i] + 31]

        sbox_out = []
        for s in range(8):
            base = s * 6
            idx = (buf[base+4] | (2*(buf[base+3] | (2*(buf[base+2] | (2*(buf[base+1] | (2*(buf[base+5] | (2*buf[base]))))))))))
            sbox_out.append(SBOXES[s][idx])

        dest = bytearray(32)
        for i in range(8):
            dest[i*4:(i+1)*4] = sbox_out[i].to_bytes(4, 'little')

        if round_num < 15:
            for i in range(32):
                tmp_r = bits[i + 32]
                tmp_l = bits[i] ^ dest[PERMUTATION[i] - 1]
                bits[i + 32] = tmp_l
                bits[i] = tmp_r
        else:
            for i in range(32):
                bits[i] = bits[i] ^ dest[PERMUTATION[i] - 1]


def _decode_layer1(key: bytes, data: bytearray, file_len: int) -> bytearray:
    """DES-like block cipher decryption (Layer 1). Operates on 8-byte blocks."""
    payload = bytearray(data[20:])  # skip 20-byte header
    block_count = (len(payload) + 7) >> 3
    read_idx = 0
    write_idx = 0
    bits = bytearray(64)

    for _ in range(block_count):
        for i in range(64):
            bits[i] = 0
        bit_idx = 0
        for b in range(8):
            if read_idx >= len(payload):
                break
            val = payload[read_idx]; read_idx += 1
            bits[bit_idx]     = (val >> 7) & 1
            bits[bit_idx + 1] = (val >> 6) & 1
            bits[bit_idx + 2] = (val >> 5) & 1
            bits[bit_idx + 3] = (val >> 4) & 1
            bits[bit_idx + 4] = (val >> 3) & 1
            bits[bit_idx + 5] = (val >> 2) & 1
            bits[bit_idx + 6] = (val >> 1) & 1
            bits[bit_idx + 7] = val & 1
            bit_idx += 8

        _feistel(key, bits, decrypt=True)

        ci = 0
        while ci < 64 and write_idx < len(payload):
            payload[write_idx] = (
                bits[ci+7] | (2*(bits[ci+6] | (2*(bits[ci+5] | (2*(bits[ci+4] |
                (2*(bits[ci+3] | (2*(bits[ci+2] | (2*(bits[ci+1] | (2*bits[ci]))))))))))))))
            write_idx += 1
            ci += 8

    return payload


def _decode_layer2(data: bytearray, key_r: int = 0x0418,
                   key_c1: int = 0x8041, key_c2: int = 0x1804) -> bytearray:
    """XOR stream cipher decryption (Layer 2)."""
    key = key_r
    for i in range(len(data)):
        raw = data[i]
        tmp = (key >> 8) & 0xFF
        data[i] = tmp ^ raw
        key = ((raw + key) * key_c1 + key_c2) & 0xFFFF
    return data


def _decrypt_standard(data: bytearray, key_r: int = 0x0816,
                      key_c1: int = 0x6081, key_c2: int = 0x1608) -> bytearray:
    """Standard encryption (<=1534)."""
    key = key_r
    for i in range(len(data)):
        encrypted = data[i]
        data[i] = ((key >> 8) & 0xFF) ^ encrypted
        key = ((encrypted + key) * key_c1 + key_c2) & 0xFFFF
    return data


def _is_chaos(data: bytes) -> bool:
    return len(data) >= 20 and data[:16] == CHAOS_HEADER


def decrypt_tbl(data: bytes) -> bytes:
    """Decrypt a .tbl file (auto-detects encryption type)."""
    buf = bytearray(data)
    if _is_chaos(buf):
        real_len = struct.unpack_from('>I', buf, 16)[0]
        layer1 = _decode_layer1(CHAOS_KEY, buf, len(buf))
        layer2 = _decode_layer2(layer1)
        return bytes(layer2[:real_len])
    else:
        return bytes(_decrypt_standard(buf))


def _parse_table(name: str, data: bytes) -> TblTable:
    """Parse decrypted binary table data."""
    off = 0

    def read_i32():
        nonlocal off
        v = struct.unpack_from('<i', data, off)[0]; off += 4; return v

    def read_i16():
        nonlocal off
        v = struct.unpack_from('<h', data, off)[0]; off += 2; return v

    def read_u32():
        nonlocal off
        v = struct.unpack_from('<I', data, off)[0]; off += 4; return v

    def read_f32():
        nonlocal off
        v = struct.unpack_from('<f', data, off)[0]; off += 4; return v

    def read_str():
        nonlocal off
        length = struct.unpack_from('<i', data, off)[0]; off += 4
        if length <= 0 or off + length > len(data):
            return ""
        s = "".join(chr(data[off + i]) for i in range(length))
        off += length
        return s

    num_cols = read_i32()

    # Chaos Expansion has a 5-byte header before columns
    if num_cols > 0 and off + 4 <= len(data):
        first_type = struct.unpack_from('<i', data, off)[0]
        if first_type < 1 or first_type > 8:
            off = 5
            num_cols = read_i32()

    col_types = [ColType(read_i32()) for _ in range(num_cols)]
    num_rows = read_i32()

    rows = []
    for _ in range(num_rows):
        if off >= len(data):
            break
        row = []
        for ct in col_types:
            if off >= len(data):
                row.append(None)
                continue
            if ct == ColType.SBYTE:
                row.append(struct.unpack_from('<b', data, off)[0]); off += 1
            elif ct == ColType.BYTE:
                row.append(data[off]); off += 1
            elif ct == ColType.SHORT:
                row.append(read_i16())
            elif ct == ColType.INT:
                row.append(read_i32())
            elif ct == ColType.UINT:
                row.append(read_u32())
            elif ct == ColType.FLOAT:
                row.append(read_f32())
            elif ct == ColType.STRING:
                row.append(read_str())
            else:
                row.append(None)
        rows.append(row)

    return TblTable(name=name, column_types=col_types, rows=rows)


def read_tbl(path: str | Path) -> TblTable:
    """Read and decrypt a .tbl file."""
    path = Path(path)
    data = path.read_bytes()
    decrypted = decrypt_tbl(data)
    return _parse_table(path.stem, decrypted)
