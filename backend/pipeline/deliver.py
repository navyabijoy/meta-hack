"""Stage 8, Deliver: push the gecko branch and open a pull request with the evidence.

Images (a GIF of the bug video, SAM grounding overlays, video vs replay) are
pushed to a separate `gecko-evidence` branch so they show in the PR body
without adding files to the PR's diff.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from backend.pipeline.fix import git

EVIDENCE_BRANCH = "gecko-evidence"


def gh(*args: str, cwd: Path, token: str | None = None) -> str:
    import os
    env = {**os.environ, "GH_TOKEN": token} if token else None
    r = subprocess.run(["gh", *args], cwd=cwd, capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])}: {r.stderr.strip()}")
    return r.stdout.strip()


def repo_slug(repo: Path) -> str:
    url = git(repo, "remote", "get-url", "origin").strip()
    m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
    if not m:
        raise RuntimeError(f"origin is not a GitHub remote: {url}")
    return m.group(1)


def make_gif(video: str, crop, out: Path) -> Path:
    x1, y1, x2, y2 = crop
    vf = f"crop={x2 - x1}:{y2 - y1}:{x1}:{y1},fps=8,scale=560:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", video, "-vf", vf, str(out)], check=True)
    return out


def collect_evidence(summary: dict, run_dir: Path, dest: Path) -> dict:
    dest.mkdir(parents=True, exist_ok=True)
    ing = json.loads((run_dir / "ingest.json").read_text())
    crop = tuple(ing.get("crop") or (0, 0, ing["width"], ing["height"]))
    files = {"gif": make_gif(ing["video"], crop, dest / "bug.gif").name}

    plan = summary["plan"]
    bug = run_dir / "frames" / f"f{plan['bug_frame']:05d}.jpg"
    Image.open(bug).crop(crop).save(dest / "video-bug-frame.jpg", quality=85)
    files["bug_frame"] = "video-bug-frame.jpg"

    finals = sorted(run_dir.glob("replay*/final.png"))
    if finals:
        # The last replay attempt is the one that reproduced.
        Image.open(finals[-1]).crop(crop).convert("RGB").save(dest / "replay-final.jpg", quality=85)
        files["replay"] = "replay-final.jpg"

    grounding = json.loads((run_dir / "grounding.json").read_text())
    best = max((g for g in grounding if g.get("overlay")), key=lambda g: (g.get("count") or 0), default=None)
    if best:
        shutil.copy(best["overlay"], dest / "sam-grounding.jpg")
        files["sam"] = "sam-grounding.jpg"
        files["sam_step"] = best
    return files


def remote(slug: str, token: str | None) -> str:
    return f"https://x-access-token:{token}@github.com/{slug}.git" if token else "origin"


def push_evidence(repo: Path, run_id: str, src: Path, origin: str = "origin") -> None:
    with tempfile.TemporaryDirectory() as tmp:
        wt = Path(tmp) / "ev"
        exists = subprocess.run(["git", "ls-remote", "--exit-code", "--heads", origin, EVIDENCE_BRANCH],
                                cwd=repo, capture_output=True).returncode == 0
        if exists:
            git(repo, "fetch", "-q", origin, f"{EVIDENCE_BRANCH}:refs/remotes/origin/{EVIDENCE_BRANCH}", "--force")
            git(repo, "worktree", "add", "-q", "-f", "--detach", str(wt), f"origin/{EVIDENCE_BRANCH}")
        else:
            git(repo, "worktree", "add", "-q", "--detach", str(wt))
            git(wt, "checkout", "-q", "--orphan", EVIDENCE_BRANCH)
            git(wt, "rm", "-rqf", ".")
            (wt / "README.md").write_text("Evidence images for Gecko pull requests. Not part of the app.\n")
        try:
            shutil.copytree(src, wt / run_id, dirs_exist_ok=True)
            git(wt, "add", "-A")
            from backend.github_app import git_author

            git(wt, *git_author(), "commit", "-qm", f"evidence for run {run_id}")
            git(wt, "push", "-q", origin, f"HEAD:refs/heads/{EVIDENCE_BRANCH}")
        finally:
            git(repo, "worktree", "remove", "--force", str(wt), check=False)


def pr_body(summary: dict, files: dict, raw: str, run_url: str | None, blob: str = "") -> str:
    plan = summary["plan"]
    heard = (plan.get("transcript") or {}).get("transcript")
    steps = "\n".join(
        f"{i}. {s['action']} **{s['target'].get('name')}**" + (f" `{s['text']}`" if s.get("text") else "")
        for i, s in enumerate(plan["steps"], 1))
    hyp = ""
    if summary.get("hypotheses") and summary.get("attempts", 1) > 1:
        h = summary["hypotheses"][0]
        hyp = (f"\n> The straight replay did **not** show the bug. Gecko hypothesised that step {h['step']} was a "
               f"**{h['variant']}** ({h['why']}) and that reproduced it.\n")
    sam = files.get("sam_step") or {}
    out = f"""## Bug

{f'> "{heard}"' if heard else ''}

**Expected:** {plan['expected']}
**Actual:** {plan['actual']}

<img src="{raw}/{files['gif']}" width="420" alt="The bug report video">

## Reproduction

Gecko worked out these steps from the video, replayed them in Chrome, and wrote a test that fails the same way:

{steps}
{hyp}
| User's video | Gecko's replay |
| --- | --- |
| <img src="{raw}/{files['bug_frame']}" width="380"> | <img src="{raw}/{files.get('replay', files['bug_frame'])}" width="380"> |

Test: [`{summary['test_path']}`]({blob}/{summary['test_path']}). It fails on `{summary['base']}`:

```
{_first_lines(summary.get('failure', ''), 4)}
```

<details><summary>How Gecko knew what was clicked (SAM 3.1)</summary>

Step {sam.get('step_id')}: {sam.get('reason')} (confidence: {sam.get('confidence')}).

<img src="{raw}/{files.get('sam', '')}" width="420">
</details>

## Fix

{summary.get('fix_explanation', '')}

Written by **Muse Code** with the failing test as its spec. Gecko verified that the test now passes, the test file is unchanged, and the full suite is green.

## Review it

The branch has two commits. Check out the first (`test: reproduce …`) to watch the test fail; the second is the fix.

---
<sub>Opened by Gecko · Meta API cost {f"${summary['usd']:.2f}" if summary.get('usd') is not None else ''} · {round(summary.get('seconds') or 0)}s from video to PR{f' · [run page]({run_url})' if run_url else ''}</sub>
"""
    return out


def _first_lines(s: str, n: int) -> str:
    return "\n".join([l for l in (s or "").splitlines() if l.strip()][:n])


def open_pr(summary: dict, run_dir: Path, run_url: str | None = None, issue: int | None = None,
            token: str | None = None) -> str:
    if summary.get("status") != "fixed":
        raise RuntimeError("only fixed runs get a pull request")
    repo = Path(summary["repo"])
    slug = repo_slug(repo)
    files = collect_evidence(summary, run_dir, run_dir / "evidence")
    origin = remote(slug, token)
    push_evidence(repo, summary["id"], run_dir / "evidence", origin)
    raw = f"https://raw.githubusercontent.com/{slug}/{EVIDENCE_BRANCH}/{summary['id']}"
    git(repo, "push", "-q", origin, f"{summary['branch']}:refs/heads/{summary['branch']}")
    body = pr_body(summary, files, raw, run_url, f"https://github.com/{slug}/blob/{summary['branch']}")
    if issue:
        body = f"Fixes #{issue}\n\n" + body
    body_file = run_dir / "pr_body.md"
    body_file.write_text(body)
    title = f"Fix: {summary['plan'].get('title')}"
    return gh("pr", "create", "--repo", slug, "--head", summary["branch"], "--base", summary["base"],
              "--title", title, "--body-file", str(body_file), cwd=repo, token=token)
