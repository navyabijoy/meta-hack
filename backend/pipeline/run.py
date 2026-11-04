"""Run the whole Repro pipeline on one bug video.

Every stage emits events to runs/<id>/events.jsonl, which the run page streams.
In the app's repo, the failing test and the fix land as two commits on a
repro/<slug> branch, so reviewers can check out the first commit and watch it fail.

Usage: python -m backend.pipeline.run <video> --repo demo-app|owner/name [--report "..."]
"""

from __future__ import annotations

import argparse
import json
import time
import traceback
import uuid
from pathlib import Path

from backend.models import budget
from backend.pipeline import fix, ground, reproduce, understand
from backend.pipeline.fix import git
from backend.pipeline.ingest import ingest
from backend.pipeline.workspace import Target, Workspace
from concurrent.futures import ThreadPoolExecutor

RUNS = Path(__file__).resolve().parents[2] / "runs"
AUTHOR = ["-c", "user.name=repro[bot]", "-c", "user.email=repro-bot@users.noreply.github.com"]


class Events:
    def __init__(self, path: Path, run_id: str | None = None):
        self.path = path
        self.run_id = run_id
        self.t0 = time.time()

    def __call__(self, kind: str, **data) -> None:
        rec = {"t": round(time.time() - self.t0, 2), "kind": kind, "usd": round(budget.spent(self.run_id), 4), **data}
        with self.path.open("a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        print(f"[{rec['t']:6.1f}s] {kind}", {k: v for k, v in data.items() if k in ("stage", "status", "why", "round", "accepted", "reproduced", "fixed", "summary")})


def run(video: str, source: str, report: str = "", run_id: str | None = None, *,
        url: str | None = None, token: str | None = None, ref: str | None = None) -> dict:
    """Reproduce and fix the bug in `video` against the app in `source`.

    source: a local git checkout or a GitHub "owner/name". Repro clones it into the
    run folder, installs it and starts it on a free port (see workspace.py), so the
    developer's own checkout is never touched. Pass `url` only to test against an
    app that is already running from `source` itself (development mode).
    """
    run_id = run_id or time.strftime("%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    out = RUNS / run_id
    out.mkdir(parents=True, exist_ok=True)
    budget.set_run(run_id)
    emit = Events(out / "events.jsonl", run_id)
    spend0 = 0.0
    summary: dict = {"id": run_id, "video": str(Path(video).resolve()), "source": source,
                     "report": report, "status": "running"}
    ws: Workspace | None = None

    try:
        emit("stage", stage="ingest", status="start", video=str(Path(video).resolve()), report=report)
        # Clone, install and boot the app while the models read the video.
        pool = ThreadPoolExecutor(max_workers=1)
        if url:
            target_future = pool.submit(lambda: Target.local(source, url))
        else:
            ws = Workspace(source, out, ref)
            target_future = pool.submit(_boot, ws, token, emit)

        ing = ingest(video, out)
        emit("stage", stage="ingest", status="done", frames=len(ing.frames), duration=ing.duration,
             sheets=ing.sheets, crop=ing.crop)

        emit("stage", stage="understand", status="start")
        plan = understand.run(ing, out, report)
        emit("stage", stage="understand", status="done", plan=plan)
        summary["plan"] = plan

        emit("stage", stage="ground", status="start")
        grounding = ground.run(ing, plan, out)
        emit("stage", stage="ground", status="done", grounding=grounding)

        tgt: Target = target_future.result(timeout=900)
        pool.shutdown(wait=False)
        repo_p = tgt.repo
        summary["repo"] = str(repo_p)
        if git(repo_p, "status", "--porcelain").strip():
            raise RuntimeError(f"{repo_p} has uncommitted changes")
        base = git(repo_p, "rev-parse", "--abbrev-ref", "HEAD").strip()
        branch = f"repro/{reproduce.slug(plan.get('title') or plan['summary'])[:40]}-{run_id[-4:]}"
        git(repo_p, "checkout", "-q", "-b", branch)
        summary["branch"], summary["base"] = branch, base

        emit("stage", stage="reproduce", status="start")
        rep = reproduce.run(ing, plan, grounding, tgt, out, emit)
        final = rep.final
        summary.update(reproduced=rep.reproduced, hypotheses=rep.hypotheses, attempts=len(rep.attempts),
                       reproduce_why=final.why if final else "")
        emit("stage", stage="reproduce", status="done", reproduced=rep.reproduced, why=final.why if final else "")

        if not rep.reproduced:
            summary["status"] = "not-reproduced"
            return _finish(summary, out, emit, spend0)

        test_path = Path(final.test_path)
        summary["test_path"] = str(test_path.relative_to(repo_p))
        summary["test_code"] = final.test_code
        summary["failure"] = final.run["error"]
        git(repo_p, "add", summary["test_path"])
        git(repo_p, *AUTHOR, "commit", "-qm", f"test: reproduce \"{plan.get('title')}\"\n\nGenerated by Repro from a screen recording. This test fails on the current code.")
        summary["test_commit"] = git(repo_p, "rev-parse", "HEAD").strip()

        emit("stage", stage="fix", status="start")
        fx = fix.run(tgt, test_path, plan, final.run["error"], out, emit)
        summary.update(fixed=fx.fixed, fix_rounds=len(fx.rounds), fix_explanation=fx.explanation)
        emit("stage", stage="fix", status="done", fixed=fx.fixed, summary=fx.explanation)
        if fx.fixed:
            summary["diff"] = fx.rounds[-1].diff
            git(repo_p, "add", "-A")
            git(repo_p, *AUTHOR, "commit", "-qm", f"fix: {plan.get('title')}\n\n{fx.explanation}\n\nWritten by Muse Code; verified by Repro.")
            summary["fix_commit"] = git(repo_p, "rev-parse", "HEAD").strip()
            summary["status"] = "fixed"
        else:
            git(repo_p, "checkout", "-q", "--", ".")
            summary["status"] = "reproduced"
        return _finish(summary, out, emit, spend0)
    except Exception as e:
        summary["status"] = "error"
        summary["error"] = f"{type(e).__name__}: {e}"
        emit("error", error=summary["error"], trace=traceback.format_exc()[-2000:])
        return _finish(summary, out, emit, spend0)
    finally:
        if ws:
            ws.stop()


def _boot(ws: Workspace, token: str | None, emit) -> Target:
    emit("stage", stage="workspace", status="start", source=ws.source)
    cfg = ws.prepare(token)
    url = ws.start(cfg)
    emit("stage", stage="workspace", status="done", url=url, port=ws.port)
    return Target(ws.dir, url, cfg, ws.env())


def _finish(summary: dict, out: Path, emit: Events, spend0: float) -> dict:
    summary["usd"] = round(budget.spent(emit.run_id) - spend0, 4)
    summary["seconds"] = round(time.time() - emit.t0, 1)
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    emit("done", status=summary["status"], usd=summary["usd"])
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--repo", default="demo-app", help="local checkout or GitHub owner/name")
    ap.add_argument("--url", default=None, help="test an already-running app in --repo (dev mode)")
    ap.add_argument("--report", default="")
    a = ap.parse_args()
    s = run(a.video, a.repo, a.report, url=a.url)
    print(json.dumps({k: s.get(k) for k in ("id", "status", "branch", "usd", "seconds", "fix_explanation")}, indent=1))
