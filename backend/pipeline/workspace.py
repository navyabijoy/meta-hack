"""Per-run isolated workspace: a fresh clone of the app repo, its own app server on a free port.

The repo describes itself in `.repro.yml`:

    install: npm ci                                   # once per lockfile; cached
    start: npm run dev -- --port {port} --strictPort  # {port} is filled in
    ready_path: /                                     # polled until it answers
    test: npx playwright test                         # Playwright reads REPRO_BASE_URL
    test_dir: tests/repro                             # where generated tests go
    fresh_state: [localStorage]                       # cleared before each replay

Runs never touch a developer's checkout, so several can run at once.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import yaml

CACHE = Path(os.environ.get("REPRO_CACHE", Path.home() / ".cache" / "repro"))
DEFAULTS = {
    "install": "npm ci",
    "start": "npm run dev -- --port {port} --strictPort",
    "ready_path": "/",
    "test": "npx playwright test",
    "test_dir": "tests/repro",
    "fresh_state": ["localStorage"],
    "start_timeout": 90,
}


@dataclass
class RepoConfig:
    install: str
    start: str
    ready_path: str
    test: str
    test_dir: str
    fresh_state: list[str] = field(default_factory=list)
    start_timeout: int = 90

    @classmethod
    def load(cls, repo: Path) -> "RepoConfig":
        data = dict(DEFAULTS)
        f = repo / ".repro.yml"
        if f.exists():
            data.update(yaml.safe_load(f.read_text()) or {})
        return cls(**{k: data[k] for k in DEFAULTS})


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run(cmd: list[str] | str, cwd: Path, timeout: int = 600, env: dict | None = None) -> str:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                       shell=isinstance(cmd, str), env=env)
    if r.returncode != 0:
        raise RuntimeError(f"`{cmd if isinstance(cmd, str) else ' '.join(cmd)}` failed:\n{(r.stderr or r.stdout)[-1500:]}")
    return r.stdout


def _clone_tree(src: Path, dst: Path) -> None:
    """Copy-on-write copy where the filesystem supports it (APFS, btrfs, XFS)."""
    if sys.platform == "darwin":
        r = subprocess.run(["cp", "-Rc", str(src), str(dst)], capture_output=True)
    else:
        r = subprocess.run(["cp", "-R", "--reflink=auto", str(src), str(dst)], capture_output=True)
    if r.returncode != 0:
        shutil.copytree(src, dst, symlinks=True)


class Workspace:
    def __init__(self, source: str, root: Path, ref: str | None = None):
        """source: a local git checkout, or a GitHub "owner/name", or a clone URL."""
        self.source = source
        self.dir = root / "workspace"
        self.ref = ref
        self.port: int | None = None
        self.proc: subprocess.Popen | None = None
        self.log = root / "app.log"

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def env(self) -> dict:
        return {**os.environ, "REPRO_BASE_URL": self.url, "CI": "1"}

    def prepare(self, token: str | None = None) -> RepoConfig:
        if self.dir.exists():
            shutil.rmtree(self.dir)
        src = self.source
        local = Path(src).expanduser()
        if local.is_dir():
            _run(["git", "clone", "-q", "--no-hardlinks", str(local.resolve()), str(self.dir)], cwd=local.parent)
            origin = subprocess.run(["git", "remote", "get-url", "origin"], cwd=local, capture_output=True, text=True)
            if origin.returncode == 0:
                _run(["git", "remote", "set-url", "origin", origin.stdout.strip()], cwd=self.dir)
        else:
            url = src if "://" in src else f"https://github.com/{src}.git"
            auth = url.replace("https://", f"https://x-access-token:{token}@") if token else url
            _run(["git", "clone", "-q", "--depth", "50", auth, str(self.dir)], cwd=self.dir.parent)
            _run(["git", "remote", "set-url", "origin", url], cwd=self.dir)
        if self.ref:
            _run(["git", "checkout", "-q", self.ref], cwd=self.dir)
        cfg = RepoConfig.load(self.dir)
        self._install(cfg)
        return cfg

    def _install(self, cfg: RepoConfig) -> None:
        lock = next((self.dir / n for n in ("package-lock.json", "pnpm-lock.yaml", "yarn.lock") if (self.dir / n).exists()), None)
        if not lock:
            _run(cfg.install, cwd=self.dir, timeout=900)
            return
        key = hashlib.sha256(lock.read_bytes() + cfg.install.encode()).hexdigest()[:16]
        cached = CACHE / "node_modules" / key
        if not cached.exists():
            _run(cfg.install, cwd=self.dir, timeout=900)
            cached.parent.mkdir(parents=True, exist_ok=True)
            tmp = cached.with_suffix(".tmp")
            shutil.rmtree(tmp, ignore_errors=True)
            _clone_tree(self.dir / "node_modules", tmp)
            tmp.rename(cached)
        else:
            _clone_tree(cached, self.dir / "node_modules")

    def start(self, cfg: RepoConfig) -> str:
        self.port = free_port()
        cmd = cfg.start.format(port=self.port)
        self.proc = subprocess.Popen(
            shlex.split(cmd), cwd=self.dir, stdout=self.log.open("w"), stderr=subprocess.STDOUT,
            env=self.env(), start_new_session=True)
        deadline = time.time() + cfg.start_timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"app exited while starting:\n{self.log.read_text()[-1500:]}")
            try:
                if httpx.get(self.url + cfg.ready_path, timeout=2).status_code < 500:
                    return self.url
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        raise RuntimeError(f"app did not answer on {self.url} within {cfg.start_timeout}s")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(timeout=10)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        self.proc = None

    def cleanup(self, keep_git: bool = True) -> None:
        """Drop node_modules after a run; keep the clone for inspection unless asked."""
        self.stop()
        shutil.rmtree(self.dir / "node_modules", ignore_errors=True)
        if not keep_git:
            shutil.rmtree(self.dir, ignore_errors=True)


@dataclass
class Target:
    """Everything a stage needs to act on the app under test."""

    repo: Path
    url: str
    cfg: RepoConfig
    env: dict | None = None

    @classmethod
    def local(cls, repo: str | Path, url: str) -> "Target":
        repo = Path(repo).resolve()
        return cls(repo, url, RepoConfig.load(repo), {**os.environ, "REPRO_BASE_URL": url})
