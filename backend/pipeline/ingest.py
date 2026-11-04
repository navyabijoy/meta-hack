"""Stage 1, Ingest: split a bug video into frames, audio and contact sheets.

Frames are sampled at SAMPLE_FPS. A frame is kept as "changed" when it differs
from the previous kept frame, so long idle stretches cost nothing downstream.
Contact sheets tile frames into labelled grids so one model call can see the
whole recording.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

SAMPLE_FPS = 4
CHANGE_THRESHOLD = 0.0002  # fraction of pixels that must change (a moving cursor is ~0.0004)
SHEET_COLS, SHEET_ROWS = 3, 3
THUMB_W = 640
CROP_PAD = 48


@dataclass
class Frame:
    index: int  # frame number in the source video
    t: float  # seconds
    path: str
    changed: float  # fraction of pixels changed vs previous kept frame


@dataclass
class Ingested:
    video: str
    fps: float
    width: int
    height: int
    duration: float
    frame_count: int
    frames: list[Frame]
    audio: str | None
    sheets: list[str]
    crop: tuple[int, int, int, int] = (0, 0, 0, 0)  # x1, y1, x2, y2 of the app's content area

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=1))

    @classmethod
    def load(cls, path: Path) -> "Ingested":
        d = json.loads(path.read_text())
        d["frames"] = [Frame(**f) for f in d["frames"]]
        return cls(**d)


def ingest(video: str | Path, out: str | Path) -> Ingested:
    video, out = Path(video), Path(out)
    frames_dir = out / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    step = max(int(round(fps / SAMPLE_FPS)), 1)

    frames: list[Frame] = []
    prev = None
    i = 0
    while True:
        ok, img = cap.read()
        if not ok:
            break
        if i % step == 0:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            changed = 1.0 if prev is None else float(np.mean(cv2.absdiff(gray, prev) > 12))
            if changed >= CHANGE_THRESHOLD:
                p = frames_dir / f"f{i:05d}.jpg"
                cv2.imwrite(str(p), img, [cv2.IMWRITE_JPEG_QUALITY, 88])
                frames.append(Frame(i, round(i / fps, 2), str(p), round(changed, 4)))
                prev = gray
        i += 1
    cap.release()
    total = total or i

    # Always keep the last frame: it usually shows the bug's end state.
    if frames and frames[-1].index < i - 1:
        cap = cv2.VideoCapture(str(video))
        cap.set(cv2.CAP_PROP_POS_FRAMES, i - 1)
        ok, img = cap.read()
        cap.release()
        if ok:
            p = frames_dir / f"f{i - 1:05d}.jpg"
            cv2.imwrite(str(p), img, [cv2.IMWRITE_JPEG_QUALITY, 88])
            frames.append(Frame(i - 1, round((i - 1) / fps, 2), str(p), 0.0))

    audio = _extract_audio(video, out / "audio.wav")
    crop = content_box(frames, w, h)
    sheets = _contact_sheets(frames, out / "sheets", crop)
    result = Ingested(str(video), fps, w, h, round(total / fps, 2), total, frames, audio, sheets, crop)
    result.save(out / "ingest.json")
    return result


def _extract_audio(video: Path, wav: Path) -> str | None:
    r = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "24000",
         "-c:a", "pcm_s16le", "-map_metadata", "-1", str(wav)],
        capture_output=True,
    )
    if r.returncode != 0 or not wav.exists() or wav.stat().st_size < 2000:
        return None
    return str(wav)


def _font(size: int):
    for name in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def content_box(frames: list[Frame], w: int, h: int) -> tuple[int, int, int, int]:
    """Bounding box of the app's visible content: non-background pixels of the
    per-pixel median frame (the median drops the moving cursor), plus any area
    that changes between frames, padded."""
    sample = frames[:: max(len(frames) // 15, 1)]
    stack = np.stack([cv2.imread(f.path, cv2.IMREAD_GRAYSCALE) for f in sample])
    med = np.median(stack, axis=0).astype(np.uint8)
    last = cv2.imread(frames[-1].path, cv2.IMREAD_GRAYSCALE)
    edges = cv2.Canny(med, 60, 160) | cv2.Canny(last, 60, 160)
    blobs = cv2.dilate(edges, np.ones((9, 9), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(blobs)
    boxes = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if bw > 0.9 * w and bh < 0.05 * h:  # full-width rule or bar edge
            continue
        if bh > 0.9 * h and bw < 0.05 * w:  # full-height rule
            continue
        if area < 30:
            continue
        boxes.append((x, y, x + bw, y + bh))
    if not boxes:
        return (0, 0, w, h)
    x1, y1 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x2, y2 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    x1, y1 = max(x1 - CROP_PAD, 0), max(y1 - CROP_PAD, 0)
    x2, y2 = min(x2 + CROP_PAD, w), min(y2 + CROP_PAD, h)
    # Keep at least a 4:3-ish box so thumbnails are not slivers.
    if (x2 - x1) < (y2 - y1) * 1.2:
        grow = int((y2 - y1) * 1.2 - (x2 - x1)) // 2
        x1, x2 = max(x1 - grow, 0), min(x2 + grow, w)
    return (int(x1), int(y1), int(x2), int(y2))


def _contact_sheets(frames: list[Frame], out: Path, crop: tuple[int, int, int, int]) -> list[str]:
    out.mkdir(parents=True, exist_ok=True)
    per = SHEET_COLS * SHEET_ROWS
    sheets = []
    font = _font(22)
    cw, ch = crop[2] - crop[0], crop[3] - crop[1]
    th = int(ch * THUMB_W / cw)
    for s in range(0, len(frames), per):
        chunk = frames[s : s + per]
        sheet = Image.new("RGB", (SHEET_COLS * THUMB_W, SHEET_ROWS * th), "black")
        d = ImageDraw.Draw(sheet)
        for k, f in enumerate(chunk):
            img = Image.open(f.path).crop(crop).resize((THUMB_W, th))
            x, y = (k % SHEET_COLS) * THUMB_W, (k // SHEET_COLS) * th
            sheet.paste(img, (x, y))
            label = f"#{f.index} t={f.t:.1f}s"
            d.rectangle((x, y, x + 190, y + 30), fill="#dc2626")
            d.text((x + 6, y + 3), label, fill="white", font=font)
            d.rectangle((x, y, x + THUMB_W - 1, y + th - 1), outline="#dc2626", width=2)
        p = out / f"sheet{len(sheets):02d}.jpg"
        sheet.save(p, quality=85)
        sheets.append(str(p))
    return sheets
