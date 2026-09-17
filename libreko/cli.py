from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import paths, pipeline, runner


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bake.py",
        description="Decode an installed Knight Online client into engine-ready assets.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python bake.py --ko \"C:/Games/KnightOnline\" --out ./assets\n"
            "  python bake.py --ko ... --out ... --only terrain characters\n"
            "  python bake.py --ko ... --out ... --skip sound --keep-going\n"
            "  python bake.py --list\n"
        ),
    )
    p.add_argument("--ko", metavar="DIR", help="retail client install (contains Data, Zones, DTex)")
    p.add_argument("--out", metavar="DIR", help="where baked assets are written")
    p.add_argument("--server-data", metavar="DIR",
                   help="the server's Seed/Data folder; skills take their gameplay fields from it "
                        "(found automatically in a LibreKO checkout)")
    p.add_argument("--only", nargs="+", metavar="NAME", help="run only these stages or steps")
    p.add_argument("--skip", nargs="+", metavar="NAME", help="run everything except these")
    p.add_argument("--list", action="store_true", help="print the pipeline and exit")
    p.add_argument("--dry-run", action="store_true", help="show what would run, change nothing")
    p.add_argument("--keep-going", action="store_true", help="continue after a failing step")
    p.add_argument("--verbose", action="store_true", help="stream each baker's output")
    return p


def _print_pipeline() -> None:
    for stage in pipeline.STAGES:
        print(f"\n{stage.name}  -- {stage.summary}")
        for step in stage.steps:
            suffix = f"   ({step.note})" if step.note else ""
            print(f"    {step.name}{suffix}")
    total = len(pipeline.all_steps())
    print(f"\n{len(pipeline.STAGES)} stages, {total} steps")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    if args.list:
        _print_pipeline()
        return 0

    if not args.ko or not args.out:
        _parser().error("--ko and --out are required (or use --list)")

    ko = Path(args.ko).expanduser().resolve()
    out = Path(args.out).expanduser().resolve()

    if not ko.is_dir():
        print(f"retail install not found: {ko}", file=sys.stderr)
        return 2

    missing = paths.missing_markers(ko)
    if missing:
        print(f"{ko} does not look like a client install -- missing {', '.join(missing)}",
              file=sys.stderr)
        return 2

    selected = pipeline.select(args.only, args.skip)
    if not selected:
        print("nothing selected; try --list to see stage and step names", file=sys.stderr)
        return 2

    server_data = (Path(args.server_data).expanduser().resolve() if args.server_data
                   else paths.default_server_data(out))
    if args.server_data and not (server_data / "Magic.json").is_file():
        print(f"server data not found: {server_data}", file=sys.stderr)
        return 2

    print(f"source  {ko}")
    print(f"output  {out}")
    print(f"server  {server_data or '(none: skills carry presentation only)'}")
    print(f"steps   {len(selected)} of {len(pipeline.all_steps())}")

    if args.dry_run:
        for stage, step in selected:
            print(f"  {stage.name}/{step.name}")
        return 0

    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env[paths.KO_ENV] = str(ko)
    env[paths.OUT_ENV] = str(out)
    if server_data:
        env[paths.SERVER_ENV] = str(server_data)
    env["PYTHONIOENCODING"] = "utf-8"

    results = runner.run(selected, env, verbose=args.verbose, keep_going=args.keep_going)
    return runner.summarise(results)
