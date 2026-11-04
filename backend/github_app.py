"""repro[bot]: a GitHub App that turns issues with a bug video into verified fix PRs.

Setup (two clicks): open http://localhost:8000/github/setup, click "Create GitHub App",
then install it on the target repo. Credentials land in .env and github-app.pem.

The bot polls installed repos for open issues that carry a video (or a `/repro`
comment), so it needs no public webhook URL.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

import httpx
import jwt

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"
PEM = ROOT / "github-app.pem"
STATE = ROOT / "runs" / "bot_state.json"
API = "https://api.github.com"
VIDEO_RE = re.compile(
    r"(https://github\.com/user-attachments/assets/[0-9a-f-]+|https?://\S+\.(?:mp4|mov|webm))", re.I)
POLL_SECONDS = 15


# ---------------------------------------------------------------- credentials

def env(name: str) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"')
    return None


def save_env(values: dict[str, str]) -> None:
    lines = ENV.read_text().splitlines() if ENV.exists() else []
    lines = [l for l in lines if l.split("=", 1)[0] not in values]
    lines += [f'{k}="{v}"' for k, v in values.items()]
    ENV.write_text("\n".join(lines) + "\n")
    for k, v in values.items():
        os.environ[k] = v


def configured() -> bool:
    return bool(env("GITHUB_APP_ID")) and PEM.exists()


def app_jwt() -> str:
    now = int(time.time())
    return jwt.encode({"iat": now - 60, "exp": now + 540, "iss": env("GITHUB_APP_ID")}, PEM.read_text(), "RS256")


def app_get(path: str):
    r = httpx.get(API + path, headers={"Authorization": f"Bearer {app_jwt()}", "Accept": "application/vnd.github+json"})
    r.raise_for_status()
    return r.json()


_tokens: dict[int, tuple[str, float]] = {}


def installation_token(installation_id: int) -> str:
    tok, exp = _tokens.get(installation_id, ("", 0))
    if time.time() < exp - 120:
        return tok
    r = httpx.post(f"{API}/app/installations/{installation_id}/access_tokens",
                   headers={"Authorization": f"Bearer {app_jwt()}", "Accept": "application/vnd.github+json"})
    r.raise_for_status()
    tok = r.json()["token"]
    _tokens[installation_id] = (tok, time.time() + 3300)
    return tok


class Repo:
    """API access to one installed repo, acting as the app."""

    def __init__(self, full_name: str, installation_id: int):
        self.full_name = full_name
        self.installation_id = installation_id

    @property
    def token(self) -> str:
        return installation_token(self.installation_id)

    def _h(self):
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json"}

    def get(self, path: str, **params):
        r = httpx.get(f"{API}/repos/{self.full_name}{path}", headers=self._h(), params=params)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, body: dict):
        r = httpx.post(f"{API}/repos/{self.full_name}{path}", headers=self._h(), json=body)
        r.raise_for_status()
        return r.json()

    def patch(self, path: str, body: dict):
        r = httpx.patch(f"{API}/repos/{self.full_name}{path}", headers=self._h(), json=body)
        r.raise_for_status()
        return r.json()


def installed_repos() -> list[Repo]:
    out = []
    for inst in app_get("/app/installations"):
        tok = installation_token(inst["id"])
        r = httpx.get(f"{API}/installation/repositories", headers={"Authorization": f"Bearer {tok}"})
        r.raise_for_status()
        out += [Repo(x["full_name"], inst["id"]) for x in r.json()["repositories"]]
    return out


# ---------------------------------------------------------------- manifest setup

def manifest(base_url: str) -> dict:
    owner = env("GITHUB_OWNER") or "dev"
    return {
        "name": f"repro-bot-{owner}"[:34],
        "url": "https://github.com/navyabijoy/meta-hack",
        "description": "Send a video of a bug. Get a pull request that proves it and fixes it.",
        "public": True,
        "redirect_url": f"{base_url}/github/callback",
        "hook_attributes": {"url": "https://example.com/repro-webhook-unused", "active": False},
        "default_permissions": {"contents": "write", "pull_requests": "write", "issues": "write", "metadata": "read"},
        "default_events": [],
    }


def setup_page(base_url: str) -> str:
    state = secrets.token_urlsafe(16)
    save_env({"GITHUB_SETUP_STATE": state})
    m = json.dumps(manifest(base_url)).replace("'", "&#39;")
    return f"""<!doctype html><meta charset="utf-8"><title>Create repro[bot]</title>
<body style="font:15px Inter,system-ui;max-width:560px;margin:80px auto;color:#1c1917">
<h2>Create the Repro GitHub App</h2>
<p>This registers a GitHub App on your account with permission to read code and open issues comments and pull requests. GitHub will ask you to confirm.</p>
<form action="https://github.com/settings/apps/new?state={state}" method="post">
<input type="hidden" name="manifest" value='{m}'>
<button style="font:600 15px Inter,system-ui;background:#1c1917;color:#fff;border:0;border-radius:8px;padding:12px 18px;cursor:pointer">Create GitHub App on GitHub</button>
</form></body>"""


def finish_setup(code: str, state: str) -> str:
    if state != env("GITHUB_SETUP_STATE"):
        raise RuntimeError("state mismatch; start again from /github/setup")
    r = httpx.post(f"{API}/app-manifests/{code}/conversions", headers={"Accept": "application/vnd.github+json"})
    r.raise_for_status()
    app = r.json()
    PEM.write_text(app["pem"])
    PEM.chmod(0o600)
    save_env({"GITHUB_APP_ID": str(app["id"]), "GITHUB_APP_SLUG": app["slug"],
              "GITHUB_APP_WEBHOOK_SECRET": app.get("webhook_secret") or ""})
    return f"https://github.com/apps/{app['slug']}/installations/new"


# ---------------------------------------------------------------- the bot loop

def load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {"handled": {}}


def save_state(s: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=1))


def find_video(repo: Repo, issue: dict) -> str | None:
    texts = [issue.get("body") or ""]
    if issue.get("comments"):
        texts += [c.get("body") or "" for c in repo.get(f"/issues/{issue['number']}/comments")]
    for t in texts:
        m = VIDEO_RE.search(t)
        if m:
            return m.group(1)
    return None


def download(url: str, dest: Path, token: str) -> Path:
    headers = {"Authorization": f"token {token}"} if "github.com" in url else {}
    with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=120) as r:
        if r.status_code in (401, 403, 404) and headers:
            r.close()
            with httpx.stream("GET", url, follow_redirects=True, timeout=120) as r2:
                r2.raise_for_status()
                with dest.open("wb") as f:
                    for chunk in r2.iter_bytes():
                        f.write(chunk)
                return dest
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    return dest


def token_for(full_name: str) -> str | None:
    for r in installed_repos():
        if r.full_name == full_name:
            return r.token
    return None


def repo_for(full_name: str) -> Repo | None:
    return next((r for r in installed_repos() if r.full_name == full_name), None)


def handle_issue(repo: Repo, issue: dict, queue_run) -> None:
    n = issue["number"]
    video = find_video(repo, issue)
    if not video:
        repo.post(f"/issues/{n}/comments", {"body": "I need a screen recording to reproduce this. Attach an MP4 or MOV of the bug and comment `/repro`."})
        return
    comment = repo.post(f"/issues/{n}/comments", {"body": (
        "**Reproducing…** I'm watching the recording, replaying it in a real browser, and writing a test "
        "that fails the same way. I'll update this comment.")})
    run_id = time.strftime("%m%d-%H%M%S-") + f"i{n}"
    folder = ROOT / "runs" / run_id
    folder.mkdir(parents=True, exist_ok=True)
    path = download(video, folder / "input.mp4", repo.token)
    report = f"{issue['title']}\n\n{VIDEO_RE.sub('', issue.get('body') or '').strip()}"[:1500]
    queue_run(path, report, repo.full_name, run_id, "github",
              {"repo": repo.full_name, "issue": n, "comment_id": comment["id"]})


def report(ctx: dict, summary: dict, run_dir: Path) -> None:
    """Called when a run started from an issue finishes: open the PR, update the comment."""
    from backend.pipeline import deliver

    repo = repo_for(ctx["repo"])
    if not repo:
        return
    status = summary.get("status")
    try:
        if status == "fixed":
            pr = deliver.open_pr(summary, run_dir, issue=ctx["issue"], token=repo.token)
            summary["pr_url"] = pr
            (run_dir / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
            body = (f"**Reproduced and fixed:** {pr}\n\n"
                    f"- A test that fails on `{summary.get('base')}`: `{summary.get('test_path')}`\n"
                    f"- A fix by Muse Code, verified: the test passes and the suite stays green\n\n"
                    f"<sub>{round(summary.get('seconds') or 0)}s · Meta API ${summary.get('usd', 0):.2f}</sub>")
        elif status == "reproduced":
            body = (f"**Reproduced, but no verified fix yet.** The failing test is on branch "
                    f"`{summary.get('branch')}`. Muse Code's attempts did not pass verification.")
        elif status == "not-reproduced":
            body = (f"**Could not reproduce.** {summary.get('reproduce_why', '')}\n\n"
                    "I'd rather say so than open a guess. A recording that starts from a fresh page load helps.")
        else:
            body = f"**Something went wrong on my side.** `{summary.get('error', 'unknown error')}`"
    except Exception as e:  # noqa: BLE001
        body = f"**Reproduced and fixed, but opening the PR failed.** `{e}`"
    repo.patch(f"/issues/comments/{ctx['comment_id']}", {"body": body})


def poll_once(repos: set[str], queue_run, state: dict) -> None:
    for repo in installed_repos():
        if repos and repo.full_name not in repos:
            continue
        for issue in repo.get("/issues", state="open", sort="created", direction="desc", per_page=20):
            if "pull_request" in issue:
                continue
            key = f"{repo.full_name}#{issue['number']}"
            if key in state["handled"]:
                continue
            body = issue.get("body") or ""
            wants = VIDEO_RE.search(body) or "/repro" in body
            if not wants and issue.get("comments"):
                wants = any("/repro" in (c.get("body") or "")
                            for c in repo.get(f"/issues/{issue['number']}/comments"))
            if not wants:
                continue
            state["handled"][key] = time.time()
            save_state(state)
            try:
                handle_issue(repo, issue, queue_run)
            except Exception as e:  # noqa: BLE001
                print(f"[bot] {key} failed: {e}")


def start_bot(repos: set[str], queue_run) -> None:
    """repos: GitHub "owner/name"s to serve (empty = every installed repo)."""

    def loop():
        state = load_state()
        print(f"[bot] watching {sorted(repos) or 'all installed repos'} every {POLL_SECONDS}s")
        while True:
            try:
                if configured():
                    poll_once(repos, queue_run, state)
            except Exception as e:  # noqa: BLE001
                print(f"[bot] poll error: {e}")
            time.sleep(POLL_SECONDS)

    threading.Thread(target=loop, daemon=True).start()
