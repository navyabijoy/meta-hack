"""Stage 4, Ground: tie each step to one concrete on-screen element using SAM 3.1.

For the step's frame, SAM segments the mouse cursor and every instance of the
target's kind ("x icon"). The instance under the cursor tip is the target, and
its ordinal among same-kind instances (top-to-bottom, left-to-right) is what the
replay uses to pick the right element in the live page. No pixel-to-page
coordinate mapping is needed, so phone-filmed or scaled videos still work.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from meta_sam_parser import SegmentationMask, decode_mask_to_raster
from PIL import Image, ImageDraw

from backend.models import meta
from backend.pipeline.ingest import Ingested

BOX_RE = re.compile(
    r"<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=\d+;h=\d+[^|]*\|>"
    r"(?:<\|mask;x=0;y=0[^;]*;data=(\d+),(\d+),([~!])(.*?)\|>)?",
    re.S,
)
CURSOR_PROMPTS = ["mouse cursor", "mouse pointer arrow"]
MAX_TIP_DISTANCE = 40  # px from cursor tip to target box


@dataclass
class Instance:
    box: tuple[int, int, int, int]
    mask: dict | None = None  # {"h", "w", "enc", "payload"} relative to box

    @property
    def center(self) -> tuple[float, float]:
        x1, y1, x2, y2 = self.box
        return ((x1 + x2) / 2, (y1 + y2) / 2)


@dataclass
class Grounding:
    step_id: int
    frame: int
    cursor: tuple[int, int] | None
    prompt: str | None
    instances: list[Instance] = field(default_factory=list)
    target: int | None = None  # index into instances
    ordinal: int | None = None  # rank of target among instances
    count: int = 0
    confidence: str = "low"
    reason: str = ""
    overlay: str | None = None


def parse(raw: str) -> list[Instance]:
    out = []
    for m in BOX_RE.finditer(raw):
        box = tuple(int(v) for v in m.group(1, 2, 3, 4))
        mask = None
        if m.group(5):
            mask = {"h": int(m.group(5)), "w": int(m.group(6)), "enc": m.group(7), "payload": m.group(8)}
        out.append(Instance(box, mask))
    return out


def decode(inst: Instance) -> np.ndarray | None:
    if not inst.mask:
        return None
    mk = inst.mask
    try:
        raw = decode_mask_to_raster(SegmentationMask(
            "lossless" if mk["enc"] == "~" else "one_bit", mk["payload"], mk["w"], mk["h"]))
        return np.frombuffer(raw, dtype=np.uint8).reshape(mk["h"], mk["w"])
    except Exception:
        return None


def find_cursor(frame_path: str) -> tuple[int, int] | None:
    for p in CURSOR_PROMPTS:
        found = parse(meta.sam_image(frame_path, p))
        # The cursor is small; ignore large boxes.
        found = [i for i in found if (i.box[2] - i.box[0]) < 60 and (i.box[3] - i.box[1]) < 60]
        if found:
            x1, y1, _, _ = found[0].box
            return (x1, y1)  # arrow tip is the top-left corner
    return None


def dist_to_box(pt, box) -> float:
    x, y = pt
    x1, y1, x2, y2 = box
    dx = max(x1 - x, 0, x - x2)
    dy = max(y1 - y, 0, y - y2)
    return math.hypot(dx, dy)


FALLBACK_PROMPTS = ["text", "button", "list row"]


def ground_step(step: dict, frame_path: str, cursor: tuple[int, int] | None) -> Grounding:
    g = Grounding(step["id"], step["frame"], cursor, None)
    prompts = list(step["target"].get("sam_prompts") or [step["target"].get("sam_prompt", "button")])[:3]
    if cursor is not None:
        prompts += [p for p in FALLBACK_PROMPTS if p not in prompts]
    candidates: list[tuple[str, list[Instance]]] = []
    for prompt in prompts:
        inst = parse(meta.sam_image(frame_path, prompt))
        if not inst:
            continue
        inst.sort(key=lambda i: (round(i.center[1] / 8), i.center[0]))
        if cursor is None:
            if len(inst) == 1:
                g.prompt, g.instances, g.target, g.ordinal, g.count = prompt, inst, 0, 0, 1
                g.confidence, g.reason = "medium", "single match, no cursor found"
                return g
            continue
        d = [dist_to_box(cursor, i.box) for i in inst]
        # Prefer the smallest instance the tip is in (a word over the whole row), else the nearest.
        inside = [j for j, dj in enumerate(d) if dj == 0]
        if inside:
            area = lambda j: (inst[j].box[2] - inst[j].box[0]) * (inst[j].box[3] - inst[j].box[1])
            k = min(inside, key=area)
        else:
            k = int(np.argmin(d))
        if d[k] <= MAX_TIP_DISTANCE:
            g.prompt, g.instances, g.target, g.ordinal, g.count = prompt, inst, k, k, len(inst)
            g.confidence = "high" if d[k] <= 4 else "medium"
            g.reason = f"cursor {d[k]:.0f}px from '{prompt}' #{k + 1} of {len(inst)}"
            return g
        candidates.append((prompt, inst))

    # Same row: the cursor's y sits inside one instance's vertical band (e.g. clicking
    # a task's text, in line with that row's checkbox).
    if cursor is not None:
        for prompt, inst in candidates:
            rows = [j for j, i in enumerate(inst) if i.box[1] - 6 <= cursor[1] <= i.box[3] + 6]
            if len(rows) == 1 and len(inst) > 1:
                k = rows[0]
                g.prompt, g.instances, g.target, g.ordinal, g.count = prompt, inst, k, k, len(inst)
                g.confidence, g.reason = "medium", f"same row as '{prompt}' #{k + 1} of {len(inst)}"
                return g
    # One unique match for the planner's own description.
    if candidates and len(candidates[0][1]) == 1:
        prompt, inst = candidates[0]
        g.prompt, g.instances, g.target, g.ordinal, g.count = prompt, inst, 0, 0, 1
        g.confidence, g.reason = "medium", f"only one '{prompt}' on screen"
        return g
    g.reason = "no instance near the cursor"
    return g


def draw_overlay(frame_path: str, g: Grounding, out_path: Path, crop=None) -> str:
    img = Image.open(frame_path).convert("RGBA")
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for k, inst in enumerate(g.instances):
        hit = k == g.target
        color = (37, 99, 235, 110) if hit else (245, 158, 11, 70)
        edge = (37, 99, 235, 255) if hit else (245, 158, 11, 200)
        m = decode(inst)
        x1, y1, x2, y2 = inst.box
        if m is not None:
            mh, mw = m.shape
            alpha = Image.fromarray((m > 0).astype(np.uint8) * color[3]).resize((x2 - x1 + 1, y2 - y1 + 1))
            fill = Image.new("RGBA", alpha.size, color[:3] + (0,))
            fill.putalpha(alpha)
            layer.alpha_composite(fill, (x1, y1))
        d.rectangle(inst.box, outline=edge, width=3 if hit else 2)
    if g.cursor:
        cx, cy = g.cursor
        d.ellipse((cx - 9, cy - 9, cx + 9, cy + 9), outline=(220, 38, 38, 255), width=3)
    out = Image.alpha_composite(img, layer).convert("RGB")
    if crop and crop[2] > crop[0]:
        out = out.crop(crop)
    out.save(out_path, quality=88)
    return str(out_path)


def run(ing: Ingested, plan: dict, out: Path) -> list[dict]:
    frames = {f.index: f.path for f in ing.frames}
    ov_dir = out / "grounding"
    ov_dir.mkdir(exist_ok=True)
    results = []
    prev: Grounding | None = None
    for step in plan["steps"]:
        if step["action"] == "type" and prev is not None:
            # Typing goes into the element focused by the previous click.
            g = Grounding(step["id"], step["frame"], None, prev.prompt, [], None, prev.ordinal,
                          prev.count, prev.confidence, "types into the field focused by the previous step")
        else:
            fp = frames[step["frame"]]
            cursor = find_cursor(fp)
            if cursor is None:
                # The cursor can vanish against dark UI; it rarely moves far between samples.
                order = sorted(frames, key=lambda i: abs(i - step["frame"]))
                for near in order[1:3]:
                    cursor = find_cursor(frames[near])
                    if cursor:
                        break
            g = ground_step(step, fp, cursor)
            g.overlay = draw_overlay(fp, g, ov_dir / f"step{step['id']:02d}.jpg", ing.crop)
            prev = g
        d = asdict(g)
        d["instances"] = [{"box": i.box} for i in g.instances]  # keep JSON small
        results.append(d)
    (out / "grounding.json").write_text(json.dumps(results, indent=1))
    return results
