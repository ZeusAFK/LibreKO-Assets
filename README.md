# LibreKO Assets

An offline pipeline that decodes an installed Knight Online client into engine-ready assets:
meshes as glTF, textures as PNG, terrain as heightmaps and splat maps, effects and game tables
as JSON.

Nothing here ships game content. You point it at your own installation and it writes the
converted output to a folder you choose.

## Requirements

- Python 3.10 or newer
- A Knight Online installation (the folder containing `Data`, `Zones` and `DTex`)
- Disk space for the output — a full bake is several gigabytes

```
pip install -r requirements.txt
```

## Usage

One command bakes everything:

```
python bake.py --ko "C:/Games/KnightOnline" --out ./assets
```

A full run takes a while — the character, object and sound stages dominate. Useful flags:

| Flag | Effect |
|---|---|
| `--list` | print the pipeline and exit |
| `--dry-run` | show what would run, change nothing |
| `--only NAME ...` | run just these stages or steps |
| `--skip NAME ...` | run everything except these |
| `--keep-going` | continue past a failing step instead of stopping |
| `--verbose` | stream each step's own output |
| `--server-data DIR` | the LibreKO server's `Seed/Data`; skills take their buff and cost fields from it. Found automatically when the output is `Client/assets` inside a LibreKO checkout |

```
python bake.py --ko ... --out ./assets --only terrain characters
python bake.py --ko ... --out ./assets --skip sound --keep-going
```

## Pipeline

Stages run in order, and the order matters — later stages read what earlier ones produced.

| Stage | Produces |
|---|---|
| `tables` | game tables as JSON |
| `items` | item records, icons, shine data, achievements |
| `quests` | quest records and dialogue |
| `terrain` | heightmaps, ground surface, water, collision |
| `objects` | placed world geometry and standalone props |
| `characters` | race bodies, armor parts, mobs, capes, wings, hand effects |
| `weapons` | weapon meshes and the enchant-guide chain |
| `fx` | effect descriptors, ids and placements |
| `skills` | skill presentation |
| `ui` | cursors, warp data, target symbol |
| `sky` | moon disc |
| `sound` | audio archive to ogg |

Two ordering rules are load-bearing and easy to break by hand:

- **`items` before `weapons`.** The weapon steps join against `items.json`.
- **The weapon chain is a chain.** `weapons` *overwrites* `items/weapon/index.json`; `weapon-anchor`
  and `visual-aliases` then add fields back to that same file. Running `weapons` on its own leaves a
  file that still looks complete but has lost the enchant-guide and alias data, and nothing reports
  an error. Run the whole `weapons` stage or none of it.

`bake.py` encodes both. They only bite if you invoke a baker directly.

A third dependency crosses stages: `fx-placements` builds its zone list from the terrain already
baked, so it produces nothing if `terrain` has not run. This is why `--only fx` against a fresh
output folder reports no zones — run the whole pipeline once, then narrow with `--only` afterwards.

## Layout

```
bake.py            entry point
libreko/           orchestration -- stage order, step selection, the runner
bakers/            the decoders, one module per asset class
bakers/kotools/    format readers: meshes, textures, terrain, tables, effects
```

Each module under `bakers/` is runnable on its own for debugging, but expects
`LIBREKO_KO_DIR` and `LIBREKO_OUT_DIR` in the environment. `bake.py` sets both, so prefer
`--only <step>` over calling a script directly.

## Scope

The pipeline converts what the client itself ships. Data that a server owns — damage, costs,
cast times, drop rates — is deliberately not produced here; skills are baked for presentation
only (names, icons, animations, effects) and the gameplay numbers come from whatever server you
connect to.

The `quests` stage is transitional. Quest definitions are moving to the server, which will send
the client a ready-made view of text, objectives and rewards rather than the client reading baked
tables. When that lands this stage goes away; until then it keeps quest text working.

## Licence

AGPL-3.0. See [LICENSE](LICENSE).

The `.tbl` cipher is a port of MIT-licensed code from another project; its attribution is in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Knight Online and all related marks are the property of their respective owners. This project is
not affiliated with, endorsed by or connected to any official operator.
