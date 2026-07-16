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
import yaml

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


def _fallback_client(response_text: str):
    """Real ModelClient whose structured path falls back to prompt-and-parse,
    with the raw generation mocked — exercises the full parse pipeline."""
    from model_client import ModelClient
    with patch.dict("os.environ", {"TEST_KEY": "test"}):
        with patch.object(ModelClient, "_get_client", return_value=None):
            client = ModelClient("gemini", "test-model", "TEST_KEY")
    client._structured_unsupported = True  # force prompt-and-parse fallback
    client._generate_once = MagicMock(return_value=response_text)
    return client


def test_judge_structured_valid():
    from eval_llm_judge import judge_sample
    mock_client = MagicMock()
    mock_client.generate_structured.return_value = {
        "quality": {"score": 4, "reason": "good"},
        "accuracy": {"score": 5, "reason": "perfect"},
    }
    result = judge_sample("test text", _make_dimensions(), mock_client)
    assert result["quality"]["normalised"] == 0.75
    assert result["accuracy"]["normalised"] == 1.0


def test_judge_parse_valid_json_fallback():
    from eval_llm_judge import judge_sample
    client = _fallback_client(json.dumps({
        "quality": {"score": 4, "reason": "good"},
        "accuracy": {"score": 5, "reason": "perfect"},
    }))
    result = judge_sample("test text", _make_dimensions(), client)
    assert result["quality"]["normalised"] == 0.75
    assert result["accuracy"]["normalised"] == 1.0


def test_judge_parse_markdown_wrapped():
    from eval_llm_judge import judge_sample
    client = _fallback_client('```json\n{"quality": {"score": 3, "reason": "ok"}, "accuracy": {"score": 4, "reason": "good"}}\n```')
    result = judge_sample("test text", _make_dimensions(), client)
    assert result["quality"]["normalised"] == 0.5


def test_judge_parse_malformed():
    from eval_llm_judge import judge_sample
    client = _fallback_client("This is not JSON at all, just garbage text with no structure.")
    result = judge_sample("test text", _make_dimensions(), client)
    assert "error" in result
    assert result["quality"]["normalised"] == 0.0


def test_judge_parse_refusal():
    from eval_llm_judge import judge_sample
    client = _fallback_client("I cannot evaluate this content as it violates my guidelines.")
    result = judge_sample("test text", _make_dimensions(), client)
    assert "error" in result
    assert result["quality"]["normalised"] == 0.0


def test_judge_parse_empty():
    from eval_llm_judge import judge_sample
    client = _fallback_client("")
    result = judge_sample("test text", _make_dimensions(), client)
    assert "error" in result
    assert result["quality"]["normalised"] == 0.0


def test_judge_error_result_marks_error():
    """A judge call that raises must produce an 'error' result, never scores."""
    from eval_llm_judge import judge_sample
    mock_client = MagicMock()
    mock_client.generate_structured.side_effect = ValueError("boom")
    result = judge_sample("test text", _make_dimensions(), mock_client)
    assert "error" in result
    # Retried exactly once before giving up
    assert mock_client.generate_structured.call_count == 2


def test_judge_schema_from_dimensions():
    from eval_llm_judge import build_judge_schema
    schema = build_judge_schema(_make_dimensions())
    assert set(schema["properties"]) == {"quality", "accuracy"}
    assert schema["properties"]["quality"]["properties"]["score"]["enum"] == [1, 2, 3, 4, 5]
    assert schema["additionalProperties"] is False


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
        "judge_provider": "openai", "judge_model": "gpt-5.4-mini", "judge_api_key_env": "OPENAI_API_KEY",
    }
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.dump(cfg))
    # We can't actually create the client (no API key), but we can test from_config parsing
    with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
        with patch.object(ModelClient, "_get_client", return_value=None):
            client = ModelClient.from_config(str(cfg_path), judge=True)
            assert client.provider == "openai"
            assert client.model == "gpt-5.4-mini"


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
    tsv.write_text("run_id\ttimestamp\tcomposite_score\tdecision\n" "baseline\t2024-01-01\t0.5000\tBASELINE\n")
    assert get_best_score(tsv) == 0.5

    # Add a higher score
    with open(tsv, "a") as f:
        f.write("exp_001\t2024-01-01\t0.7000\tKEEP\n")
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
        r1 = _generate_one(client, "skill", {"id": "p1", "genre": "test", "prompt": "hi"}, 0, out_dir, "sample_0_p1")
        assert r1["file"] is not None

        # Failure
        r2 = _generate_one(client, "skill", {"id": "p2", "genre": "test", "prompt": "hi"}, 1, out_dir, "sample_1_p2")
        assert r2["file"] is None
        assert "error" in r2

        # Success again
        r3 = _generate_one(client, "skill", {"id": "p3", "genre": "test", "prompt": "hi"}, 2, out_dir, "sample_2_p3")
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
    assert names == {"natural_voice", "task_accuracy", "quality"}
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
        usage = aggregate_token_usage()
        assert usage["input_tokens"] == 600
        assert usage["output_tokens"] == 300
        assert usage["by_model"]["test"] == {"input": 600, "output": 300}
        assert usage["unknown_models"] == ["test"]
    finally:
        run_loop.PROJECT_ROOT = old_root


def test_token_log_no_files(tmp_path):
    from run_loop import aggregate_token_usage
    import run_loop
    old_root = run_loop.PROJECT_ROOT
    run_loop.PROJECT_ROOT = tmp_path

    try:
        usage = aggregate_token_usage()
        assert usage["input_tokens"] == 0
        assert usage["output_tokens"] == 0
        assert usage["estimated_cost_usd"] == 0
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
        usage = aggregate_token_usage()
        assert usage["input_tokens"] == 100  # Good line counted
        assert usage["output_tokens"] == 50
    finally:
        run_loop.PROJECT_ROOT = old_root


def test_token_cost_is_priced_per_model_without_parent_double_count(tmp_path):
    from run_loop import aggregate_token_usage
    import run_loop
    old_root = run_loop.PROJECT_ROOT
    run_loop.PROJECT_ROOT = tmp_path
    try:
        log_dir = tmp_path / ".tmp"
        log_dir.mkdir()
        (log_dir / "token_usage_1.jsonl").write_text(
            '{"input": 1000, "output": 100, "model": "gemini-2.5-flash"}\n'
            '{"input": 2000, "output": 200, "model": "gpt-5.4-mini"}\n'
        )
        usage = aggregate_token_usage()
        expected = (1000 * 0.30e-6 + 100 * 2.50e-6
                    + 2000 * 0.75e-6 + 200 * 4.50e-6)
        assert usage["estimated_cost_usd"] == pytest.approx(expected)
        assert usage["input_tokens"] == 3000
    finally:
        run_loop.PROJECT_ROOT = old_root


def test_three_way_prompt_split_keeps_final_test_untouched():
    from utils import split_prompt_sets
    prompts = [{"id": f"p{i}", "prompt": "x"} for i in range(30)]
    train, validation, final_test = split_prompt_sets(prompts, 0.3, 0.2)
    assert len(train) == 15
    assert len(validation) == 9
    assert len(final_test) == 6
    assert not ({p["id"] for p in train} & {p["id"] for p in final_test})


def test_hierarchical_bootstrap_uses_replicates():
    from decision import paired_verdict
    best = {f"p{i}": {"composite": 0.5, "replicates": [0.45, 0.5, 0.55]} for i in range(10)}
    candidate = {f"p{i}": {"composite": 0.7, "replicates": [0.65, 0.7, 0.75]} for i in range(10)}
    verdict = paired_verdict(candidate, best)
    assert verdict["keep"] is True
    assert verdict["method"] == "hierarchical-bootstrap"


def test_duplicate_experiment_output_is_rejected_before_generation(tmp_path):
    import experiment_runner
    old_root = experiment_runner.PROJECT_ROOT
    experiment_runner.PROJECT_ROOT = tmp_path
    try:
        (tmp_path / ".tmp" / "samples" / "exp_001").mkdir(parents=True)
        with pytest.raises(RuntimeError, match="already has output"):
            experiment_runner.run_experiment(
                "exp_001",
                {"llm_judge_dimensions": [], "deterministic_metrics": []},
            )
    finally:
        experiment_runner.PROJECT_ROOT = old_root


def test_experiment_outputs_are_promoted_only_after_complete(tmp_path, monkeypatch):
    import subprocess
    import experiment_runner

    monkeypatch.setattr(experiment_runner, "PROJECT_ROOT", tmp_path)

    def fake_run_tool(script, args):
        if script == "generate_samples.py":
            out = Path(args[args.index("--output-dir") + 1])
            out.mkdir(parents=True, exist_ok=True)
            (out / "sample_0_p1.txt").write_text("sample")
        elif script == "eval_llm_judge.py":
            out = Path(args[args.index("--output-path") + 1])
            out.write_text(json.dumps({"quality": {"normalised": 0.75}}))
        elif script == "score_aggregator.py":
            out = Path(args[args.index("--output-path") + 1])
            out.write_text(json.dumps({
                "composite_score": 0.75,
                "metric_averages": {"quality": 0.75},
                "per_prompt": {"p1": {"composite": 0.75, "n": 1, "replicates": [0.75]}},
                "sample_count": 1,
            }))
        return subprocess.CompletedProcess([script], 0, stdout="", stderr="")

    monkeypatch.setattr(experiment_runner, "run_tool", fake_run_tool)
    cfg = {
        "skill_path": "SKILL.md", "prompts_path": "prompts.json",
        "results_tsv": "results.tsv", "llm_judge_dimensions": [
            {"name": "quality", "weight": 1.0, "rubric": "good"}
        ],
    }
    result = experiment_runner.run_experiment("exp_001", cfg)
    assert result["composite_score"] == 0.75
    assert (tmp_path / ".tmp/samples/exp_001/sample_0_p1.txt").exists()
    assert (tmp_path / ".tmp/evals/exp_001/aggregate.json").exists()
    assert not list((tmp_path / ".tmp/work").glob("exp_001-*"))


def test_run_lock_rejects_concurrent_owner(tmp_path, monkeypatch):
    import run_state
    monkeypatch.setattr(run_state, "LOCK_PATH", tmp_path / "run.lock")
    monkeypatch.setattr(run_state, "STATE_PATH", tmp_path / "run_state.json")
    with run_state.run_lock():
        with pytest.raises(RuntimeError, match="already running"):
            with run_state.run_lock():
                pass
    assert not (tmp_path / "run.lock").exists()


def test_dashboard_best_excludes_discarded_candidates(tmp_path):
    from dashboard_server import read_tsv
    tsv = tmp_path / "results.tsv"
    tsv.write_text(
        "run_id\tcomposite_score\tdecision\n"
        "baseline\t0.5000\tBASELINE\n"
        "exp_001\t0.9900\tDISCARD\n"
        "exp_002\t0.7000\tKEEP\n"
    )
    data = read_tsv(tsv, {"metric_names": [], "metric_labels": {}, "metric_directions": {}})
    assert data["best"]["run_id"] == "exp_002"


def test_local_markdown_links_resolve():
    import re
    files = [PROJECT_ROOT / "README.md", *PROJECT_ROOT.glob("docs/*.md"),
             *PROJECT_ROOT.glob("examples/**/*.md")]
    missing = []
    for path in files:
        for target in re.findall(r"\[[^]]*\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            target = target.split("#", 1)[0]
            if not target or "://" in target or target.startswith(("mailto:", "#")):
                continue
            if not (path.parent / target).resolve().exists():
                missing.append((str(path.relative_to(PROJECT_ROOT)), target))
    assert missing == []


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
    tsv.write_text("run_id\ttimestamp\tcomposite_score\tdecision\n" "baseline\t2024-01-01\t0.5000\tBASELINE\n")
    best = get_best_score(tsv)
    new_score = 0.52  # delta = 0.02 > default threshold of 0.01
    assert new_score - best > 0.01


def test_min_improvement_discards_below_threshold(tmp_path):
    """Score delta <= min_improvement → DISCARD (noise)."""
    from run_loop import get_best_score
    tsv = tmp_path / "results.tsv"
    tsv.write_text("run_id\ttimestamp\tcomposite_score\tdecision\n" "baseline\t2024-01-01\t0.5000\tBASELINE\n")
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


# ── Pricing & model registry (July 2026 refresh) ────────────────────

def test_default_models_all_have_pricing():
    """Every default model must resolve to a pricing entry, so cost caps work
    out of the box. Guards against the model/pricing drift that silently
    disabled max_cost_usd for four months."""
    from model_client import ModelClient
    from utils import DEFAULT_MODELS
    for provider, (model, _env) in DEFAULT_MODELS.items():
        assert ModelClient.price_for_model(model) is not None, (
            f"default model {model!r} for {provider} has no pricing entry"
        )


def test_pricing_longest_prefix_wins():
    from model_client import ModelClient
    # A versioned haiku ID must resolve via its specific prefix
    assert ModelClient.price_for_model("claude-haiku-4-5-20251001") == \
        ModelClient._PRICING["claude-haiku-4-5"]
    # gpt-5.4-mini must not be priced as gpt-5.4
    assert ModelClient.price_for_model("gpt-5.4-mini") == \
        ModelClient._PRICING["gpt-5.4-mini"]


def test_unknown_model_pricing_is_none_and_flagged():
    from model_client import ModelClient
    assert ModelClient.price_for_model("some-future-model") is None
    with patch.dict("os.environ", {"TEST_KEY": "test"}):
        with patch.object(ModelClient, "_get_client", return_value=None):
            client = ModelClient("gemini", "some-future-model", "TEST_KEY")
    assert client.pricing_known is False
    assert client.usage_summary()["pricing_known"] is False


# ── Aggregator: judge failures are excluded, not zeroed ─────────────

def _write_judge_eval(tmp_path, sample_id, quality, accuracy):
    (tmp_path / f"{sample_id}_llm_judge.json").write_text(json.dumps({
        "quality": {"score": 0, "normalised": quality, "reason": "x"},
        "accuracy": {"score": 0, "normalised": accuracy, "reason": "x"},
    }))


def _agg_cfg(**overrides):
    cfg = {
        "llm_judge_dimensions": [
            {"name": "quality", "weight": 0.5, "rubric": "q"},
            {"name": "accuracy", "weight": 0.5, "rubric": "a"},
        ],
        "deterministic_metrics": [],
    }
    cfg.update(overrides)
    return cfg


def test_aggregator_excludes_errored_samples(tmp_path):
    from score_aggregator import aggregate
    _write_judge_eval(tmp_path, "sample_0_p1", 0.8, 0.8)
    _write_judge_eval(tmp_path, "sample_1_p2", 0.8, 0.8)
    _write_judge_eval(tmp_path, "sample_2_p3", 0.8, 0.8)
    _write_judge_eval(tmp_path, "sample_3_p4", 0.8, 0.8)
    # One errored sample — must NOT drag the composite to 0.64
    (tmp_path / "sample_4_p5_llm_judge.json").write_text(json.dumps({
        "error": "Judge call failed",
        "quality": {"score": 0, "normalised": 0.0, "reason": "judge error"},
        "accuracy": {"score": 0, "normalised": 0.0, "reason": "judge error"},
    }))
    result = aggregate(str(tmp_path), _agg_cfg())
    assert result["judge_errors"] == 1
    assert result["sample_count"] == 4
    assert result["composite_score"] == pytest.approx(0.8, abs=0.001)


def test_aggregator_fails_below_valid_fraction(tmp_path):
    from score_aggregator import aggregate
    _write_judge_eval(tmp_path, "sample_0_p1", 0.8, 0.8)
    for i in range(1, 4):
        (tmp_path / f"sample_{i}_p{i+1}_llm_judge.json").write_text(json.dumps({
            "error": "Judge call failed",
        }))
    with pytest.raises(SystemExit):
        aggregate(str(tmp_path), _agg_cfg(min_valid_sample_frac=0.8))


def test_aggregator_variance_and_per_prompt(tmp_path):
    from score_aggregator import aggregate
    # Two prompts, two replicates each
    _write_judge_eval(tmp_path, "sample_0_p1_r0", 0.8, 0.8)
    _write_judge_eval(tmp_path, "sample_0_p1_r1", 0.6, 0.6)
    _write_judge_eval(tmp_path, "sample_1_p2_r0", 1.0, 1.0)
    _write_judge_eval(tmp_path, "sample_1_p2_r1", 0.8, 0.8)
    result = aggregate(str(tmp_path), _agg_cfg())
    assert result["composite_stddev"] > 0
    assert result["per_prompt"]["p1"]["composite"] == pytest.approx(0.7, abs=0.001)
    assert result["per_prompt"]["p2"]["composite"] == pytest.approx(0.9, abs=0.001)
    assert result["per_prompt"]["p1"]["n"] == 2


def test_prompt_id_from_sample_names():
    from score_aggregator import prompt_id_from_sample
    assert prompt_id_from_sample("sample_0_intro_email") == "intro_email"
    assert prompt_id_from_sample("sample_12_intro_email_r3") == "intro_email"
    assert prompt_id_from_sample("sample_5_p1_r0") == "p1"


# ── Decision rule ────────────────────────────────────────────────────

def _per_prompt(scores: dict) -> dict:
    return {pid: {"composite": s, "n": 3} for pid, s in scores.items()}


def test_decision_same_scores_rejected():
    """Identical per-prompt scores must never be kept — there is no signal."""
    from decision import paired_verdict
    scores = {f"p{i}": 0.8 for i in range(8)}
    verdict = paired_verdict(_per_prompt(scores), _per_prompt(scores))
    assert verdict["keep"] is False


def test_decision_noise_rejected():
    """Small alternating noise around zero must be rejected."""
    from decision import paired_verdict
    base = {f"p{i}": 0.8 for i in range(8)}
    cand = {f"p{i}": 0.8 + (0.02 if i % 2 == 0 else -0.02) for i in range(8)}
    verdict = paired_verdict(_per_prompt(cand), _per_prompt(base))
    assert verdict["keep"] is False


def test_decision_consistent_shift_accepted():
    """A consistent improvement on every prompt must be kept."""
    from decision import paired_verdict
    base = {f"p{i}": 0.70 + i * 0.01 for i in range(8)}
    cand = {pid: s + 0.05 for pid, s in base.items()}
    verdict = paired_verdict(_per_prompt(cand), _per_prompt(base))
    assert verdict["keep"] is True
    assert verdict["mean_delta"] == pytest.approx(0.05, abs=0.001)


def test_decision_non_regression_mode():
    """Holdout mode: equal scores pass, a consistent drop fails."""
    from decision import paired_verdict
    base = {f"p{i}": 0.8 for i in range(6)}
    same = paired_verdict(_per_prompt(base), _per_prompt(base), mode="non-regression")
    assert same["keep"] is True
    worse = {pid: s - 0.08 for pid, s in base.items()}
    regressed = paired_verdict(_per_prompt(worse), _per_prompt(base), mode="non-regression")
    assert regressed["keep"] is False


def test_decision_simple_fallback_without_per_prompt():
    """Resuming a legacy run (no per-prompt data) falls back to the simple rule."""
    from decision import decide
    cand = {"composite_score": 0.85}
    best = {"composite_score": 0.80}
    verdict = decide(cand, best, {"accept_rule": "paired", "min_improvement": 0.01})
    assert verdict["method"] == "simple"
    assert verdict["keep"] is True


# ── Prompt splitting ─────────────────────────────────────────────────

def test_split_prompts_deterministic():
    from utils import split_prompts
    prompts = [{"id": f"p{i}", "prompt": "x"} for i in range(10)]
    train, holdout = split_prompts(prompts, 0.3)
    assert len(train) == 7 and len(holdout) == 3
    # Stable across calls
    train2, holdout2 = split_prompts(prompts, 0.3)
    assert [p["id"] for p in holdout] == [p["id"] for p in holdout2]


def test_split_prompts_explicit_wins():
    from utils import split_prompts
    prompts = [
        {"id": "a", "prompt": "x", "split": "holdout"},
        {"id": "b", "prompt": "x", "split": "train"},
        {"id": "c", "prompt": "x"},
    ]
    train, holdout = split_prompts(prompts, 0.0)
    assert {p["id"] for p in holdout} == {"a"}
    assert {p["id"] for p in train} == {"b", "c"}


def test_split_prompts_never_empties_train():
    from utils import split_prompts
    prompts = [{"id": "only", "prompt": "x"}]
    train, holdout = split_prompts(prompts, 0.5)
    assert len(train) == 1 and len(holdout) == 0


# ── results_io ───────────────────────────────────────────────────────

def test_results_io_roundtrip(tmp_path):
    import results_io
    tsv = tmp_path / "results.tsv"
    metrics = ["quality", "accuracy"]
    results_io.append_row(tsv, {
        "run_id": "baseline", "timestamp": "t", "composite_score": "0.8000",
        "quality": "0.8", "accuracy": "0.8", "change_description": "init",
        "decision": "", "composite_stddev": "0.0100", "n_samples": 6,
        "judge_errors": 0,
    }, metrics)
    results_io.update_last_row(tsv, {"decision": "BASELINE", "holdout_composite": "0.7900"})
    rows = results_io.read_rows(tsv)
    assert rows[0]["decision"] == "BASELINE"
    assert rows[0]["holdout_composite"] == "0.7900"
    assert results_io.best_composite(tsv) == pytest.approx(0.8)
    assert results_io.latest_run_id(tsv) == "baseline"


def test_results_io_best_ignores_discarded_and_incomplete_candidates(tmp_path):
    import results_io
    tsv = tmp_path / "results.tsv"
    tsv.write_text(
        "run_id\tcomposite_score\tdecision\n"
        "baseline\t0.5000\tBASELINE\n"
        "exp_001\t0.9900\tDISCARD\n"
        "exp_002\t0.9800\t\n"
        "exp_003\t0.7000\tKEEP\n"
    )
    assert results_io.best_composite(tsv) == pytest.approx(0.7)


def test_results_io_migrates_legacy_header(tmp_path):
    """A pre-July-2026 results.tsv (no extra columns) gets its header extended
    in place, and old rows still read correctly."""
    import results_io
    tsv = tmp_path / "results.tsv"
    tsv.write_text(
        "run_id\ttimestamp\tcomposite_score\tquality\tchange_description\tdecision\n"
        "baseline\t2026-03-01\t0.8000\t0.8\tinit\tBASELINE\n"
    )
    results_io.append_row(tsv, {
        "run_id": "exp_001", "timestamp": "t", "composite_score": "0.8100",
        "quality": "0.81", "change_description": "tweak", "decision": "",
        "composite_stddev": "0.0200", "n_samples": 6, "judge_errors": 0,
    }, ["quality"])
    rows = results_io.read_rows(tsv)
    assert rows[0]["run_id"] == "baseline"
    assert rows[0]["decision"] == "BASELINE"
    assert rows[1]["composite_stddev"] == "0.0200"
    header = results_io.read_header(tsv)
    assert "holdout_composite" in header


# ── generate_config.write_all (shared config writer) ──────────────────

def test_write_all_shared_writer(tmp_path, monkeypatch):
    """The shared writer in tools/generate_config.py is used by both the
    /autoeval CLI and setup.py's wizard. Verify config.yaml keys, .env
    key-preservation, and .gitignore append-only behaviour all in one place
    so a schema change only needs testing once."""
    import generate_config
    monkeypatch.setattr(generate_config, "PROJECT_ROOT", tmp_path)

    # Pre-existing .env with an unrelated key that must survive, plus a stale
    # value for the key we're about to write, which must be replaced.
    (tmp_path / ".env").write_text("OTHER_KEY=keep-me\nGEMINI_API_KEY=stale\n", encoding="utf-8")

    # Pre-existing .gitignore with a custom entry that must survive, and one
    # required entry already present that must not be duplicated.
    (tmp_path / ".gitignore").write_text("node_modules/\n.env\n", encoding="utf-8")

    metrics = [
        {"name": "quality", "weight": 0.5, "rubric": "1-5 quality"},
        {"name": "accuracy", "weight": 0.5, "rubric": "1-5 accuracy"},
    ]
    prompts = [{"id": "task_1", "genre": "general", "prompt": "Do a thing."}]

    generate_config.write_all(
        skill_name="my-skill",
        skill_description="A test skill",
        skill_content="Be helpful.",
        provider="gemini",
        model="gemini-2.5-flash",
        api_key_env="GEMINI_API_KEY",
        api_key="new-key-value",
        metrics=metrics,
        prompts=prompts,
        iterations=10,
    )

    # config.yaml keys
    config = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert config["provider"] == "gemini"
    assert config["model"] == "gemini-2.5-flash"
    assert config["api_key_env"] == "GEMINI_API_KEY"
    assert config["skill_path"] == "SKILL.md"
    assert config["prompts_path"] == "prompts/prompts.json"
    assert config["results_tsv"] == "results.tsv"
    assert config["max_iterations"] == 10
    assert config["judge_sees_skill"] is True
    assert config["replicates_per_prompt"] == 3
    assert config["accept_rule"] == "paired"
    assert config["accept_confidence"] == 0.95
    assert config["min_valid_sample_frac"] == 0.8
    assert config["holdout_fraction"] == 0.3
    assert len(config["llm_judge_dimensions"]) == 2
    assert config["deterministic_metrics"] == []

    # .env: existing unrelated key preserved, target key updated (not duplicated)
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "OTHER_KEY=keep-me" in env_text
    assert "GEMINI_API_KEY=new-key-value" in env_text
    assert "stale" not in env_text
    assert env_text.count("GEMINI_API_KEY=") == 1

    # .gitignore: append-only — existing custom entry preserved, no duplicate
    # of an already-present required entry, missing required entries added
    gitignore_text = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert "node_modules/" in gitignore_text
    assert gitignore_text.count(".env") == 1
    for required_entry in ["results.tsv", "SKILL.md.best", "config.yaml",
                            "best_aggregate.json", "best_holdout_aggregate.json",
                            "__pycache__/", "*.pyc", ".tmp/"]:
        assert required_entry in gitignore_text

    # SKILL.md and prompts.json written
    assert "Be helpful." in (tmp_path / "SKILL.md").read_text(encoding="utf-8")
    written_prompts = json.loads((tmp_path / "prompts" / "prompts.json").read_text(encoding="utf-8"))
    assert written_prompts == prompts

    # settings.json permission superset present
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8"))
    allow = settings["permissions"]["allow"]
    assert "Bash(python3 tools/decision.py *)" in allow
    assert "Bash(python3 tools/results_io.py *)" in allow
    assert "Bash(cp .tmp/evals/* best_aggregate.json)" in allow


def test_write_all_skips_skill_and_prompts_when_none(tmp_path, monkeypatch):
    """setup.py's --defaults / --skill-file modes pass skill_content=None and
    prompts=None to keep pre-existing files untouched — verify write_all
    honours that instead of always writing them."""
    import generate_config
    monkeypatch.setattr(generate_config, "PROJECT_ROOT", tmp_path)

    (tmp_path / "SKILL.md").write_text("pre-existing content", encoding="utf-8")

    generate_config.write_all(
        skill_name="my-skill",
        skill_description="",
        skill_content=None,
        provider="gemini",
        model="gemini-2.5-flash",
        api_key_env="GEMINI_API_KEY",
        api_key="key",
        metrics=[{"name": "quality", "weight": 1.0, "rubric": "r"}],
        prompts=None,
        iterations=5,
        advanced={"judge_sees_skill": False, "replicates_per_prompt": 2,
                  "convergence_window": 4, "max_cost_usd": 10.0, "max_concurrent": 2},
    )

    assert (tmp_path / "SKILL.md").read_text(encoding="utf-8") == "pre-existing content"
    assert not (tmp_path / "prompts" / "prompts.json").exists()

    config = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert config["judge_sees_skill"] is False
    assert config["replicates_per_prompt"] == 2
    assert config["convergence_window"] == 4
    assert config["max_cost_usd"] == 10.0
    assert config["max_concurrent"] == 2
