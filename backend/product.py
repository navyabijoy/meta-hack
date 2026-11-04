"""Customer-facing product layer: report links, the bugs inbox, repo setup, impact and settings.

Roles:
- Reporter (support, QA, PM, a customer): sends a recording through a project's report
  link. Never needs GitHub. Gecko files the GitHub issue for them.
- Developer: gets the issue answered with a PR (failing test + verified fix).
- Admin: connects repos, shares report links, sets a monthly fix limit, sees impact.
"""

from __future__ import annotations

import base64
import json
import os
import re
import statistics
import subprocess
import time
from pathlib import Path

import httpx

from backend import github_app, jobs

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"
HOURS_SAVED_PER_FIX = float(os.environ.get("GECKO_HOURS_SAVED_PER_FIX", "1.5"))
EVIDENCE_BRANCH = "gecko-evidence"
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


# ---------------------------------------------------------------- settings

def _db():
    c = jobs.db()
    c.execute("""CREATE TABLE IF NOT EXISTS repo_settings (
        repo TEXT PRIMARY KEY, report_link INTEGER DEFAULT 1, monthly_fix_limit INTEGER DEFAULT 50,
        updated REAL)""")
    return c


def settings(repo: str) -> dict:
    r = _db().execute("SELECT * FROM repo_settings WHERE repo=?", (repo,)).fetchone()
    return dict(r) if r else {"repo": repo, "report_link": 1, "monthly_fix_limit": 50}


def update_settings(repo: str, report_link: bool | None = None, monthly_fix_limit: int | None = None) -> dict:
    cur = settings(repo)
    if report_link is not None:
        cur["report_link"] = int(bool(report_link))
    if monthly_fix_limit is not None:
        cur["monthly_fix_limit"] = max(0, int(monthly_fix_limit))
    _db().execute("INSERT OR REPLACE INTO repo_settings (repo, report_link, monthly_fix_limit, updated) VALUES (?,?,?,?)",
                  (repo, cur["report_link"], cur["monthly_fix_limit"], time.time()))
    return settings(repo)


def fixes_this_month(repo: str) -> int:
    start = time.mktime(time.strptime(time.strftime("%Y-%m-01"), "%Y-%m-%d"))
    return _db().execute("SELECT COUNT(*) FROM jobs WHERE source=? AND status='fixed' AND finished>=?",
                         (repo, start)).fetchone()[0]


def check_quota(repo: str) -> None:
    s = settings(repo)
    if fixes_this_month(repo) >= s["monthly_fix_limit"]:
        raise PermissionError(f"{repo} has used its {s['monthly_fix_limit']} fixes for this month")


# ---------------------------------------------------------------- report link

def evidence_put(repo: github_app.Repo, path: str, data: bytes, message: str) -> str:
    """Store a file on the repo's gecko-evidence branch (created empty if missing); return its raw URL."""
    try:
        repo.get(f"/branches/{EVIDENCE_BRANCH}")
    except httpx.HTTPStatusError as e:
        if e.response.status_code != 404:
            raise
        commit = repo.post("/git/commits", {"message": "Gecko evidence branch", "tree": EMPTY_TREE, "parents": []})
        repo.post("/git/refs", {"ref": f"refs/heads/{EVIDENCE_BRANCH}", "sha": commit["sha"]})
    repo.put(f"/contents/{path}", {"message": message, "branch": EVIDENCE_BRANCH,
                                   "content": base64.b64encode(data).decode()})
    return f"https://raw.githubusercontent.com/{repo.full_name}/{EVIDENCE_BRANCH}/{path}"


def report_gif(video: Path, out: Path) -> Path:
    vf = "fps=8,scale=560:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-t", "20", "-i", str(video), "-vf", vf, str(out)], check=True)
    return out


def submit_report(full_name: str, video: Path, run_id: str, description: str, reporter: str,
                  queue_run) -> dict:
    """A reporter sent a recording through the report link: file the issue, then run."""
    repo = github_app.repo_for(full_name)
    if not repo:
        raise LookupError(f"Gecko is not installed on {full_name}")
    if not settings(full_name)["report_link"]:
        raise PermissionError("the report link for this project is turned off")
    check_quota(full_name)

    gif = report_gif(video, video.with_name("report.gif"))
    gif_url = evidence_put(repo, f"{run_id}/report.gif", gif.read_bytes(), f"report recording for {run_id}")
    first_line = (description.strip().splitlines() or ["Bug report"])[0][:90]
    title = first_line if len(first_line) > 8 else f"Bug report: {first_line}"
    who = reporter.strip() or "a reporter"
    body = (f"{description.strip()}\n\n<img src=\"{gif_url}\" width=\"480\" alt=\"Screen recording of the bug\">\n\n"
            f"<sub>Reported by {who} through Gecko's report link.</sub>")
    issue = repo.post("/issues", {"title": title, "body": body})
    comment = repo.post(f"/issues/{issue['number']}/comments", {"body": (
        "**On it.** Watching the recording, replaying it in a real browser, and writing a test "
        "that fails the same way. I'll update this comment.")})

    # The poller must not pick this issue up a second time.
    state = github_app.load_state()
    state["handled"][f"{full_name}#{issue['number']}"] = time.time()
    github_app.save_state(state)

    queue_run(video, f"{title}\n\n{description}", full_name, run_id, "report",
              {"repo": full_name, "issue": issue["number"], "comment_id": comment["id"], "reporter": who,
               "issue_url": issue["html_url"]})
    return {"id": run_id, "issue_url": issue["html_url"]}


# ---------------------------------------------------------------- inbox, repos, impact

_pr_cache: dict[str, tuple[float, str]] = {}


def pr_state(pr_url: str | None) -> str | None:
    """open | merged | closed, cached for a minute."""
    if not pr_url:
        return None
    hit = _pr_cache.get(pr_url)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    m = re.search(r"github\.com/([^/]+/[^/]+)/pull/(\d+)", pr_url)
    state = None
    if m:
        repo = github_app.repo_for(m.group(1)) if github_app.configured() else None
        try:
            if repo:
                pr = repo.get(f"/pulls/{m.group(2)}")
            else:
                pr = httpx.get(f"https://api.github.com/repos/{m.group(1)}/pulls/{m.group(2)}", timeout=10).json()
            state = "merged" if pr.get("merged_at") else pr.get("state")
        except Exception:  # noqa: BLE001
            state = None
    _pr_cache[pr_url] = (time.time(), state)
    return state


def bugs(repo: str | None = None, include_eval: bool = False) -> list[dict]:
    rows = jobs.db().execute("SELECT * FROM jobs ORDER BY created DESC").fetchall()
    known = {r["id"] for r in rows}
    out = []
    for r in rows:
        r = dict(r)
        if repo and r["source"] != repo:
            continue
        out.append(_bug(r["id"], r))
    # Runs made outside the queue (CLI, eval) only exist as folders.
    for d in sorted(RUNS.glob("*/summary.json"), reverse=True):
        rid = d.parent.name
        if rid in known or (rid.startswith("eval-") and not include_eval):
            continue
        b = _bug(rid, None)
        if not repo or b["repo"] == repo:
            out.append(b)
    return out


def _bug(run_id: str, job: dict | None) -> dict:
    d = RUNS / run_id
    s = json.loads((d / "summary.json").read_text()) if (d / "summary.json").exists() else {}
    ctx = json.loads(job["context"]) if job and job.get("context") else {}
    origin = (job or {}).get("origin") or ("eval" if run_id.startswith("eval-") else "upload")
    status = s.get("status") or (job or {}).get("status") or "running"
    plan = s.get("plan") or {}
    return {
        "id": run_id,
        "title": plan.get("title") or (((job or {}).get("report") or "").splitlines() or ["New bug report"])[0][:90],
        "repo": s.get("source") or (job or {}).get("source"),
        "status": status,  # queued | running | fixed | reproduced | not-reproduced | error
        "origin": {"web": "upload", "github": "github-issue", "report": "report-link"}.get(origin, origin),
        "reporter": ctx.get("reporter"),
        "issue_url": ctx.get("issue_url") or (f"https://github.com/{ctx['repo']}/issues/{ctx['issue']}" if ctx.get("issue") else None),
        "pr_url": s.get("pr_url"),
        "pr_state": pr_state(s.get("pr_url")),
        "expected": plan.get("expected"),
        "actual": plan.get("actual"),
        "created": (job or {}).get("created") or d.stat().st_ctime,
        "seconds": s.get("seconds"),
        "why": s.get("reproduce_why") if status == "not-reproduced" else s.get("error"),
    }


def repos() -> list[dict]:
    out = []
    for r in github_app.installed_repos() if github_app.configured() else []:
        configured = None
        try:
            r.get("/contents/.gecko.yml")
            configured = True
        except httpx.HTTPStatusError:
            try:
                r.get("/contents/.repro.yml")
                configured = True
            except httpx.HTTPStatusError:
                configured = False
        st = settings(r.full_name)
        out.append({
            "repo": r.full_name, "configured": configured,
            "report_link": f"/r/{r.full_name}" if st["report_link"] else None,
            "monthly_fix_limit": st["monthly_fix_limit"], "fixes_this_month": fixes_this_month(r.full_name),
        })
    return out


def impact(repo: str | None = None, days: int = 30) -> dict:
    since = time.time() - days * 86400
    items = [b for b in bugs(repo) if b["created"] >= since and b["origin"] != "eval"]
    fixed = [b for b in items if b["status"] == "fixed"]
    times = [b["seconds"] for b in fixed if b["seconds"]]
    return {
        "days": days,
        "reports": len(items),
        "fixed": len(fixed),
        "merged": sum(1 for b in fixed if b["pr_state"] == "merged"),
        "declined": sum(1 for b in items if b["status"] == "not-reproduced"),
        "median_minutes_to_fix": round(statistics.median(times) / 60, 1) if times else None,
        "hours_saved": round(len(fixed) * HOURS_SAVED_PER_FIX, 1),
        "hours_saved_assumption": f"{HOURS_SAVED_PER_FIX} developer hours per verified fix (reproducing + fixing by hand)",
    }
