"""Thin clients for Meta Model API models, all routed through the budget guard."""

from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from openai import OpenAI

from . import budget

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

BASE_URL = "https://api.meta.ai/v1"
SPARK_MODEL = os.environ.get("REPRO_SPARK_MODEL", "muse-spark-1.3")


def api_key() -> str:
    key = os.environ.get("MODEL_API_KEY") or os.environ.get("META_HACK")
    if not key:
        raise RuntimeError("Set MODEL_API_KEY (or META_HACK) in .env")
    return key


_client: OpenAI | None = None


def client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(base_url=BASE_URL, api_key=api_key())
    return _client


def data_url(path: str | Path) -> str:
    path = Path(path)
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


# --- Muse Spark -------------------------------------------------------------

def spark(
    messages: list[dict],
    *,
    model: str = SPARK_MODEL,
    max_tokens: int = 6000,
    reasoning_effort: str = "low",
    **kw,
) -> str:
    """Chat completion with Muse Spark. Images go in as image_url content parts.

    Muse Spark is a reasoning model: max_tokens covers hidden reasoning plus the
    answer, so keep it generous. reasoning_effort: minimal | low | medium | high.
    """
    budget.check(estimate_usd=0.05)
    resp = client().chat.completions.create(
        model=model, messages=messages, max_tokens=max_tokens, reasoning_effort=reasoning_effort, **kw
    )
    if resp.choices[0].finish_reason == "length" and not resp.choices[0].message.content:
        print("[spark] warning: hit max_tokens during reasoning; raise max_tokens or lower reasoning_effort")
    usage = resp.usage
    cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0
    cost = budget.token_cost(model, usage.prompt_tokens, usage.completion_tokens, cached)
    budget.record(model, cost, input=usage.prompt_tokens, output=usage.completion_tokens, cached=cached)
    return resp.choices[0].message.content or ""


def image_part(path: str | Path) -> dict:
    return {"type": "image_url", "image_url": {"url": data_url(path)}}


# --- SAM 3.1 ----------------------------------------------------------------

SAM_CACHE = Path(__file__).resolve().parents[2] / "budget" / "sam_cache"


def sam_image(image_path: str | Path, prompt: str) -> str:
    """Segment one concept in an image. Returns the raw special-token text.

    Results are cached on disk by (image bytes, prompt), so reruns cost nothing.
    """
    import hashlib

    key = hashlib.sha256(Path(image_path).read_bytes() + b"\0" + prompt.encode()).hexdigest()[:32]
    cached = SAM_CACHE / f"{key}.txt"
    if cached.exists():
        return cached.read_text()
    budget.check(estimate_usd=budget.sam_cost(images=1))
    text = _sam_stream([
        {"type": "input_text", "text": prompt},
        {"type": "input_image", "image_url": data_url(image_path)},
    ])
    budget.record("sam-3.1", budget.sam_cost(images=1), kind="image", prompt=prompt)
    SAM_CACHE.mkdir(parents=True, exist_ok=True)
    cached.write_text(text)
    return text


def sam_video(video_path: str | Path, prompt: str, frame_count: int, **metadata: str) -> str:
    """Segment and track one concept (or prompt tokens) across a video."""
    budget.check(estimate_usd=budget.sam_cost(video_frames=frame_count))
    file = client().files.create(purpose="user_data", file=Path(video_path).open("rb"))
    text = _sam_stream(
        [{"type": "input_text", "text": prompt}, {"type": "input_video", "file_id": file.id}],
        metadata=metadata or None,
        store=False,
    )
    budget.record("sam-3.1", budget.sam_cost(video_frames=frame_count), kind="video", frames=frame_count)
    return text


def _sam_stream(content: list[dict], **kw) -> str:
    out: list[str] = []
    with client().responses.stream(
        model="sam-3.1",
        input=[{"type": "message", "role": "user", "content": content}],
        **{k: v for k, v in kw.items() if v is not None},
    ) as stream:
        for event in stream:
            if event.type == "response.output_text.delta":
                out.append(event.delta)
    return "".join(out)


# --- Muse Voice Transcribe --------------------------------------------------

def transcribe(wav_path: str | Path, mode: str = "ENDPOINTING") -> dict:
    """Transcribe a mono 16-bit 16/24 kHz WAV. Returns {transcript, turns, audioDurationMs}."""
    budget.check(estimate_usd=0.01)
    import json

    with Path(wav_path).open("rb") as f:
        r = httpx.post(
            f"{BASE_URL}/asr/transcribe",
            headers={"Authorization": f"Bearer {api_key()}"},
            files={
                "request": (None, json.dumps({"mode": mode, "model": "muse-voice-transcribe-1.0", "audioEncoding": "WAV"}), "application/json"),
                "audio": (Path(wav_path).name, f, "audio/wav"),
            },
            timeout=120,
        )
    r.raise_for_status()
    data = r.json()
    budget.record("muse-voice-transcribe-1.0", budget.transcribe_cost(data.get("audioDurationMs", 0) / 1000))
    return data
