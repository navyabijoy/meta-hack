"""Dev helper: ingest -> understand -> ground -> reproduce for one sample. Usage: python -m scripts.try_full <bug>"""
import json, sys, shutil
from pathlib import Path
from backend.pipeline.ingest import ingest
from backend.pipeline import understand, ground, reproduce
from backend.models import budget
b = sys.argv[1]; out = Path("runs") / b
shutil.rmtree(out, ignore_errors=True)
ing = ingest(f"samples/{b}/report.mp4", out)
plan = understand.run(ing, out)
print("PLAN:", plan["title"], "|", plan["expected"])
for s in plan["steps"]: print("  ", s["id"], s["frame"], s["action"], s["target"]["name"], s["target"].get("sam_prompts"))
gr = ground.run(ing, plan, out)
for g in gr: print("  ground", g["step_id"], g["confidence"], g["reason"])
def ev(kind, **k):
    if kind in ("judge", "hypotheses"): print("EVENT", kind, json.dumps(k)[:500])
    if kind == "test": print("EVENT test", k["attempt"], "passed=", k["passed"], k["failure"], k["error"][:200].replace("\n", " "))
    if kind == "replay": print("EVENT replay", k["attempt"], "ok=", k["ok"], "variants=", k["variants"])
r = reproduce.run(ing, plan, gr, Path("demo-app"), "http://localhost:5173", out, ev)
print("REPRODUCED:", r.reproduced, "|", r.final.why)
print(r.final.test_code)
print(f"spend ${budget.spent():.3f}")
