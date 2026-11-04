import json
from pathlib import Path

import pytest

from backend.models import budget, muse_code
from backend.pipeline import reproduce, understand
from backend.pipeline.workspace import RepoConfig


# ---------------------------------------------------------------- budget

@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(budget, "LEDGER", path)
    budget.set_run(None)
    yield path
    budget.set_run(None)


def test_token_cost():
    # 1M fresh input at $1.25 + 1M output at $4.25
    assert budget.token_cost("muse-spark-1.3", 1_000_000, 1_000_000) == pytest.approx(5.50)
    # cached input is cheaper
    assert budget.token_cost("muse-spark-1.3", 1_000_000, 0, cached_tokens=1_000_000) == pytest.approx(0.15)


def test_sam_video_minimum_frames():
    assert budget.sam_cost(video_frames=10) == pytest.approx(50 * 0.0002)


def test_global_cap(ledger, monkeypatch):
    monkeypatch.setattr(budget, "HARD_CAP_USD", 1.0)
    budget.record("muse-spark-1.3", 0.9)
    budget.check(0.05)
    with pytest.raises(budget.BudgetExceeded):
        budget.check(0.2)


def test_per_run_cap_and_attribution(ledger, monkeypatch):
    monkeypatch.setattr(budget, "RUN_CAP_USD", 0.5)
    budget.set_run("a")
    budget.record("sam-3.1", 0.45)
    budget.set_run("b")
    budget.record("sam-3.1", 0.10)
    assert budget.spent("a") == pytest.approx(0.45)
    assert budget.spent("b") == pytest.approx(0.10)
    budget.check(0.1)  # run b is fine
    budget.set_run("a")
    with pytest.raises(budget.BudgetExceeded):
        budget.check(0.1)


# ---------------------------------------------------------------- test generation

def test_slug():
    assert reproduce.slug("Saving 'Buy milk' once adds it once!") == "saving-buy-milk-once-adds-it-once"
    assert reproduce.slug("") == "bug"


def test_write_test_keeps_pacing_and_assertions(tmp_path):
    rep = {"steps": [
        {"action": "type", "code": 'await page.getByRole("textbox").fill("A")'},
        {"action": "click", "code": 'await page.getByRole("button", { name: "Save" }).click()'},
    ]}
    cfg = RepoConfig.load(tmp_path)
    path, code = reproduce.write_test(tmp_path, {"title": "Adds once", "summary": "x"}, rep,
                                      ["await expect(page.getByText('A')).toHaveCount(1);"], "adds-once", cfg)
    assert path == tmp_path / "tests/gecko/adds-once.spec.ts"
    assert "localStorage.clear()" in code
    # a pause after the click, none after typing
    assert code.index(".click()") < code.index("waitForTimeout(700)")
    assert "toHaveCount(1)" in code


def test_fresh_state_can_be_disabled(tmp_path):
    (tmp_path / ".gecko.yml").write_text("fresh_state: []\ntest_dir: e2e/gecko\n")
    cfg = RepoConfig.load(tmp_path)
    path, code = reproduce.write_test(tmp_path, {"title": "t", "summary": "s"}, {"steps": []}, [], "t", cfg)
    assert "localStorage" not in code
    assert path.parent == tmp_path / "e2e/gecko"


# ---------------------------------------------------------------- planning helpers

def test_parse_json_from_prose():
    assert understand.parse_json('Sure:\n{"a": 1, "b": {"c": 2}}\nDone') == {"a": 1, "b": {"c": 2}}
    with pytest.raises(ValueError):
        understand.parse_json("no json here")


def test_nearest_frame():
    assert understand.nearest(97, {90, 96, 102}) == 96


# ---------------------------------------------------------------- Muse Code

def test_parse_muse_events():
    lines = [
        {"payload": {"kind": "run_output_delta", "text": "partial"}},
        {"payload": {"kind": "run_usage", "models": [{"model": "muse-spark-1.3", "input_tokens": 10, "output_tokens": 2}]}},
        {"payload": {"kind": "run_terminal", "terminal": "completed", "text": "Root cause: X. Fix: Y."}},
    ]
    usage, text = muse_code._parse("\n".join(json.dumps(l) for l in lines) + "\nnot json")
    assert usage[0]["input_tokens"] == 10
    assert text == "Root cause: X. Fix: Y."


def test_strip_local_links():
    text = "Guard added in [App.tsx](/home/u/run/workspace/src/App.tsx); see /home/u/run/workspace/src."
    out = muse_code._strip_local_links(text, Path("/home/u/run/workspace"))
    assert "/home/u" not in out and "`App.tsx`" in out


# ---------------------------------------------------------------- config

def test_repo_config_defaults_and_override(tmp_path):
    assert RepoConfig.load(tmp_path).test_dir == "tests/gecko"
    (tmp_path / ".gecko.yml").write_text("start: pnpm dev --port {port}\n")
    cfg = RepoConfig.load(tmp_path)
    assert cfg.start.format(port=1234) == "pnpm dev --port 1234"
    assert cfg.install == "npm ci"
