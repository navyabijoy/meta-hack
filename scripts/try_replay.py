"""Dev helper: replay a run's plan + grounding. Usage: python -m scripts.try_replay <run> [step=variant ...]"""
import json, sys
from pathlib import Path
from backend.pipeline import replay
out = Path("runs") / sys.argv[1]
variants = {int(a.split("=")[0]): a.split("=")[1] for a in sys.argv[2:]}
plan = json.loads((out / "plan.json").read_text()); gr = json.loads((out / "grounding.json").read_text())
r = replay.replay("http://localhost:5173", replay.build_steps(plan, gr, variants), out / "replay")
print("ok:", r["ok"], r.get("error"))
for s in r["steps"]:
    print(s["id"], s.get("ok"), s.get("confidence"), "|", s.get("reason"), "|", s.get("code"), s.get("error", ""))
print("FINAL TEXT:", (r.get("text") or "").replace("\n", " / "))
