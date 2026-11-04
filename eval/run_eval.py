"""Run Gecko on every case in eval/cases.yml and score it.

A case passes when:
- expect=fixed: Gecko reproduced it, its fix was verified, AND the hidden
  hand-written reference test passes on Gecko's branch (so the fix is right,
  not just tuned to Gecko's own test).
- expect=not-reproduced: Gecko declined to reproduce (no PR).

Usage: .venv/bin/python -m eval.run_eval [case-id ...]
Writes eval/results.md and eval/results.json.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import yaml

from backend.pipeline import run as pipeline
from backend.pipeline.reproduce import run_test
from backend.pipeline.workspace import RepoConfig, Workspace

ROOT = Path(__file__).resolve().parents[1]


def check_reference(summary: dict, reference: Path) -> dict:
    """Run the hidden reference test against Gecko's fixed branch, in its workspace."""
    run_dir = ROOT / "runs" / summary["id"]
    ws = Workspace(summary["source"], run_dir)
    cfg = RepoConfig.load(ws.dir)
    if not (ws.dir / "node_modules").exists():
        ws._install(cfg)
    dest = ws.dir / "tests" / "reference" / reference.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(reference, dest)
    try:
        ws.start(cfg)
        return run_test(ws.dir, dest, ws.env(), cfg.test)
    finally:
        ws.stop()
        dest.unlink(missing_ok=True)


def main(only: list[str]) -> None:
    cases = yaml.safe_load((ROOT / "eval" / "cases.yml").read_text())
    if only:
        cases = [c for c in cases if c["id"] in only]
    results = []
    for c in cases:
        print(f"\n=== {c['id']}")
        s = pipeline.run(str(ROOT / c["video"]), c["source"], c.get("report", ""), f"eval-{time.strftime('%m%d-%H%M%S')}-{c['id']}")
        r = {"id": c["id"], "expect": c["expect"], "status": s["status"], "seconds": s.get("seconds"),
             "usd": s.get("usd"), "attempts": s.get("attempts"), "fix_rounds": s.get("fix_rounds"),
             "run": s["id"], "reference": None}
        if c["expect"] == "fixed":
            if s["status"] == "fixed" and c.get("reference"):
                ref = check_reference(s, ROOT / c["reference"])
                r["reference"] = "pass" if ref["passed"] else "fail"
                r["passed"] = ref["passed"]
            else:
                r["passed"] = False
        else:
            r["passed"] = s["status"] == "not-reproduced"
        results.append(r)
        print(json.dumps(r))

    if only and (ROOT / "eval" / "results.json").exists():
        # Re-running some cases: keep the latest result for every other case.
        prev = {r["id"]: r for r in json.loads((ROOT / "eval" / "results.json").read_text())}
        prev.update({r["id"]: r for r in results})
        order = [c["id"] for c in yaml.safe_load((ROOT / "eval" / "cases.yml").read_text())]
        results = [prev[i] for i in order if i in prev]
        cases = yaml.safe_load((ROOT / "eval" / "cases.yml").read_text())
    (ROOT / "eval" / "results.json").write_text(json.dumps(results, indent=1))
    write_markdown(results, cases)


def write_markdown(results: list[dict], cases: list[dict]) -> None:
    notes = {c["id"]: c.get("notes", "") for c in cases}
    n = len(results)
    ok = sum(r["passed"] for r in results)
    fixed_cases = [r for r in results if r["expect"] == "fixed"]
    lines = [
        "# Gecko eval results",
        "",
        f"Run on {time.strftime('%Y-%m-%d %H:%M')}. **{ok} of {n} cases correct.**",
        "",
        f"- Reproduced and fixed with the hidden reference test passing: {sum(r['passed'] for r in fixed_cases)} of {len(fixed_cases)}",
        f"- Median time, video to verified fix: {_median([r['seconds'] for r in fixed_cases if r['status'] == 'fixed'])} s",
        f"- Median Meta API cost per run: ${_median([r['usd'] for r in results if r['usd'] is not None], 3)}",
        "",
        "| Case | Expected | Gecko's result | Reference test | Replay attempts | Fix rounds | Time | Cost | Correct |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        lines.append(f"| {r['id']} | {r['expect']} | {r['status']} | {r['reference'] or '—'} | {r['attempts'] or '—'} | "
                     f"{r['fix_rounds'] or '—'} | {round(r['seconds'] or 0)} s | ${(r['usd'] or 0):.2f} | {'yes' if r['passed'] else 'no'} |")
    lines += ["", "## Cases", ""] + [f"- **{k}**: {v}" for k, v in notes.items()]
    (ROOT / "eval" / "results.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def _median(xs: list, digits: int = 0):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return "—"
    m = xs[len(xs) // 2] if len(xs) % 2 else (xs[len(xs) // 2 - 1] + xs[len(xs) // 2]) / 2
    return round(m, digits) if digits else round(m)


if __name__ == "__main__":
    main(sys.argv[1:])
