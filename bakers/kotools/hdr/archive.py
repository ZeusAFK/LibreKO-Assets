"""HDR/SRC archive format for Knight Online packed assets.

HDR layout:
  [uint32] version_and_count
    - If (value & 0xFF0000) != 0: "new version" (encrypted entries), count = value & 0xFFFF
    - Else: "old version" (unencrypted), count = value
  [if new_version: 4 bytes padding/unknown]

  For each entry (count times):
    [uint16 or uint32] name_length (2 bytes if new_version, 4 bytes if old)
    [N bytes] filename (ASCII, may contain backslash separators)
    [uint32] offset in .src file
    [uint32] size in .src file

SRC layout:
  Raw concatenation of file data at the offsets specified in HDR entries.
"""

import os
import fnmatch
from dataclasses import dataclass
from pathlib import Path

from ..io.binary_reader import BinaryReader
from ..io.binary_writer import BinaryWriter
from ..crypto.wincrypt import KOCipher, DEFAULT_KEY


@dataclass
class HDREntry:
    """A single file entry in an HDR archive."""
    name: str
    offset: int
    size: int


@dataclass
class HDRArchive:
    """Parsed HDR archive directory."""
    version: int
    is_encrypted: bool
    entries: list[HDREntry]
    hdr_path: Path
    src_path: Path

    def find(self, pattern: str) -> list[HDREntry]:
        """Find entries matching a glob pattern (e.g., '*.dxt')."""
        return [e for e in self.entries if fnmatch.fnmatch(e.name.lower(), pattern.lower())]


def read_hdr(hdr_path: str | Path, key: bytes = DEFAULT_KEY) -> HDRArchive:
    """Read an HDR archive directory.

    Supports two formats:
      - Old: [u32 count] [entries with u32 name lengths]
      - New: [u32 version_flags] [u32 count_or_padding] [entries with u16 name lengths]
        The new format may or may not be encrypted. If the first parse attempt
        on the raw data fails (no valid entries), we retry with RC4 decryption.
    """
    hdr_path = Path(hdr_path)
    src_path = hdr_path.with_suffix(".src")

    with open(str(hdr_path), "rb") as f:
        data = f.read()

    reader = BinaryReader(data)
    version_raw = reader.read_uint32()

    is_new = (version_raw & 0xFF0000) != 0
    if is_new:
        # New format: skip 4 bytes (count or padding), then u16 name lengths.
        # Try parsing raw first; if that fails, decrypt and retry.
        entry_data = data[8:]  # skip version(4) + count/padding(4)

        entries = _parse_entries(entry_data, use_uint16=True)

        # If raw parse found nothing, try decrypting
        if not entries:
            cipher = KOCipher(key)
            decrypted = cipher.decrypt(data[4:])
            # Decrypted block: [4 bytes count] [entries...]
            entry_data = decrypted[4:]
            entries = _parse_entries(entry_data, use_uint16=True)
    else:
        # Old format: version_raw IS the count, entries use u32 name lengths
        entries = _parse_entries(data[4:], use_uint16=False)

    return HDRArchive(
        version=version_raw,
        is_encrypted=is_new,
        entries=entries,
        hdr_path=hdr_path,
        src_path=src_path,
    )


def _parse_entries(data: bytes, use_uint16: bool) -> list[HDREntry]:
    """Parse entry records from raw bytes. Returns entries list."""
    reader = BinaryReader(data)
    entries = []
    while not reader.at_eof() and reader.remaining() > 4:
        if use_uint16:
            name_len = reader.read_uint16()
        else:
            name_len = reader.read_uint32()
        if name_len == 0 or name_len > 500:
            break
        if reader.remaining() < name_len + 8:
            break
        name = reader.read_string(name_len)
        offset = reader.read_uint32()
        size = reader.read_uint32()
        entries.append(HDREntry(name=name, offset=offset, size=size))
    return entries


def extract_entry(archive: HDRArchive, entry: HDREntry, output_dir: Path) -> Path:
    """Extract a single entry from the archive to output_dir."""
    out_path = output_dir / entry.name.replace("\\", "/")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(str(archive.src_path), "rb") as f:
        f.seek(entry.offset)
        data = f.read(entry.size)

    with open(str(out_path), "wb") as f:
        f.write(data)

    return out_path


def extract_all(archive: HDRArchive, output_dir: Path, pattern: str = "*") -> list[Path]:
    """Extract all (or filtered) entries from the archive."""
    output_dir = Path(output_dir)
    entries = archive.find(pattern) if pattern != "*" else archive.entries
    paths = []
    for entry in entries:
        paths.append(extract_entry(archive, entry, output_dir))
    return paths


def patch_archive(archive: HDRArchive, updates: dict[str, bytes],
                   key: bytes = DEFAULT_KEY):
    """Patch an existing archive by appending/replacing entries.

    Appends new data to the end of the .src file, then rewrites the .hdr
    with updated offsets. Existing entries that aren't updated keep their
    original offsets. Much faster than a full repack.

    Args:
        archive: The loaded HDRArchive to patch
        updates: Dict of {entry_name: new_bytes_data} to update or add
    """
    import shutil

    hdr_path = archive.hdr_path
    src_path = archive.src_path

    # Backup both files before patching
    hdr_bak = hdr_path.with_suffix(".hdr.bak")
    src_bak = src_path.with_suffix(".src.bak")
    if not hdr_bak.exists():
        shutil.copy2(str(hdr_path), str(hdr_bak))
    if not src_bak.exists():
        shutil.copy2(str(src_path), str(src_bak))

    # Append new data to .src and track new offsets
    new_offsets = {}
    with open(str(src_path), "ab") as f:
        for name, data in updates.items():
            offset = f.tell()
            f.write(data)
            new_offsets[name] = (offset, len(data))

    # Build updated entry list
    entry_map = {e.name.lower(): e for e in archive.entries}
    for name, (offset, size) in new_offsets.items():
        key_lower = name.lower()
        if key_lower in entry_map:
            # Update existing entry
            entry_map[key_lower].offset = offset
            entry_map[key_lower].size = size
        else:
            # Add new entry
            entry_map[key_lower] = HDREntry(name=name, offset=offset, size=size)

    updated_entries = list(entry_map.values())

    # Rewrite HDR
    is_new = archive.is_encrypted
    w = BinaryWriter()
    if is_new:
        # New format: version(4) + count/padding(4) + entries with uint16 name lengths
        w.write_uint32(archive.version)
        w.write_uint32(len(updated_entries))
        for entry in updated_entries:
            encoded = entry.name.encode("ascii")
            w.write_uint16(len(encoded))
            w.write_bytes(encoded)
            w.write_uint32(entry.offset)
            w.write_uint32(entry.size)
    else:
        # Old format: count(4) + entries with uint32 name lengths
        w.write_uint32(len(updated_entries))
        for entry in updated_entries:
            encoded = entry.name.encode("ascii")
            w.write_uint32(len(encoded))
            w.write_bytes(encoded)
            w.write_uint32(entry.offset)
            w.write_uint32(entry.size)

    with open(str(hdr_path), "wb") as f:
        f.write(w.getvalue())

    # Update the in-memory archive
    archive.entries = updated_entries


def pack_hdr(
    input_dir: str | Path,
    output_base: str | Path,
    encrypt: bool = False,
    key: bytes = DEFAULT_KEY,
):
    """Pack a directory into HDR/SRC archive pair."""
    input_dir = Path(input_dir)
    output_base = Path(output_base)
    hdr_path = output_base.with_suffix(".hdr")
    src_path = output_base.with_suffix(".src")

    entries = []
    with open(str(src_path), "wb") as src_f:
        for file_path in sorted(input_dir.rglob("*")):
            if file_path.is_dir():
                continue
            rel = file_path.relative_to(input_dir)
            name = str(rel).replace("/", "\\")
            offset = src_f.tell()
            data = file_path.read_bytes()
            src_f.write(data)
            entries.append(HDREntry(name=name, offset=offset, size=len(data)))

    # Build HDR
    w = BinaryWriter()
    if encrypt:
        inner = BinaryWriter()
        inner.write_uint32(0)  # padding
        for entry in entries:
            encoded_name = entry.name.encode("ascii")
            inner.write_uint16(len(encoded_name))
            inner.write_bytes(encoded_name)
            inner.write_uint32(entry.offset)
            inner.write_uint32(entry.size)
        cipher = KOCipher(key)
        encrypted = cipher.encrypt(inner.getvalue())
        version = 0x010000 | len(entries)
        w.write_uint32(version)
        w.write_bytes(encrypted)
    else:
        w.write_uint32(len(entries))
        for entry in entries:
            encoded_name = entry.name.encode("ascii")
            w.write_uint32(len(encoded_name))
            w.write_bytes(encoded_name)
            w.write_uint32(entry.offset)
            w.write_uint32(entry.size)

    with open(str(hdr_path), "wb") as f:
        f.write(w.getvalue())
