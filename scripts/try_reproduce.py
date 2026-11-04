"""Dev helper: run the reproduce stage on a run folder. Usage: python -m scripts.try_reproduce <run>"""
import json, sys
from pathlib import Path
from backend.pipeline.ingest import Ingested
from backend.pipeline import reproduce
from backend.models import budget
out = Path("runs") / sys.argv[1]; ing = Ingested.load(out / "ingest.json")
plan = json.loads((out / "plan.json").read_text()); gr = json.loads((out / "grounding.json").read_text())
def ev(kind, **k):
    if kind in ("judge", "hypotheses"): print("EVENT", kind, json.dumps(k)[:600])
    if kind == "test": print("EVENT test attempt", k["attempt"], "passed=", k["passed"], k["failure"], k["error"][:300])
    if kind == "replay": print("EVENT replay attempt", k["attempt"], "ok=", k["ok"], "variants=", k["variants"])
r = reproduce.run(ing, plan, gr, Path("demo-app"), "http://localhost:5173", out, ev)
print("REPRODUCED:", r.reproduced, "|", r.final.why)
print(r.final.test_code)
print(f"spend ${budget.spent():.3f}")
