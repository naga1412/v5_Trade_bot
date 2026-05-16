"""SMOKE test for the bench script. NOT a numbers gate.

Runs the script's main() in both modes with n_samples=10 and asserts:
  - JSON output validates (right keys + types)
  - p50 < p95 < p99 (sanity)
  - n_samples matches what we requested

The REAL gate (50ms/200ms thresholds) is operator-verified locally on
a quiet machine — not in CI where shared compute is noisy.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_json_objects(text: str) -> list[dict[str, Any]]:
    """Parse one or more JSON objects from stdout text (newline-separated)."""
    # The bench script emits each JSON object as a pretty-printed block.
    # We need to split them correctly. Walk through and collect complete objects.
    objects: list[dict[str, Any]] = []
    decoder = json.JSONDecoder()
    pos = 0
    text = text.strip()
    while pos < len(text):
        # Skip whitespace
        while pos < len(text) and text[pos] in " \t\n\r":
            pos += 1
        if pos >= len(text):
            break
        obj, end = decoder.raw_decode(text, pos)
        objects.append(obj)
        pos = end
    return objects


_REQUIRED_STAT_KEYS = {
    "mode", "n_warmup", "n_samples",
    "p50_ms", "p95_ms", "p99_ms", "p999_ms",
    "mean_ms", "stdev_ms", "min_ms", "max_ms",
    "python_version", "platform", "timestamp_utc",
}

_GATE_KEYS = {
    "delta_p50_ms", "delta_p99_ms",
    "p50_budget_ms", "p99_budget_ms",
    "verdict", "failed_budgets",
}


def _assert_stat_object(obj: dict[str, Any], expected_mode: str, expected_n: int) -> None:
    """Assert that a benchmark result object has the right structure and values."""
    missing = _REQUIRED_STAT_KEYS - set(obj.keys())
    assert not missing, f"Result object missing keys: {missing}"

    assert obj["mode"] == expected_mode, f"Expected mode={expected_mode!r}, got {obj['mode']!r}"
    assert obj["n_samples"] == expected_n, f"Expected n_samples={expected_n}, got {obj['n_samples']}"

    # Sanity: percentile ordering
    assert obj["p50_ms"] <= obj["p95_ms"], (
        f"p50={obj['p50_ms']} > p95={obj['p95_ms']}"
    )
    assert obj["p95_ms"] <= obj["p99_ms"], (
        f"p95={obj['p95_ms']} > p99={obj['p99_ms']}"
    )

    # All numeric fields should be non-negative floats
    for key in ("p50_ms", "p95_ms", "p99_ms", "p999_ms", "mean_ms", "min_ms", "max_ms"):
        assert isinstance(obj[key], (int, float)), f"{key} should be numeric"
        assert obj[key] >= 0, f"{key} should be >= 0"

    # stdev can be 0 with n_samples=1 but not negative
    assert obj["stdev_ms"] >= 0, "stdev_ms should be >= 0"

    assert isinstance(obj["python_version"], str) and obj["python_version"]
    assert isinstance(obj["platform"], str) and obj["platform"]
    assert isinstance(obj["timestamp_utc"], str) and obj["timestamp_utc"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_bench_mtf_disabled_smoke(capsys: pytest.CaptureFixture[str]) -> None:
    """Invoke main(['--mtf-disabled', '--n', '10']), parse stdout JSON, assert structure."""
    from scripts.bench_aggregator_latency import main

    exit_code = main(["--mtf-disabled", "--n", "10", "--warmup", "2"])
    captured = capsys.readouterr()

    assert exit_code == 0, f"Expected exit 0, got {exit_code}"

    objects = _parse_json_objects(captured.out)
    assert len(objects) == 1, f"Expected 1 JSON object, got {len(objects)}"

    _assert_stat_object(objects[0], expected_mode="mtf-disabled", expected_n=10)


def test_bench_mtf_recording_smoke(capsys: pytest.CaptureFixture[str]) -> None:
    """Invoke main(['--mtf-recording', '--n', '10']), parse stdout JSON, assert structure."""
    from scripts.bench_aggregator_latency import main

    exit_code = main(["--mtf-recording", "--n", "10", "--warmup", "2"])
    captured = capsys.readouterr()

    # Exit code 0 for single-mode runs regardless
    assert exit_code == 0, f"Expected exit 0, got {exit_code}"

    objects = _parse_json_objects(captured.out)
    assert len(objects) == 1, f"Expected 1 JSON object, got {len(objects)}"

    _assert_stat_object(objects[0], expected_mode="mtf-recording", expected_n=10)


def test_bench_no_args_runs_both_modes_and_emits_gate(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No flag → runs both modes + emits gate verdict JSON."""
    from scripts.bench_aggregator_latency import main

    # Run with small n to stay fast; exit code may be 0 (PASS) or 1 (FAIL)
    exit_code = main(["--n", "10", "--warmup", "2"])
    captured = capsys.readouterr()

    objects = _parse_json_objects(captured.out)
    # Expect: mtf-disabled result + mtf-recording result + gate object = 3
    assert len(objects) == 3, (
        f"Expected 3 JSON objects (disabled, recording, gate), got {len(objects)}\n"
        f"stdout:\n{captured.out}"
    )

    _assert_stat_object(objects[0], expected_mode="mtf-disabled", expected_n=10)
    _assert_stat_object(objects[1], expected_mode="mtf-recording", expected_n=10)

    gate_wrapper = objects[2]
    assert "gate" in gate_wrapper, f"Third object should have 'gate' key: {gate_wrapper}"

    gate = gate_wrapper["gate"]
    missing = _GATE_KEYS - set(gate.keys())
    assert not missing, f"Gate object missing keys: {missing}"

    assert gate["verdict"] in ("PASS", "FAIL"), f"verdict must be PASS or FAIL: {gate['verdict']}"
    assert isinstance(gate["failed_budgets"], list)
    assert all(b in ("p50", "p99") for b in gate["failed_budgets"])
    assert gate["p50_budget_ms"] == 50.0
    assert gate["p99_budget_ms"] == 200.0

    # delta values must be numeric
    assert isinstance(gate["delta_p50_ms"], (int, float))
    assert isinstance(gate["delta_p99_ms"], (int, float))

    # exit_code matches verdict
    expected_code = 0 if gate["verdict"] == "PASS" else 1
    assert exit_code == expected_code, (
        f"Exit code {exit_code} does not match verdict {gate['verdict']!r}"
    )
