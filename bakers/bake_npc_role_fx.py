"""bake_npc_role_fx.py -- the effect the client shows over an NPC (merchant, service, quest marker).

The retail client does not infer these from an NPC's name or type. It loads Data/guidefx.tbl, a
flat [npcProtoId, fxId] table, looks the spawned NPC's prototype id up in it and resolves the fx id
through Data/fx.tbl to the bundle. Only when that lookup misses does it fall back to a quest marker,
choosing fx 13068 / 13070 / 13069 by quest state, so a guidefx row takes precedence over the quest
indicator for that NPC.

Output <output>/npcs/role_fx.json:
    { "questMarkers": {"available": ..., "inProgress": ..., "readyToTurnIn": ...},
      "npcs": {"<npcProtoId>": "<baked fx stem>", ...} }

Runs after the fx stage: an effect without a baked descriptor is reported and skipped.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from tbl_reader import read_tbl  # noqa: E402
from _paths import ko as _ko, assets as _assets, server_data as _server_data  # noqa: E402

QUEST_MARKER_FX = {"available": 13068, "inProgress": 13070, "readyToTurnIn": 13069}


def normalized_stem(path: str) -> str:
    return os.path.splitext(os.path.basename(path.replace("\\", "/")))[0]


def main() -> int:
    ko_data = _ko() / "Data"
    fx_dir = _assets() / "fx"
    out = _assets() / "npcs" / "role_fx.json"

    fx_by_id: dict[int, str] = {}
    for row in read_tbl(str(ko_data / "fx.tbl")).rows:
        if len(row) > 2 and isinstance(row[2], str) and row[2]:
            fx_by_id[int(row[0])] = normalized_stem(row[2])

    guide_rows = read_tbl(str(ko_data / "guidefx.tbl")).rows
    baked = {p.stem.lower() for p in fx_dir.glob("*.json") if p.name != "index.json"}

    selected: dict[str, str] = {}
    unknown_fx: dict[int, int] = {}
    unbaked: dict[str, int] = {}
    for row in guide_rows:
        npc_id, fx_id = int(row[0]), int(row[1])
        name = fx_by_id.get(fx_id)
        if name is None:
            unknown_fx[npc_id] = fx_id
            continue
        if name.lower() not in baked:
            unbaked[name] = unbaked.get(name, 0) + 1
            continue
        selected[str(npc_id)] = name

    if unknown_fx:
        print(f"[npc-role-fx] guidefx rows referencing fx ids absent from fx.tbl: {unknown_fx}",
              file=sys.stderr)
    if unbaked:
        print(f"[npc-role-fx] no baked descriptor for: {unbaked}; run the fx stage first", file=sys.stderr)

    markers: dict[str, str] = {}
    for state, fx_id in QUEST_MARKER_FX.items():
        name = fx_by_id.get(fx_id)
        if name is None or name.lower() not in baked:
            print(f"[npc-role-fx] quest marker fx {fx_id} ({name}) is missing or unbaked", file=sys.stderr)
            return 1
        markers[state] = name

    server = _server_data()
    npcs_json = server / "Npcs.json" if server else None
    if npcs_json and npcs_json.is_file():
        server_ids = {int(n["Id"]) for n in json.loads(npcs_json.read_text(encoding="utf-8-sig"))}
        guide_ids = {int(r[0]) for r in guide_rows}
        missing = sorted(guide_ids - server_ids)
        print(f"[npc-role-fx] {len(guide_ids & server_ids)}/{len(guide_ids)} guidefx NPCs exist in the "
              f"server's Npcs.json" + (f"; not spawned by this server: {missing}" if missing else ""))

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"questMarkers": markers,
                               "npcs": dict(sorted(selected.items(), key=lambda item: int(item[0])))},
                              separators=(",", ":")), encoding="utf-8")
    print(f"[npc-role-fx] {len(selected)} NPC role effects + {len(markers)} quest markers -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
