from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Step:
    name: str
    module: str
    args: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class Stage:
    name: str
    summary: str
    steps: tuple[Step, ...] = field(default_factory=tuple)


STAGES: tuple[Stage, ...] = (
    Stage(
        "tables",
        "Game tables to JSON",
        (
            Step("tables", "bake_tables"),
        ),
    ),
    Stage(
        "items",
        "Item records and icons",
        (
            Step(
                "items", "bake_items",
                note="must precede weapons and shine: both join against items.json",
            ),
            Step("item-shine", "bake_item_shine"),
            Step("achievements", "bake_achievements"),
            Step("achievement-titles", "bake_achievement_titles"),
        ),
    ),
    Stage(
        "quests",
        "Quest records and dialogue",
        (
            Step("quests", "bake_quests",
                 note="transitional: quest data is moving server-side"),
            Step("quest-text", "bake_quest_text",
                 note="transitional: text becomes server-sent localized strings"),
        ),
    ),
    Stage(
        "terrain",
        "Heightmaps, ground surface, water and collision",
        (
            Step("terrain", "bake_terrain", ("--all",)),
            Step("terrain-ground", "bake_terrain_ground"),
            Step("water", "bake_water"),
            Step("collision", "bake_collision", ("--all",)),
        ),
    ),
    Stage(
        "objects",
        "Placed world geometry",
        (
            Step("placements", "opd_convert", ("--all",),
                 note="writes terrain/<zone>/placements.json, the scenery the client reads"),
            Step("object-meshes", "n3_convert", ("--all",),
                 note="every shape any zone places; models already in the pool are kept"),
            Step("itembox", "bake_itembox", ("--shape", "itembox_jo_a1.n3shape", "--stem", "itembox_jo_a1"),
                 note="standalone shapes: no zone references them, so a bulk rebuild skips them"),
            Step("itembox-plain", "bake_itembox"),
            Step("stall-selling", "bake_itembox", ("--shape", "Misc/obj_counter.n3shape", "--stem", "obj_counter")),
            Step("stall-premium", "bake_itembox", ("--shape", "Misc/obj_counter_g.n3shape", "--stem", "obj_counter_g")),
            Step("stall-buying", "bake_itembox", ("--shape", "Misc/buyer.n3shape", "--stem", "buyer")),
        ),
    ),
    Stage(
        "characters",
        "Race bodies, armor parts, mobs and attachments",
        (
            Step("players", "bake_players"),
            Step("parts", "bake_parts", ("--all",)),
            Step("mobs", "bake_mobs", ("--all-looks",)),
            Step("capes", "bake_capes"),
            Step("wings", "bake_wings"),
        ),
    ),
    Stage(
        "weapons",
        "Weapon meshes and the enchant-guide chain",
        (
            Step(
                "weapons", "bake_weapons", ("--all",),
                note="OVERWRITES weapon/index.json - the three steps below re-add fields to it",
            ),
            Step("weapon-anchor", "bake_weapon_anchor", note="adds fxg/fxp/fxr to weapon/index.json"),
            Step("visual-aliases", "bake_item_visual_aliases", note="adds visual aliases to weapon/index.json"),
            Step("weapon-fx", "bake_weapon_fx", note="reads index.json, writes glow.json"),
            Step("clan-gauntlet", "bake_clan_gauntlet"),
        ),
    ),
    Stage(
        "fx",
        "Effect descriptors and placements",
        (
            Step("fx", "bake_fx", ("--all",)),
            Step("fx-ids", "bake_fx_ids"),
            Step(
                "fx-placements", "bake_fx_placements",
                note="needs the terrain stage: its zone list comes from the baked terrain dirs",
            ),
            Step("hand-fx", "bake_hand_fx",
                 note="after fx: a glove effect whose descriptor is not baked yet is skipped"),
            Step("npc-role-fx", "bake_npc_role_fx",
                 note="guidefx.tbl: the merchant, service and quest marker effects over NPCs"),
        ),
    ),
    Stage(
        "skills",
        "Skill presentation",
        (
            Step(
                "skills", "bake_skills",
                note="after fx: effect ids only resolve against bundles already baked",
            ),
        ),
    ),
    Stage(
        "ui",
        "Interface art",
        (
            Step("cursors", "bake_cursors"),
            Step("warps", "bake_warps"),
            Step("target-symbol", "bake_target_symbol"),
        ),
    ),
    Stage(
        "sky",
        "Sky and celestial art",
        (
            Step("moon", "bake_moon"),
        ),
    ),
    Stage(
        "sound",
        "Audio archive to ogg",
        (
            Step("sounds", "bake_sounds"),
        ),
    ),
)


def all_steps() -> list[tuple[Stage, Step]]:
    return [(stage, step) for stage in STAGES for step in stage.steps]


def stage_names() -> list[str]:
    return [stage.name for stage in STAGES]


def select(only: list[str] | None, skip: list[str] | None) -> list[tuple[Stage, Step]]:
    chosen = all_steps()
    if only:
        wanted = set(only)
        chosen = [(st, sp) for st, sp in chosen if st.name in wanted or sp.name in wanted]
    if skip:
        unwanted = set(skip)
        chosen = [(st, sp) for st, sp in chosen if st.name not in unwanted and sp.name not in unwanted]
    return chosen
