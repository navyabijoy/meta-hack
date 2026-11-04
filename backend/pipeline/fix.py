"""Stage 7, Fix: Muse Code writes the patch; Repro verifies it by running the tests.

Muse Code gets the failing test as its spec. Repro accepts the patch only when
the new test passes, the test file is unchanged, and the full suite is green.
Otherwise the failure goes back to Muse Code, up to MAX_ROUNDS.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from backend.models import muse_code
from backend.pipeline.reproduce import run_test
from backend.pipeline.workspace import Target

MAX_ROUNDS = 3

FIX_PROMPT = """A user reported a bug with a screen recording. Repro reproduced it as a failing Playwright test.

Bug: {summary}
Expected: {expected}
Actual: {actual}

Failing test: {test_path}
```ts
{test_code}
```

Test failure:
```
{failure}
```

Fix the bug in the application source so this test passes. Rules:
- Find the root cause; make the smallest correct change. Do not just special-case the test's data.
- Do not edit, move or delete anything under tests/.
- Do not run the tests, start servers, or install packages; Repro runs the tests itself.
- When done, reply with 2-3 sentences: the root cause, then the fix."""

RETRY_PROMPT = """Your previous change did not pass verification.

{problem}

Fix it. Same rules: change application source only, never tests/, do not run tests or install anything. Reply with 2-3 sentences on what you changed."""


@dataclass
class FixRound:
    n: int
    summary: str
    usd: float
    diff: str
    test: dict
    suite: dict | None
    accepted: bool
    problem: str = ""


@dataclass
class FixResult:
    fixed: bool
    rounds: list[FixRound] = field(default_factory=list)

    @property
    def explanation(self) -> str:
        return self.rounds[-1].summary if self.rounds else ""


def git(repo: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(tgt: Target, test_path: Path, plan: dict, failure: str, out: Path, on_event=None) -> FixResult:
    repo = tgt.repo
    emit = on_event or (lambda *a, **k: None)
    test_code = test_path.read_text()
    test_hash = sha(test_path)
    rel = str(test_path.relative_to(repo))
    result = FixResult(False)
    prompt = FIX_PROMPT.format(summary=plan["summary"], expected=plan["expected"], actual=plan["actual"],
                               test_path=rel, test_code=test_code, failure=failure[:1500])

    for n in range(1, MAX_ROUNDS + 1):
        emit("fix_start", round=n)
        mr = muse_code.run(repo, prompt, events_path=out / f"muse_round{n}.jsonl")
        diff = git(repo, "diff", "--", ".", f":(exclude){rel}")
        problem = ""
        suite = None

        if not test_path.exists() or sha(test_path) != test_hash:
            git(repo, "checkout", "--", rel, check=False)
            problem = "You modified the test file. It has been restored. Only change application source."
            test = {"passed": False, "failure": "test-modified", "error": problem}
        elif not diff.strip():
            problem = "You made no change to the application source."
            test = {"passed": False, "failure": "no-change", "error": problem}
        else:
            test = run_test(repo, test_path, tgt.env, tgt.cfg.test)
            if not test["passed"]:
                problem = f"The test still fails:\n{test['error'][:1200]}"
            else:
                suite = run_test(repo, None, tgt.env, tgt.cfg.test)
                if not suite["passed"]:
                    problem = f"The new test passes, but the existing suite now fails:\n{suite['error'][:1200]}"

        accepted = not problem
        rnd = FixRound(n, mr.final_text, mr.usd, diff, test, suite, accepted, problem)
        result.rounds.append(rnd)
        emit("fix_round", round=n, summary=mr.final_text, usd=mr.usd, diff=diff, test=test, suite=suite,
             accepted=accepted, problem=problem)
        if accepted:
            result.fixed = True
            return result
        prompt = RETRY_PROMPT.format(problem=problem)
    return result
