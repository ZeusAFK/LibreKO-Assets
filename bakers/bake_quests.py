"""bake_quests.py -- decode KO Quest_Helper.tbl + Quest_Monster_Exchange.tbl into a plain
JSON the client loads at runtime for the per-quest presentation data the wire does not carry.

Quest titles and journal lines are NOT here. They come from the `Quest` block of the .quest
file that defines the quest, and the server sends them over GS_QUEST sub 15 already localized
(the client/docs/packets/0x64-quest.md). Baking them a second time here is what that packet replaced.

The server drives the quest STATE MACHINE (GS_QUEST 0x64: state, kill counts, rewards --
see the format notes). The client only needs the per-quest *presentation*
data, which lives client-side in KO:

  Quest_Helper.tbl           -- one row per (NPC, quest-state); grouped by EventDataIndex.
    col 0  Index            (helper id)
    col 2  Level            (min level gate)
    col 3  Exp              (8-byte int64 reward; type code 10)
    col 6  Class  (5 = any)
    col 7  Nation (3 = both)
    col 9  Zone
    col 10 NpcId            (0 = a zone auto-event, e.g. the starter quest)
    col 11 EventDataIndex   (THE QUEST ID -- the key in QuestMap)
    col 12 EventStatus      (NOT the state this helper fires on: value 4 marks the row as one
                             selectable line in the NPC's dialog, and cols 4/5/8 gate it)
    col 15 ExchangeIndex    (Item_Exchange turn-in set)
    col 16 EventTalkIndex   (NPC-dialog text id -> Quest_Talk_us; fallback objective)
    col 17 LuaFilename
    col 18 GuideIndex       (the Quest_Guide row id -- the quest's name/journal text)

  Quest_guide_us.tbl         -- the ENGLISH quest journal, keyed by GuideIndex (col 18 above).
    [ id, _, _, _, NAME, summary/hint (col5), journal "asked me to..." (col6) ]
    This is the retail client's source for the quest-log title + description (the binary
    loads Data\Quest_guide_us.tbl; the base Quest_Guide.tbl is Korean, Quest_Guide_tk is
    Turkish). Helper col 18 indexes this table. ~80% of quests link;
    the rest fall back to the EventTalkIndex dialog text / "Quest #N".

  Quest_Monster_Exchange.tbl -- the kill targets, keyed by quest id (== QuestNum on the
    server). 37 columns: [questId] + 4 groups x 9 cols, each group [m0,_,m1,_,m2,_,m3,_,count]
    (monsters at +0/+2/+4/+6, required count at +8). Quest 500 has NO row (server special-case).

  Item_Exchange.tbl          -- the turn-in set the helper's ExchangeIndex points at, and with it
    the quest's REWARD ITEMS. 27 columns: [index, npcId, randomFlag] + 5 origin (item,count)
    pairs at 3..12 + 5 reward (item,count) pairs at 13..22. Rewards are ordinary item ids, so
    money and experience arrive as the pseudo-items 900000000 "Coin" and 900001000 "EXP" -- both
    have real icons in the baked item set, which is what the quest window draws.

Output: <output>/quests/quests.json
  { "questItems": [ itemId, ... ],   -- every item any quest asks for: the inventory Quest tab
    "quests": { "<questId>": { "talk": <textId>,
                               "exp": <n>, "exchange": <n>,
                               "level": <n>, "class": <n>, "nation": <n>, "zone": <n>,
                               "give": [ [itemId, count], ... ], "need": [ [itemId, count], ... ],
                               "groups": [ { "npcs": [..], "count": <n> }, ... ] }, ... } }

Domain/QuestData.cs reads only this JSON (joining the EventTalkIndex fallback through
quest_text.json / QuestText.cs). This tool never runs at game time.

Usage:
    python bake.py --only bake_quests --data "<retail install>\\Data"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_TOOLS_DIR))
from tbl_reader import read_tbl  # noqa: E402
from _paths import ko as _ko, assets as _assets  # noqa: E402

OUT = _assets() / "quests" / "quests.json"

# Quest_Helper column indices (see module docstring).
H_LEVEL, H_EXP, H_CLASS, H_NATION, H_ZONE = 2, 3, 6, 7, 9
H_NPC, H_QUESTID, H_STATUS, H_EXCHANGE, H_TALK, H_GUIDE = 10, 11, 12, 15, 16, 18
H_REQUIRED, H_GROUP, H_QUESTTYPE = 4, 5, 8

# EventStatus on a helper is not the state it fires on: the NPC dialog lists exactly the
# helpers whose EventStatus is this value, and gates them on the columns below instead.
MENU_ENTRY_STATUS = 4

# Item_Exchange column indices.
X_ORIGIN, X_REWARD, X_PAIRS = 3, 13, 5

# 900005000 "Hunt" is a marker row carrying the quest id, not something the player hands over.
HUNT_MARKER_ITEM = 900005000


def _find(data_dir: Path, stem: str) -> Path:
    src = data_dir / f"{stem}.tbl"
    if src.exists():
        return src
    matches = [p for p in data_dir.glob("*.tbl") if p.stem.lower() == stem.lower()]
    if not matches:
        raise FileNotFoundError(f"{stem}.tbl not found in {data_dir}")
    return matches[0]


def _load_monster_groups(data_dir: Path) -> dict[int, list[dict]]:
    """questId -> [ {npcs:[...], count:n}, ... ] from Quest_Monster_Exchange.tbl."""
    t = read_tbl(_find(data_dir, "Quest_Monster_Exchange"))
    out: dict[int, list[dict]] = {}
    for row in t.rows:
        if len(row) < 37:
            continue
        qid = int(row[0])
        groups = []
        for g in range(4):
            base = 1 + g * 9
            npcs = [int(row[base + i * 2]) for i in range(4) if int(row[base + i * 2]) > 0]
            count = int(row[base + 8])
            if npcs and count > 0:
                groups.append({"npcs": npcs, "count": count})
        if groups:
            out[qid] = groups
    print(f"[quests] monster groups: {len(out)} quests")
    return out


def _pairs(row: list, base: int) -> list[list[int]]:
    out: list[list[int]] = []
    seen: set[int] = set()
    for i in range(X_PAIRS):
        item, count = int(row[base + i * 2]), int(row[base + i * 2 + 1])
        if item <= 0 or count <= 0 or item == HUNT_MARKER_ITEM or item in seen:
            continue
        seen.add(item)
        out.append([item, count])
    return out


def _load_exchanges(data_dir: Path) -> dict[int, tuple[list[list[int]], list[list[int]]]]:
    """ExchangeIndex -> (rewards, hand-ins) from Item_Exchange.tbl."""
    t = read_tbl(_find(data_dir, "Item_Exchange"))
    out: dict[int, tuple[list[list[int]], list[list[int]]]] = {}
    for row in t.rows:
        if len(row) <= X_REWARD + X_PAIRS * 2 - 1:
            continue
        out[int(row[0])] = (_pairs(row, X_REWARD), _pairs(row, X_ORIGIN))
    print(f"[quests] exchange sets: {len(out)}")
    return out


def _load_helpers(data_dir: Path,
                  monster_groups: dict[int, list[dict]],
                  exchanges: dict[int, tuple[list[list[int]], list[list[int]]]]) -> dict[str, dict]:
    """Group Quest_Helper rows by EventDataIndex (the quest id) into per-quest metadata."""
    t = read_tbl(_find(data_dir, "Quest_Helper"))
    # questId -> { status -> helper row }. A quest can have several helpers per state
    # (multi-phase / per-NPC); sort by Index and keep the lowest-Index one per state so the
    # pick is deterministic (the first phase). Status 255 = a bare NPC greeting -- skip it.
    by_quest: dict[int, dict[int, list]] = {}
    npc_states: dict[int, dict[int, set[int]]] = {}
    npc_helpers: dict[int, dict[int, dict[int, int]]] = {}
    for row in sorted((r for r in t.rows if len(r) > H_GUIDE), key=lambda r: int(r[0])):
        qid = int(row[H_QUESTID])
        status = int(row[H_STATUS])
        if qid <= 0 or status > 4:
            continue
        npc = int(row[H_NPC])
        if npc > 0:
            npc_states.setdefault(qid, {}).setdefault(status, set()).add(npc)
            npc_helpers.setdefault(qid, {}).setdefault(status, {}).setdefault(npc, int(row[0]))
        by_quest.setdefault(qid, {}).setdefault(status, row)

    quests: dict[str, dict] = {}
    for qid, states in by_quest.items():
        # Prefer the in-progress (1) helper for the journal entry, then start (0), then complete (2).
        primary = states.get(1) or states.get(0) or states.get(2) or next(iter(states.values()))
        complete = states.get(2) or primary
        talk = next((int(states[s][H_TALK]) for s in (1, 0, 2)
                     if s in states and int(states[s][H_TALK]) > 0), 0)

        entry = {
            "talk": talk,
            "exp": int(complete[H_EXP]),
            "exchange": int(complete[H_EXCHANGE]),
            "level": int(primary[H_LEVEL]),
            "class": int(primary[H_CLASS]),
            "nation": int(primary[H_NATION]),
            "zone": int(primary[H_ZONE]),
            "npcs": {
                str(state): sorted(npcs)
                for state, npcs in npc_states.get(qid, {}).items()
            },
            "helpers": {
                str(state): {str(npc): index for npc, index in sorted(by_npc.items())}
                for state, by_npc in npc_helpers.get(qid, {}).items()
            },
        }
        if qid in monster_groups:
            entry["groups"] = monster_groups[qid]
        give, need = exchanges.get(entry["exchange"], ([], []))
        if give:
            entry["give"] = give
        if need:
            entry["need"] = need
        quests[str(qid)] = entry
    print(f"[quests] quests: {len(quests)} "
          f"(with kill targets: {sum(1 for q in quests.values() if q.get('groups'))}, "
          f"with rewards: {sum(1 for q in quests.values() if q.get('give'))})")
    return quests


def _load_npc_menu(data_dir: Path) -> dict[str, list[dict]]:
    """The option list an NPC dialog shows, per NPC.

    Every helper row whose EventStatus is MENU_ENTRY_STATUS is one selectable line; the row's
    own Level/Exp/Class/Nation gates decide whether it is offered, and two further columns gate
    it on quest progress -- `req` must be finished first (merely started, when QuestType is 5),
    and `grp` hides the line while another quest sharing that group id is still running.
    Ordered by helper index, which is the order they are listed in."""
    t = read_tbl(_find(data_dir, "Quest_Helper"))
    menu: dict[str, list[dict]] = {}
    for row in sorted((r for r in t.rows if len(r) > H_GUIDE), key=lambda r: int(r[0])):
        if int(row[H_STATUS]) != MENU_ENTRY_STATUS:
            continue
        npc = int(row[H_NPC])
        qid = int(row[H_QUESTID])
        if npc <= 0 or qid <= 0:
            continue
        menu.setdefault(str(npc), []).append({
            "h": int(row[0]),
            "q": qid,
            "level": int(row[H_LEVEL]),
            "exp": int(row[H_EXP]),
            "class": int(row[H_CLASS]),
            "nation": int(row[H_NATION]),
            "zone": int(row[H_ZONE]),
            "type": int(row[H_QUESTTYPE]),
            "req": int(row[H_REQUIRED]),
            "grp": int(row[H_GROUP]),
        })
    print(f"[quests] npc menus: {len(menu)} NPCs, "
          f"{sum(len(v) for v in menu.values())} options")
    return menu


def main() -> int:
    ap = argparse.ArgumentParser(description="Bake KO Quest_Helper + Quest_Monster_Exchange -> quests.json")
    ap.add_argument("--data", help="KO Data dir containing Quest_Helper.tbl", default=str(_ko() / "Data"))
    ap.add_argument("--out", default=str(OUT), help="JSON output path")
    args = ap.parse_args()

    data_dir = Path(args.data)
    if not data_dir.is_dir():
        print(f"ERROR: data dir not found: {data_dir}", file=sys.stderr)
        return 1

    monster_groups = _load_monster_groups(data_dir)
    exchanges = _load_exchanges(data_dir)
    quests = _load_helpers(data_dir, monster_groups, exchanges)
    npc_menu = _load_npc_menu(data_dir)

    quest_items = sorted({item for q in quests.values() for item, _ in q.get("need", [])})

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"quests": quests, "questItems": quest_items, "npcMenu": npc_menu},
                   ensure_ascii=False),
        encoding="utf-8")
    print(f"[quests] {len(quests)} quests, {len(quest_items)} quest items -> {out}")
    return 0 if quests else 1


if __name__ == "__main__":
    raise SystemExit(main())
