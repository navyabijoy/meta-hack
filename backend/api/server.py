"""Gecko HTTP server: landing page, sign-in, workspace, report links, runs, and the bot.

Run: .venv/bin/uvicorn backend.api.server:app --port 8000

Access:
- Public: landing page, sign-in, report links (/r/{token}), static assets.
- Signed-in users: the workspace, and runs, files and actions for repos they can access on GitHub.
- Admins (GECKO_ADMINS, default the app owner): /ops, budget, GitHub App setup.
Auth turns on once GITHUB_CLIENT_SECRET is set; until then everything is open for local development.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse

from backend import auth, github_app, jobs, product
from backend.models import budget
from backend.pipeline import run as pipeline

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "runs"
WEB = ROOT / "web"
DEFAULT_SOURCE = os.environ.get("GECKO_DEFAULT_SOURCE", "navyabijoy/taskly")
BASE_URL = os.environ.get("GECKO_BASE_URL_PUBLIC", "http://localhost:8000").rstrip("/")
# GitHub repos the bot answers issues on (empty = every installed repo).
BOT_REPOS = set(filter(None, os.environ.get("GECKO_BOT_REPOS", "navyabijoy/taskly").split(",")))
MAX_UPLOAD = 300 * 1024 * 1024
VIDEO_TYPES = {".mp4", ".mov", ".m4v", ".webm"}

app = FastAPI(title="Gecko")


# ---------------------------------------------------------------- jobs and bot

def queue_run(video: Path, report: str, source: str, run_id: str, origin: str = "web",
              context: dict | None = None) -> str:
    (RUNS / run_id).mkdir(parents=True, exist_ok=True)
    (RUNS / run_id / "events.jsonl").touch()
    return jobs.enqueue(run_id, source, str(video), report, origin, context)


def execute(job: dict) -> dict:
    token = github_app.token_for(job["source"]) if github_app.configured() else None
    return pipeline.run(job["video"], job["source"], job["report"], job["id"], token=token, ref=job.get("ref"))


def on_done(job: dict, summary: dict) -> None:
    if job["origin"] in ("github", "report"):
        github_app.report(json.loads(job["context"]), summary, RUNS / job["id"])


@app.on_event("startup")
def _startup():
    jobs.start_workers(execute, on_done)
    github_app.start_bot(BOT_REPOS, queue_run)
    print(f"[auth] {'on' if auth.enabled() else 'OFF (set GITHUB_CLIENT_SECRET to require sign-in)'}")


# ---------------------------------------------------------------- access

def user(request: Request) -> dict | None:
    if not auth.enabled():
        return auth.DEV_USER
    return auth.session(request.cookies.get(auth.COOKIE))


def need_user(request: Request) -> dict:
    u = user(request)
    if not u:
        raise HTTPException(401, "Sign in to continue")
    return u


def need_admin(request: Request) -> dict:
    u = need_user(request)
    if not u["admin"]:
        raise HTTPException(403, "Admins only")
    return u


def need_repo(u: dict, repo: str | None) -> None:
    if not auth.can_see(u, repo):
        raise HTTPException(404, "Not found")  # don't reveal that it exists


def run_repo(run_id: str) -> str | None:
    job = jobs.get(run_id)
    if job:
        return job["source"]
    s = RUNS / run_id / "summary.json"
    return json.loads(s.read_text()).get("source") if s.exists() else None


def page_or_login(request: Request, file: str, admin: bool = False):
    u = user(request)
    if not u:
        nxt = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/login?next={quote(nxt)}", status_code=303)
    if admin and not u["admin"]:
        return RedirectResponse("/workspace", status_code=303)
    return HTMLResponse((WEB / file).read_text())


# ---------------------------------------------------------------- sign-in

@app.get("/login", response_class=HTMLResponse)
def login_page():
    return (WEB / "login.html").read_text()


@app.get("/auth/login")
def auth_login(next: str = "/workspace"):
    if not auth.enabled():
        return RedirectResponse(auth.safe_next(next), status_code=303)
    return RedirectResponse(auth.login_url(BASE_URL, next), status_code=303)


@app.get("/auth/callback")
def auth_callback(code: str = "", state: str = "", error: str = ""):
    if error or not code:
        return RedirectResponse("/login?error=" + quote(error or "cancelled"), status_code=303)
    try:
        sid, nxt = auth.finish_login(code, state, BASE_URL)
    except Exception as e:  # noqa: BLE001
        return RedirectResponse("/login?error=" + quote(str(e)[:200]), status_code=303)
    resp = RedirectResponse(nxt, status_code=303)
    resp.set_cookie(auth.COOKIE, sid, max_age=auth.SESSION_TTL, httponly=True, samesite="lax",
                    secure=BASE_URL.startswith("https://"), path="/")
    return resp


@app.get("/auth/logout")
def auth_logout(request: Request):
    auth.logout(request.cookies.get(auth.COOKIE))
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


@app.get("/api/workspace/me")
def ws_me(request: Request):
    u = user(request)
    slug = github_app.env("GITHUB_APP_SLUG")
    return {"auth_enabled": auth.enabled(), "signed_in": bool(u and u["login"]),
            "login": u["login"] if u else None, "avatar": u["avatar"] if u else None,
            "admin": bool(u and u["admin"]), "app_slug": slug,
            "install_url": f"https://github.com/apps/{slug}/installations/new" if slug else None}


# ---------------------------------------------------------------- runs

@app.post("/api/runs")
async def create_run(request: Request, video: UploadFile = File(...), report: str = Form(""),
                     source: str = Form(DEFAULT_SOURCE)):
    need_repo(need_user(request), source)
    run_id = time.strftime("%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    dest = await _save_upload(video, RUNS / run_id)
    queue_run(dest, report[:2000], source, run_id)
    return {"id": run_id}


async def _save_upload(video: UploadFile, folder: Path) -> Path:
    ext = Path(video.filename or "v.mp4").suffix.lower()
    if ext not in VIDEO_TYPES:
        raise HTTPException(400, f"unsupported video type {ext}; use MP4 or MOV")
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
    return dest


@app.get("/api/runs")
def list_runs(request: Request):
    need_admin(request)
    out = []
    for d in sorted(RUNS.iterdir(), reverse=True) if RUNS.exists() else []:
        s = d / "summary.json"
        if s.exists():
            j = json.loads(s.read_text())
            out.append({k: j.get(k) for k in ("id", "status", "branch", "usd", "seconds", "pr_url")}
                       | {"title": (j.get("plan") or {}).get("title")})
        elif (d / "events.jsonl").exists():
            job = jobs.get(d.name) or {}
            out.append({"id": d.name, "status": "queued" if job.get("status") == "queued" else "running"})
    return out


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, request: Request):
    need_repo(need_user(request), run_repo(run_id))
    d = _run_dir(run_id)
    events = [json.loads(l) for l in (d / "events.jsonl").read_text().splitlines() if l.strip()]
    summary = json.loads((d / "summary.json").read_text()) if (d / "summary.json").exists() else None
    job = jobs.get(run_id) or {}
    return {"id": run_id, "events": events, "summary": summary, "job_status": job.get("status"),
            "queue_position": jobs.queue_position(run_id)}


@app.get("/api/runs/{run_id}/stream")
async def stream(run_id: str, request: Request):
    need_repo(need_user(request), run_repo(run_id))
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
def open_pr(run_id: str, request: Request):
    from backend.pipeline import deliver

    need_repo(need_user(request), run_repo(run_id))
    d = _run_dir(run_id)
    summary = json.loads((d / "summary.json").read_text())
    token = github_app.token_for(deliver.repo_slug(Path(summary["repo"]))) if github_app.configured() else None
    try:
        pr = deliver.open_pr(summary, d, token=token)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e))
    summary["pr_url"] = pr
    (d / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    return {"pr_url": pr}


@app.get("/files/{path:path}")
def files(path: str, request: Request):
    u = need_user(request)
    head, _, rest = path.partition("/")
    base = {"runs": RUNS, "samples": ROOT / "samples"}.get(head)
    if base is None or not rest:
        raise HTTPException(404)
    p = (base / rest).resolve()
    if not str(p).startswith(str(base.resolve()) + os.sep) or "/.git/" in str(p) or p.suffix in (".db", ".pem") \
            or (p.suffix == ".jsonl" and p.name != "events.jsonl"):
        raise HTTPException(404)
    if head == "runs":
        need_repo(u, run_repo(p.relative_to(base.resolve()).parts[0]))
    elif not u["admin"]:
        raise HTTPException(404)
    if not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


# ---------------------------------------------------------------- report links (public)

@app.get("/r/{token}", response_class=HTMLResponse)
def report_page(token: str):
    return (WEB / "report.html").read_text()


def _report_repo(token: str) -> str:
    repo = product.repo_for_token(token)
    if not repo or not github_app.configured() or not github_app.repo_for(repo):
        raise HTTPException(404, "This report link isn't active. Ask the team for a new one.")
    if not product.settings(repo)["report_link"]:
        raise HTTPException(403, "The report link for this project is turned off")
    return repo


@app.get("/api/report/{token}")
def report_info(token: str):
    repo = _report_repo(token)
    s = product.settings(repo)
    return {"project": repo.split("/")[1], "accepting": product.fixes_this_month(repo) < s["monthly_fix_limit"]}


@app.post("/api/report/{token}")
async def report_submit(token: str, request: Request, video: UploadFile = File(...),
                        description: str = Form(...), reporter: str = Form("")):
    repo = _report_repo(token)
    if not description.strip():
        raise HTTPException(400, "Describe what went wrong in a sentence")
    try:
        product.rate_limit(f"{token}:{request.client.host if request.client else 'unknown'}")
    except PermissionError as e:
        raise HTTPException(429, str(e))
    run_id = time.strftime("%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    dest = await _save_upload(video, RUNS / run_id)
    try:
        r = product.submit_report(repo, dest, run_id, description[:2000], reporter[:100], queue_run)
        return {"issue_url": r["issue_url"]}
    except LookupError as e:
        shutil.rmtree(RUNS / run_id, ignore_errors=True)
        raise HTTPException(404, str(e))
    except PermissionError as e:
        shutil.rmtree(RUNS / run_id, ignore_errors=True)
        raise HTTPException(402 if "fixes" in str(e) else 403, str(e))


# ---------------------------------------------------------------- workspace API (signed in)

@app.get("/api/workspace/bugs")
def ws_bugs(request: Request, repo: str | None = None):
    return product.bugs(repo, allowed=need_user(request)["repos"])


@app.get("/api/workspace/repos")
def ws_repos(request: Request):
    return product.repos(allowed=need_user(request)["repos"])


@app.get("/api/workspace/impact")
def ws_impact(request: Request, repo: str | None = None, days: int = 30):
    return product.impact(repo, max(1, min(days, 365)), allowed=need_user(request)["repos"])


@app.patch("/api/workspace/repos/{owner}/{name}/settings")
async def ws_settings(owner: str, name: str, request: Request):
    repo = f"{owner}/{name}"
    need_repo(need_user(request), repo)
    body = await request.json()
    s = product.update_settings(repo, body.get("report_link"), body.get("monthly_fix_limit"),
                                bool(body.get("rotate_link")))
    return {k: s[k] for k in ("repo", "report_link", "monthly_fix_limit")} | {
        "report_link_url": f"/r/{s['report_token']}" if s["report_link"] else None}


# ---------------------------------------------------------------- internal (admins)

@app.get("/api/ops")
def ops(request: Request):
    """Aggregates for the internal ops page: runs, stage timings, queue, bot, Meta API budget."""
    need_admin(request)
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
def get_budget(request: Request):
    need_admin(request)
    return {"spent": round(budget.spent(), 4), "cap": budget.HARD_CAP_USD}


@app.get("/api/bot")
def bot_status(request: Request):
    need_admin(request)
    if not github_app.configured():
        return {"configured": False, "setup": f"{BASE_URL}/github/setup"}
    return {"configured": True, "slug": github_app.env("GITHUB_APP_SLUG"),
            "repos": [r.full_name for r in github_app.installed_repos()]}


@app.get("/github/setup", response_class=HTMLResponse)
def github_setup(request: Request):
    if github_app.configured():
        need_admin(request)
    return github_app.setup_page(BASE_URL)


@app.get("/github/callback")
def github_callback(code: str, state: str = ""):
    return RedirectResponse(github_app.finish_setup(code, state))


# ---------------------------------------------------------------- pages

@app.get("/", response_class=HTMLResponse)
@app.get("/welcome", response_class=HTMLResponse)
def landing():
    return (WEB / "landing.html").read_text()


@app.get("/workspace", response_class=HTMLResponse)
def workspace_page(request: Request):
    return page_or_login(request, "workspace.html")


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def run_page(run_id: str, request: Request):
    return page_or_login(request, "index.html")


@app.get("/ops", response_class=HTMLResponse)
@app.get("/ops/runs", response_class=HTMLResponse)
def ops_page(request: Request):
    return page_or_login(request, "index.html", admin=True)


@app.get("/static/{name:path}")
def static(name: str):
    p = (WEB / name).resolve()
    if not str(p).startswith(str(WEB.resolve()) + os.sep) or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


def _run_dir(run_id: str) -> Path:
    d = (RUNS / run_id).resolve()
    if not str(d).startswith(str(RUNS.resolve()) + os.sep) or not d.is_dir():
        raise HTTPException(404, "no such run")
    return d
