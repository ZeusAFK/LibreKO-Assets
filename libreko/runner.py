from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .pipeline import Stage, Step

BAKERS = Path(__file__).resolve().parent.parent / "bakers"


@dataclass
class Result:
    stage: str
    step: str
    status: str
    seconds: float
    detail: str = ""


def _command(step: Step) -> list[str]:
    return [sys.executable, str(BAKERS / f"{step.module}.py"), *step.args]


def run_step(stage: Stage, step: Step, env: dict[str, str], verbose: bool) -> Result:
    started = time.monotonic()
    script = BAKERS / f"{step.module}.py"
    if not script.exists():
        return Result(stage.name, step.name, "missing", 0.0, f"{step.module}.py not vendored")

    proc = subprocess.run(
        _command(step), env=env, cwd=str(BAKERS),
        capture_output=not verbose, text=True, errors="replace",
    )
    elapsed = time.monotonic() - started
    if proc.returncode == 0:
        return Result(stage.name, step.name, "ok", elapsed)

    detail = ""
    if not verbose and proc.stderr:
        lines = [ln for ln in proc.stderr.strip().splitlines() if ln.strip()]
        detail = lines[-1][:200] if lines else ""
    return Result(stage.name, step.name, "failed", elapsed, detail or f"exit {proc.returncode}")


def run(selected: list[tuple[Stage, Step]], env: dict[str, str], *,
        verbose: bool, keep_going: bool) -> list[Result]:
    results: list[Result] = []
    total = len(selected)
    current_stage = None

    for index, (stage, step) in enumerate(selected, 1):
        if stage.name != current_stage:
            current_stage = stage.name
            print(f"\n[{stage.name}] {stage.summary}")

        print(f"  ({index}/{total}) {step.name} ... ", end="", flush=True)
        result = run_step(stage, step, env, verbose)
        results.append(result)

        if result.status == "ok":
            print(f"ok  {result.seconds:.1f}s")
        elif result.status == "missing":
            print(f"skipped -- {result.detail}")
        else:
            print(f"FAILED  {result.seconds:.1f}s")
            if result.detail:
                print(f"        {result.detail}")
            if not keep_going:
                print("\nStopped at the first failure. Re-run with --keep-going to continue past it.")
                break

    return results


def summarise(results: list[Result]) -> int:
    ok = sum(1 for r in results if r.status == "ok")
    failed = [r for r in results if r.status == "failed"]
    skipped = [r for r in results if r.status == "missing"]
    seconds = sum(r.seconds for r in results)

    print(f"\n{ok} ok, {len(failed)} failed, {len(skipped)} skipped  in {seconds/60:.1f} min")
    if failed:
        print("\nfailed steps:")
        for r in failed:
            print(f"  {r.stage}/{r.step}: {r.detail}")
    return 1 if failed else 0
