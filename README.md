# Gecko

**Send a video. Get a fix.** Gecko catches the bug in a screen recording and opens a pull request that proves it and fixes it.

Gecko watches a plain screen recording of a web-app bug (no browser extension, no SDK, no logs), works out exactly what the user did, replays it in a real browser, and writes a Playwright test that **fails the same way the video shows**. Then Muse Code fixes the bug with that failing test as its spec, and Gecko only accepts the fix once the test passes and the rest of the suite stays green.

Every claim Gecko makes is checked by running code. When it cannot reproduce a bug, it says so, with its evidence, instead of guessing.

Built for the Meta Global AI Developer Hackathon (Startup track, *Push the Frontier*).

**At a glance:** 4 of 4 eval cases correct, including one where Gecko correctly declines a bug that isn't there · median **139 s** from video to verified fix · median **$0.12** of Meta API cost per run ([results](eval/results.md)). Sample bug recordings: [double-save](eval/videos/double-save.mp4), [wrong-delete](eval/videos/wrong-delete.mp4), [counter](eval/videos/counter.mp4), [not-a-bug-reload](eval/videos/not-a-bug-reload.mp4).

**Meta models used:** Muse Spark (reads the video, plans the steps, judges the replay against the bug frame, proposes hypotheses), SAM 3.1 (finds the cursor and the clicked element), Muse Voice Transcribe (narration), Muse Code (writes the fix against the failing test).

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

## The product

Gecko is built as a small SaaS on top of the pipeline: a GitHub App, a customer workspace, and a link anyone can send a bug through.

- **Three roles, one flow.** A *reporter* (support, QA, a customer) records the bug and never needs GitHub. A *developer* gets a pull request with a failing test and a verified fix. An *admin* installs the app, shares the report link, sets a monthly fix limit and watches impact.
- **Report link.** Each repo has an unguessable link (`/r/<token>`). A reporter drops a recording and a sentence; Gecko files the GitHub issue (with a GIF of the recording), answers it, and opens the PR. Links can be rotated, switched off, and are rate limited.
- **Workspace** (`/workspace`, sign in with GitHub). Overview with reports, verified fixes, time to fix and estimated hours saved against the previous period; a bugs inbox with each run's live stage; repositories with setup status, report link and fix limits; and a "try it" upload. Each run has a proof page: the replay next to the video, SAM's masks, the failing test, and the fix.
- **Sign in with GitHub, scoped to your repos.** People see only the repositories they can access on GitHub that have Gecko installed. Every API, evidence file and page is checked; anything else returns "not found". Internal pages (`/ops`, budget) are admin-only.
- **GitHub bot** (`gecko-fix[bot]`). Attach a recording to an issue, or comment `/gecko` on an older one.

API reference: [docs/API.md](docs/API.md). Brand and voice notes: [brand/BRAND.md](brand/BRAND.md).

## Use it on your repo

1. Add Gecko to GitHub: [github.com/apps/gecko-fix](https://github.com/apps/gecko-fix). Sign in at `/login` with the same account to see your workspace.
2. Add a `.gecko.yml` to the repo, and let Playwright read its base URL from `GECKO_BASE_URL`:

```yaml
install: npm ci
start: npx vite --port {port} --strictPort --host 127.0.0.1
ready_path: /
test: npx playwright test
test_dir: tests/gecko
fresh_state: [localStorage]
```

3. Share your report link from the workspace, or open an issue with a screen recording attached. `gecko-fix[bot]` comments "On it.", then edits the comment with the result and links the PR. Comment `/gecko` on an older issue to trigger it.

## Built for production

- **Isolated runs.** Every run clones the repo into its own folder, installs from a lockfile-keyed cache (copy-on-write, so a warm install is near-instant), and starts the app on a free port. A developer's checkout is never touched, and runs don't share state.
- **Durable queue.** Runs are rows in SQLite, claimed by `GECKO_WORKERS` workers (default 2). A run interrupted by a crash is re-queued once on restart; issue context is stored with the job, so the bot reports back even across restarts.
- **Verification, not trust.** A run reports *reproduced* only when the test fails on an assertion **and** Muse Spark judges the replay's screen to match the video's bug frame. A fix is accepted only when the new test passes, the test file is byte-identical, and the existing suite is green.
- **Honest failure.** If Gecko cannot reproduce a bug, it says so on the issue instead of opening a speculative PR (see the `not-a-bug-reload` eval case).
- **Spend control.** A global cap (`GECKO_BUDGET_USD`) and a per-run cap (`GECKO_RUN_BUDGET_USD`, default $3); every call is attributed to its run in a ledger.
- **Resilience.** Model calls retry 429s, 5xx and network errors with jittered exponential backoff and honour `Retry-After`; SAM results are cached by image and prompt.
- **Least privilege.** The GitHub App asks only for contents, issues and pull requests on repos it is installed on, and uses short-lived installation tokens. Muse Code runs with its OS sandbox on and web tools off, and Gecko turns off Muse Code's contributor (training) mode so customer code is not used for training.
- **Access control.** Sessions are server-side (HTTP-only cookie); the workspace API, run pages and evidence files all check that the signed-in person can access the run's repository.
- **Tested.** Unit tests for grounding, budgeting, test generation, agent-output parsing and the access rules run in CI; the end-to-end eval below runs against real models.

## Eval

`python -m eval.run_eval` runs every case in `eval/cases.yml` end to end and checks each fix against a **hidden, hand-written reference test** Gecko never sees. Results: [eval/results.md](eval/results.md).

Be clear about what this shows: four cases (three real bugs and one report of a bug that isn't there, which Gecko correctly declines), all on one small demo app, with recordings made by script. It shows the pipeline works end to end and that its checks are real, not that it generalises. The first full run scored 3 of 4; the miss is documented in the results file along with its fix, and the current run scores 4 of 4 (median 139 s and $0.12 per run).

## Limits today

- **Web apps only**, and bugs that show up on screen. No native apps, and no backend-only bugs.
- **Proven on one app.** Phone-filmed recordings and unfamiliar codebases are untested.
- **Repo commands run on the host.** Customer install/test commands should run in a container with no secrets; today they do not. Muse Code runs with its own OS sandbox, with contributor (training) mode off.
- **Single server.** One machine, SQLite, two workers. A hosted, multi-tenant deployment is the next step.
- **No billing yet.** The model is free for public repos and priced per verified fix; a monthly fix limit is enforced, payments are not built.

## What's next

Container isolation per run; hosted deployment; Gecko opening a "set up Gecko" pull request itself (detect the framework, write `.gecko.yml`); more eval cases from real recordings across several apps; billing per verified fix.

## Run it

Requirements: Python 3.12+, Node 20+, ffmpeg, Google Chrome, and the [Muse Code CLI](https://dev.meta.ai/docs/muse-code).

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
(cd backend/browser && npm install)
echo 'MODEL_API_KEY=...' > .env            # Meta Model API key
# Optional, for the GitHub app and sign-in: create the app at /github/setup, then in its settings add the
# callback URL http://localhost:8000/auth/callback and put the client secret in .env as GITHUB_CLIENT_SECRET.
# Without GITHUB_CLIENT_SECRET the workspace runs open, for local development.

.venv/bin/uvicorn backend.api.server:app --port 8000   # open http://localhost:8000
# or headless:
.venv/bin/python -m backend.pipeline.run path/to/bug.mp4 --repo navyabijoy/taskly
```

Spend is capped: every Meta API call goes through `backend/models/budget.py`, which logs cost and refuses calls past `GECKO_BUDGET_USD` (default $100). In the eval a run cost $0.08–0.18 (median $0.12).

## Layout

```
backend/
  models/      Meta Model API clients (Muse Spark, SAM 3.1, Voice Transcribe), Muse Code runner, budget guard
  pipeline/    ingest, understand, ground, replay, reproduce, fix, run (orchestrator)
  browser/     Playwright replay driver (Node)
  api/         FastAPI server: sign-in, workspace and report-link APIs, run streaming, access checks
  auth.py      Sign in with GitHub through the Gecko app; per-repo access
  product.py   Report links, bugs inbox, repos, impact, monthly fix limits
  github_app.py  The GitHub App: setup, installation tokens, the issue-watching bot
  jobs.py      Durable run queue (SQLite)
web/           Landing page, workspace, report page, sign-in, run proof page (plain HTML/JS)
docs/          API reference
brand/         Brand and voice notes
eval/          Eval cases, hidden reference tests, results
scripts/       Dev helpers, sample bug recorder, reference tests for the demo bugs
```

## Built during the hackathon

Gecko was called *Repro* earlier in the hackathon; older commits use that name.

All code in this repository was written during the hackathon building period (Oct 27 – Nov 4, 2026); the first commit is on Nov 5, 2026 (IST). No prior codebase was used. The Taskly demo app with its seeded bugs was also written during the event, as a test subject.
