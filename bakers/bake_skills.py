"""bake_skills.py -- skills for the client: presentation from the retail tables, gameplay from the server seed.

    Skill_Magic_Main_us.tbl -> name, description, icon, animations, effects, attachments
    Skill_Magic_1.tbl       -> the per-WEAPON animation of melee (Type-1) skills
    fx.tbl                  -> effect id -> bundle stem, kept only when that fx is baked
    <server>/Magic.json and MagicType1..9.json -> type, moral, costs, cast times, range, and the
                               effect row (duration, buff type, speed and stat changes) the client
                               reads for the buff HUD and movement

The server folder is the LibreKO server's Seed/Data (bake.py finds it in a LibreKO checkout, or
pass --server-data). Without it the file carries presentation only and the client cannot show
buffs or apply speed changes.

Output <output>/skills/skills.json keyed by skill id, plus <output>/skills/icons/<skillid>.png.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from tbl_reader import read_tbl  # noqa: E402
from _paths import ko as _ko, assets as _assets, server_data as _server_data  # noqa: E402

_FX_ID, _FX_PATH = 0, 2

_SK_ID, _SK_NAME, _SK_DESC, _SK_ICON = 0, 2, 3, 34
_SK_COOLDOWN_GROUP = 35
_SK_NEED_WEAPON, _SK_NEED_ITEM = 20, 21
(_SK_SELF_ANIM1, _SK_SELF_ANIM2, _SK_TARGET_ANIM,
 _SK_SELF_FX1, _SK_SELF_PART1, _SK_SELF_FX2, _SK_SELF_PART2,
 _SK_FLYING_FX, _SK_TARGET_FX, _SK_TARGET_PART, _SK_MORAL) = range(4, 15)

# Skill_Magic_1.tbl: the last three columns are the melee skill animation picked by the caster's
# equipped weapon (one-handed, two-handed, jamadar). The main table's SelfAnim1 only classifies a
# Type-1 skill; it is never its animation.
_T1_ID, _T1_ANIM_1H, _T1_ANIM_2H, _T1_ANIM_JAMADAR = 0, 9, 10, 11


def _fx_stem(path: str, fx_dir: Path) -> str | None:
    stem = path.replace("/", "\\").split("\\")[-1]
    if stem.lower().endswith(".fxb"):
        stem = stem[:-4]
    if (fx_dir / f"{stem}.json").exists():
        return stem
    for candidate in fx_dir.glob("*.json"):
        if candidate.stem.lower() == stem.lower():
            return candidate.stem
    return None


def _int(row, index, default=0):
    if len(row) <= index:
        return default
    try:
        return int(row[index] or 0)
    except (TypeError, ValueError):
        return default


def _load_json_list(path: Path, key="Id"):
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    rows = data if isinstance(data, list) else list(data.values())
    return {r[key]: r for r in rows}


def _items(assets: Path) -> dict[int, dict]:
    path = assets / "items" / "items.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {int(k): v for k, v in data.items() if k.isdigit()}


def main() -> int:
    ko_data = _ko() / "Data"
    assets = _assets()
    out_dir = assets / "skills"
    fx_dir = assets / "fx"

    fx_stem: dict[int, str] = {}
    for r in read_tbl(str(ko_data / "fx.tbl")).rows:
        if r and isinstance(r[_FX_ID], int) and len(r) > _FX_PATH and isinstance(r[_FX_PATH], str):
            stem = _fx_stem(r[_FX_PATH], fx_dir)
            if stem:
                fx_stem[r[_FX_ID]] = stem

    def resolve(fx_id):
        return fx_stem.get(fx_id) if isinstance(fx_id, int) and fx_id not in (0, 255) else None

    presentation: dict[int, dict] = {}
    for row in read_tbl(str(ko_data / "Skill_Magic_Main_us.tbl")).rows:
        if not row or not isinstance(row[_SK_ID], int) or len(row) < 15:
            continue
        presentation[row[_SK_ID]] = {
            "name": row[_SK_NAME] if isinstance(row[_SK_NAME], str) else str(row[_SK_ID]),
            "desc": row[_SK_DESC] if isinstance(row[_SK_DESC], str) else "",
            "iconId": _int(row, _SK_ICON),
            "selfAnim1": _int(row, _SK_SELF_ANIM1),
            "selfAnim2": _int(row, _SK_SELF_ANIM2),
            "targetAnim": _int(row, _SK_TARGET_ANIM),
            "selfFx1Id": _int(row, _SK_SELF_FX1),
            "selfPart1": _int(row, _SK_SELF_PART1),
            "selfFx2Id": _int(row, _SK_SELF_FX2),
            "selfPart2": _int(row, _SK_SELF_PART2),
            "flyingFxId": _int(row, _SK_FLYING_FX),
            "targetFxId": _int(row, _SK_TARGET_FX),
            "targetPart": _int(row, _SK_TARGET_PART),
            "moral": _int(row, _SK_MORAL),
            "needWeapon": _int(row, _SK_NEED_WEAPON),
            "needItem": _int(row, _SK_NEED_ITEM),
            "cooldownGroup": _int(row, _SK_COOLDOWN_GROUP),
        }

    melee: dict[int, tuple[int, int, int]] = {}
    type1_tbl = ko_data / "Skill_Magic_1.tbl"
    if type1_tbl.exists():
        for row in read_tbl(str(type1_tbl)).rows:
            if row and isinstance(row[_T1_ID], int) and len(row) > _T1_ANIM_JAMADAR:
                melee[row[_T1_ID]] = (_int(row, _T1_ANIM_1H), _int(row, _T1_ANIM_2H), _int(row, _T1_ANIM_JAMADAR))
    else:
        print(f"[skills] WARNING: {type1_tbl} missing; melee skills get no animation", file=sys.stderr)

    def presentation_fields(sid: int, pr: dict | None, fallback_name: str, fx_ids) -> dict:
        anim_1h, anim_2h, anim_jamadar = melee.get(sid, (-1, -1, -1))
        return {
            "name": pr["name"] if pr else fallback_name,
            "desc": pr["desc"] if pr else "",
            "iconId": pr["iconId"] if pr else 0,
            "selfAnim1": pr["selfAnim1"] if pr else 0,
            "selfAnim2": pr["selfAnim2"] if pr else 0,
            "targetAnim": pr["targetAnim"] if pr else 0,
            "selfFx": resolve(fx_ids[0]),
            "selfPart1": pr["selfPart1"] if pr else 0,
            "selfFx2": resolve(fx_ids[1]),
            "selfPart2": pr["selfPart2"] if pr else 0,
            "flyingFx": resolve(fx_ids[2]),
            "targetFx": resolve(fx_ids[3]),
            "targetPart": pr["targetPart"] if pr else 0,
            "moral": pr["moral"] if pr else 0,
            "needWeapon": pr["needWeapon"] if pr else 0,
            "needItem": pr["needItem"] if pr else 0,
            "cooldownGroup": pr["cooldownGroup"] if pr else 0,
            "anim1H": anim_1h,
            "anim2H": anim_2h,
            "animJamadar": anim_jamadar,
        }

    server = _server_data()
    skills: dict[str, dict] = {}
    if server and (server / "Magic.json").is_file():
        magic = _load_json_list(server / "Magic.json")
        type_tables = {n: _load_json_list(server / f"MagicType{n}.json")
                       for n in range(1, 10) if (server / f"MagicType{n}.json").exists()}
        items = _items(assets)
        for sid, m in magic.items():
            pr = presentation.get(sid)
            granted = items.get(int(m.get("UseItem", 0) or 0)) if pr is None else None
            fx_ids = ((pr["selfFx1Id"], pr["selfFx2Id"], pr["flyingFxId"], pr["targetFxId"]) if pr
                      else (m.get("SelfEffect"), 0, m.get("FlyingEffect"), m.get("TargetEffect")))
            type1 = int(m.get("Type1", 0) or 0)
            table = type_tables.get(type1, {})
            effect = table.get(sid)
            if effect is None and int(m.get("Etc", 0) or 0) > 0:
                effect = table.get(int(m.get("Etc", 0) or 0))
            effect = dict(effect or {})
            record = presentation_fields(sid, pr, granted["name"] if granted else str(sid), fx_ids)
            if pr is None and granted:
                record["desc"] = granted.get("desc", "")
            if pr is None:
                record["selfAnim1"] = m.get("BeforeAction", 0)
                record["targetAnim"] = m.get("TargetAction", 0)
            record.update({
                "type1": type1,
                "type2": m.get("Type2", 0),
                "moral": m.get("Moral", 0),
                "level": m.get("SkillLevel", 0),
                "tree": m.get("Skill", 0),
                "msp": m.get("Msp", 0),
                "hp": m.get("Hp", 0),
                "sp": m.get("Sp", 0),
                "itemGroup": m.get("ItemGroup", 0),
                "useItem": m.get("UseItem", 0),
                "cast": m.get("CastTime", 0),
                "recast": m.get("ReCastTime", 0),
                "success": m.get("SuccessRate", 0),
                "range": m.get("Range", 0),
                "etc": m.get("Etc", 0),
                "before": record["selfAnim1"],
                "target": record["targetAnim"],
                "hit": effect.get("Hit"),
                "hitType": effect.get("HitType"),
                "hitRate": effect.get("HitRate"),
                "addDamage": effect.get("AddDamage"),
                "addRange": effect.get("AddRange"),
                "needArrow": effect.get("NeedArrow", 0),
                "directType": effect.get("DirectType", 0),
                "firstDamage": effect.get("FirstDamage", 0),
                "endDamage": effect.get("EndDamage", 0),
                "timeDamage": effect.get("TimeDamage", 0),
                "duration": effect.get("Duration", 0),
                "attribute": effect.get("Attribute", 0),
                "radius": effect.get("Radius", 0),
                "angle": effect.get("Angle", 0),
                "buffType": effect.get("BuffType", effect.get("Type", 0)),
                "effect": effect,
            })
            skills[str(sid)] = record
        source = f"{len(magic)} server rows joined with {len(presentation)} client rows"
    else:
        print("[skills] WARNING: no server Seed/Data (pass --server-data); skills carry presentation only, "
              "so the client cannot show buffs or apply speed changes", file=sys.stderr)
        for sid, pr in presentation.items():
            fx_ids = (pr["selfFx1Id"], pr["selfFx2Id"], pr["flyingFxId"], pr["targetFxId"])
            skills[str(sid)] = presentation_fields(sid, pr, str(sid), fx_ids)
        source = f"{len(presentation)} client rows, presentation only"

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "skills.json").write_text(
        json.dumps(skills, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"[skills] {len(skills)} skills ({source}), "
          f"{sum(1 for s in skills.values() if s['anim1H'] >= 0)} melee -> {out_dir / 'skills.json'}")
    if "--no-icons" not in sys.argv:
        bake_skill_icons(skills, out_dir)
    return 0


def bake_skill_icons(skills: dict, out_dir: Path) -> None:
    """Extract each skill icon from ui.hdr to <output>/skills/icons/<skillid>.png.

    The client names the file from the skill id (id % 100, id // 100), but per-class copies of a
    shared skill have no file of their own, so a skill without an icon borrows the icon of a skill
    with the same name and description."""
    import bake_items as bi
    bi._ensure_icon_deps()
    arc = bi.Archive(bi.UI_HDR, bi.UI_SRC)
    icon_dir = out_dir / "icons"
    icon_dir.mkdir(parents=True, exist_ok=True)
    enigma = icon_dir / "enigma.png"
    if not enigma.exists():
        blob = arc.read("skillicon_enigma.dxt")
        img = bi.decode_icon(blob) if blob else None
        if img is not None:
            img.save(enigma)
    decoded = cached = missing = failed = 0
    have: set[int] = set()
    for sid in sorted(int(k) for k in skills):
        out = icon_dir / f"{sid}.png"
        if out.exists():
            cached += 1
            have.add(sid)
            continue
        icon_id = int(skills[str(sid)].get("iconId") or 0) or sid
        blob = arc.read(f"skillicon_{icon_id % 100:02d}_{icon_id // 100}.dxt")
        if blob is None and icon_id != sid:
            blob = arc.read(f"skillicon_{sid % 100:02d}_{sid // 100}.dxt")
        if blob is None:
            missing += 1
            continue
        try:
            img = bi.decode_icon(blob)
        except Exception:
            img = None
        if img is None:
            failed += 1
            continue
        img.save(out)
        decoded += 1
        have.add(sid)

    def sig(sid: int):
        rec = skills[str(sid)]
        return (str(rec.get("name", "")).strip().lower(), str(rec.get("desc", "")).strip().lower())

    donors: dict[tuple, int] = {}
    for sid in sorted(have):
        donors.setdefault(sig(sid), sid)
    shared = 0
    for sid in sorted(int(k) for k in skills):
        if sid in have:
            continue
        src = donors.get(sig(sid))
        if src is None or not sig(sid)[0]:
            continue
        (icon_dir / f"{sid}.png").write_bytes((icon_dir / f"{src}.png").read_bytes())
        shared += 1
    print(f"[skills] icons: {decoded} decoded, {cached} cached, {shared} shared from an identical "
          f"skill, {missing - shared} still without one, {failed} decode-failed -> {icon_dir}")


if __name__ == "__main__":
    sys.exit(main())
