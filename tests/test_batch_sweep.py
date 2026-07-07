"""
Unit tests for tools/batch_sweep.py (Anthropic Batches API sweep).
All API interaction is mocked — the live path needs an Anthropic key.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

TOOLS_DIR = Path(__file__).parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))


def _dimensions():
    return [
        {"name": "quality", "weight": 0.5, "rubric": "Is it good?"},
        {"name": "accuracy", "weight": 0.5, "rubric": "Is it accurate?"},
    ]


def test_generation_requests_shape():
    from batch_sweep import build_generation_requests
    prompts = [{"id": "p1", "prompt": "write a haiku"}, {"id": "p2", "prompt": "write a memo"}]
    reqs = build_generation_requests("SKILL TEXT", prompts, "claude-sonnet-5", replicates=2)
    assert len(reqs) == 4
    ids = [r["custom_id"] for r in reqs]
    assert len(set(ids)) == 4, "custom_ids must be unique"
    assert ids[0] == "gen__sample_0_p1_r0"
    # Shared skill prefix must carry the 1h cache marker
    sys_block = reqs[0]["params"]["system"][0]
    assert sys_block["text"] == "SKILL TEXT"
    assert sys_block["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


def test_generation_requests_legacy_names_single_replicate():
    from batch_sweep import build_generation_requests
    reqs = build_generation_requests("S", [{"id": "p1", "prompt": "x"}], "m", replicates=1)
    assert reqs[0]["custom_id"] == "gen__sample_0_p1"


def test_judge_requests_have_schema_and_cache():
    from batch_sweep import build_judge_requests
    samples = {"sample_0_p1_r0": "output text"}
    reqs = build_judge_requests(samples, _dimensions(), "claude-haiku-4-5")
    assert len(reqs) == 1
    params = reqs[0]["params"]
    assert reqs[0]["custom_id"] == "judge__sample_0_p1_r0"
    assert params["output_config"]["format"]["type"] == "json_schema"
    schema = params["output_config"]["format"]["schema"]
    assert set(schema["properties"]) == {"quality", "accuracy"}
    assert params["system"][0]["cache_control"]["ttl"] == "1h"
    assert "output text" in params["messages"][0]["content"]


def test_normalise_judge_result_valid():
    from batch_sweep import normalise_judge_result
    raw = json.dumps({
        "quality": {"score": 4, "reason": "good"},
        "accuracy": {"score": 5, "reason": "spot on"},
    })
    result = normalise_judge_result(raw, _dimensions())
    assert result["quality"]["normalised"] == 0.75
    assert result["accuracy"]["normalised"] == 1.0
    assert "error" not in result


def test_normalise_judge_result_malformed_is_error():
    from batch_sweep import normalise_judge_result
    result = normalise_judge_result("not json", _dimensions())
    assert "error" in result


def _fake_batch_client(responses: dict):
    """Fake anthropic client whose batch completes immediately."""
    client = MagicMock()
    batch = SimpleNamespace(id="batch_1", processing_status="ended", request_counts=None)
    client.messages.batches.create.return_value = batch
    client.messages.batches.retrieve.return_value = batch

    results = []
    for custom_id, text in responses.items():
        msg = SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])
        results.append(SimpleNamespace(
            custom_id=custom_id,
            result=SimpleNamespace(type="succeeded", message=msg),
        ))
    # One failure to verify it is reported and omitted
    results.append(SimpleNamespace(
        custom_id="gen__sample_9_broken",
        result=SimpleNamespace(type="errored", message=None),
    ))
    client.messages.batches.results.return_value = iter(results)
    return client


def test_run_batch_correlates_by_custom_id():
    from batch_sweep import run_batch
    client = _fake_batch_client({
        "gen__sample_0_p1_r0": "first output",
        "gen__sample_1_p2_r0": "second output",
    })
    outputs = run_batch(client, [{"custom_id": "x", "params": {}}], "generation")
    assert outputs == {
        "gen__sample_0_p1_r0": "first output",
        "gen__sample_1_p2_r0": "second output",
    }


def test_require_anthropic_rejects_other_providers():
    from batch_sweep import _require_anthropic
    with pytest.raises(SystemExit):
        _require_anthropic({"provider": "openai"})
    with pytest.raises(SystemExit):
        _require_anthropic({"provider": "anthropic", "judge_provider": "gemini"})
    # Valid: anthropic everywhere (judge defaults to provider)
    _require_anthropic({"provider": "anthropic"})
