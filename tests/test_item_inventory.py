from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bakers"))
sys.path.insert(0, str(ROOT))

from bake_item_inventory import RetailInventory, build_catalog, encoded, load_catalog, main
from libreko import pipeline


def item(item_id: int, weight: int = 10, countable: int = 1) -> dict:
    return {"Num": item_id, "Weight": weight, "Countable": countable}


class InventoryCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.assets = self.directory / "assets"
        self.server = self.directory / "server"
        self.items_file = self.assets / "items" / "items.json"
        self.output = self.items_file.with_name("inventory.json")
        self.items_file.parent.mkdir(parents=True)
        self.server.mkdir()
        self.write_inputs({"1000": {"weight": 10, "countable": 1}}, [item(1001)])

    def write_inputs(self, retail: dict, rows: list[dict]):
        self.items_file.write_text(json.dumps(retail), encoding="utf-8")
        (self.server / "Items.slot00.json").write_text(json.dumps(rows), encoding="utf-8")

    def run_baker(self, *extra: str) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = main(["--out", str(self.assets), "--server-data", str(self.server), *extra])
        return code, output.getvalue()

    def test_sparse_inventory_overrides_and_full_parity(self):
        retail = {
            "1000": {"weight": 10, "countable": 1},
            "2000": {"weight": 20, "countable": 0},
        }
        rows = [item(1001), item(1002, 40, 1), item(2001, 20, 2), item(3001, 0, 0)]
        catalog = build_catalog(retail, rows)
        self.assertEqual(catalog, {
            "schemaVersion": 1,
            "items": {"1002": [40, 1], "2001": [20, 2], "3001": [0, 0]},
        })
        resolver = RetailInventory(retail)
        for row in rows:
            actual = catalog["items"].get(str(row["Num"]), resolver.resolve(row["Num"]))
            self.assertEqual(tuple(actual), (row["Weight"], row["Countable"]))

    def test_exact_lookup_precedes_base_and_does_not_resolve_siblings(self):
        retail = {
            "1000": {"weight": 10, "countable": 1},
            "1001": {"weight": 20, "countable": 2},
            "2001": {"weight": 99, "countable": 1},
        }
        rows = [item(1001, 20, 2), item(1002), item(2002)]
        self.assertEqual(build_catalog(retail, rows)["items"], {"2002": [10, 1]})

    def test_missing_retail_fields_have_client_zero_defaults(self):
        self.assertEqual(build_catalog({"1000": {}}, [item(1001, 0, 0)])["items"], {})

    def test_extensions_do_not_modify_inventory_metadata(self):
        retail = {
            "1000": {"weight": 10, "countable": 1, "cat": 4},
            "_ext": {"4": {"1": {"linked": 1001, "weight": 500, "countable": 0}}},
        }
        self.assertEqual(build_catalog(retail, [item(1001)])["items"], {})

    def test_preserves_nonbinary_countable_and_numeric_limits(self):
        rows = [item(1, 0, 2), item(2_147_483_647, 32_767, 255)]
        self.assertEqual(build_catalog({}, rows)["items"], {
            "1": [0, 2], "2147483647": [32767, 255],
        })

    def test_rejects_invalid_server_ranges_and_types(self):
        invalid = {
            "Num": [0, -1, 2_147_483_648, True, 1001.0, "1001", None],
            "Weight": [-1, 32_768, True, 10.0, "10", None],
            "Countable": [-1, 256, False, 1.0, "1", None],
        }
        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    row = item(1001)
                    row[field] = value
                    build_catalog({}, [row])

    def test_duplicate_server_ids_fail(self):
        with self.assertRaisesRegex(ValueError, "duplicate server item ID: 1001"):
            build_catalog({}, [item(1001), item(1001, 20)])

    def test_invalid_input_preserves_existing_output(self):
        self.output.write_bytes(b"existing catalog\n")
        malformed = [
            ({"1000": []}, [item(1001)]),
            ({"1000": {"weight": True}}, [item(1001)]),
            ({}, [{"Num": 1001, "Weight": 10}]),
            ({}, [item(1001, -1)]),
        ]
        for retail, rows in malformed:
            with self.subTest(retail=retail, rows=rows):
                self.write_inputs(retail, rows)
                self.assertEqual(self.run_baker()[0], 2)
                self.assertEqual(self.output.read_bytes(), b"existing catalog\n")

        for path, raw in [
            (self.items_file, b"[]"),
            (self.items_file, b"{broken"),
            (self.server / "Items.slot00.json", b"{}"),
            (self.server / "Items.slot00.json", b"{broken"),
        ]:
            with self.subTest(path=path, raw=raw):
                self.write_inputs({}, [])
                path.write_bytes(raw)
                self.assertEqual(self.run_baker()[0], 2)
                self.assertEqual(self.output.read_bytes(), b"existing catalog\n")
        self.items_file.unlink()
        self.assertEqual(self.run_baker()[0], 2)
        self.assertEqual(self.output.read_bytes(), b"existing catalog\n")

    def test_duplicate_ids_across_seed_files_fail_without_writing(self):
        (self.server / "Items.slot01.json").write_text(json.dumps([item(1001)]), encoding="utf-8")
        self.assertEqual(self.run_baker()[0], 2)
        self.assertFalse(self.output.exists())

    def test_deterministic_output_and_seed_file_order(self):
        rows = [item(3000, 3), item(1000, 1), item(2000, 2)]
        self.assertEqual(encoded(build_catalog({}, rows)), encoded(build_catalog({}, list(reversed(rows)))))
        self.write_inputs({}, rows[:1])
        (self.server / "Items.slot01.json").write_text(json.dumps(rows[1:]), encoding="utf-8")
        seeds = list(self.server.glob("Items.slot*.json"))
        a = load_catalog(self.items_file, seeds)
        b = load_catalog(self.items_file, list(reversed(seeds)))
        self.assertEqual(encoded(a), encoded(b))
        self.assertEqual(a["source"]["serverItemCount"], 3)
        for key in ("clientItemsSha256", "serverItemsSha256"):
            self.assertEqual(len(a["source"][key]), 64)
        self.assertNotIn(str(self.directory), encoded(a).decode())

    def test_check_detects_missing_stale_and_changed_sources_without_writing(self):
        self.assertEqual(self.run_baker("--check")[0], 1)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.run_baker()[0], 0)
        original = self.output.read_bytes()
        self.assertEqual(self.run_baker("--check")[0], 0)
        self.assertEqual(self.output.read_bytes(), original)
        self.items_file.write_bytes(self.items_file.read_bytes() + b"\n")
        self.assertEqual(self.run_baker("--check")[0], 1)
        self.assertEqual(self.output.read_bytes(), original)
        self.assertEqual(self.run_baker()[0], 0)
        current = self.output.read_bytes()
        seed = self.server / "Items.slot00.json"
        seed.write_bytes(seed.read_bytes() + b"\n")
        self.assertEqual(self.run_baker("--check")[0], 1)
        self.assertEqual(self.output.read_bytes(), current)

    def test_missing_server_data_warns_and_preserves_output_but_check_fails(self):
        self.output.write_bytes(b"existing catalog\n")
        (self.server / "Items.slot00.json").unlink()
        code, message = self.run_baker()
        self.assertEqual(code, 0)
        self.assertIn("WARNING", message)
        self.assertEqual(self.run_baker("--check")[0], 2)
        self.assertEqual(self.output.read_bytes(), b"existing catalog\n")

    def test_pipeline_runs_only_companion_without_rebaking_retail_items(self):
        ko = self.directory / "ko"
        for name in ("Data", "Zones", "DTex"):
            (ko / name).mkdir(parents=True)
        (self.server / "Magic.json").write_text("[]", encoding="utf-8")
        self.write_inputs({"1000": {"weight": 0, "countable": 0}}, [item(1001, 10, 2)])
        original = self.items_file.read_bytes()
        run = subprocess.run([
            sys.executable, str(ROOT / "bake.py"), "--ko", str(ko), "--out", str(self.assets),
            "--server-data", str(self.server), "--only", "item-inventory", "--skip", "imports",
        ], capture_output=True, text=True, env=dict(os.environ), cwd=ROOT)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(json.loads(self.output.read_bytes())["items"], {"1001": [10, 2]})
        self.assertEqual(self.items_file.read_bytes(), original)
        items_steps = [step.name for stage, step in pipeline.all_steps() if stage.name == "items"]
        self.assertEqual(items_steps[items_steps.index("items") + 1], "item-inventory")
        self.assertEqual([step.name for _, step in pipeline.select(["item-inventory"], ["imports"])],
                         ["item-inventory"])


if __name__ == "__main__":
    unittest.main()
