"""Spend guard for Meta Model API calls.

Every model call records its estimated cost in budget/ledger.jsonl. Before each
call, `check()` refuses to proceed once total spend reaches the hard cap.

Prices from https://dev.meta.ai/docs/pricing-rate-limits (checked 2026-11-04).
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

HARD_CAP_USD = float(os.environ.get("REPRO_BUDGET_USD", "100"))
WARN_AT_USD = HARD_CAP_USD * 0.8

LEDGER = Path(__file__).resolve().parents[2] / "budget" / "ledger.jsonl"

# USD per 1M tokens: (input, cached_input, output)
TOKEN_PRICES = {
    "muse-spark-1.3": (1.25, 0.15, 4.25),
    "muse-spark-1.2": (1.25, 0.15, 4.25),
    "muse-spark-1.1": (1.25, 0.15, 4.25),
    "muse-wonder-1.0": (4.00, 0.20, 20.00),
}
SAM_PER_IMAGE = 2.50 / 1000
SAM_PER_VIDEO_FRAME = 0.20 / 1000
SAM_MIN_VIDEO_FRAMES = 50
TRANSCRIBE_PER_HOUR = 0.18

_lock = threading.Lock()


class BudgetExceeded(RuntimeError):
    pass


def spent() -> float:
    if not LEDGER.exists():
        return 0.0
    total = 0.0
    with LEDGER.open() as f:
        for line in f:
            line = line.strip()
            if line:
                total += json.loads(line)["usd"]
    return total


def check(estimate_usd: float = 0.0) -> None:
    total = spent()
    if total + estimate_usd >= HARD_CAP_USD:
        raise BudgetExceeded(
            f"Spend ${total:.2f} + estimate ${estimate_usd:.2f} would reach the ${HARD_CAP_USD:.0f} cap."
        )


def record(model: str, usd: float, **detail) -> float:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        with LEDGER.open("a") as f:
            f.write(json.dumps({"t": time.time(), "model": model, "usd": usd, **detail}) + "\n")
    total = spent()
    if total >= WARN_AT_USD:
        print(f"[budget] warning: ${total:.2f} of ${HARD_CAP_USD:.0f} spent")
    return total


def token_cost(model: str, input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> float:
    price_in, price_cached, price_out = TOKEN_PRICES.get(model, TOKEN_PRICES["muse-spark-1.3"])
    fresh = max(input_tokens - cached_tokens, 0)
    return (fresh * price_in + cached_tokens * price_cached + output_tokens * price_out) / 1_000_000


def sam_cost(images: int = 0, video_frames: int = 0) -> float:
    frames = max(video_frames, SAM_MIN_VIDEO_FRAMES) if video_frames else 0
    return images * SAM_PER_IMAGE + frames * SAM_PER_VIDEO_FRAME


def transcribe_cost(seconds: float) -> float:
    return int(seconds) / 3600 * TRANSCRIBE_PER_HOUR
