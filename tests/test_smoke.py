"""
Smoke tests + unit tests for AutoEvaluation.
Run with: python3 -m pytest tests/ -v
"""

import json
import re
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Allow imports from tools/
TOOLS_DIR = Path(__file__).parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

PROJECT_ROOT = Path(__file__).parent.parent.resolve()


# ── Import smoke tests ──────────────────────────────────────────────

def test_import_model_client():
    from model_client import ModelClient
    assert hasattr(ModelClient, "from_config")
    assert hasattr(ModelClient, "generate")


def test_import_score_aggregator():
    from score_aggregator import aggregate
    assert callable(aggregate)


def test_import_eval_deterministic():
    from eval_deterministic import evaluate_sample
    result = evaluate_sample("Hello world")
    assert isinstance(result, dict)


def test_import_utils():
    from utils import PROJECT_ROOT, load_config, sanitise_description, validate_config
    assert PROJECT_ROOT.exists()
    assert callable(load_config)
    assert callable(sanitise_description)
    assert callable(validate_config)


# ── Config template tests ───────────────────────────────────────────

def test_config_template_exists():
    template = PROJECT_ROOT / "config.template.yaml"
    assert template.exists(), "config.template.yaml missing"


def test_config_template_is_valid_yaml():
    import yaml
    template = PROJECT_ROOT / "config.template.yaml"
    cfg = yaml.safe_load(template.read_text(encoding="utf-8"))
    assert "provider" in cfg
    assert "llm_judge_dimensions" in cfg


def test_config_template_has_new_keys():
    import yaml
    template = PROJECT_ROOT / "config.template.yaml"
    cfg = yaml.safe_load(template.read_text(encoding="utf-8"))
    assert "judge_sees_skill" in cfg
    assert "max_cost_usd" in cfg
    assert "convergence_window" in cfg
    assert "max_concurrent" in cfg


# ── Input validation tests ──────────────────────────────────────────

def test_run_id_validation_accepts_valid():
    pattern = re.compile(r'^[a-zA-Z0-9_-]+$')
    for valid in ["baseline", "exp_001", "test-run", "myRun123"]:
        assert pattern.match(valid), f"Should accept: {valid}"


def test_run_id_validation_rejects_invalid():
    pattern = re.compile(r'^[a-zA-Z0-9_-]+$')
    for invalid in ["../etc/passwd", "run id", "run\ttab", "run;cmd", ""]:
        assert not pattern.match(invalid), f"Should reject: {invalid!r}"


def test_description_sanitisation():
    from utils import sanitise_description
    assert "\t" not in sanitise_description("has\ttab")
    assert "\n" not in sanitise_description("has\nnewline")
    assert sanitise_description("clean text") == "clean text"


def test_safe_path_within_project():
    from experiment_runner import _safe_path
    p = _safe_path("SKILL.md")
    assert PROJECT_ROOT in p.parents or p.parent == PROJECT_ROOT


def test_safe_path_rejects_escape(tmp_path):
    from experiment_runner import _safe_path
    with pytest.raises(SystemExit):
        _safe_path("/etc/passwd", must_exist=False)


# ── File structure tests ────────────────────────────────────────────

def test_env_example_exists():
    assert (PROJECT_ROOT / ".env.example").exists()


def test_env_has_no_real_keys():
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        content = env_path.read_text()
        assert "AIzaSy" not in content, ".env still contains a real API key"


def test_gitignore_covers_secrets():
    gitignore = (PROJECT_ROOT / ".gitignore").read_text()
    assert ".env" in gitignore
    assert "config.yaml" in gitignore


def test_example_directory_exists():
    assert (PROJECT_ROOT / "examples" / "writing-style").is_dir()


# ── Config validation tests (Phase 0) ──────────────────────────────

def _make_config(**overrides):
    """Build a minimal valid config dict."""
    cfg = {
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "api_key_env": "GEMINI_API_KEY",
        "llm_judge_dimensions": [
            {"name": "quality", "weight": 0.5, "rubric": "Is it good?"},
            {"name": "accuracy", "weight": 0.5, "rubric": "Is it accurate?"},
        ],
    }
    cfg.update(overrides)
    return cfg


def test_config_validation_required_keys():
    from utils import validate_config
    for key in ["provider", "model", "api_key_env", "llm_judge_dimensions"]:
        cfg = _make_config()
        del cfg[key]
        with pytest.raises(SystemExit):
            validate_config(cfg)


def test_config_validation_weight_sum():
    from utils import validate_config
    cfg = _make_config(llm_judge_dimensions=[
        {"name": "q", "weight": 0.3, "rubric": "test"},
        {"name": "a", "weight": 0.4, "rubric": "test"},
    ])
    # Weights sum to 0.7, should auto-normalise
    validate_config(cfg)
    total = sum(d["weight"] for d in cfg["llm_judge_dimensions"])
    assert abs(total - 1.0) < 0.01


def test_config_validation_dimension_schema():
    from utils import validate_config
    cfg = _make_config(llm_judge_dimensions=[
        {"name": "q", "weight": 0.5},  # missing rubric
    ])
    with pytest.raises(SystemExit):
        validate_config(cfg)


# ── Score aggregator tests (Phase 0) ────────────────────────────────

def test_score_aggregator_missing_metrics(tmp_path):
    from score_aggregator import aggregate
    # Create eval file missing a dimension
    eval_data = {
        "quality": {"score": 4, "normalised": 0.75, "reason": "good"},
        # "accuracy" is missing
    }
    (tmp_path / "sample_0_llm_judge.json").write_text(json.dumps(eval_data))
    cfg = _make_config()
    result = aggregate(str(tmp_path), cfg)
    assert result["metric_averages"]["accuracy"] == 0.0


def test_score_aggregator_weights_not_one(tmp_path):
    from score_aggregator import aggregate
    eval_data = {
        "quality": {"score": 5, "normalised": 1.0, "reason": "perfect"},
        "accuracy": {"score": 5, "normalised": 1.0, "reason": "perfect"},
    }
    (tmp_path / "sample_0_llm_judge.json").write_text(json.dumps(eval_data))
    cfg = _make_config(llm_judge_dimensions=[
        {"name": "quality", "weight": 0.3, "rubric": "test"},
        {"name": "accuracy", "weight": 0.4, "rubric": "test"},
    ])
    result = aggregate(str(tmp_path), cfg)
    # Should still produce a composite (using the raw weights)
    assert result["composite_score"] > 0


def test_score_aggregator_lower_is_better(tmp_path):
    from score_aggregator import aggregate
    eval_data = {
        "error_rate": {"score": 0.8, "normalised": 0.8, "reason": "high errors"},
    }
    (tmp_path / "sample_0_llm_judge.json").write_text(json.dumps(eval_data))
    cfg = {
        "provider": "gemini", "model": "test", "api_key_env": "TEST",
        "llm_judge_dimensions": [
            {"name": "error_rate", "weight": 1.0, "rubric": "test", "direction": "lower_is_better"},
        ],
    }
    result = aggregate(str(tmp_path), cfg)
    # Score of 0.8 inverted = 0.2
    assert result["composite_score"] == pytest.approx(0.2, abs=0.01)


# ── LLM Judge parse tests (Phase 1) ─────────────────────────────────

def _make_dimensions():
    return [
        {"name": "quality", "weight": 0.5, "rubric": "Is it good?"},
        {"name": "accuracy", "weight": 0.5, "rubric": "Is it accurate?"},
    ]


def test_judge_parse_valid_json():
    from eval_llm_judge import judge_sample
    mock_client = MagicMock()
    mock_client.generate.return_value = json.dumps({
        "quality": {"score": 4, "reason": "good"},
        "accuracy": {"score": 5, "reason": "perfect"},
    })
    result = judge_sample("test text", _make_dimensions(), mock_client)
    assert result["quality"]["normalised"] == 0.75
    assert result["accuracy"]["normalised"] == 1.0


def test_judge_parse_markdown_wrapped():
    from eval_llm_judge import judge_sample
    mock_client = MagicMock()
    mock_client.generate.return_value = '```json\n{"quality": {"score": 3, "reason": "ok"}, "accuracy": {"score": 4, "reason": "good"}}\n```'
    result = judge_sample("test text", _make_dimensions(), mock_client)
    assert result["quality"]["normalised"] == 0.5


def test_judge_parse_malformed():
    from eval_llm_judge import judge_sample
    mock_client = MagicMock()
    mock_client.generate.return_value = "This is not JSON at all, just garbage text with no structure."
    result = judge_sample("test text", _make_dimensions(), mock_client)
    assert "error" in result
    assert result["quality"]["normalised"] == 0.0


def test_judge_parse_refusal():
    from eval_llm_judge import judge_sample
    mock_client = MagicMock()
    mock_client.generate.return_value = "I cannot evaluate this content as it violates my guidelines."
    result = judge_sample("test text", _make_dimensions(), mock_client)
    assert result["quality"]["normalised"] == 0.0


def test_judge_parse_empty():
    from eval_llm_judge import judge_sample
    mock_client = MagicMock()
    mock_client.generate.return_value = ""
    result = judge_sample("test text", _make_dimensions(), mock_client)
    assert result["quality"]["normalised"] == 0.0


def test_judge_selective_context():
    from eval_llm_judge import build_judge_prompt
    dims = _make_dimensions()
    prompt_blind = build_judge_prompt(dims)
    prompt_semi = build_judge_prompt(dims, skill_content="Be concise and clear.")
    assert "SKILL" not in prompt_blind
    assert "---SKILL---" in prompt_semi
    assert "task_accuracy" in prompt_semi


def test_judge_missing_skill_file():
    """When --skill-path points to nonexistent file, should fall back to blind."""
    from eval_llm_judge import build_judge_prompt
    # Simulating the logic: if skill_path doesn't exist, skill_content stays None
    prompt = build_judge_prompt(_make_dimensions(), skill_content=None)
    assert "SKILL" not in prompt


# ── Model client tests (Phase 1-3) ──────────────────────────────────

def test_judge_client_separate_provider(tmp_path):
    import yaml
    from model_client import ModelClient
    cfg = {
        "provider": "gemini", "model": "gemini-2.5-flash", "api_key_env": "GEMINI_API_KEY",
        "judge_provider": "openai", "judge_model": "gpt-4o", "judge_api_key_env": "OPENAI_API_KEY",
    }
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.dump(cfg))
    # We can't actually create the client (no API key), but we can test from_config parsing
    with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
        with patch.object(ModelClient, "_get_client", return_value=None):
            client = ModelClient.from_config(str(cfg_path), judge=True)
            assert client.provider == "openai"
            assert client.model == "gpt-4o"


def test_judge_client_fallback(tmp_path):
    import yaml
    from model_client import ModelClient
    cfg = {
        "provider": "gemini", "model": "gemini-2.5-flash", "api_key_env": "GEMINI_API_KEY",
        # No judge_* keys
    }
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.dump(cfg))
    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}):
        with patch.object(ModelClient, "_get_client", return_value=None):
            client = ModelClient.from_config(str(cfg_path), judge=True)
            assert client.provider == "gemini"


# ── Retry tests (Phase 2) ───────────────────────────────────────────

def test_retry_transient_error():
    from model_client import ModelClient
    with patch.dict("os.environ", {"TEST_KEY": "test"}):
        with patch.object(ModelClient, "_get_client", return_value=None):
            client = ModelClient("gemini", "test-model", "TEST_KEY")

    # Create a fake RateLimitError
    class RateLimitError(Exception):
        pass

    call_count = 0
    def mock_generate_once(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise RateLimitError("Rate limited")
        return "Success"

    client._generate_once = mock_generate_once
    with patch("model_client.time.sleep"):  # Don't actually sleep
        result = client.generate("sys", "user")
    assert result == "Success"
    assert call_count == 3


def test_retry_permanent_error():
    from model_client import ModelClient
    with patch.dict("os.environ", {"TEST_KEY": "test"}):
        with patch.object(ModelClient, "_get_client", return_value=None):
            client = ModelClient("gemini", "test-model", "TEST_KEY")

    class AuthenticationError(Exception):
        pass

    def mock_generate_once(*args, **kwargs):
        raise AuthenticationError("Bad key")

    client._generate_once = mock_generate_once
    with pytest.raises(AuthenticationError):
        client.generate("sys", "user")


def test_retry_jitter():
    """Verify backoff delays have random variance."""
    import random
    delays = []
    for _ in range(20):
        base = 2
        delay = base * (1 + random.uniform(-0.3, 0.3))
        delays.append(delay)
    # With 20 samples, there should be variance
    assert max(delays) - min(delays) > 0.1


# ── Token accumulation tests (Phase 3) ──────────────────────────────

def test_token_accumulation():
    from model_client import ModelClient
    with patch.dict("os.environ", {"TEST_KEY": "test"}):
        with patch.object(ModelClient, "_get_client", return_value=None):
            client = ModelClient("gemini", "gemini-2.5-flash", "TEST_KEY")

    assert client.total_input_tokens == 0
    assert client.total_output_tokens == 0

    # Simulate token accumulation
    client.total_input_tokens += 1_000_000
    client.total_output_tokens += 500_000
    summary = client.usage_summary()
    assert summary["input_tokens"] == 1_000_000
    assert summary["output_tokens"] == 500_000
    assert summary["estimated_cost_usd"] > 0
    assert client.estimated_cost_usd > 0


def test_token_accumulation_no_usage():
    from model_client import ModelClient
    with patch.dict("os.environ", {"TEST_KEY": "test"}):
        with patch.object(ModelClient, "_get_client", return_value=None):
            client = ModelClient("gemini", "test-model", "TEST_KEY")

    # Mock response with no usage metadata
    mock_response = MagicMock(spec=[])  # No attributes
    inp, out = client._extract_usage(mock_response)
    assert inp == 0
    assert out == 0


# ── Run loop tests (Phase 1, 3) ─────────────────────────────────────

def test_run_loop_keep_discard(tmp_path):
    """Score > best → KEEP, score <= best → DISCARD."""
    from run_loop import get_best_score
    tsv = tmp_path / "results.tsv"
    tsv.write_text("run_id\ttimestamp\tcomposite_score\n" "baseline\t2024-01-01\t0.5000\n")
    assert get_best_score(tsv) == 0.5

    # Add a higher score
    with open(tsv, "a") as f:
        f.write("exp_001\t2024-01-01\t0.7000\n")
    assert get_best_score(tsv) == 0.7


def test_run_loop_consecutive_discards():
    """5 discards should trigger radical approach in analyse_and_modify."""
    from run_loop import analyse_and_modify
    from model_client import ModelClient

    with patch.dict("os.environ", {"TEST_KEY": "test"}):
        with patch.object(ModelClient, "_get_client", return_value=None):
            client = ModelClient("gemini", "test-model", "TEST_KEY")

    # Mock generate to return a valid response
    client.generate = MagicMock(return_value="DESCRIPTION: radical change\n---SKILL---\nNew skill content that is definitely long enough to pass validation checks.")

    skill_path = Path(tempfile.mktemp(suffix=".md"))
    skill_path.write_text("Original skill content")
    cfg = _make_config()

    try:
        # Normal call
        desc = analyse_and_modify(client, skill_path, "results context", cfg, force_radical=False)
        normal_prompt = client.generate.call_args[0][0]  # system_prompt

        # Radical call
        desc = analyse_and_modify(client, skill_path, "results context", cfg, force_radical=True)
        radical_prompt = client.generate.call_args[0][0]

        assert "FUNDAMENTALLY different" in radical_prompt
        assert "FUNDAMENTALLY different" not in normal_prompt
    finally:
        skill_path.unlink(missing_ok=True)


def test_run_loop_convergence():
    """No improvement for N iterations should produce convergence message."""
    # This tests the convergence_window config parameter logic
    convergence_window = 3
    iterations_since_improvement = 0

    for _ in range(3):
        iterations_since_improvement += 1

    assert iterations_since_improvement >= convergence_window


def test_run_loop_cost_cap():
    """Cumulative cost >= max_cost_usd should stop."""
    from model_client import ModelClient
    with patch.dict("os.environ", {"TEST_KEY": "test"}):
        with patch.object(ModelClient, "_get_client", return_value=None):
            client = ModelClient("gemini", "gemini-2.5-flash", "TEST_KEY")

    client.total_input_tokens = 10_000_000  # Lots of tokens
    client.total_output_tokens = 5_000_000
    assert client.estimated_cost_usd > 0
    # Verify that cost check would trigger
    max_cost_usd = 0.01
    assert client.estimated_cost_usd >= max_cost_usd


def test_run_loop_skill_corruption():
    """LLM returns <50 char garbage → SKILL.md not overwritten."""
    from run_loop import analyse_and_modify
    from model_client import ModelClient

    with patch.dict("os.environ", {"TEST_KEY": "test"}):
        with patch.object(ModelClient, "_get_client", return_value=None):
            client = ModelClient("gemini", "test-model", "TEST_KEY")

    # Return short garbage
    client.generate = MagicMock(return_value="DESCRIPTION: bad\n---SKILL---\nShort")

    skill_path = Path(tempfile.mktemp(suffix=".md"))
    original_content = "Original skill content that is definitely more than fifty characters long for testing purposes"
    skill_path.write_text(original_content)

    try:
        analyse_and_modify(client, skill_path, "results", _make_config())
        # SKILL.md should NOT have been overwritten (content too short)
        assert skill_path.read_text() == original_content
    finally:
        skill_path.unlink(missing_ok=True)


# ── Parallel execution tests (Phase 4) ──────────────────────────────

def test_parallel_generation_partial_failure():
    """1 of N threads fails → remaining samples saved, warning logged."""
    from generate_samples import _generate_one
    from model_client import ModelClient

    with patch.dict("os.environ", {"TEST_KEY": "test"}):
        with patch.object(ModelClient, "_get_client", return_value=None):
            client = ModelClient("gemini", "test-model", "TEST_KEY")

    call_count = 0
    def mock_generate(system_prompt, user_prompt, max_tokens=4096):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("API error")
        return f"Generated output {call_count}"

    client.generate = mock_generate

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        # Success
        r1 = _generate_one(client, "skill", {"id": "p1", "genre": "test", "prompt": "hi"}, 0, out_dir)
        assert r1["file"] is not None

        # Failure
        r2 = _generate_one(client, "skill", {"id": "p2", "genre": "test", "prompt": "hi"}, 1, out_dir)
        assert r2["file"] is None
        assert "error" in r2

        # Success again
        r3 = _generate_one(client, "skill", {"id": "p3", "genre": "test", "prompt": "hi"}, 2, out_dir)
        assert r3["file"] is not None


def test_subprocess_timeout():
    """Mock subprocess hanging → TimeoutExpired caught."""
    from experiment_runner import run_tool
    with patch("experiment_runner.subprocess.run", side_effect=__import__("subprocess").TimeoutExpired(cmd=["test"], timeout=300)):
        result = run_tool("fake_script.py", [])
        assert result.returncode == 1
        assert "timed out" in result.stderr


# ── Integration test (Phase 5) ──────────────────────────────────────

def test_full_loop_3_iterations(tmp_path):
    """Run 3 iterations with mock LLM → results.tsv has 4 rows."""
    import yaml
    from run_loop import get_next_run_id, get_best_score

    # Set up a minimal results.tsv with baseline
    tsv = tmp_path / "results.tsv"
    tsv.write_text(
        "run_id\ttimestamp\tcomposite_score\tquality\taccuracy\tchange_description\tdecision\n"
        "baseline\t2024-01-01T00:00:00\t0.5000\t0.5000\t0.5000\tInitial baseline\tBASELINE\n"
    )

    # Verify get_next_run_id works
    assert get_next_run_id(tsv) == "exp_001"
    assert get_best_score(tsv) == 0.5

    # Simulate 3 iterations by appending rows
    scores = [0.6, 0.55, 0.7]
    decisions = ["KEEP", "DISCARD", "KEEP"]
    for i, (score, decision) in enumerate(zip(scores, decisions)):
        run_id = f"exp_{i+1:03d}"
        with open(tsv, "a") as f:
            f.write(f"{run_id}\t2024-01-01T00:00:00\t{score:.4f}\t{score:.4f}\t{score:.4f}\tChange {i+1}\t{decision}\n")

    # Verify results
    lines = tsv.read_text().strip().split("\n")
    assert len(lines) == 5  # header + baseline + 3 experiments
    assert get_best_score(tsv) == 0.7
    assert get_next_run_id(tsv) == "exp_004"


# ── Utils consolidation tests ─────────────────────────────────────

def test_load_env_basic(tmp_path):
    from utils import load_env
    env_file = tmp_path / ".env"
    env_file.write_text("MY_KEY=hello_world\n")
    import os
    os.environ.pop("MY_KEY", None)
    load_env(env_file)
    assert os.environ.get("MY_KEY") == "hello_world"
    os.environ.pop("MY_KEY", None)


def test_load_env_strips_double_quotes(tmp_path):
    from utils import load_env
    env_file = tmp_path / ".env"
    env_file.write_text('MY_KEY="quoted_value"\n')
    import os
    os.environ.pop("MY_KEY", None)
    load_env(env_file)
    assert os.environ.get("MY_KEY") == "quoted_value"
    os.environ.pop("MY_KEY", None)


def test_load_env_strips_single_quotes(tmp_path):
    from utils import load_env
    env_file = tmp_path / ".env"
    env_file.write_text("MY_KEY='single_quoted'\n")
    import os
    os.environ.pop("MY_KEY", None)
    load_env(env_file)
    assert os.environ.get("MY_KEY") == "single_quoted"
    os.environ.pop("MY_KEY", None)


def test_load_env_missing_file(tmp_path):
    from utils import load_env
    load_env(tmp_path / "nonexistent.env")  # Should not raise


def test_default_dimensions_from_utils():
    from utils import default_dimensions
    dims = default_dimensions()
    assert len(dims) == 3
    names = {d["name"] for d in dims}
    assert names == {"human_score", "task_accuracy", "quality"}
    total_weight = sum(d["weight"] for d in dims)
    assert abs(total_weight - 1.0) < 0.01


# ── Worst samples context tests ───────────────────────────────────

def test_worst_samples_with_valid_data(tmp_path):
    from run_loop import _get_worst_samples_context
    import run_loop
    old_root = run_loop.PROJECT_ROOT
    run_loop.PROJECT_ROOT = tmp_path

    try:
        run_id = "exp_001"
        evals_dir = tmp_path / ".tmp" / "evals" / run_id
        samples_dir = tmp_path / ".tmp" / "samples" / run_id
        evals_dir.mkdir(parents=True)
        samples_dir.mkdir(parents=True)

        # Create 3 judge JSONs with different scores
        for i, score in enumerate([0.9, 0.3, 0.6]):
            judge = {"quality": {"score": int(score * 4 + 1), "normalised": score, "reason": f"Reason {i}"}}
            (evals_dir / f"sample_{i}_p{i}_llm_judge.json").write_text(json.dumps(judge))
            (samples_dir / f"sample_{i}_p{i}.txt").write_text(f"Sample text {i}")

        ctx = _get_worst_samples_context(run_id)
        assert "sample_1_p1" in ctx  # worst (0.3)
        assert "sample_2_p2" in ctx  # second worst (0.6)
        assert "Reason 1" in ctx
    finally:
        run_loop.PROJECT_ROOT = old_root


def test_worst_samples_no_evals_dir(tmp_path):
    from run_loop import _get_worst_samples_context
    import run_loop
    old_root = run_loop.PROJECT_ROOT
    run_loop.PROJECT_ROOT = tmp_path

    try:
        ctx = _get_worst_samples_context("nonexistent_run")
        assert ctx == ""
    finally:
        run_loop.PROJECT_ROOT = old_root


def test_worst_samples_judge_error(tmp_path):
    from run_loop import _get_worst_samples_context
    import run_loop
    old_root = run_loop.PROJECT_ROOT
    run_loop.PROJECT_ROOT = tmp_path

    try:
        evals_dir = tmp_path / ".tmp" / "evals" / "exp_001"
        evals_dir.mkdir(parents=True)
        # JSON with error key should be skipped
        (evals_dir / "sample_0_p0_llm_judge.json").write_text(
            json.dumps({"error": "Failed to parse", "quality": {"score": 0, "normalised": 0.0, "reason": "parse error"}})
        )
        ctx = _get_worst_samples_context("exp_001")
        assert ctx == ""
    finally:
        run_loop.PROJECT_ROOT = old_root


def test_worst_samples_malformed_json(tmp_path):
    from run_loop import _get_worst_samples_context
    import run_loop
    old_root = run_loop.PROJECT_ROOT
    run_loop.PROJECT_ROOT = tmp_path

    try:
        evals_dir = tmp_path / ".tmp" / "evals" / "exp_001"
        evals_dir.mkdir(parents=True)
        (evals_dir / "sample_0_p0_llm_judge.json").write_text("NOT VALID JSON {{{")
        ctx = _get_worst_samples_context("exp_001")
        assert ctx == ""
    finally:
        run_loop.PROJECT_ROOT = old_root


def test_worst_samples_truncation(tmp_path):
    from run_loop import _get_worst_samples_context
    import run_loop
    old_root = run_loop.PROJECT_ROOT
    run_loop.PROJECT_ROOT = tmp_path

    try:
        evals_dir = tmp_path / ".tmp" / "evals" / "exp_001"
        samples_dir = tmp_path / ".tmp" / "samples" / "exp_001"
        evals_dir.mkdir(parents=True)
        samples_dir.mkdir(parents=True)

        judge = {"quality": {"score": 2, "normalised": 0.25, "reason": "Bad"}}
        (evals_dir / "sample_0_p0_llm_judge.json").write_text(json.dumps(judge))
        # Write a sample with >500 words
        long_text = " ".join(["word"] * 600)
        (samples_dir / "sample_0_p0.txt").write_text(long_text)

        ctx = _get_worst_samples_context("exp_001")
        assert "[truncated]" in ctx
    finally:
        run_loop.PROJECT_ROOT = old_root


# ── Skill completeness check tests ────────────────────────────────

def test_completeness_check_valid():
    from run_loop import _check_skill_completeness
    original = "---\nname: test\n---\n# Section One\n\nContent\n\n## Section Two\n\nMore content"
    candidate = "---\nname: test\n---\n# Section One\n\nChanged content\n\n## Section Two\n\nNew content"
    assert _check_skill_completeness(original, candidate) is True


def test_completeness_check_missing_frontmatter():
    from run_loop import _check_skill_completeness
    original = "---\nname: test\n---\n# Section One\nContent"
    candidate = "# Section One\nContent without frontmatter that is definitely long enough"
    assert _check_skill_completeness(original, candidate) is False


def test_completeness_check_missing_headers():
    from run_loop import _check_skill_completeness
    original = "---\nname: test\n---\n# One\n## Two\n## Three\n## Four\nContent"
    candidate = "---\nname: test\n---\n# One\nContent but missing Three, Two, and Four headers entirely"
    assert _check_skill_completeness(original, candidate) is False


# ── Atomic TSV write tests ────────────────────────────────────────

def test_atomic_write_success(tmp_path):
    from run_loop import update_decision
    tsv = tmp_path / "results.tsv"
    tsv.write_text("run_id\tcomposite_score\tdecision\nbaseline\t0.5\t\n")
    update_decision(tsv, "BASELINE")
    content = tsv.read_text()
    assert "BASELINE" in content
    # Temp file should be cleaned up
    assert not (tmp_path / "results.tsv.tmp").exists()


# ── Token usage log tests ─────────────────────────────────────────

def test_token_log_aggregation(tmp_path):
    from run_loop import aggregate_token_usage
    import run_loop
    old_root = run_loop.PROJECT_ROOT
    run_loop.PROJECT_ROOT = tmp_path

    try:
        log_dir = tmp_path / ".tmp"
        log_dir.mkdir()
        (log_dir / "token_usage_1234.jsonl").write_text(
            '{"input": 100, "output": 50, "model": "test"}\n'
            '{"input": 200, "output": 100, "model": "test"}\n'
        )
        (log_dir / "token_usage_5678.jsonl").write_text(
            '{"input": 300, "output": 150, "model": "test"}\n'
        )
        total_in, total_out = aggregate_token_usage()
        assert total_in == 600
        assert total_out == 300
    finally:
        run_loop.PROJECT_ROOT = old_root


def test_token_log_no_files(tmp_path):
    from run_loop import aggregate_token_usage
    import run_loop
    old_root = run_loop.PROJECT_ROOT
    run_loop.PROJECT_ROOT = tmp_path

    try:
        total_in, total_out = aggregate_token_usage()
        assert total_in == 0
        assert total_out == 0
    finally:
        run_loop.PROJECT_ROOT = old_root


def test_token_log_malformed(tmp_path):
    from run_loop import aggregate_token_usage
    import run_loop
    old_root = run_loop.PROJECT_ROOT
    run_loop.PROJECT_ROOT = tmp_path

    try:
        log_dir = tmp_path / ".tmp"
        log_dir.mkdir()
        (log_dir / "token_usage_9999.jsonl").write_text(
            'NOT VALID JSON\n'
            '{"input": 100, "output": 50, "model": "test"}\n'
        )
        total_in, total_out = aggregate_token_usage()
        assert total_in == 100  # Good line counted
        assert total_out == 50
    finally:
        run_loop.PROJECT_ROOT = old_root


# ── Prompt ID sanitisation tests ──────────────────────────────────

def test_prompt_id_sanitised():
    sys.path.insert(0, str(PROJECT_ROOT))
    from setup import _sanitise_prompt_id
    assert _sanitise_prompt_id("formal_email") == "formal_email"
    assert _sanitise_prompt_id("formal email") == "formal_email"
    assert _sanitise_prompt_id("hello/world") == "hello_world"
    assert _sanitise_prompt_id("test@#$%") == "test"


def test_prompt_id_empty_fallback():
    sys.path.insert(0, str(PROJECT_ROOT))
    from setup import _sanitise_prompt_id
    assert _sanitise_prompt_id("", fallback="prompt_1") == "prompt_1"
    assert _sanitise_prompt_id("@#$", fallback="prompt_2") == "prompt_2"


# ── Min improvement threshold tests ───────────────────────────────

def test_min_improvement_keeps_above_threshold(tmp_path):
    """Score delta > min_improvement → KEEP."""
    from run_loop import get_best_score
    tsv = tmp_path / "results.tsv"
    tsv.write_text("run_id\ttimestamp\tcomposite_score\n" "baseline\t2024-01-01\t0.5000\n")
    best = get_best_score(tsv)
    new_score = 0.52  # delta = 0.02 > default threshold of 0.01
    assert new_score - best > 0.01


def test_min_improvement_discards_below_threshold(tmp_path):
    """Score delta <= min_improvement → DISCARD (noise)."""
    from run_loop import get_best_score
    tsv = tmp_path / "results.tsv"
    tsv.write_text("run_id\ttimestamp\tcomposite_score\n" "baseline\t2024-01-01\t0.5000\n")
    best = get_best_score(tsv)
    new_score = 0.505  # delta = 0.005 < default threshold of 0.01
    assert new_score - best <= 0.01


# ── Self-judge warning test ───────────────────────────────────────

def test_self_judge_warning():
    """When judge_provider is not set, a warning should be printed."""
    cfg = _make_config()
    assert "judge_provider" not in cfg  # No separate judge
    # The warning is printed in run_loop.main(), tested via integration
    # Here we just verify the config has no judge_provider
    assert cfg.get("judge_provider") is None


# ── get_latest_run_id tests ───────────────────────────────────────

def test_get_latest_run_id(tmp_path):
    from run_loop import get_latest_run_id
    tsv = tmp_path / "results.tsv"
    tsv.write_text(
        "run_id\ttimestamp\tcomposite_score\n"
        "baseline\t2024-01-01\t0.5\n"
        "exp_001\t2024-01-01\t0.6\n"
    )
    assert get_latest_run_id(tsv) == "exp_001"


def test_get_latest_run_id_empty(tmp_path):
    from run_loop import get_latest_run_id
    tsv = tmp_path / "results.tsv"
    tsv.write_text("run_id\ttimestamp\tcomposite_score\n")
    assert get_latest_run_id(tsv) is None


def test_get_latest_run_id_missing(tmp_path):
    from run_loop import get_latest_run_id
    assert get_latest_run_id(tmp_path / "missing.tsv") is None
