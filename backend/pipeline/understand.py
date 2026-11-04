"""Stages 2-3, Understand and Plan: narration + frames -> a step plan for the bug.

Muse Voice Transcribe turns the narration into "what the user expected". Muse
Spark reads the labelled contact sheets and writes the step list, pointing
every step at the frame where it happens.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from backend.models import meta
from backend.pipeline.ingest import Ingested

PLAN_PROMPT = """You are Gecko, an agent that turns a screen recording of a web-app bug into exact reproduction steps.

The images are contact sheets: frames from the recording in time order, each labelled "#<frame> t=<seconds>". A black arrow is the user's mouse cursor.
{narration}

Work out exactly what the user did, step by step, and what went wrong.

Rules:
- One step per user action: click, dblclick, type (text typed into a field), press (a key), check (toggle a checkbox or click a task to complete it).
- "frame" = the labelled frame number where the cursor is on the target just before or during the action. Use only frame numbers that appear in the labels.
- target.role = the accessible role: button, textbox, checkbox, link, tab, or text (plain text the user clicked).
- target.name = the visible text or label of the element, exactly as shown (for an icon button like "x", describe it, e.g. "Delete").
- target.sam_prompts = 1-3 short noun phrases describing how that KIND of element LOOKS, so a segmentation model can find every one on screen. Describe appearance, not purpose: "x icon" not "delete button"; "black save button", "text input box", "checkbox", "tab label". Most specific first.
- target.which = when several elements look alike, say which one in words (e.g. "the x on the 'Book flights' row"); else "".
- If text is typed, put it in "text" exactly.
- title = a test name under 60 characters, phrased as the expected behaviour (e.g. "Saving once adds the task once").
- bug_frame = the frame that best shows the wrong result.
- expected and actual: one sentence each, concrete and checkable in the UI (counts, text, which items exist).

Reply with JSON only:
{{"title": "...", "summary": "...", "expected": "...", "actual": "...", "bug_frame": 0,
  "steps": [{{"id": 1, "frame": 0, "action": "click", "target": {{"role": "button", "name": "Save", "sam_prompts": ["black save button", "button"], "which": ""}}, "text": ""}}]}}"""


def transcribe(ing: Ingested) -> dict | None:
    if not ing.audio:
        return None
    return meta.transcribe(ing.audio)


def plan(ing: Ingested, transcript: dict | None, typed_report: str = "") -> dict:
    parts = []
    if transcript and transcript.get("transcript"):
        parts.append(f'The user narrated while recording: "{transcript["transcript"]}"')
    if typed_report:
        parts.append(f'The user wrote in the bug report: "{typed_report}"')
    narration = "\n".join(parts) or "There is no narration; infer the bug from the frames."

    content = [{"type": "text", "text": PLAN_PROMPT.format(narration=narration)}]
    content += [meta.image_part(s) for s in ing.sheets]
    raw = meta.spark([{"role": "user", "content": content}], reasoning_effort="medium", max_tokens=12000)
    result = parse_json(raw)
    valid = {f.index for f in ing.frames}
    for s in result.get("steps", []):
        s["frame"] = nearest(s.get("frame", 0), valid)
    result["bug_frame"] = nearest(result.get("bug_frame", 0), valid)
    return result


def nearest(i: int, valid: set[int]) -> int:
    return min(valid, key=lambda v: abs(v - int(i)))


def parse_json(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError(f"no JSON in model reply: {raw[:300]}")
    return json.loads(m.group(0))


def run(ing: Ingested, out: Path, typed_report: str = "") -> dict:
    transcript = transcribe(ing)
    result = plan(ing, transcript, typed_report)
    result["transcript"] = transcript
    (out / "plan.json").write_text(json.dumps(result, indent=1))
    return result
