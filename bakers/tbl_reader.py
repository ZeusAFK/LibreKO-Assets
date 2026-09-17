"""Knight Online .tbl file reader (Chaos Expansion + Standard)."""
from __future__ import annotations
import struct
from dataclasses import dataclass
from pathlib import Path
from enum import IntEnum


class ColType(IntEnum):
    SBYTE = 1; BYTE = 2; SHORT = 3; INT = 5; UINT = 6; STRING = 7; FLOAT = 8; INT64 = 10


@dataclass
class TblTable:
    name: str
    column_types: list
    rows: list
    def find_rows(self, col_index, value):
        return [r for r in self.rows if r[col_index] == value]
    def search(self, col_index, text):
        t = text.lower()
        return [r for r in self.rows if isinstance(r[col_index], str) and t in r[col_index].lower()]


CHAOS_HEADER = bytes([0x4C,0x26,0x43,0x7F,0x80,0xF1,0x57,0x98,0x79,0xFC,0xAF,0x26,0x86,0xD6,0x20,0x8E])

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
    1,1,1,1,1,1,0,1,1,1,1,1,0,0,1,1,1,0,1,0,1,0,0,1,0,1,0,0,0,1,1,0])

EXPANSION_MATRIX = bytes([32,1,2,3,4,5,4,5,6,7,8,9,8,9,10,11,12,13,12,13,14,15,16,17,
    16,17,18,19,20,21,20,21,22,23,24,25,24,25,26,27,28,29,28,29,30,31,32,1])
PERMUTATION = bytes([16,7,20,21,29,12,28,17,1,15,23,26,5,18,31,10,
    2,8,24,14,32,27,3,9,19,13,30,6,22,11,4,25])

SBOX1 = [0x10101,0x100,0x1000101,0x1000000,0x10000,0x1010101,0x1010001,1,0x1010000,0x10001,0x10100,0x101,0x1000100,0x1000001,0,0x1010100,0,0x1010101,0x1010100,0x100,0x10101,0x10000,0x1000101,0x1000000,0x10001,0x10100,0x101,0x1010001,0x1000001,0x1000100,0x1010000,1,0x100,0x1000000,0x10101,1,0x1000101,0x10100,0x10000,0x1010001,0x1010101,0x101,0x1000001,0x1010100,0x1010000,0x10001,0x1000100,0,0x1010101,0x101,1,0x10000,0x100,0x1000001,0x1000000,0x1010100,0x1000100,0x1010001,0x1010000,0x10101,0x10001,0,0x10100,0x1000101]
SBOX2 = [0x1010101,0x1000000,1,0x10101,0x10100,0x1010001,0x1010000,0x100,0x1000001,0x1010100,0x10000,0x1000101,0x101,0,0x1000100,0x10001,0x1010000,0x1000101,0x100,0x1010100,0x1010101,0x10000,1,0x10101,0x101,0,0x1000000,0x10001,0x10100,0x1000001,0x1010001,0x1000100,0,0x10101,0x1010100,0x1010001,0x10001,0x100,0x1000101,0x1000000,0x1000100,1,0x101,0x10100,0x1000001,0x1010000,0x10000,0x1010101,0x1000101,1,0x10001,0x1000000,0x1010000,0x1010101,0x100,0x10000,0x1010001,0x10100,0x1010100,0x101,0,0x1000100,0x10101,0x1000001]
SBOX3 = [0x10001,0,0x1000001,0x10101,0x10100,0x1010000,0x1010101,0x1000100,0x1000000,0x1000101,0x101,0x1010100,0x1010001,0x100,0x10000,1,0x1000101,0x1010100,0,0x1000001,0x1010000,0x100,0x10100,0x10001,0x10000,1,0x1000100,0x10101,0x101,0x1010001,0x1010101,0x1000000,0x1000101,0x10100,0x100,0x1000001,1,0x1010101,0x1010000,0,0x1010001,0x1000000,0x10000,0x101,0x1000100,0x10001,0x10101,0x1010100,0x1000000,0x10001,0x1000101,0,0x10100,0x1000001,1,0x1010100,0x100,0x1010101,0x10101,0x1010000,0x1010001,0x1000100,0x10000,0x101]
SBOX4 = [0x1010100,0x1000101,0x10101,0x1010000,0,0x10100,0x1000001,0x10001,0x1000000,0x10000,1,0x1000100,0x1010001,0x101,0x100,0x1010101,0x1000101,1,0x1010001,0x1000100,0x10100,0x1010101,0,0x1010000,0x100,0x1010100,0x10000,0x101,0x1000000,0x10001,0x10101,0x1000001,0x10001,0x10100,0x1000001,0,0x101,0x1010001,0x1010100,0x1000101,0x1010101,0x1000000,0x1010000,0x10101,0x1000100,0x10000,1,0x100,0x1010000,0x1010101,0,0x10100,0x10001,0x1000000,0x1000101,1,0x1000001,0x100,0x1000100,0x1010001,0x101,0x1010100,0x10000,0x10101]
SBOX5 = [0x10000,0x101,0x100,0x1000000,0x1010100,0x10001,0x1010001,0x10100,1,0x1000100,0x1010000,0x1010101,0x1000101,0,0x10101,0x1000001,0x10101,0x1010001,0x10000,0x101,0x100,0x1010100,0x1000101,0x1000000,0x1000100,0,0x1010101,0x10001,0x1010000,0x1000001,1,0x10100,0x100,0x10000,0x1000000,0x1010001,0x10001,0x1000101,0x1010100,1,0x1010101,0x1000001,0x101,0x1000100,0x10100,0x1010000,0,0x10101,0x1010001,1,0x101,0x1010100,0x1000000,0x10101,0x10000,0x1000101,0x10100,0x1010101,0,0x1000001,0x10001,0x100,0x1000100,0x1010000]
SBOX6 = [0x101,0x1000000,0x10001,0x1010101,0x1000001,0x10000,0x10100,1,0,0x1000101,0x1010000,0x100,0x10101,0x1010100,0x1000100,0x1010001,0x10001,0x1010101,0x100,0x10000,0x1010100,0x101,0x1000001,0x1000100,0x10100,0x1000000,0x1000101,0x10101,0,0x1010001,0x1010000,1,0x1000001,0x10101,0x1010101,0x1000100,0x10000,1,0x101,0x1010000,0x1010100,0,0x100,0x10001,0x1000000,0x1000101,0x1010001,0x10100,0x100,0x1010000,0x10000,0x101,0x1000001,0x1000100,0x1010101,0x10001,0x1010001,0x10101,0x1000000,0x1010100,0x10100,0,1,0x1000101]
SBOX7 = [0x100,0x1010001,0x10000,0x10101,0x1010101,0,1,0x1000101,0x1010000,0x101,0x1000001,0x1010100,0x1000100,0x10001,0x10100,0x1000000,0x1000101,0,0x1010001,0x1010100,0x100,0x1000001,0x1000000,0x10001,0x10101,0x1010000,0x1000100,0x101,0x10000,0x1010101,1,0x10100,0x1000000,0x100,0x1010001,0x1000101,0x101,0x1010000,0x1010100,0x10101,0x10001,0x1010101,0x10100,1,0,0x1000100,0x1000001,0x10000,0x10100,0x1010001,0x1000101,1,0x1000000,0x100,0x10001,0x1010100,0x1000001,0x1000100,0,0x1010101,0x10101,0x10000,0x1010000,0x101]
SBOX8 = [0x1000101,0x10000,1,0x100,0x10100,0x1010101,0x1010001,0x1000000,0x10001,0x1000001,0x1010000,0x10101,0x1000100,0,0x101,0x1010100,0x1000000,0x1010101,0x1000101,1,0x10001,0x1010000,0x1010100,0x100,0x101,0x1000100,0x10100,0x1010001,0,0x10101,0x1000001,0x10000,0x1010100,0x1010001,0x100,0x1000000,0x1000001,0x101,0x10101,0x10000,0,0x10100,0x10001,0x1000101,0x1010101,0x1010000,0x1000100,1,0x10000,0x1000000,0x10101,0x1010100,0x100,0x10001,1,0x1000101,0x1010101,0x101,0x1000001,0,0x1010000,0x1000100,0x10100,0x1010001]
SBOXES = [SBOX1,SBOX2,SBOX3,SBOX4,SBOX5,SBOX6,SBOX7,SBOX8]


def _feistel(key, bits, decrypt):
    buf = bytearray(48)
    for rn in range(16):
        kr = (15 - rn) if decrypt else rn
        for i in range(48):
            buf[i] = key[48*kr + i] ^ bits[EXPANSION_MATRIX[i] + 31]
        sout = []
        for s in range(8):
            base = s*6
            idx = (buf[base+4] | (2*(buf[base+3] | (2*(buf[base+2] | (2*(buf[base+1] | (2*(buf[base+5] | (2*buf[base]))))))))))
            sout.append(SBOXES[s][idx])
        dest = bytearray(32)
        for i in range(8):
            dest[i*4:(i+1)*4] = sout[i].to_bytes(4,'little')
        if rn < 15:
            for i in range(32):
                tr = bits[i+32]; tl = bits[i] ^ dest[PERMUTATION[i]-1]
                bits[i+32] = tl; bits[i] = tr
        else:
            for i in range(32):
                bits[i] = bits[i] ^ dest[PERMUTATION[i]-1]


def _decode_layer1(key, data, file_len):
    payload = bytearray(data[20:])
    block_count = (len(payload)+7) >> 3
    ri = wi = 0; bits = bytearray(64)
    for _ in range(block_count):
        for i in range(64): bits[i] = 0
        bi = 0
        for b in range(8):
            if ri >= len(payload): break
            val = payload[ri]; ri += 1
            for k in range(8): bits[bi+k] = (val >> (7-k)) & 1
            bi += 8
        _feistel(key, bits, True)
        ci = 0
        while ci < 64 and wi < len(payload):
            payload[wi] = (bits[ci+7]|(2*(bits[ci+6]|(2*(bits[ci+5]|(2*(bits[ci+4]|(2*(bits[ci+3]|(2*(bits[ci+2]|(2*(bits[ci+1]|(2*bits[ci])))))))))))) ))
            wi += 1; ci += 8
    return payload


def _decode_layer2(data, key_r=0x0418, c1=0x8041, c2=0x1804):
    key = key_r
    for i in range(len(data)):
        raw = data[i]; data[i] = ((key>>8)&0xFF) ^ raw
        key = ((raw+key)*c1+c2) & 0xFFFF
    return data


def _decrypt_standard(data, key_r=0x0816, c1=0x6081, c2=0x1608):
    key = key_r
    for i in range(len(data)):
        e = data[i]; data[i] = ((key>>8)&0xFF) ^ e
        key = ((e+key)*c1+c2) & 0xFFFF
    return data


def _is_chaos(data):
    return len(data) >= 20 and data[:16] == CHAOS_HEADER


def decrypt_tbl(data):
    buf = bytearray(data)
    if _is_chaos(buf):
        real_len = struct.unpack_from('>I', buf, 16)[0]
        l1 = _decode_layer1(CHAOS_KEY, buf, len(buf))
        l2 = _decode_layer2(l1)
        return bytes(l2[:real_len])
    return bytes(_decrypt_standard(buf))


def _parse_table(name, data):
    off = 0
    def ri32():
        nonlocal off; v = struct.unpack_from('<i',data,off)[0]; off+=4; return v
    def ri16():
        nonlocal off; v = struct.unpack_from('<h',data,off)[0]; off+=2; return v
    def ru32():
        nonlocal off; v = struct.unpack_from('<I',data,off)[0]; off+=4; return v
    def ri64():
        nonlocal off; v = struct.unpack_from('<q',data,off)[0]; off+=8; return v
    def rf32():
        nonlocal off; v = struct.unpack_from('<f',data,off)[0]; off+=4; return v
    def rstr():
        nonlocal off
        length = struct.unpack_from('<i',data,off)[0]; off+=4
        if length <= 0 or off+length > len(data): return ""
        s = "".join(chr(data[off+i]) for i in range(length)); off += length; return s
    num_cols = ri32()
    # Some .tbl files carry a 5-byte prefix before [num_cols][types...]. Detect it by
    # peeking the first column type: a valid type is 1..8, so an out-of-range value
    # (or a zero-leading prefix that read num_cols==0) means we must skip the prefix.
    # NOTE: don't gate on num_cols>0 — UPC_DefaultLooks's prefix begins 0x00000000,
    # so num_cols reads as 0 yet the prefix is still present (off=5 recovers it).
    if off+4 <= len(data):
        ft = struct.unpack_from('<i',data,off)[0]
        if ft < 1 or ft > 10:
            off = 5; num_cols = ri32()
    col_types = [ColType(ri32()) for _ in range(num_cols)]
    num_rows = ri32()
    rows = []
    for _ in range(num_rows):
        if off >= len(data): break
        row = []
        for ct in col_types:
            if off >= len(data): row.append(None); continue
            if ct == ColType.SBYTE: row.append(struct.unpack_from('<b',data,off)[0]); off+=1
            elif ct == ColType.BYTE: row.append(data[off]); off+=1
            elif ct == ColType.SHORT: row.append(ri16())
            elif ct == ColType.INT: row.append(ri32())
            elif ct == ColType.INT64: row.append(ri64())
            elif ct == ColType.UINT: row.append(ru32())
            elif ct == ColType.FLOAT: row.append(rf32())
            elif ct == ColType.STRING: row.append(rstr())
            else: row.append(None)
        rows.append(row)
    return TblTable(name=name, column_types=col_types, rows=rows)


def read_tbl(path):
    path = Path(path)
    return _parse_table(path.stem, decrypt_tbl(path.read_bytes()))
