"""Stage 5, Replay: run the grounded steps in a real browser via backend/browser/replay.mjs."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

DRIVER = Path(__file__).resolve().parents[1] / "browser" / "replay.mjs"


def build_steps(plan: dict, grounding: list[dict], variants: dict[int, str] | None = None) -> list[dict]:
    g_by_id = {g["step_id"]: g for g in grounding}
    steps = []
    for s in plan["steps"]:
        g = g_by_id.get(s["id"], {})
        t = s.get("target", {})
        steps.append({
            "id": s["id"],
            "action": s["action"],
            "text": s.get("text", ""),
            "role": t.get("role"),
            "name": t.get("name"),
            "which": t.get("which", ""),
            "ordinal": g.get("ordinal"),
            "count": g.get("count", 0),
            "variant": (variants or {}).get(s["id"]),
        })
    return steps


def replay(url: str, steps: list[dict], out_dir: Path, fresh_state: list[str] | None = None) -> dict:
    payload = json.dumps({"url": url, "outDir": str(Path(out_dir).resolve()), "steps": steps,
                          "freshState": fresh_state if fresh_state is not None else ["localStorage"]})
    proc = subprocess.run(
        ["node", str(DRIVER)], input=payload, capture_output=True, text=True,
        cwd=DRIVER.parent, timeout=180,
    )
    if proc.returncode != 0:
        return {"ok": False, "steps": [], "error": proc.stderr.strip().splitlines()[-1:] or ["driver failed"]}
    return json.loads(proc.stdout)
