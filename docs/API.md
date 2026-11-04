# Gecko API (for the web app)

Everything the landing page, workspace and report page need. JSON over HTTP from the Gecko server (`uvicorn backend.api.server:app`, default `http://localhost:8000`). No auth yet: this is a single-team deployment.

## Who uses what

| Role | Surface | Endpoints |
| --- | --- | --- |
| Reporter (support, QA, a customer) | Report page `/r/{owner}/{repo}` | `GET/POST /api/report/{owner}/{repo}` |
| Developer | Workspace: bugs inbox, bug detail | `/api/workspace/bugs`, `/api/runs/{id}`, `/api/runs/{id}/stream` |
| Admin | Workspace: repos, impact, settings | `/api/workspace/repos`, `/api/workspace/impact`, `PATCH .../settings` |
| Gecko team (internal) | `/ops` | `/api/ops`, `/api/budget` |

## Statuses

A bug (run) is one of:

| status | Meaning | Show as |
| --- | --- | --- |
| `queued` | Waiting for a worker | Queued |
| `running` | Gecko is working | In progress |
| `fixed` | Reproduced, and Muse Code's fix passed verification | Fixed (with PR) |
| `reproduced` | Failing test exists, no verified fix | Reproduced |
| `not-reproduced` | Gecko could not show the bug; it says why | Couldn't reproduce (neutral, not an error) |
| `error` | Something broke on Gecko's side | Error |

`pr_state` (when a PR exists): `open`, `merged`, `closed`.

## Report link

### `GET /api/report/{owner}/{repo}`
Is the link live? `404` if Gecko isn't installed on the repo, `403` if the admin turned the link off.
```json
{ "repo": "navyabijoy/taskly", "accepting": true }
```
`accepting: false` means the monthly fix limit is reached.

### `POST /api/report/{owner}/{repo}` (multipart)
| field | |
| --- | --- |
| `video` | MP4 / MOV / M4V / WebM, up to 300 MB |
| `description` | required, what went wrong |
| `reporter` | optional display name |

Gecko files a GitHub issue (description + a GIF of the recording), comments "On it.", and queues the run.
```json
{ "id": "1105-005910-5a65", "issue_url": "https://github.com/navyabijoy/taskly/issues/2" }
```
Errors: `400` bad input, `402` monthly limit reached, `403` link off, `404` not installed, `413` too large.

## Workspace

### `GET /api/workspace/bugs?repo=owner/name`
Newest first. `repo` optional.
```json
[{
  "id": "1105-005910-5a65",
  "title": "Deleting in Active view deletes the right task",
  "repo": "navyabijoy/taskly",
  "status": "fixed",
  "origin": "report-link",            // report-link | github-issue | upload
  "reporter": "Sam from Support",
  "issue_url": "https://github.com/navyabijoy/taskly/issues/2",
  "pr_url": "https://github.com/navyabijoy/taskly/pull/3",
  "pr_state": "open",
  "expected": "…", "actual": "…",
  "created": 1793816350.2,            // unix seconds
  "seconds": 152.4,                   // video to result
  "why": null                         // reason, for not-reproduced / error
}]
```

### `GET /api/workspace/repos`
```json
[{ "repo": "navyabijoy/taskly", "configured": true, "report_link": "/r/navyabijoy/taskly",
   "monthly_fix_limit": 50, "fixes_this_month": 3 }]
```
`configured: false` means the repo has no `.gecko.yml` yet: show setup instructions (see README "Use it on your repo"). `report_link: null` means the link is off.

### `GET /api/workspace/impact?repo=&days=30`
```json
{ "days": 30, "reports": 4, "fixed": 4, "merged": 0, "declined": 0,
  "median_minutes_to_fix": 2.7, "hours_saved": 6.0,
  "hours_saved_assumption": "1.5 developer hours per verified fix (reproducing + fixing by hand)" }
```
Always show the assumption next to "hours saved".

### `PATCH /api/workspace/repos/{owner}/{repo}/settings`
Body: `{ "report_link": true, "monthly_fix_limit": 100 }` (either field). Returns the settings.

## Bug detail (live)

### `GET /api/runs/{id}`
`{ id, events: [...], summary: {...} | null, job_status, queue_position }`. `summary` appears when the run ends.

### `GET /api/runs/{id}/stream`
Server-sent events, one JSON event per message, ending with `kind: "done"`. Each event has `t` (seconds since start) and `usd` (spend so far).

| kind | Fields | Use |
| --- | --- | --- |
| `stage` | `stage` (ingest, workspace, understand, ground, reproduce, fix), `status` (start, done), plus stage data: `plan` on understand/done, `grounding` on ground/done, `reproduced` and `why` on reproduce/done, `fixed` and `summary` on fix/done | Progress steps |
| `replay` | `attempt`, `ok`, `steps`, `variants`, `final` (screenshot path) | Replay vs video |
| `judge` | `attempt`, `same_bug`, `why`, `assertions` | Muse Spark's verdict |
| `test` | `attempt`, `passed`, `failure`, `error`, `code` | The red test |
| `hypotheses` | `hypotheses: [{step, variant, why}]` | "Video didn't show it; trying a double click" |
| `fix_start`, `fix_round` | `round`, `summary`, `diff`, `accepted`, `problem` | Muse Code's patch |
| `error` | `error` | Failure |
| `done` | `status`, `usd` | End |

Files referenced in events (frames, SAM overlays, screenshots, the input video) are served at `/files/runs/{id}/...`: take the path from `/runs/` onward and prefix `/files`.

## Internal

- `GET /api/ops`: every run with per-stage timings, queue counts, workers, bot status, Meta API budget.
- `GET /api/budget`: `{ spent, cap }` in USD.
- `POST /api/runs` (multipart `video`, `report`, `source`): developer upload.
- `POST /api/runs/{id}/pr`: open the PR for a fixed run started by upload.
