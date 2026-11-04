"""Repro HTTP server: start runs, stream their events, serve the run page and artifacts.

Run: .venv/bin/uvicorn backend.api.server:app --port 8000
"""

from __future__ import annotations

import asyncio
import json
import shutil
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from backend.models import budget
from backend.pipeline import run as pipeline

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "runs"
WEB = ROOT / "web"
DEFAULT_REPO = str(ROOT / "demo-app")
DEFAULT_URL = "http://localhost:5173"

app = FastAPI(title="Repro")
_lock = threading.Lock()  # one run at a time: runs share the app repo's working tree


def start_run(video: Path, report: str, repo: str, url: str, run_id: str, meta: dict | None = None) -> None:
    def work():
        with _lock:
            s = pipeline.run(str(video), repo, url, report, run_id)
        if meta and meta.get("on_done"):
            meta["on_done"](s)

    (RUNS / run_id).mkdir(parents=True, exist_ok=True)
    (RUNS / run_id / "events.jsonl").touch()
    threading.Thread(target=work, daemon=True).start()


@app.post("/api/runs")
async def create_run(video: UploadFile = File(...), report: str = Form(""), repo: str = Form(DEFAULT_REPO),
                     url: str = Form(DEFAULT_URL)):
    run_id = time.strftime("%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    folder = RUNS / run_id
    folder.mkdir(parents=True)
    dest = folder / ("input" + Path(video.filename or "v.mp4").suffix)
    with dest.open("wb") as f:
        shutil.copyfileobj(video.file, f)
    start_run(dest, report, repo, url, run_id)
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
            out.append({"id": d.name, "status": "running"})
    return out


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    d = _run_dir(run_id)
    events = [json.loads(l) for l in (d / "events.jsonl").read_text().splitlines() if l.strip()]
    summary = json.loads((d / "summary.json").read_text()) if (d / "summary.json").exists() else None
    return {"id": run_id, "events": events, "summary": summary}


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
    try:
        pr = deliver.open_pr(summary, d)
    except Exception as e:
        raise HTTPException(400, str(e))
    summary["pr_url"] = pr
    (d / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    return {"pr_url": pr}


@app.get("/api/budget")
def get_budget():
    return {"spent": round(budget.spent(), 4), "cap": budget.HARD_CAP_USD}


@app.get("/files/{path:path}")
def files(path: str):
    p = (ROOT / path).resolve()
    allowed = [RUNS.resolve(), (ROOT / "samples").resolve()]
    if not any(str(p).startswith(str(a)) for a in allowed) and not str(p).startswith(str(Path(DEFAULT_REPO).resolve())):
        raise HTTPException(404)
    if not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


@app.get("/", response_class=HTMLResponse)
@app.get("/runs/{run_id}", response_class=HTMLResponse)
def page(run_id: str | None = None):
    return (WEB / "index.html").read_text()


@app.get("/static/{name}")
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
