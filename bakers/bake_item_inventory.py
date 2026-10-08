"""Bake sparse inventory metadata from the server's expanded item seeds.

Exact server IDs override retail weight and stackability only when they differ
from ItemData.Get's exact/base lookup, or the retail catalog cannot resolve them.
Neither items.json nor the server seeds are changed.

Run through ``bake.py --only item-inventory`` after items have been baked, or use
``--out ASSETS --server-data Seed/Data --check`` for a read-only freshness check.
Without server item seeds, a normal bake preserves any existing catalog; a check
fails because its freshness cannot be established.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from _paths import assets as _assets, server_data as _server_data

BASE_ID_SPAN = 1000
INT_MAX = 2_147_483_647
WEIGHT_MAX = 32_767
COUNTABLE_MAX = 255


def integer(value: object, field: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"invalid {field}: expected integer {minimum}..{maximum}, got {value!r}")
    return value


class RetailInventory:
    """The weight/countable fields resolved by ItemData.Get, without Godot."""

    def __init__(self, catalog: dict):
        self.items: dict[int, tuple[int, int]] = {}
        for key, row in catalog.items():
            if not key.isdecimal():
                continue
            item_id = integer(int(key), "retail item ID", 1, INT_MAX)
            if not isinstance(row, dict):
                raise ValueError(f"expected a retail item object for ID {item_id}")
            self.items[item_id] = (
                integer(row.get("weight", 0), f"retail weight for item {item_id}", -INT_MAX - 1, INT_MAX),
                integer(row.get("countable", 0), f"retail countable for item {item_id}", -INT_MAX - 1, INT_MAX),
            )

    def resolve(self, item_id: int) -> tuple[int, int] | None:
        item = self.items.get(item_id)
        return item if item is not None else self.items.get(item_id // BASE_ID_SPAN * BASE_ID_SPAN)


def build_catalog(retail: dict, server_rows: list[dict], source: dict | None = None) -> dict:
    resolver = RetailInventory(retail)
    server_items: dict[int, tuple[int, int]] = {}
    for row in server_rows:
        if not isinstance(row, dict):
            raise ValueError("expected a server item object")
        item_id = integer(row["Num"], "server item ID", 1, INT_MAX)
        if item_id in server_items:
            raise ValueError(f"duplicate server item ID: {item_id}")
        server_items[item_id] = (
            integer(row["Weight"], f"server Weight for item {item_id}", 0, WEIGHT_MAX),
            integer(row["Countable"], f"server Countable for item {item_id}", 0, COUNTABLE_MAX),
        )

    overrides = {
        str(item_id): list(inventory)
        for item_id, inventory in sorted(server_items.items())
        if resolver.resolve(item_id) != inventory
    }
    result = {"schemaVersion": 1, "items": overrides}
    if source is not None:
        result["source"] = source
    return result


def load_catalog(items_file: Path, seed_files: list[Path]) -> dict:
    raw_items = items_file.read_bytes()
    retail = json.loads(raw_items)
    if not isinstance(retail, dict):
        raise ValueError(f"expected an item object: {items_file}")

    rows = []
    seed_hash = hashlib.sha256()
    for path in sorted(seed_files, key=lambda path: path.name):
        raw = path.read_bytes()
        name = path.name.encode("utf-8")
        seed_hash.update(len(name).to_bytes(4, "big"))
        seed_hash.update(name)
        seed_hash.update(len(raw).to_bytes(8, "big"))
        seed_hash.update(raw)
        file_rows = json.loads(raw)
        if not isinstance(file_rows, list):
            raise ValueError(f"expected an item array: {path}")
        rows.extend(file_rows)

    return build_catalog(retail, rows, {
        "clientItemsSha256": hashlib.sha256(raw_items).hexdigest(),
        "serverItemsSha256": seed_hash.hexdigest(),
        "serverItemCount": len(rows),
    })


def encoded(catalog: dict) -> bytes:
    return (json.dumps(catalog, separators=(",", ":"), ensure_ascii=True) + "\n").encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="baked asset root (defaults to LIBREKO_OUT_DIR)")
    parser.add_argument("--server-data", type=Path,
                        help="server Seed/Data (defaults to LIBREKO_SERVER_DATA)")
    parser.add_argument("--check", action="store_true", help="verify output freshness without writing")
    args = parser.parse_args(argv)
    assets = args.out if args.out is not None else _assets()
    server = args.server_data if args.server_data is not None else _server_data()
    seed_files = sorted(server.glob("Items.slot*.json")) if server is not None else []
    if not seed_files:
        print("[item-inventory] WARNING: no server item seeds (pass --server-data); "
              "existing inventory catalog is unchanged", file=sys.stderr)
        return 2 if args.check else 0

    output = assets / "items" / "inventory.json"
    try:
        catalog = load_catalog(assets / "items" / "items.json", seed_files)
        contents = encoded(catalog)
        if args.check:
            if not output.is_file() or output.read_bytes() != contents:
                print(f"[item-inventory] ERROR: stale or missing catalog: {output}; "
                      "run bake.py --only item-inventory", file=sys.stderr)
                return 1
            print(f"[item-inventory] current: {len(catalog['items'])} authoritative overrides")
            return 0

        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(contents)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"[item-inventory] ERROR: {error}", file=sys.stderr)
        return 2

    print(f"[item-inventory] wrote {len(catalog['items'])} authoritative overrides "
          f"from {catalog['source']['serverItemCount']} server rows -> {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
