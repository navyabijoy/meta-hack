#!/usr/bin/env bash
# Builds test bug reports: screen video (MP4, H.264) + spoken narration, like a user would send.
# Needs the demo app running on :5173.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=samples
mkdir -p "$OUT"

say_text() {
  case "$1" in
    double-save) echo "I typed buy milk and clicked save. It added buy milk twice. It should only add it once." ;;
    wrong-delete) echo "I finished call mom, switched to active, and deleted book flights. But when I go back to all, pay rent is gone and book flights is still there." ;;
    counter) echo "I added two tasks and then checked off water plants. The counter still says two tasks left. It should say one." ;;
  esac
}

for bug in double-save wrong-delete counter; do
  dir="$OUT/$bug"
  rm -rf "$dir"; mkdir -p "$dir"
  node scripts/record_bug.mjs "$bug" "$dir" >/dev/null
  say -o "$dir/voice.aiff" "$(say_text "$bug")"
  # Video with narration; video length wins, narration padded with silence.
  ffmpeg -loglevel error -y -i "$dir/raw.webm" -i "$dir/voice.aiff" \
    -filter_complex "[1:a]apad[a]" -map 0:v -map "[a]" -shortest \
    -c:v libx264 -pix_fmt yuv420p -r 25 -c:a aac "$dir/report.mp4"
  rm -f "$dir/raw.webm" "$dir/voice.aiff"
  echo "made $dir/report.mp4"
done
