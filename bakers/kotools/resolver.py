"""Unified asset resolver — finds KO files on disk or inside HDR/SRC archives.

The N3 engine references files by relative paths like 'item\\11_1042_20_0.n3cpart'.
These may exist as loose files under the game directory, or packed inside
HDR/SRC archives (Item/item.hdr, Object/object.hdr, etc.).

This resolver checks loose files first, then searches archives.
"""

from pathlib import Path
from typing import Optional
from functools import lru_cache

from .hdr.archive import read_hdr, HDRArchive, HDREntry


# Map of path prefixes to archive names
_ARCHIVE_MAP = {
    "item": "Item/item",
    "object": "Object/object",
    "ui": "UI/ui",
    "fx": "fx/fx",
    "snd": "Snd/snd",
}


class AssetResolver:
    """Resolves KO asset paths to actual file data."""

    def __init__(self, game_dir: str | Path):
        self.game_dir = Path(game_dir)
        self._archives: dict[str, HDRArchive] = {}
        self._archive_index: dict[str, tuple[str, HDREntry]] = {}
        self._entries_by_archive: dict[str, dict[str, HDREntry]] = {}

    def _ensure_archives_loaded(self):
        if self._archive_index:
            return
        for prefix, base in _ARCHIVE_MAP.items():
            hdr_path = self.game_dir / f"{base}.hdr"
            if hdr_path.exists():
                try:
                    archive = read_hdr(str(hdr_path))
                    self._archives[prefix] = archive
                    own = self._entries_by_archive.setdefault(prefix, {})
                    for entry in archive.entries:
                        # Index by the entry name (lowercase, forward slashes)
                        key = entry.name.lower().replace("\\", "/")
                        self._archive_index[key] = (prefix, entry)
                        own.setdefault(key, entry)
                except Exception:
                    pass

    def _folder_entry(self, key: str) -> Optional[tuple[str, HDREntry]]:
        folder, _, name = key.rpartition("/")
        entry = self._entries_by_archive.get(folder, {}).get(name)
        return (folder, entry) if entry is not None else None

    def _read_entry(self, prefix: str, entry: HDREntry) -> bytes:
        with open(str(self._archives[prefix].src_path), "rb") as f:
            f.seek(entry.offset)
            return f.read(entry.size)

    def resolve_path(self, ref_path: str) -> Optional[Path]:
        """Resolve a reference path to a loose file on disk. Returns None if not found."""
        normalized = ref_path.replace("\\", "/")
        full = self.game_dir / normalized
        if full.exists():
            return full
        # Try case-insensitive match
        parts = normalized.split("/")
        current = self.game_dir
        for part in parts:
            found = None
            if current.exists():
                for child in current.iterdir():
                    if child.name.lower() == part.lower():
                        found = child
                        break
            if found:
                current = found
            else:
                return None
        return current if current.exists() else None

    def read(self, ref_path: str) -> Optional[bytes]:
        """Read file data from loose file or archive. Returns None if not found."""
        # Try loose file first
        loose = self.resolve_path(ref_path)
        if loose and loose.is_file():
            return loose.read_bytes()

        # Try archives
        self._ensure_archives_loaded()
        key = ref_path.lower().replace("\\", "/")
        own = self._folder_entry(key)
        if own is not None:
            return self._read_entry(*own)
        if key in self._archive_index:
            return self._read_entry(*self._archive_index[key])

        # Try without the directory prefix (e.g., 'item\foo.dxt' -> 'foo.dxt')
        basename = key.split("/")[-1]
        for full_key, (prefix, entry) in self._archive_index.items():
            if full_key.endswith("/" + basename) or full_key == basename:
                return self._read_entry(prefix, entry)

        return None

    def exists(self, ref_path: str) -> bool:
        """Check if a file exists (loose or in archive)."""
        loose = self.resolve_path(ref_path)
        if loose and loose.is_file():
            return True
        self._ensure_archives_loaded()
        key = ref_path.lower().replace("\\", "/")
        return self._folder_entry(key) is not None or key in self._archive_index

    def list_archive(self, archive_name: str, pattern: str = "*") -> list[HDREntry]:
        """List entries in a specific archive."""
        self._ensure_archives_loaded()
        if archive_name in self._archives:
            return self._archives[archive_name].find(pattern)
        return []

    def list_all_textures(self) -> list[str]:
        """List all .dxt texture paths across loose files and archives."""
        textures = []
        # Loose files
        for dxt in self.game_dir.rglob("*.dxt"):
            textures.append(str(dxt.relative_to(self.game_dir)))
        for dxt in self.game_dir.rglob("*.DXT"):
            textures.append(str(dxt.relative_to(self.game_dir)))
        # Archive files
        self._ensure_archives_loaded()
        for key, (prefix, entry) in self._archive_index.items():
            if key.endswith(".dxt"):
                textures.append(entry.name)
        return sorted(set(textures))
