# Repro

**Send a video of a bug. Get a pull request that proves it and fixes it.**

Repro watches a plain screen recording of a web-app bug (no browser extension, no SDK, no logs), works out exactly what the user did, replays it in a real browser, and writes a Playwright test that **fails the same way the video shows**. Then Muse Code fixes the bug with that failing test as its spec, and Repro only accepts the fix once the test passes and the rest of the suite stays green.

Every claim Repro makes is checked by running code. When it cannot reproduce a bug, it says so, with its evidence, instead of guessing.

Built for the Meta Global AI Developer Hackathon (Indie track, *Push the Frontier*).

## How it works

| Stage | What happens | Meta model |
| --- | --- | --- |
| 1. Ingest | Frames sampled where the screen changes, cropped to the app, tiled into labelled contact sheets; audio extracted | |
| 2. Understand | Narration transcribed; Muse Spark reads the contact sheets and writes the step plan, the expected and actual behaviour, and the frame that shows the bug | Muse Voice Transcribe, Muse Spark |
| 3. Ground | SAM 3.1 segments the mouse cursor and every look-alike element (e.g. every "x icon"); the one under the cursor gives an ordinal ("2nd of 2") that the replay matches in the live DOM | SAM 3.1 |
| 4. Replay | A Playwright driver runs the steps in Chrome, resolving each target by name, SAM ordinal and row description, and records a stable locator for each | |
| 5. Reproduce | Muse Spark compares the replay's final screen with the video's bug frame and writes assertions for the expected behaviour. The test must fail on an assertion **and** match the video. If the straight replay doesn't show the bug, Muse Spark proposes how the user's actions differed (e.g. a double click) and Repro tries each | Muse Spark |
| 6. Fix | Muse Code (headless) patches the app. Repro rejects patches that touch the test, don't make it pass, or break the suite, and feeds the failure back (up to 3 rounds) | Muse Code |
| 7. Deliver | Two commits on a `repro/...` branch, the failing test first and then the fix, opened as a PR | |

### Grounding without coordinate mapping

Matching pixels in a video to elements in a live page normally needs the exact viewport, scale and scroll. Repro sidesteps that: SAM finds every instance of the clicked element's *kind* and the cursor tip, so Repro knows "the 2nd x icon from the top". The live page resolves the same ordinal among elements with the same name, and cross-checks it against the planner's description ("the x on the 'Book flights' row"). When both signals agree the step is marked high confidence.

## Run it

Requirements: Python 3.12+, Node 20+, ffmpeg, Google Chrome, and the [Muse Code CLI](https://dev.meta.ai/docs/muse-code).

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
(cd backend/browser && npm install)
echo 'MODEL_API_KEY=...' > .env            # Meta Model API key

# Start the app under test (any web app with Playwright configured), e.g. the Taskly demo:
(cd demo-app && npm install && npm run dev)

.venv/bin/uvicorn backend.api.server:app --port 8000   # open http://localhost:8000
# or headless:
.venv/bin/python -m backend.pipeline.run path/to/bug.mp4 --repo demo-app --url http://localhost:5173
```

Spend is capped: every Meta API call goes through `backend/models/budget.py`, which logs cost and refuses calls past `REPRO_BUDGET_USD` (default $100). A full run costs about $0.15–0.25.

## Layout

```
backend/
  models/      Meta Model API clients (Muse Spark, SAM 3.1, Voice Transcribe), Muse Code runner, budget guard
  pipeline/    ingest, understand, ground, replay, reproduce, fix, run (orchestrator)
  browser/     Playwright replay driver (Node)
  api/         FastAPI server: start runs, stream events, serve the run page
web/           Run page (plain HTML/JS)
scripts/       Dev helpers, sample bug recorder, reference tests for the demo bugs
```

## Built during the hackathon

All code in this repository was written during the hackathon building period (Oct 27 – Nov 4, 2026); the first commit is on Nov 5, 2026 (IST). No prior codebase was used. The Taskly demo app with its seeded bugs was also written during the event, as a test subject.
