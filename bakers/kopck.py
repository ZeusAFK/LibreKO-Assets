"""Read and write Godot .pck packages.

Matches the launcher's LibreKO.Patch.Core Pck writer: format 4, 16-byte alignment, offsets stored
relative to a file base that equals the header size.
"""

import hashlib
import os
import struct

MAGIC = 0x43504447
FORMAT_VERSION = 4
ALIGNMENT = 16
RESERVED_WORDS = 16
FLAG_RELATIVE_FILE_BASE = 2
ENTRY_FLAG_REMOVAL = 2

HEADER_FIELDS = 4 + 4 * 4 + 4 + 8 + 8


def align(value, alignment=ALIGNMENT):
    return (value + alignment - 1) // alignment * alignment


HEADER_BYTES = align(HEADER_FIELDS + RESERVED_WORDS * 4)


class Entry:
    __slots__ = ("path", "offset", "size", "md5", "flags")

    def __init__(self, path, offset, size, md5, flags):
        self.path = path
        self.offset = offset
        self.size = size
        self.md5 = md5
        self.flags = flags

    @property
    def removed(self):
        return bool(self.flags & ENTRY_FLAG_REMOVAL)


class PckReader:
    def __init__(self, path):
        self.path = path
        self._f = open(path, "rb")
        head = self._f.read(40)
        magic, fmt, major, minor, patch, flags = struct.unpack("<IIIIII", head[:24])
        if magic != MAGIC:
            raise ValueError("not a Godot pack: %s" % path)
        base, dir_offset = struct.unpack("<QQ", head[24:40])
        self.format = fmt
        self.engine = (major, minor, patch)
        self.flags = flags
        self.file_base = base
        self._relative = bool(flags & FLAG_RELATIVE_FILE_BASE)
        self._f.seek(dir_offset if fmt >= 3 else HEADER_FIELDS + RESERVED_WORDS * 4)
        count, = struct.unpack("<I", self._f.read(4))
        self.entries = {}
        for _ in range(count):
            field, = struct.unpack("<I", self._f.read(4))
            raw = self._f.read(field).rstrip(b"\x00").decode("utf-8")
            offset, size = struct.unpack("<QQ", self._f.read(16))
            md5 = self._f.read(16)
            eflags = 0
            if fmt >= 2:
                eflags, = struct.unpack("<I", self._f.read(4))
            if self._relative:
                offset += self.file_base
            self.entries[raw] = Entry(raw, offset, size, md5, eflags)

    def read(self, path):
        e = self.entries[path]
        self._f.seek(e.offset)
        return self._f.read(e.size)

    def sha256(self, path):
        e = self.entries[path]
        self._f.seek(e.offset)
        h = hashlib.sha256()
        left = e.size
        while left > 0:
            chunk = self._f.read(min(1 << 20, left))
            if not chunk:
                break
            h.update(chunk)
            left -= len(chunk)
        return h.hexdigest()

    def close(self):
        self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def write_pck(out_path, items, engine, removals=(), progress=None):
    """items: iterable of (res_path, bytes_or_callable). removals: res paths."""
    materialised = []
    for path, payload in items:
        data = payload() if callable(payload) else payload
        materialised.append((path, data))
    materialised.sort(key=lambda kv: kv[0])
    removals = sorted(set(removals))

    cursor = HEADER_BYTES
    placed = []
    shared = {}
    for path, data in materialised:
        digest = hashlib.sha256(data).hexdigest()
        if digest in shared:
            placed.append((path, data, shared[digest]))
            continue
        cursor = align(cursor)
        placed.append((path, data, cursor))
        shared[digest] = cursor
        cursor += len(data)

    dir_offset = align(cursor)

    with open(out_path, "wb") as f:
        f.write(b"\x00" * HEADER_BYTES)
        for i, (path, data, offset) in enumerate(placed):
            f.seek(offset)
            f.write(data)
            if progress and i % 500 == 0:
                progress(i, len(placed))

        f.seek(dir_offset)
        f.write(struct.pack("<I", len(placed) + len(removals)))
        for path, data, offset in placed:
            raw = path.encode("utf-8")
            field = align(len(raw), 4)
            f.write(struct.pack("<I", field))
            f.write(raw + b"\x00" * (field - len(raw)))
            f.write(struct.pack("<QQ", offset - HEADER_BYTES, len(data)))
            f.write(hashlib.md5(data).digest())
            f.write(struct.pack("<I", 0))
        for path in removals:
            raw = path.encode("utf-8")
            field = align(len(raw), 4)
            f.write(struct.pack("<I", field))
            f.write(raw + b"\x00" * (field - len(raw)))
            f.write(struct.pack("<QQ", 0, 0))
            f.write(b"\x00" * 16)
            f.write(struct.pack("<I", ENTRY_FLAG_REMOVAL))

        f.seek(0)
        f.write(struct.pack("<IIIIII", MAGIC, FORMAT_VERSION, engine[0], engine[1],
                            engine[2], FLAG_RELATIVE_FILE_BASE))
        f.write(struct.pack("<QQ", HEADER_BYTES, dir_offset))
    return out_path


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def diff(old_pck, new_pck):
    """Return (changed_paths, removed_paths) going from old to new."""
    changed, removed = [], []
    old_entries = old_pck.entries if old_pck else {}
    for path in new_pck.entries:
        e = new_pck.entries[path]
        if e.removed:
            continue
        o = old_entries.get(path)
        if o is None or o.size != e.size or o.md5 != e.md5:
            changed.append(path)
    for path, e in old_entries.items():
        if e.removed:
            continue
        if path not in new_pck.entries or new_pck.entries[path].removed:
            removed.append(path)
    return sorted(changed), sorted(removed)
