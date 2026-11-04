"""Durable job queue for Repro runs (SQLite).

- Runs are queued rows; REPRO_WORKERS threads claim and execute them.
- A run interrupted by a crash or restart is re-queued once on startup, then marked failed.
- Jobs started from a GitHub issue carry the issue and comment, so the bot can
  report back even if the server restarted in between.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "runs" / "repro.db"
WORKERS = int(os.environ.get("REPRO_WORKERS", "2"))
MAX_ATTEMPTS = 2

_local = threading.local()
_claim = threading.Lock()


def db() -> sqlite3.Connection:
    if not getattr(_local, "conn", None):
        DB.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(DB, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, status TEXT NOT NULL, source TEXT NOT NULL, video TEXT NOT NULL,
            report TEXT DEFAULT '', ref TEXT, origin TEXT DEFAULT 'web', context TEXT DEFAULT '{}',
            attempts INTEGER DEFAULT 0, created REAL, started REAL, finished REAL, result TEXT)""")
        _local.conn = c
    return _local.conn


def enqueue(run_id: str, source: str, video: str, report: str = "", origin: str = "web",
            context: dict | None = None, ref: str | None = None) -> str:
    db().execute("INSERT INTO jobs (id, status, source, video, report, ref, origin, context, created) VALUES (?,?,?,?,?,?,?,?,?)",
                 (run_id, "queued", source, video, report, ref, origin, json.dumps(context or {}), time.time()))
    return run_id


def get(run_id: str) -> dict | None:
    r = db().execute("SELECT * FROM jobs WHERE id=?", (run_id,)).fetchone()
    return dict(r) if r else None


def queue_position(run_id: str) -> int:
    row = get(run_id)
    if not row or row["status"] != "queued":
        return 0
    return db().execute("SELECT COUNT(*) FROM jobs WHERE status='queued' AND created < ?", (row["created"],)).fetchone()[0] + 1


def _claim_next() -> dict | None:
    with _claim:
        r = db().execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
        if not r:
            return None
        db().execute("UPDATE jobs SET status='running', started=?, attempts=attempts+1 WHERE id=?", (time.time(), r["id"]))
        return dict(r)


def _finish(run_id: str, status: str, result: dict) -> None:
    db().execute("UPDATE jobs SET status=?, finished=?, result=? WHERE id=?",
                 (status, time.time(), json.dumps(result, default=str)[:20000], run_id))


def recover() -> None:
    """On startup: anything left 'running' was interrupted."""
    for r in db().execute("SELECT id, attempts FROM jobs WHERE status='running'").fetchall():
        if r["attempts"] < MAX_ATTEMPTS:
            db().execute("UPDATE jobs SET status='queued' WHERE id=?", (r["id"],))
            print(f"[jobs] re-queued interrupted run {r['id']}")
        else:
            _finish(r["id"], "failed", {"error": "interrupted twice"})


def start_workers(execute, on_done=None) -> None:
    """execute(job) -> summary dict; on_done(job, summary) runs afterwards (e.g. report to GitHub)."""
    recover()

    def loop(n: int):
        while True:
            job = _claim_next()
            if not job:
                time.sleep(1)
                continue
            try:
                summary = execute(job)
                _finish(job["id"], summary.get("status", "done"), summary)
            except Exception as e:  # noqa: BLE001
                summary = {"status": "error", "error": f"{type(e).__name__}: {e}"}
                _finish(job["id"], "error", summary)
            if on_done:
                try:
                    on_done(job, summary)
                except Exception as e:  # noqa: BLE001
                    print(f"[jobs] on_done failed for {job['id']}: {e}")

    for n in range(WORKERS):
        threading.Thread(target=loop, args=(n,), daemon=True, name=f"repro-worker-{n}").start()
    print(f"[jobs] {WORKERS} workers started")
