"""Sign in with GitHub, through Gecko's own GitHub App.

A person signs in with GitHub; Gecko asks GitHub which installations of the Gecko
app they can see, and which repositories those cover. That list is all they can
see in the workspace. Sessions live server-side; the browser holds only an
HTTP-only cookie with a random id.

Auth is switched on once GITHUB_CLIENT_SECRET is set (the client id comes from
the app itself). Until then the workspace stays open, for local development.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from urllib.parse import urlencode

import httpx

from backend import github_app, jobs

COOKIE = "gecko_session"
SESSION_TTL = 8 * 3600  # GitHub App user tokens expire after 8 hours
STATE_TTL = 600
API = "https://api.github.com"


def _db():
    c = jobs.db()
    c.execute("""CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY, login TEXT, avatar TEXT, repos TEXT, admin INTEGER, created REAL, expires REAL)""")
    c.execute("CREATE TABLE IF NOT EXISTS oauth_states (state TEXT PRIMARY KEY, next TEXT, created REAL)")
    return c


_client_id: str | None = None


def client_id() -> str | None:
    global _client_id
    if _client_id:
        return _client_id
    _client_id = github_app.env("GITHUB_CLIENT_ID")
    if not _client_id and github_app.configured():
        try:
            _client_id = github_app.app_get("/app")["client_id"]
        except Exception:  # noqa: BLE001
            _client_id = None
    return _client_id


def enabled() -> bool:
    return bool(github_app.env("GITHUB_CLIENT_SECRET")) and bool(client_id())


def admins() -> set[str]:
    raw = os.environ.get("GECKO_ADMINS") or github_app.env("GECKO_ADMINS") or github_app.env("GITHUB_OWNER") or ""
    return {a.strip().lower() for a in raw.split(",") if a.strip()}


def safe_next(nxt: str | None) -> str:
    """Only same-site paths, never an open redirect."""
    if not nxt or not nxt.startswith("/") or nxt.startswith("//") or "\\" in nxt:
        return "/workspace"
    return nxt


def login_url(base_url: str, nxt: str | None) -> str:
    state = secrets.token_urlsafe(24)
    db = _db()
    db.execute("DELETE FROM oauth_states WHERE created < ?", (time.time() - STATE_TTL,))
    db.execute("INSERT INTO oauth_states VALUES (?,?,?)", (state, safe_next(nxt), time.time()))
    q = urlencode({"client_id": client_id(), "redirect_uri": f"{base_url}/auth/callback", "state": state})
    return f"https://github.com/login/oauth/authorize?{q}"


def finish_login(code: str, state: str, base_url: str) -> tuple[str, str]:
    """Exchange the OAuth code; return (session id, where to go next)."""
    db = _db()
    row = db.execute("SELECT next, created FROM oauth_states WHERE state=?", (state,)).fetchone()
    db.execute("DELETE FROM oauth_states WHERE state=?", (state,))
    if not row or time.time() - row["created"] > STATE_TTL:
        raise PermissionError("Sign-in expired or was not started here. Try again.")
    r = httpx.post("https://github.com/login/oauth/access_token", headers={"Accept": "application/json"}, data={
        "client_id": client_id(), "client_secret": github_app.env("GITHUB_CLIENT_SECRET"),
        "code": code, "redirect_uri": f"{base_url}/auth/callback"}, timeout=20)
    r.raise_for_status()
    tok = r.json().get("access_token")
    if not tok:
        raise PermissionError(r.json().get("error_description") or "GitHub did not return a token")
    h = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}
    user = httpx.get(f"{API}/user", headers=h, timeout=20).json()
    repos = user_repos(h)
    sid = secrets.token_urlsafe(32)
    login = user["login"]
    db.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
    db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?)", (
        sid, login, user.get("avatar_url"), json.dumps(sorted(repos)), int(login.lower() in admins()),
        time.time(), time.time() + SESSION_TTL))
    return sid, row["next"]


def user_repos(headers: dict) -> set[str]:
    """Repositories the person can access that have the Gecko app installed."""
    app_id = str(github_app.env("GITHUB_APP_ID"))
    out: set[str] = set()
    insts = httpx.get(f"{API}/user/installations", headers=headers, params={"per_page": 100}, timeout=20).json()
    for inst in insts.get("installations", []):
        if str(inst.get("app_id")) != app_id:
            continue
        page = 1
        while True:
            r = httpx.get(f"{API}/user/installations/{inst['id']}/repositories", headers=headers,
                          params={"per_page": 100, "page": page}, timeout=20).json()
            out |= {x["full_name"] for x in r.get("repositories", [])}
            if len(r.get("repositories", [])) < 100:
                break
            page += 1
    return out


def session(sid: str | None) -> dict | None:
    if not sid:
        return None
    row = _db().execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not row or row["expires"] < time.time():
        return None
    return {"login": row["login"], "avatar": row["avatar"], "repos": set(json.loads(row["repos"])),
            "admin": bool(row["admin"])}


def logout(sid: str | None) -> None:
    if sid:
        _db().execute("DELETE FROM sessions WHERE id=?", (sid,))


# A stand-in user while auth is off (local development): sees everything.
DEV_USER = {"login": None, "avatar": None, "repos": None, "admin": True}


def can_see(user: dict, repo: str | None) -> bool:
    return user["repos"] is None or (repo is not None and repo in user["repos"])
