r"""bake_sounds.py -- extract the retail KO sound bank + the tables that decide when a sound plays.

Format notes, column meanings and how the Zones/Move_Sound mappings were derived: the format notes

Usage:
    python bake.py --only bake_sounds
    python bake.py --only bake_sounds --ko str(_ko()) --out <output>/sounds
    python bake.py --only bake_sounds --manifest-only
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from fxb_parse import _hdr_index                            # noqa: E402
from tbl_reader import read_tbl                             # noqa: E402
from _paths import ko as _ko, assets as _assets  # noqa: E402

DEFAULT_KO = _ko()
DEFAULT_OUT = _assets() / "sounds"

SND_2D, SND_3D, SND_STREAM = 0, 1, 2

LOOKS_SND = {
    "move": 22, "attack0": 23, "attack1": 24, "struck0": 25, "struck1": 26,
    "dead0": 27, "dead1": 28, "breathe0": 29, "breathe1": 30,
}
ITEM_SND0, ITEM_SND1 = 8, 9
ZONE_BGM = {"elBattle": 18, "elAmbient": 19, "kaBattle": 20, "kaAmbient": 21}
MOVE_SND_COLS = ["walk1", "walk2", "run1", "run2", "run3"]


def rel_name(entry: str) -> str:
    return entry.replace("\\", "/").lstrip("/").lower()


def _ogg_crc(page: bytes) -> int:
    crc = 0
    for b in page:
        crc ^= b << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if crc & 0x80000000 else (crc << 1) & 0xFFFFFFFF
    return crc


def fix_vorbis_comments(blob: bytes) -> bytes:
    """Retail's encoder wrote the comment 'Ogg Vorbis 1.0 Final' with no '=', which is illegal and
    makes Godot log a warning plus a full backtrace the first time each file is loaded. Turn the
    first space into '=' so the entry is well-formed, keeping the byte length so only the page CRC
    has to be recomputed."""
    d = bytearray(blob)
    off = 0
    while off + 27 <= len(d) and d[off:off + 4] == b"OggS":
        nseg = d[off + 26]
        body = off + 27 + nseg
        end = body + sum(d[off + 27:off + 27 + nseg])
        if end > len(d):
            break
        i = d.find(b"\x03vorbis", body, end)
        if i >= 0:
            p = i + 7
            vlen = struct.unpack_from("<I", d, p)[0]; p += 4 + vlen
            count = struct.unpack_from("<I", d, p)[0]; p += 4
            dirty = False
            for _ in range(count):
                if p + 4 > end:
                    break
                clen = struct.unpack_from("<I", d, p)[0]; p += 4
                text = d[p:p + clen]
                if b"=" not in text and b" " in text:
                    d[p + text.index(b" ")] = 0x3D
                    dirty = True
                p += clen
            if dirty:
                struct.pack_into("<I", d, off + 22, 0)
                struct.pack_into("<I", d, off + 22, _ogg_crc(bytes(d[off:end])))
        off = end
    return bytes(d)


def tbl_path(data_dir: Path, *names: str) -> Path:
    for n in names:
        p = data_dir / n
        if p.exists():
            return p
    raise SystemExit("missing table: tried %s in %s" % (", ".join(names), data_dir))


def extract(ko: Path, out: Path, dry: bool) -> dict[str, int]:
    hdr = ko / "Snd" / "snd.hdr"
    src = ko / "Snd" / "snd.src"
    if not hdr.exists() or not src.exists():
        raise SystemExit("no sound archive at %s" % (ko / "Snd"))

    index = _hdr_index(str(hdr))
    sizes: dict[str, int] = {}
    written = skipped = 0
    with open(src, "rb") as f:
        for entry, (off, size) in sorted(index.items()):
            name = rel_name(entry)
            if not name.endswith(".ogg"):
                skipped += 1
                continue
            f.seek(off)
            blob = f.read(size)
            if len(blob) != size or blob[:4] != b"OggS":
                print("  !! %s: short read or not Ogg -- skipped" % name)
                skipped += 1
                continue
            sizes[name] = size
            if dry:
                continue
            blob = fix_vorbis_comments(blob)
            dst = out / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists() or dst.read_bytes() != blob:
                dst.write_bytes(blob)
            written += 1
    print("  %d ogg extracted, %d skipped (%.1f MiB)"
          % (written if not dry else len(sizes), skipped, sum(sizes.values()) / 1048576))
    return sizes


def build_manifest(ko: Path, present: dict[str, int]) -> dict:
    data = ko / "Data"

    sounds: dict[str, dict] = {}
    missing = 0
    for row in read_tbl(str(tbl_path(data, "sound.tbl"))).rows:
        sid, path, itype, inst = row[0], row[1], row[2], row[3]
        name = rel_name(path)
        if name.startswith("snd/"):
            name = name[4:]
        if name not in present:
            missing += 1
            continue
        sounds[str(sid)] = {"file": name, "type": int(itype), "inst": int(inst)}
    print("  sound.tbl: %d ids resolve, %d reference files absent from the archive"
          % (len(sounds), missing))

    known = set(sounds)

    def keep(v) -> int:
        v = int(v or 0)
        return v if str(v) in known else 0

    looks: dict[str, dict] = {}
    for tbl in ("NPC_Looks.tbl", "UPC_DefaultLooks.tbl"):
        for row in read_tbl(str(tbl_path(data, tbl))).rows:
            ids = {k: keep(row[c]) for k, c in LOOKS_SND.items() if c < len(row)}
            ids = {k: v for k, v in ids.items() if v}
            if ids:
                looks[str(row[0])] = ids
    print("  looks: %d models/races with sounds" % len(looks))

    items: dict[str, list[int]] = {}
    for row in read_tbl(str(tbl_path(data, "Item_Org_us.tbl", "Item_Org.tbl"))).rows:
        a, b = keep(row[ITEM_SND0]), keep(row[ITEM_SND1])
        if a or b:
            items[str(row[0])] = [a, b]
    print("  items: %d with a swing/impact sound" % len(items))

    fx_by_id: dict[str, int] = {}
    fx_by_name: dict[str, int] = {}
    for row in read_tbl(str(tbl_path(data, "fx.tbl"))).rows:
        sid = keep(row[3])
        if not sid:
            continue
        fx_by_id[str(row[0])] = sid
        stem = Path(row[2].replace("\\", "/")).stem.lower()
        if stem:
            fx_by_name.setdefault(stem, sid)
    print("  fx: %d rows / %d distinct .fxb stems carry a sound" % (len(fx_by_id), len(fx_by_name)))

    zones: dict[str, dict] = {}
    for row in read_tbl(str(tbl_path(data, "Zones.tbl"))).rows:
        if not row[0]:
            continue
        bgm = {k: keep(row[c]) for k, c in ZONE_BGM.items() if c < len(row)}
        if any(bgm.values()):
            zones[str(row[0] // 10 if row[0] % 10 == 0 else row[0])] = bgm
    print("  zones: %d with BGM (keyed by SERVER zone id)" % len(zones))

    moves: dict[str, dict] = {}
    for row in read_tbl(str(tbl_path(data, "Move_Sound.tbl", "move_sound.tbl"))).rows:
        step = {k: keep(row[i + 1]) for i, k in enumerate(MOVE_SND_COLS) if i + 1 < len(row)}
        if any(step.values()):
            moves[str(row[0])] = step
    print("  footsteps: %d races" % len(moves))

    return {
        "_note": "baked by tools/bake_sounds.py from retail KO -- see the format notes",
        "_type": {"0": "2d", "1": "3d", "2": "stream"},
        "sounds": sounds,
        "looks": looks,
        "items": items,
        "fx": fx_by_id,
        "fxByName": fx_by_name,
        "zones": zones,
        "footsteps": moves,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ko", type=Path, default=DEFAULT_KO, help="retail KO install root")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output asset dir")
    ap.add_argument("--manifest-only", action="store_true", help="rebuild sounds.json, keep the .ogg")
    args = ap.parse_args()

    if not args.ko.exists():
        raise SystemExit("retail KO not found at %s (pass --ko)" % args.ko)

    print("[sounds] archive -> %s" % args.out)
    present = extract(args.ko, args.out, dry=args.manifest_only)

    print("[sounds] manifest")
    manifest = build_manifest(args.ko, present)
    args.out.mkdir(parents=True, exist_ok=True)
    dst = args.out / "sounds.json"
    dst.write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
    print("[sounds] wrote %s (%.0f KiB)" % (dst, dst.stat().st_size / 1024))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
