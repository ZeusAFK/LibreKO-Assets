r"""bake_items.py -- bake item metadata + inventory icons for the Godot client (the format notes).

Two outputs, both one-time offline (client reads only the baked PNG/JSON, never the .tbl/.hdr):

  Item_Org_us.tbl  -> <output>/items/items.json   (keyed by item id)
       per item: base stats, requirements, durability, icon resource, localized tooltip text labels,
                 and a nested Item_Ext map with normalized enchant/bonus fields.
  UI\ui.hdr+.src   -> <output>/items/icons/<iconResrc>.png   (one icon per referenced resource)
       icon archive key = "itemicon_%d_%04d_%02d_%d.dxt" % (r/1e7, r/1e3%1e4, r/10%100, r%10), r=col7.
       decoded via kotools read_dxt_from_bytes (handles the v7 decrypt), saved as PNG.

Usage:
    python bake.py --only bake_items            # full bake (items.json + every referenced icon)
    python bake.py --only bake_items --json-only # stats/text/ext bake only, no icon dependencies
    python bake.py --only bake_items --probe N  # decode N sample icons only (validate the pipeline)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "docs"))      # fxb_parse.py

import hashlib                                           # noqa: E402
from tbl_reader import read_tbl                          # noqa: E402
from _paths import ko as _ko, assets as _assets  # noqa: E402

np = None
Image = None
_hdr_index = None
NTFHeader = None
BinaryReader = None
decode_to_rgba = None
RC4 = None


def _ensure_icon_deps():
    """Load heavy icon-only dependencies lazily so --json-only works on lean dev environments."""
    global np, Image, _hdr_index, NTFHeader, BinaryReader, decode_to_rgba, RC4
    if np is not None:
        return
    import numpy as _np
    from PIL import Image as _Image
    from fxb_parse import _hdr_index as _hdr_index_func
    from kotools.ntf.header import NTFHeader as _NTFHeader
    from kotools.io.binary_reader import BinaryReader as _BinaryReader
    from kotools.ntf.decoder import decode_to_rgba as _decode_to_rgba
    from kotools.crypto.wincrypt import RC4 as _RC4

    np = _np
    Image = _Image
    _hdr_index = _hdr_index_func
    NTFHeader = _NTFHeader
    BinaryReader = _BinaryReader
    decode_to_rgba = _decode_to_rgba
    RC4 = _RC4

# KO NTF v7 decryption — CRACKED for UI/v7 textures (see the format notes / bake notes):
#   the key comes from CryptDeriveKey(CALG_RC4, SHA1(cipher), flags) => RC4 key = SHA1(cipher)[:16],
#   and the texture loader decrypts ROW BY ROW with CryptDecrypt(..., Final=TRUE, ...). For the MS RC4
#   provider, Final=TRUE RESETS the keystream each call, so EVERY row decrypts from keystream pos 0
#   (chunk = bytesPerPixel * width). kotools' read_dxt_from_bytes assumes ONE continuous stream, which
#   is why every v7 texture (incl. all item icons) came out as rainbow noise. Per-row reset fixes it.
_RC4_KEY = hashlib.sha1(b"owsd9012%$1as!wpow1033b%!@%12").digest()[:16]   # 28-byte cipher, no null (sizeof-1)
# D3DFORMAT -> bytes/pixel for the uncompressed formats KO ships (DXT handled separately).
_BPP = {20: 3, 21: 4, 22: 4, 23: 2, 24: 2, 25: 2, 26: 2}
_DXT_FOURCC = {0x31545844: "DXT1", 0x33545844: "DXT3", 0x35545844: "DXT5"}

def _first_existing(candidates: list[Path], fallback: Path) -> Path:
    for p in candidates:
        if p.exists():
            return p
    return fallback


KO_ROOT = _ko()
KO_DATA = KO_ROOT / "Data"
ITEM_ORG = KO_DATA / "Item_Org_us.tbl"
ITEM_SELL = KO_DATA / "itemsell_table.tbl"
TEXTS = KO_DATA / "Texts_us.tbl"
PIECE_EXCHANGE = KO_DATA / "piece_Exchange_us.tbl"
ATTENDANCE = KO_DATA / "Attendance.tbl"
UI_HELP = KO_DATA / "UI_Help_us.tbl"
UI_HDR = KO_ROOT / "UI" / "ui.hdr"
UI_SRC = KO_ROOT / "UI" / "ui.src"
OUT_DIR = _assets() / "items"
ICON_DIR = OUT_DIR / "icons"

# Retail draws an item icon with UV (0,0)-(45/64, 45/64) = 0.703125. So the art is a
# 45x45 cell in the TOP-LEFT of a 64x64 texture and the rest is padding whose content is
# undefined -- black on the 196 icons stored in a no-alpha format, leftover garbage on others.
# Keeping it made GetUsedRect() see the whole texture, so the client could not crop to the art
# and those icons drew ~70% size inside their slot.
ICON_TEX = 64
ICON_CELL = 45

# Item_Org column map (verified against retail Item_Org_us.tbl dumps and the server's seeded rows).
# col6 is the MODEL resource, col7 the ICON resource. They match for 4771/5269 rows, which hid the
# difference: the 498 that disagree were drawing another item's icon (Ring of the Felankor 330620000
# has col6=70000800, a "Redistribution Item" icon, vs col7=33062000, its own). col7 also resolves in
# the archive more often (5122 vs 4781 rows).
C_ID, C_CAT, C_NAME, C_DESC, C_MODEL, C_RESRC = 0, 1, 2, 3, 6, 7
C_KIND, C_RACE, C_SLOT, C_CLASS = 10, 13, 12, 14
C_DAMAGE, C_DELAY, C_RANGE, C_WEIGHT, C_DURATION = 15, 16, 17, 18, 19
C_BUY_PRICE, C_SALE_TYPE, C_AC, C_COUNTABLE, C_EFFECT1, C_EFFECT2 = 20, 21, 22, 23, 24, 25
C_REQ_LEVEL, C_REQ_LEVEL_MAX, C_REQ_RANK, C_REQ_TITLE = 26, 27, 28, 29
C_REQ_STR, C_REQ_STA, C_REQ_DEX, C_REQ_INT, C_REQ_CHA = 30, 31, 32, 33, 34
C_SELLING_GROUP, C_ITEM_TYPE, C_GRADE, C_BOUND, C_NOTICE = 35, 36, 38, 39, 40

# C_SALE_TYPE is an e_ItemSaleType enum, NOT money, and every retail price is BuyPrice times the
# Item_Ext column below. Reading either as a plain price mispriced the whole shop.
C_EXT_PRICE_MULTIPLY = 13


class Archive:
    """KO .hdr/.src archive (same format as fx.hdr) — case-insensitive key lookup."""
    def __init__(self, hdr: Path, src: Path):
        _ensure_icon_deps()
        self.idx = _hdr_index(str(hdr))
        self.low = {k.lower(): k for k in self.idx}
        self.src = src

    def read(self, key: str) -> bytes | None:
        real = self.low.get(key.lower())
        if real is None:
            return None
        fo, fs = self.idx[real]
        with open(self.src, "rb") as f:
            f.seek(fo)
            return f.read(fs)


def _decode16(u, fmt):
    if fmt == 26:                                          # A4R4G4B4
        a, r, g, b = ((u >> 12) & 0xF) * 17, ((u >> 8) & 0xF) * 17, ((u >> 4) & 0xF) * 17, (u & 0xF) * 17
    elif fmt == 25:                                        # A1R5G5B5
        a = ((u >> 15) & 1) * 255
        r, g, b = ((u >> 10) & 0x1F) * 255 // 31, ((u >> 5) & 0x1F) * 255 // 31, (u & 0x1F) * 255 // 31
    elif fmt == 24:                                        # X1R5G5B5
        a = np.full_like(u, 255)
        r, g, b = ((u >> 10) & 0x1F) * 255 // 31, ((u >> 5) & 0x1F) * 255 // 31, (u & 0x1F) * 255 // 31
    else:                                                  # 23 = R5G6B5
        a = np.full_like(u, 255)
        r, g, b = ((u >> 11) & 0x1F) * 255 // 31, ((u >> 5) & 0x3F) * 255 // 63, (u & 0x1F) * 255 // 31
    return np.stack([r, g, b, a], -1).astype(np.uint8)


def decode_icon(blob: bytes):
    """KO NTF .dxt -> RGBA PIL image, with the v7 per-row-reset RC4 decryption. Returns None for
    unsupported (DXT-compressed / unknown) formats so the caller can skip them."""
    _ensure_icon_deps()
    h = NTFHeader.from_reader(BinaryReader(blob))
    fmt = h.format.value if hasattr(h.format, "value") else int(h.format)
    px = blob[h.header_size:]
    bpp = _BPP.get(fmt)
    if bpp is None:
        # DXT-compressed icons (e.g. the "Chitin Shell" set is DXT3). The client reads the whole mip
        # in ONE crypt.ReadFile, so encryption (if any) is a single RC4 stream — NOT the per-row reset
        # the uncompressed path needs. Most DXT icons are v3 (unencrypted). Decode level 0 via kotools.
        if fmt not in _DXT_FOURCC:
            return None
        if h.is_encrypted:
            px = bytes(RC4(_RC4_KEY).process(px))
        rgba = decode_to_rgba(px, h.width, h.height, h.format)
        return Image.frombytes("RGBA", (h.width, h.height), bytes(rgba[: h.width * h.height * 4]))
    w, hgt, rb = h.width, h.height, bpp * h.width
    if h.is_encrypted:
        dec = bytearray()
        for y in range(hgt):                               # fresh RC4 per row (CryptDecrypt Final=TRUE resets)
            dec += RC4(_RC4_KEY).process(px[y * rb:(y + 1) * rb])
        px = bytes(dec)
    px = px[: rb * hgt]
    if bpp == 2:
        img = _decode16(np.frombuffer(px, np.uint16).reshape(hgt, w), fmt)
    elif bpp == 4:                                         # A8R8G8B8 / X8R8G8B8 (stored BGRA)
        a = np.frombuffer(px, np.uint8).reshape(hgt, w, 4)
        img = a[..., [2, 1, 0, 3]].copy()
        if fmt == 22:
            img[..., 3] = 255
    else:                                                  # bpp 3: R8G8B8 (stored BGR)
        a = np.frombuffer(px, np.uint8).reshape(hgt, w, 3)
        img = np.dstack([a[..., 2], a[..., 1], a[..., 0], np.full((hgt, w), 255, np.uint8)])
    return Image.fromarray(img, "RGBA")


def icon_keys(resrc: int):
    """Candidate archive keys for an item resource id (graded cc, then cc=00 fallback)."""
    a, b, c, d = resrc // 10000000, (resrc // 1000) % 10000, (resrc // 10) % 100, resrc % 10
    keys = [f"itemicon_{a}_{b:04d}_{c:02d}_{d}.dxt"]
    if c != 0:
        keys.append(f"itemicon_{a}_{b:04d}_00_{d}.dxt")
    return keys


def _i(row, idx):
    return int(row[idx]) if idx < len(row) and isinstance(row[idx], int) else 0


def _s(row, idx):
    return row[idx] if idx < len(row) and isinstance(row[idx], str) else ""


def _is_weapon(kind: int, slot: int) -> bool:
    return slot in (0, 1, 3, 4) and kind != 60


def _clean_text(value: str) -> str:
    return value.replace("\r", "").replace("\x00", "").strip()


def _ext_tbl_path(cat: int) -> Path | None:
    for name in (f"Item_Ext_{cat}_us.tbl", f"item_ext_{cat}_us.tbl"):
        p = KO_DATA / name
        if p.exists():
            return p
    return None


def _cs(row, idx: int) -> int:
    return int(row[idx]) if idx < len(row) and isinstance(row[idx], int) else 0


def _raw_special_amount(row) -> int:
    """The retail builders interpret several extension columns per category. Until every builder is
    fully ported, expose the obvious single special amount for elemental/drain-like names."""
    vals = [_cs(row, i) for i in (23, 24, 25, 26, 27, 28, 29)]
    vals = [v for v in vals if v != 0]
    return max(vals, key=abs) if vals else 0


def _ext_bonuses(row) -> dict[str, int]:
    # One uniform Item_Ext column layout, validated against the server's expanded item table across all
    # 44 extension categories (1231/1232 stat fields agree, Duration/Damage 88/88, ReqStr 87/88):
    # col8 damage, col12 durability, col14 AC, col15-19 + col31-35 the five stats, col36/37 HP/MP,
    # col38-43 the six resists, col22-25 elemental damage, col44 enchant level, col49 required strength.
    # The per-category branches this replaced read only a handful of these, so most ext bonuses were
    # silently baked as zero (a ring showing INT only, no AC/HP/MP/resists).
    return {
        "magicOrRare": _cs(row, 7),
        "bonusDamage": _cs(row, 8),
        "bonusAc": _cs(row, 14),
        "bonusStr": _cs(row, 15) + _cs(row, 31),
        "bonusSta": _cs(row, 16) + _cs(row, 32),
        "bonusDex": _cs(row, 17) + _cs(row, 33),
        "bonusInt": _cs(row, 18) + _cs(row, 34),
        "bonusCha": _cs(row, 19) + _cs(row, 35),
        "bonusMaxHp": _cs(row, 36),
        "bonusMaxMp": _cs(row, 37),
        "bonusFireR": _cs(row, 38),
        "bonusColdR": _cs(row, 39),
        "bonusLightningR": _cs(row, 40),
        "bonusMagicR": _cs(row, 41),
        "bonusPoisonR": _cs(row, 42),
        "bonusCurseR": _cs(row, 43),
        "fireDamage": _cs(row, 22),
        "iceDamage": _cs(row, 23),
        "lightningDamage": _cs(row, 24),
        "poisonDamage": _cs(row, 25),
        "durationBonus": _cs(row, 12),
        "reqStrBonus": _cs(row, 49),
        "plus": _cs(row, 44),
    }


def bake_extensions() -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for cat in range(45):
        path = _ext_tbl_path(cat)
        if path is None:
            continue
        try:
            rows = read_tbl(str(path)).rows
        except Exception as e:
            print(f"[items] {path.name}: failed to read ({type(e).__name__}: {e})")
            continue

        cat_map: dict[str, dict] = {}
        for r in rows:
            if not r or not isinstance(r[0], int):
                continue
            ext_id = _i(r, 0)
            bonuses = _ext_bonuses(r)
            name = _clean_text(_s(r, 1))
            desc = _clean_text(_s(r, 3))
            special = _raw_special_amount(r)
            raw = {str(i): _i(r, i) for i in range(len(r)) if isinstance(r[i], int) and _i(r, i) != 0}
            if ext_id == 0 and not name and special == 0 and not any(bonuses.values()):
                continue
            rec = {
                "name": name,
                "linked": _i(r, 2),
                "desc": desc,
                "glowFx": _i(r, 4),
                # Retail uses non-zero Item_Ext visual IDs before Item_Org's IDs.
                "resrc": _i(r, 5),
                "icon": _i(r, 6),
                "special": special,
                "priceMultiply": _i(r, C_EXT_PRICE_MULTIPLY),
                "raw": raw,
            }
            rec.update(bonuses)
            cat_map[str(ext_id)] = rec
        if cat_map:
            out[str(cat)] = cat_map
    return out


def bake_sell_groups() -> dict[str, list]:
    """Merchant buy lists for the vendor panel (World.Vendor.cs).

    A trade NPC (NpcType 21) carries a `SellingGroup` id; the server's GS_TRADE_NPC sends only that
    id and trusts the client to present the matching item list. `itemsell_table.tbl` IS that list:
    col1 = the selling-group id (e.g. 101001), and a group spans 12 consecutive rows ("lines"/tabs),
    each row holding up to 24 item-id columns (0 = empty). We flatten to [itemId, line, listIndex];
    line/listIndex are echoed back in GS_ITEM_TRADE (the server reads but doesn't validate them).
    Item ids carry an extension suffix (…001/…002); the client resolves name/icon/price through
    ItemData's base-id fallback, and the server resolves the exact id when charging BuyPrice."""
    if not ITEM_SELL.exists():
        print(f"[items] {ITEM_SELL.name}: not found, skipping sell groups")
        return {}
    try:
        rows = read_tbl(str(ITEM_SELL)).rows
    except Exception as e:
        print(f"[items] {ITEM_SELL.name}: failed to read ({type(e).__name__}: {e})")
        return {}
    groups: dict[str, list] = {}
    line_of: dict[int, int] = {}
    for r in rows:
        if len(r) < 3 or not isinstance(r[1], int):
            continue
        group = _i(r, 1)
        if group <= 0:
            continue
        line = line_of.get(group, 0)
        line_of[group] = line + 1
        entries = groups.setdefault(str(group), [])
        for col in range(2, len(r)):
            iid = _i(r, col)
            if iid > 0:
                entries.append([iid, line, col - 2])
    print(f"[items] sell groups: {len(groups)} groups, "
          f"{sum(len(v) for v in groups.values())} listed items")
    return groups


PIECE_COUNT_BASE = 100
PIECE_REWARD_STRIDE = 10000


def bake_piece_exchange(items: dict[str, dict]) -> dict[str, list]:
    """Chaotic Generator reward lists for the piece-change panel (World.PieceChange.cs).

    `piece_Exchange_us.tbl` is a flat (index, value) uint pair table the retail window reads three
    ways: index 0 = how many piece types exist, index N = piece N's input item id, index 100+N =
    how many rewards piece N can roll, and index N*10000+K = reward K. The window spins its three
    display slots off `rand() % count + N*10000 + 1`, so the declared count -- not the number of
    rows present -- bounds what retail can ever show; seven of the dark fragments carry eight extra
    rows past their count that retail never reaches, and they are dropped here for the same reason.
    Pieces whose input item has no Item_Org row are dropped too: retail cannot render them either.
    Reward values are `baseId + extension`, matching the id the server grants."""
    if not PIECE_EXCHANGE.exists():
        print(f"[items] {PIECE_EXCHANGE.name}: not found, skipping piece exchange")
        return {}
    try:
        rows = read_tbl(str(PIECE_EXCHANGE)).rows
    except Exception as e:
        print(f"[items] {PIECE_EXCHANGE.name}: failed to read ({type(e).__name__}: {e})")
        return {}

    table: dict[int, int] = {}
    for r in rows:
        if len(r) >= 2 and isinstance(r[0], int) and isinstance(r[1], int):
            table[int(r[0])] = int(r[1])

    total = table.get(0, 0)
    out: dict[str, list] = {}
    dropped: list[int] = []
    for n in range(1, total + 1):
        piece_id = table.get(n, 0)
        if piece_id <= 0:
            continue
        if str(piece_id) not in items:
            dropped.append(piece_id)
            continue
        count = table.get(PIECE_COUNT_BASE + n, 0)
        rewards = []
        for k in range(1, count + 1):
            reward = table.get(n * PIECE_REWARD_STRIDE + k, 0)
            if reward > 0:
                rewards.append(reward)
        if rewards:
            out[str(piece_id)] = rewards

    print(f"[items] piece exchange: {len(out)}/{total} pieces, "
          f"{sum(len(v) for v in out.values())} rewards"
          + (f", dropped {len(dropped)} with no Item_Org row" if dropped else ""))
    return out


def bake_attendance() -> dict[str, list]:
    """Reward per attendance slot for the daily attendance board (World.Attendance.cs).

    `Attendance.tbl` is (slot, korean name, item id, count). Slots 1-25 are the daily rewards;
    101-103 are the cumulative milestones the retail panel labels at 15 / 20 / 25 attended days.
    The name column is never shown -- retail builds each slot label from Item_Org -- so it is
    dropped. The server owns the per-slot state; only the reward preview lives here."""
    if not ATTENDANCE.exists():
        print(f"[items] {ATTENDANCE.name}: not found, skipping attendance")
        return {}
    try:
        rows = read_tbl(str(ATTENDANCE)).rows
    except Exception as e:
        print(f"[items] {ATTENDANCE.name}: failed to read ({type(e).__name__}: {e})")
        return {}
    out: dict[str, list] = {}
    for r in rows:
        if len(r) < 4 or not isinstance(r[0], int):
            continue
        slot, item_id, count = _i(r, 0), _i(r, 2), _i(r, 3)
        if slot <= 0 or item_id <= 0:
            continue
        out[str(slot)] = [item_id, max(1, count)]
    print(f"[items] attendance: {len(out)} slots")
    return out


def bake_ui_help() -> dict[str, list]:
    """Title + body for the help window the anvil and Chaotic Generator open from their Talk button.

    `UI_Help_us.tbl` holds three rows: 1 and 2 are the anvil's upgrade and accessory-mix spiels,
    3 is the generator's. Retail's button passes the row id straight to the help window, so the id
    is the contract -- keep the keys as they come. `|` is KO's line break."""
    if not UI_HELP.exists():
        print(f"[items] {UI_HELP.name}: not found, skipping ui help")
        return {}
    try:
        rows = read_tbl(str(UI_HELP)).rows
    except Exception as e:
        print(f"[items] {UI_HELP.name}: failed to read ({type(e).__name__}: {e})")
        return {}
    out: dict[str, list] = {}
    for r in rows:
        if len(r) < 3 or not isinstance(r[0], int):
            continue
        out[str(_i(r, 0))] = [_clean_text(_s(r, 1)), _clean_text(_s(r, 2))]
    print(f"[items] ui help: {len(out)} entries")
    return out


def bake_texts() -> dict[str, str]:
    if not TEXTS.exists():
        return {}
    rows = read_tbl(str(TEXTS)).rows
    return {
        str(_i(r, 0)): _clean_text(_s(r, 1))
        for r in rows
        if r and isinstance(r[0], int) and isinstance(r[1], str)
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", type=int, default=0, help="decode only N sample icons, no JSON")
    ap.add_argument("--json-only", action="store_true", help="write items.json only; skip icon archive/decode")
    args = ap.parse_args()

    rows = [r for r in read_tbl(str(ITEM_ORG)).rows if r and isinstance(r[C_ID], int)]
    print(f"[items] {len(rows)} Item_Org rows")

    # resource id -> first item that uses it (for probe logging); dedupe icons by resrc.
    resrcs: dict[int, int] = {}
    items: dict[str, dict] = {}
    for r in rows:
        iid = _i(r, C_ID)
        resrc = _i(r, C_RESRC)
        kind, slot = _i(r, C_KIND), _i(r, C_SLOT)
        damage, ac = _i(r, C_DAMAGE), _i(r, C_AC)
        value = damage if _is_weapon(kind, slot) else (ac if ac > 0 else damage)
        items[str(iid)] = {
            "name": _clean_text(_s(r, C_NAME)),
            "desc": _clean_text(_s(r, C_DESC)),
            "cat": _i(r, C_CAT),
            "kind": kind,
            "race": _i(r, C_RACE),
            "slot": slot,
            "class": _i(r, C_CLASS),
            "value": value,
            "minDamage": 0,
            "damage": damage,
            "delay": _i(r, C_DELAY),
            "range": _i(r, C_RANGE),
            "weight": _i(r, C_WEIGHT),
            "duration": _i(r, C_DURATION),
            "buyPrice": _i(r, C_BUY_PRICE),
            "saleType": _i(r, C_SALE_TYPE),
            "ac": ac,
            "countable": _i(r, C_COUNTABLE),
            "effect1": _i(r, C_EFFECT1),
            "effect2": _i(r, C_EFFECT2),
            "reqLvl": _i(r, C_REQ_LEVEL),
            "reqLevel": _i(r, C_REQ_LEVEL),
            "reqLevelMax": _i(r, C_REQ_LEVEL_MAX),
            "reqRank": _i(r, C_REQ_RANK),
            "reqTitle": _i(r, C_REQ_TITLE),
            "reqStr": _i(r, C_REQ_STR),
            "reqSta": _i(r, C_REQ_STA),
            "reqDex": _i(r, C_REQ_DEX),
            "reqInt": _i(r, C_REQ_INT),
            "reqCha": _i(r, C_REQ_CHA),
            "sellingGroup": _i(r, C_SELLING_GROUP),
            "reqCls": _i(r, C_CLASS),
            "itemType": _i(r, C_ITEM_TYPE),
            "grade": _i(r, C_GRADE),
            "bound": _i(r, C_BOUND),
            "notice": _i(r, C_NOTICE),
            "icon": resrc,
        }
        if resrc > 0:
            resrcs.setdefault(resrc, iid)

    if not args.probe:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        baked: dict[str, object] = {
            "_texts": bake_texts(),
            "_ext": bake_extensions(),
            "_sell": bake_sell_groups(),
            "_pieces": bake_piece_exchange(items),
            "_help": bake_ui_help(),
            "_attendance": bake_attendance(),
        }
        baked.update(items)
        (OUT_DIR / "items.json").write_text(json.dumps(baked, separators=(",", ":")))
        print(f"[items] wrote items.json ({len(items)} items, {len(baked['_ext'])} ext categories)")
        if args.json_only:
            return 0

    arc = Archive(UI_HDR, UI_SRC)
    ICON_DIR.mkdir(parents=True, exist_ok=True)

    todo = list(resrcs)
    if args.probe:
        todo = todo[: args.probe]

    decoded = missing = failed = 0
    for resrc in todo:
        out = ICON_DIR / f"{resrc}.png"
        if out.exists() and not args.probe:
            decoded += 1
            continue
        blob = None
        for k in icon_keys(resrc):
            blob = arc.read(k)
            if blob is not None:
                break
        if blob is None:
            missing += 1
            continue
        try:
            img = decode_icon(blob)
            if img is not None and img.size == (ICON_TEX, ICON_TEX):
                img = img.crop((0, 0, ICON_CELL, ICON_CELL))
            if img is None:
                failed += 1
                if args.probe:
                    print(f"  unsupported-format resrc={resrc} item={resrcs[resrc]} (skipped)")
                continue
            img.save(out)
            decoded += 1
            if args.probe:
                print(f"  ok  resrc={resrc} item={resrcs[resrc]} -> {out.name} {img.size}")
        except Exception as e:
            failed += 1
            if args.probe:
                print(f"  FAIL resrc={resrc}: {type(e).__name__}: {e}")

    print(f"[items] icons: {decoded} decoded, {missing} no-archive-entry, {failed} decode-failed "
          f"(of {len(resrcs)} unique resources)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
