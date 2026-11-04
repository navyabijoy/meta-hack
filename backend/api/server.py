"""Repro HTTP server: start runs, stream their events, serve the run page and artifacts.

Run: .venv/bin/uvicorn backend.api.server:app --port 8000
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from backend import github_app, jobs

from backend.models import budget
from backend.pipeline import run as pipeline

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "runs"
WEB = ROOT / "web"
DEFAULT_SOURCE = os.environ.get("REPRO_DEFAULT_SOURCE", "navyabijoy/taskly")
BASE_URL = os.environ.get("REPRO_BASE_URL_PUBLIC", "http://localhost:8000")
# GitHub repos the bot answers issues on.
BOT_REPOS = set(filter(None, os.environ.get("REPRO_BOT_REPOS", "navyabijoy/taskly").split(",")))
MAX_UPLOAD = 300 * 1024 * 1024
VIDEO_TYPES = {".mp4", ".mov", ".m4v", ".webm"}

app = FastAPI(title="Repro")


def queue_run(video: Path, report: str, source: str, run_id: str, origin: str = "web",
              context: dict | None = None) -> str:
    (RUNS / run_id).mkdir(parents=True, exist_ok=True)
    (RUNS / run_id / "events.jsonl").touch()
    return jobs.enqueue(run_id, source, str(video), report, origin, context)


def execute(job: dict) -> dict:
    token = github_app.token_for(job["source"]) if github_app.configured() else None
    return pipeline.run(job["video"], job["source"], job["report"], job["id"], token=token, ref=job.get("ref"))


def on_done(job: dict, summary: dict) -> None:
    if job["origin"] == "github":
        github_app.report(json.loads(job["context"]), summary, RUNS / job["id"])


@app.on_event("startup")
def _startup():
    jobs.start_workers(execute, on_done)
    github_app.start_bot(BOT_REPOS, queue_run)


@app.post("/api/runs")
async def create_run(video: UploadFile = File(...), report: str = Form(""), source: str = Form(DEFAULT_SOURCE)):
    ext = Path(video.filename or "v.mp4").suffix.lower()
    if ext not in VIDEO_TYPES:
        raise HTTPException(400, f"unsupported video type {ext}; use MP4 or MOV")
    run_id = time.strftime("%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    folder = RUNS / run_id
    folder.mkdir(parents=True)
    dest = folder / ("input" + ext)
    size = 0
    with dest.open("wb") as f:
        while chunk := await video.read(1 << 20):
            size += len(chunk)
            if size > MAX_UPLOAD:
                f.close()
                shutil.rmtree(folder)
                raise HTTPException(413, "video is larger than 300 MB")
            f.write(chunk)
    queue_run(dest, report, source, run_id)
    return {"id": run_id}


@app.get("/api/runs")
def list_runs():
    out = []
    for d in sorted(RUNS.iterdir(), reverse=True) if RUNS.exists() else []:
        s = d / "summary.json"
        if s.exists():
            j = json.loads(s.read_text())
            out.append({k: j.get(k) for k in ("id", "status", "branch", "usd", "seconds", "pr_url")}
                       | {"title": (j.get("plan") or {}).get("title")})
        elif (d / "events.jsonl").exists():
            job = jobs.get(d.name) or {}
            st = job.get("status", "running")
            out.append({"id": d.name, "status": "queued" if st == "queued" else "running"})
    return out


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    d = _run_dir(run_id)
    events = [json.loads(l) for l in (d / "events.jsonl").read_text().splitlines() if l.strip()]
    summary = json.loads((d / "summary.json").read_text()) if (d / "summary.json").exists() else None
    job = jobs.get(run_id) or {}
    return {"id": run_id, "events": events, "summary": summary, "job_status": job.get("status"),
            "queue_position": jobs.queue_position(run_id)}


@app.get("/api/runs/{run_id}/stream")
async def stream(run_id: str, request: Request):
    d = _run_dir(run_id)

    async def gen():
        pos = 0
        while True:
            if await request.is_disconnected():
                return
            path = d / "events.jsonl"
            lines = path.read_text().splitlines() if path.exists() else []
            for line in lines[pos:]:
                yield f"data: {line}\n\n"
                if json.loads(line).get("kind") == "done":
                    return
            pos = len(lines)
            await asyncio.sleep(0.4)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/runs/{run_id}/pr")
def open_pr(run_id: str):
    from backend.pipeline import deliver

    d = _run_dir(run_id)
    summary = json.loads((d / "summary.json").read_text())
    token = None
    if github_app.configured():
        token = github_app.token_for(deliver.repo_slug(Path(summary["repo"])))
    try:
        pr = deliver.open_pr(summary, d, token=token)
    except Exception as e:
        raise HTTPException(400, str(e))
    summary["pr_url"] = pr
    (d / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    return {"pr_url": pr}


@app.get("/github/setup", response_class=HTMLResponse)
def github_setup():
    return github_app.setup_page(BASE_URL)


@app.get("/github/callback")
def github_callback(code: str, state: str = ""):
    install_url = github_app.finish_setup(code, state)
    return RedirectResponse(install_url)


@app.get("/api/bot")
def bot_status():
    if not github_app.configured():
        return {"configured": False, "setup": f"{BASE_URL}/github/setup"}
    return {"configured": True, "slug": github_app.env("GITHUB_APP_SLUG"),
            "repos": [r.full_name for r in github_app.installed_repos()]}


@app.get("/api/dashboard")
def dashboard():
    """Aggregates for the team dashboard, computed from run folders, the job table and the ledger."""
    runs = []
    for d in sorted(RUNS.iterdir()) if RUNS.exists() else []:
        sf = d / "summary.json"
        if not d.is_dir() or not sf.exists():
            continue
        s = json.loads(sf.read_text())
        stages, t_start = {}, {}
        ev = d / "events.jsonl"
        for line in ev.read_text().splitlines() if ev.exists() else []:
            e = json.loads(line)
            if e.get("kind") == "stage":
                if e["status"] == "start":
                    t_start[e["stage"]] = e["t"]
                elif e["stage"] in t_start:
                    stages[e["stage"]] = round(e["t"] - t_start[e["stage"]], 1)
        src = s.get("source") or Path(s.get("repo") or "").name
        runs.append({
            "id": s["id"], "status": s.get("status"), "title": (s.get("plan") or {}).get("title"),
            "source": src, "seconds": s.get("seconds"), "usd": s.get("usd"), "pr_url": s.get("pr_url"),
            "attempts": s.get("attempts"), "fix_rounds": s.get("fix_rounds"), "stages": stages,
            "eval": s["id"].startswith("eval-"), "origin": "github" if "-i" in s["id"][-5:] else "web",
            "day": time.strftime("%Y-%m-%d", time.localtime(d.stat().st_ctime)),
        })
    q = jobs.db().execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status").fetchall()
    bot = {"configured": github_app.configured()}
    if bot["configured"]:
        try:
            bot["repos"] = [r.full_name for r in github_app.installed_repos()]
        except Exception as e:  # noqa: BLE001
            bot["error"] = str(e)
    return {
        "runs": runs,
        "queue": {r["status"]: r["n"] for r in q},
        "workers": jobs.WORKERS,
        "budget": {"spent": round(budget.spent(), 4), "cap": budget.HARD_CAP_USD, "run_cap": budget.RUN_CAP_USD},
        "bot": bot,
    }


@app.get("/api/budget")
def get_budget():
    return {"spent": round(budget.spent(), 4), "cap": budget.HARD_CAP_USD}


@app.get("/files/{path:path}")
def files(path: str):
    p = (ROOT / path).resolve()
    allowed = [RUNS.resolve(), (ROOT / "samples").resolve()]
    if not any(str(p).startswith(str(a) + os.sep) for a in allowed) or "/.git/" in str(p) or p.suffix in (".db", ".pem"):
        raise HTTPException(404)
    if not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
@app.get("/runs/{run_id}", response_class=HTMLResponse)
def page(run_id: str | None = None):
    return (WEB / "index.html").read_text()


@app.get("/static/{name:path}")
def static(name: str):
    p = (WEB / name).resolve()
    if not str(p).startswith(str(WEB.resolve())) or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


def _run_dir(run_id: str) -> Path:
    d = (RUNS / run_id).resolve()
    if not str(d).startswith(str(RUNS.resolve())) or not d.is_dir():
        raise HTTPException(404, "no such run")
    return d
