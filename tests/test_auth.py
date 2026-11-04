"""Access rules: who can see which bugs, runs and pages once sign-in is on."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from backend import auth, jobs, product
from backend.api import server


@pytest.fixture
def app(tmp_path, monkeypatch):
    # Isolated database and run folder.
    monkeypatch.setattr(jobs, "DB", tmp_path / "gecko.db")
    monkeypatch.setattr(jobs._local, "conn", None, raising=False)
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(server, "RUNS", runs)
    monkeypatch.setattr(product, "RUNS", runs)
    monkeypatch.setattr(auth, "enabled", lambda: True)
    # Two runs, in two repos.
    for rid, repo in (("r-mine", "acme/web"), ("r-theirs", "other/app")):
        (runs / rid).mkdir()
        (runs / rid / "events.jsonl").write_text(json.dumps({"kind": "stage", "stage": "ingest", "status": "start", "t": 0}) + "\n")
        (runs / rid / "summary.json").write_text(json.dumps({"id": rid, "source": repo, "status": "fixed", "plan": {"title": rid}}))
        jobs.enqueue(rid, repo, "v.mp4")
    yield TestClient(server.app)
    jobs._local.conn = None


def sign_in(client, login="dev", repos=("acme/web",), admin=False):
    sid = "s-" + login
    auth._db().execute("INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?,?,?)",
                       (sid, login, None, json.dumps(list(repos)), int(admin), time.time(), time.time() + 3600))
    client.cookies.set(auth.COOKIE, sid)


def test_signed_out_gets_401_and_login_redirect(app):
    assert app.get("/api/workspace/bugs").status_code == 401
    r = app.get("/workspace", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login?next=/workspace"


def test_only_own_repos_are_visible(app):
    sign_in(app)
    ids = {b["id"] for b in app.get("/api/workspace/bugs").json()}
    assert ids == {"r-mine"}
    assert app.get("/api/runs/r-mine").status_code == 200
    assert app.get("/api/runs/r-theirs").status_code == 404  # not even revealed
    assert app.get("/files/runs/r-theirs/summary.json").status_code == 404
    assert app.get("/files/runs/r-mine/summary.json").status_code == 200


def test_settings_only_for_own_repos(app):
    sign_in(app)
    r = app.patch("/api/workspace/repos/other/app/settings", json={"monthly_fix_limit": 1})
    assert r.status_code == 404


def test_ops_is_admin_only(app):
    sign_in(app)
    assert app.get("/api/ops").status_code == 403
    sign_in(app, login="boss", admin=True)
    assert app.get("/api/ops").status_code == 200


def test_expired_session_is_signed_out(app):
    auth._db().execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?)",
                       ("old", "dev", None, "[]", 0, time.time() - 10000, time.time() - 1))
    app.cookies.set(auth.COOKIE, "old")
    assert app.get("/api/workspace/bugs").status_code == 401


def test_report_links_are_tokens(app):
    tok = product.settings("acme/web")["report_token"]
    assert len(tok) >= 12
    assert product.repo_for_token(tok) == "acme/web"
    assert app.get("/api/report/acme").status_code == 404
    new = product.update_settings("acme/web", rotate_link=True)["report_token"]
    assert new != tok and product.repo_for_token(tok) is None


def test_report_rate_limit(monkeypatch):
    monkeypatch.setattr(product, "REPORTS_PER_HOUR", 2)
    product._recent.clear()
    product.rate_limit("k")
    product.rate_limit("k")
    with pytest.raises(PermissionError):
        product.rate_limit("k")


@pytest.mark.parametrize("bad", ["//evil.com", "https://evil.com", "/\\evil.com", "", None])
def test_no_open_redirect(bad):
    assert auth.safe_next(bad) == "/workspace"


def test_next_path_kept():
    assert auth.safe_next("/runs/abc#x") == "/runs/abc#x"
