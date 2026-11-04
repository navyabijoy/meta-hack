# Gecko

**Send a video. Get a fix.** Gecko catches the bug in a screen recording and opens a pull request that proves it and fixes it.

Gecko watches a plain screen recording of a web-app bug (no browser extension, no SDK, no logs), works out exactly what the user did, replays it in a real browser, and writes a Playwright test that **fails the same way the video shows**. Then Muse Code fixes the bug with that failing test as its spec, and Gecko only accepts the fix once the test passes and the rest of the suite stays green.

Every claim Gecko makes is checked by running code. When it cannot reproduce a bug, it says so, with its evidence, instead of guessing.

Built for the Meta Global AI Developer Hackathon (Startup track, *Push the Frontier*).

## How it works

| Stage | What happens | Meta model |
| --- | --- | --- |
| 1. Ingest | Frames sampled where the screen changes, cropped to the app, tiled into labelled contact sheets; audio extracted | |
| 2. Understand | Narration transcribed; Muse Spark reads the contact sheets and writes the step plan, the expected and actual behaviour, and the frame that shows the bug | Muse Voice Transcribe, Muse Spark |
| 3. Ground | SAM 3.1 segments the mouse cursor and every look-alike element (e.g. every "x icon"); the one under the cursor gives an ordinal ("2nd of 2") that the replay matches in the live DOM | SAM 3.1 |
| 4. Replay | A Playwright driver runs the steps in Chrome, resolving each target by name, SAM ordinal and row description, and records a stable locator for each | |
| 5. Reproduce | Muse Spark compares the replay's final screen with the video's bug frame and writes assertions for the expected behaviour. The test must fail on an assertion **and** match the video. If the straight replay doesn't show the bug, Muse Spark proposes how the user's actions differed (e.g. a double click) and Gecko tries each | Muse Spark |
| 6. Fix | Muse Code (headless) patches the app. Gecko rejects patches that touch the test, don't make it pass, or break the suite, and feeds the failure back (up to 3 rounds) | Muse Code |
| 7. Deliver | Two commits on a `gecko/...` branch, the failing test first and then the fix, opened as a PR | |

### Grounding without coordinate mapping

Matching pixels in a video to elements in a live page normally needs the exact viewport, scale and scroll. Gecko sidesteps that: SAM finds every instance of the clicked element's *kind* and the cursor tip, so Gecko knows "the 2nd x icon from the top". The live page resolves the same ordinal among elements with the same name, and cross-checks it against the planner's description ("the x on the 'Book flights' row"). When both signals agree the step is marked high confidence.

## Use it on your repo

1. Install the GitHub App (`/github/setup` on your Gecko server creates it; then install it on a repo).
2. Add a `.gecko.yml` to the repo, and let Playwright read its base URL from `GECKO_BASE_URL`:

```yaml
install: npm ci
start: npx vite --port {port} --strictPort --host 127.0.0.1
ready_path: /
test: npx playwright test
test_dir: tests/gecko
fresh_state: [localStorage]
```

3. Open an issue with a screen recording attached. `gecko-fix[bot]` comments "On it.", then edits the comment with the result and links the PR. Comment `/gecko` on an older issue to trigger it.

## Built for production

- **Isolated runs.** Every run clones the repo into its own folder, installs from a lockfile-keyed cache (copy-on-write, so a warm install is near-instant), and starts the app on a free port. A developer's checkout is never touched, and runs don't share state.
- **Durable queue.** Runs are rows in SQLite, claimed by `GECKO_WORKERS` workers (default 2). A run interrupted by a crash is re-queued once on restart; issue context is stored with the job, so the bot reports back even across restarts.
- **Verification, not trust.** A run reports *reproduced* only when the test fails on an assertion **and** Muse Spark judges the replay's screen to match the video's bug frame. A fix is accepted only when the new test passes, the test file is byte-identical, and the existing suite is green.
- **Honest failure.** If Gecko cannot reproduce a bug, it says so on the issue instead of opening a speculative PR (see the `not-a-bug-reload` eval case).
- **Spend control.** A global cap (`GECKO_BUDGET_USD`) and a per-run cap (`GECKO_RUN_BUDGET_USD`, default $3); every call is attributed to its run in a ledger.
- **Resilience.** Model calls retry 429s, 5xx and network errors with jittered exponential backoff and honour `Retry-After`; SAM results are cached by image and prompt.
- **Least privilege.** The GitHub App asks only for contents, issues and pull requests on repos it is installed on, and uses short-lived installation tokens. Muse Code runs with its OS sandbox on and web tools off, and Gecko turns off Muse Code's contributor (training) mode so customer code is not used for training.
- **Tested.** Unit tests for grounding, budgeting, test generation and agent-output parsing run in CI; the end-to-end eval below runs against real models.

## Eval

`python -m eval.run_eval` runs every case in `eval/cases.yml` end to end and checks each fix against a **hidden, hand-written reference test** Gecko never sees. Results: [eval/results.md](eval/results.md).

## Run it

Requirements: Python 3.12+, Node 20+, ffmpeg, Google Chrome, and the [Muse Code CLI](https://dev.meta.ai/docs/muse-code).

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
(cd backend/browser && npm install)
echo 'MODEL_API_KEY=...' > .env            # Meta Model API key

.venv/bin/uvicorn backend.api.server:app --port 8000   # open http://localhost:8000
# or headless:
.venv/bin/python -m backend.pipeline.run path/to/bug.mp4 --repo navyabijoy/taskly
```

Spend is capped: every Meta API call goes through `backend/models/budget.py`, which logs cost and refuses calls past `GECKO_BUDGET_USD` (default $100). A full run costs about $0.15–0.25.

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

Gecko was called *Repro* earlier in the hackathon; older commits use that name.

All code in this repository was written during the hackathon building period (Oct 27 – Nov 4, 2026); the first commit is on Nov 5, 2026 (IST). No prior codebase was used. The Taskly demo app with its seeded bugs was also written during the event, as a test subject.
