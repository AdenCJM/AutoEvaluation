"""Product-flow tests for campaign lifecycle, setup preflight, and dashboard."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
TOOLS = ROOT / "tools"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(TOOLS))


def _prompts(count=30):
    return [
        {"id": f"p{i}", "genre": f"genre-{i % 6}", "prompt": f"Write scenario {i} with constraint {i}."}
        for i in range(count)
    ]


def test_prompt_coverage_reports_three_way_split_and_duplicates():
    import setup
    prompts = _prompts()
    prompts[1]["prompt"] = prompts[0]["prompt"]
    coverage = setup.prompt_coverage(prompts)
    assert coverage["split"] == (15, 9, 6)
    assert len(coverage["genres"]) == 6
    assert coverage["duplicates"]


def test_campaign_plan_is_bounded_and_priced():
    import setup
    plan = setup.estimate_campaign_plan(
        _prompts(),
        [{"name": "quality", "weight": 1, "rubric": "Observable quality from 1 to 5 with concrete criteria."}],
        "gemini", "gemini-3.5-flash", "gemini-3.1-flash-lite",
        iterations=10, replicates=3, skill_content="Follow these clear instructions." * 20,
    )
    assert plan["iterations"] == 10
    assert plan["calls"][0] > 0
    assert plan["calls"][1] >= plan["calls"][0]
    assert plan["cost"][0] > 0


def test_new_campaign_archives_then_clears_runtime(tmp_path, monkeypatch):
    import campaigns
    monkeypatch.setattr(campaigns, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(campaigns, "CAMPAIGNS_DIR", tmp_path / "campaigns")
    monkeypatch.setattr(campaigns, "ACTIVE_MANIFEST", tmp_path / ".tmp/campaign.json")
    monkeypatch.setattr(campaigns, "load_config", lambda: {"results_tsv": "results.tsv", "prompts_path": "prompts/prompts.json"})
    monkeypatch.setattr(campaigns, "_git_commit", lambda: "abc123")

    (tmp_path / ".tmp/evals/baseline").mkdir(parents=True)
    (tmp_path / "prompts").mkdir()
    (tmp_path / "config.yaml").write_text("provider: gemini\n")
    (tmp_path / "SKILL.md").write_text("original")
    (tmp_path / "SKILL.md.best").write_text("confirmed best")
    (tmp_path / "results.tsv").write_text("run_id\tdecision\nbaseline\tBASELINE\n")
    (tmp_path / "prompts/prompts.json").write_text("[]")
    (tmp_path / ".tmp/evals/baseline/aggregate.json").write_text("{}")

    archive, manifest = campaigns.new_campaign("second")
    assert archive is not None
    assert (archive / "results.tsv").exists()
    assert (archive / ".tmp/evals/baseline/aggregate.json").exists()
    assert (tmp_path / "SKILL.md").read_text() == "confirmed best"
    assert not (tmp_path / "results.tsv").exists()
    assert manifest["id"] == "second"


def test_dashboard_detail_exposes_decision_diff_and_samples(tmp_path, monkeypatch):
    import dashboard_server
    monkeypatch.setattr(dashboard_server, "PROJECT_ROOT", tmp_path)
    tsv = tmp_path / "results.tsv"
    tsv.write_text(
        "run_id\ttimestamp\tcomposite_score\tquality\tchange_description\tdecision\n"
        "baseline\t2026-01-01T00:00:00\t0.5\t0.5\tInitial\tBASELINE\n"
        "exp_000\t2026-01-01T00:00:30\t0.4\t0.4\tBad change\tDISCARD\n"
        "exp_001\t2026-01-01T00:01:00\t0.6\t0.6\tAdd example\tKEEP\n"
    )
    (tmp_path / ".tmp/skills").mkdir(parents=True)
    (tmp_path / ".tmp/skills/baseline.md").write_text("# Rule\nBe clear.\n")
    (tmp_path / ".tmp/skills/exp_000.md").write_text("# Rule\nBe vague.\n")
    (tmp_path / ".tmp/skills/exp_001.md").write_text("# Rule\nBe very clear.\n")
    eval_dir = tmp_path / ".tmp/evals/exp_001"
    sample_dir = tmp_path / ".tmp/samples/exp_001"
    eval_dir.mkdir(parents=True)
    sample_dir.mkdir(parents=True)
    (eval_dir / "decision.json").write_text(json.dumps({"decision": "KEEP", "reason": "CI passed"}))
    (eval_dir / "sample_0_p1_llm_judge.json").write_text(json.dumps({
        "quality": {"normalised": 0.6, "reason": "Clear enough"}
    }))
    (sample_dir / "sample_0_p1.txt").write_text("Example output")
    config = {"metric_names": ["quality"], "metric_labels": {"quality": "Quality"}, "metric_directions": {}, "skill_name": "Test"}
    detail = dashboard_server.read_experiment_detail("exp_001", str(tsv), config)
    assert detail["decision"]["reason"] == "CI passed"
    assert "-Be clear." in detail["diff"]
    assert detail["worst_samples"][0]["output"] == "Example output"


def test_dashboard_compares_campaign_baseline_with_confirmed_best(tmp_path, monkeypatch):
    import dashboard_server
    monkeypatch.setattr(dashboard_server, "PROJECT_ROOT", tmp_path)
    tsv = tmp_path / "results.tsv"
    tsv.write_text(
        "run_id\ttimestamp\tcomposite_score\tquality\tchange_description\tdecision\n"
        "baseline\t2026-01-01T00:00:00\t0.5\t0.4\tInitial\tBASELINE\n"
        "exp_001\t2026-01-01T00:01:00\t0.7\t0.8\tAdd example\tKEEP\n"
    )
    (tmp_path / ".tmp/skills").mkdir(parents=True)
    (tmp_path / ".tmp/skills/baseline.md").write_text("Be clear.\n")
    (tmp_path / "SKILL.md.best").write_text("Be clear.\nUse an example.\n")
    config = {"metric_names": ["quality"], "metric_labels": {"quality": "Quality"}, "metric_directions": {}, "skill_name": "Test"}
    comparison = dashboard_server.read_campaign_comparison(str(tsv), config)
    assert comparison["delta"] == pytest.approx(0.2)
    assert comparison["metrics"][0]["delta"] == pytest.approx(0.4)
    assert "+Use an example." in comparison["diff"]


def test_dashboard_is_local_first_responsive_and_accessible():
    import dashboard_server
    html = dashboard_server.DASHBOARD_HTML
    assert "https://cdn" not in html
    assert "@media (max-width: 600px)" in html
    assert "aria-label=\"Toggle light or dark theme\"" in html
    assert "role=\"dialog\"" in html
    assert "python3 autoeval.py run" in html


def test_unified_cli_help():
    result = subprocess.run(
        [sys.executable, str(ROOT / "autoeval.py"), "--help"],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 0
    for command in ("run", "status", "finalize", "new", "dashboard", "demo", "report", "benchmark"):
        assert command in result.stdout
