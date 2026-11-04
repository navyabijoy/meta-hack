"""Run Muse Code (Meta's coding agent) headless on a workspace, with spend logged to the budget ledger."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import budget
from .meta import api_key

MUSE_BIN = shutil.which("muse") or str(Path.home() / ".local/bin/muse")


@dataclass
class MuseResult:
    exit_code: int
    final_text: str
    usd: float
    usage: list[dict] = field(default_factory=list)
    events_path: Path | None = None


def run(
    workspace: str | Path,
    prompt: str,
    *,
    images: list[str | Path] = (),
    reasoning_effort: str = "medium",
    max_steps: int = 30,
    contributor_mode: bool = False,
    events_path: str | Path | None = None,
    timeout: int = 900,
) -> MuseResult:
    """Run one prompt to completion. Approval prompts are off; Muse's OS sandbox stays on."""
    budget.check(estimate_usd=0.50)
    cmd = [
        MUSE_BIN, "exec", "--json", "--api-key-stdin",
        "--workspace", str(workspace),
        "--approval-mode", "never",
        "--reasoning-effort", reasoning_effort,
        "--max-model-steps", str(max_steps),
        "--contributor-mode", "on" if contributor_mode else "off",
        "--disable-web-tools", "--no-session-log", "--no-foreign-personal-context",
    ]
    for img in images:
        cmd += ["--image", str(img)]
    cmd.append(prompt)

    proc = subprocess.run(
        cmd, input=api_key(), capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "NO_COLOR": "1"},
    )
    if events_path:
        Path(events_path).write_text(proc.stdout)

    usage, final_text = _parse(proc.stdout)
    usd = 0.0
    for m in usage:
        usd += budget.token_cost(
            m["model"].removesuffix("-contributor") if not contributor_mode else "muse-spark-1.3",
            m.get("input_tokens", 0), m.get("output_tokens", 0), m.get("cached_tokens", 0),
        )
    if contributor_mode:
        usd *= 0.1  # contributor pricing is roughly a tenth; conservative upper bound
    budget.record("muse-code", usd, models=usage)
    return MuseResult(proc.returncode, final_text, usd, usage, Path(events_path) if events_path else None)


def _parse(stdout: str) -> tuple[list[dict], str]:
    usage: list[dict] = []
    final_text = ""
    for line in stdout.splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = e.get("payload") or {}
        kind = payload.get("kind")
        if kind == "run_usage":
            usage = payload.get("models", usage)  # cumulative; keep the latest
        elif kind == "run_terminal":
            final_text = payload.get("text") or ""
    return usage, final_text
