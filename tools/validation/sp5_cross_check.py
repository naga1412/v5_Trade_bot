"""SP-5 Phase E3 — wired cross-check.

Loads ``sp5_fixtures.json``, calls ``app.core.scoring.aggregator.aggregate(...)``
with the fixture's ``layer_scores`` + ``trap_fires`` + ``brain_adjust`` +
``news_multiplier``, calls ``app.core.scoring.tiers.classify_tier(...)``,
compares both numeric and tier outputs to ``expected_*`` within 0.001 absolute
tolerance. Exits 0 on 50/50 match; exits 1 with a per-fixture diff report on
mismatch.

The fixtures are produced by ``build_sp5_fixtures.py`` which re-implements the
SP-5 FINAL_SCORE formula independently of ``app.*`` — so a passing run proves
the two implementations agree (drift detection).

Run from the worktree root::

    python tools/validation/sp5_cross_check.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.scoring.aggregator import aggregate  # noqa: E402
from app.core.scoring.tiers import classify_tier  # noqa: E402
from app.core.scoring.traps.base import TrapFire  # noqa: E402
from app.core.scoring.types import Direction, LayerScore  # noqa: E402

TOLERANCE = 0.001
EXPECTED_FIXTURE_COUNT = 50
FIXTURES = Path(__file__).resolve().parent / "sp5_fixtures.json"

REQUIRED_KEYS = (
    "name",
    "layer_scores",
    "trap_fires",
    "brain_adjust",
    "news_multiplier",
    "expected_static",
    "expected_final",
    "expected_direction",
    "expected_tier",
)

DIR_MAP = {
    "LONG": Direction.LONG,
    "SHORT": Direction.SHORT,
    "NEUTRAL": Direction.NEUTRAL,
}


def _validate_shape(fixtures: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    if len(fixtures) != EXPECTED_FIXTURE_COUNT:
        errors.append(
            f"expected {EXPECTED_FIXTURE_COUNT} fixtures, got {len(fixtures)}"
        )
    for i, f in enumerate(fixtures):
        for k in REQUIRED_KEYS:
            if k not in f:
                errors.append(f"fixture #{i} ({f.get('name', '?')}) missing key {k!r}")
        ls = f.get("layer_scores", {})
        if not isinstance(ls, dict):
            errors.append(f"fixture #{i}: layer_scores must be a dict")
            continue
        missing = [str(j) for j in range(1, 11) if str(j) not in ls]
        if missing:
            errors.append(
                f"fixture #{i} ({f['name']}): layer_scores missing ids {missing}"
            )
    return errors


def _layer_from_fixture(d: dict[str, Any] | None) -> LayerScore | None:
    if d is None:
        return None
    return LayerScore(
        direction=DIR_MAP[d["d"]],
        strength=float(d["s"]),
        confidence=float(d["c"]),
    )


def _trap_from_fixture(d: dict[str, Any]) -> TrapFire:
    return TrapFire(
        trap_id=d["trap_id"],
        severity=d["severity"],
        side=d["side"],
        reason=d.get("reason", ""),
        evidence=d.get("evidence", {}),
    )


def main() -> int:
    if not FIXTURES.exists():
        print(f"FAIL: fixtures file not found: {FIXTURES}")
        return 1
    fixtures = json.loads(FIXTURES.read_text(encoding="utf-8"))
    print(f"Loaded {len(fixtures)} fixtures from {FIXTURES.name}")

    shape_errors = _validate_shape(fixtures)
    if shape_errors:
        print("FAIL: fixture shape errors:")
        for e in shape_errors:
            print(f"  - {e}")
        return 1

    failures: list[str] = []
    for i, fx in enumerate(fixtures):
        layer_scores: dict[int, LayerScore | None] = {
            int(k): _layer_from_fixture(v) for k, v in fx["layer_scores"].items()
        }
        traps = [_trap_from_fixture(t) for t in fx["trap_fires"]]
        result = aggregate(
            layer_scores,
            trap_fires=traps,
            brain_adjust=float(fx.get("brain_adjust", 1.0)),
            news_multiplier=float(fx.get("news_multiplier", 1.0)),
        )
        tier = classify_tier(result)

        msgs: list[str] = []
        if abs(result.score - float(fx["expected_final"])) > TOLERANCE:
            msgs.append(
                f"final {result.score:.6f} != expected {fx['expected_final']:.6f}"
            )
        if result.direction.value != fx["expected_direction"]:
            msgs.append(
                f"direction {result.direction.value} != {fx['expected_direction']}"
            )
        if tier != fx["expected_tier"]:
            msgs.append(f"tier {tier} != {fx['expected_tier']}")
        if msgs:
            failures.append(f"[{i}:{fx['name']}] " + "; ".join(msgs))

    if failures:
        print(f"FAIL: {len(failures)}/{len(fixtures)} fixtures mismatched:")
        for line in failures:
            print(f"  {line}")
        return 1

    print(f"PASS: {len(fixtures)}/{len(fixtures)} fixtures matched within {TOLERANCE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
