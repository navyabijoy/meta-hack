"""M0: one cheap call to each hosted Meta model. Run: .venv/bin/python -m scripts.smoke_test"""

import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from backend.models import budget, meta

tmp = Path(tempfile.mkdtemp(prefix="repro-smoke-"))
which = set(sys.argv[1:]) or {"models", "spark", "sam", "asr"}

# A fake app screenshot: a todo list with a Save button and a duplicated item.
img = Image.new("RGB", (800, 500), "white")
d = ImageDraw.Draw(img)
d.text((40, 30), "My Todos", fill="black")
for i, label in enumerate(["Buy milk", "Buy milk", "Walk dog"]):
    d.rectangle((40, 80 + i * 50, 500, 120 + i * 50), outline="gray")
    d.text((55, 92 + i * 50), label, fill="black")
d.rounded_rectangle((560, 80, 700, 120), radius=8, fill="#2563eb")
d.text((605, 92), "Save", fill="white")
shot = tmp / "screen.png"
img.save(shot)

if "models" in which:
    print("models:", [m.id for m in meta.client().models.list().data])

if "spark" in which:
    out = meta.spark([{
        "role": "user",
        "content": [
            {"type": "text", "text": "This is a screenshot of a web app. Name every visible UI element and point out anything that looks like a bug. Be brief."},
            meta.image_part(shot),
        ],
    }])
    print("spark:", out)

if "sam" in which:
    raw = meta.sam_image(shot, "button")
    print("sam raw (first 300 chars):", raw[:300])

if "asr" in which:
    aiff, wav = tmp / "n.aiff", tmp / "n.wav"
    subprocess.run(["say", "-o", str(aiff), "When I click save twice, the item shows up two times."], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(aiff), "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(wav)], check=True)
    print("asr:", meta.transcribe(wav))

print(f"total spend so far: ${budget.spent():.4f}")
