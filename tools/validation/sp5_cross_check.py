"""SP-5 Phase A (skeleton) / Phase E3 (wired) — cross-validate aggregator vs 50 fixtures.

Loads ``sp5_fixtures.json``, calls ``app.core.scoring.aggregator.aggregate(...)``
with the fixture's ``layer_scores`` + ``trap_fires`` + ``brain_adjust`` +
``news_multiplier``, calls ``app.core.scoring.tiers.classify_tier(...)``,
compares both numeric and tier outputs to ``expected_*`` within 0.001 absolute
tolerance. Exits 0 on 50/50 match; exits 1 with a per-fixture diff report on
mismatch.

Phase A (this commit) only verifies the fixture file shape (50 entries with
the required keys). The aggregator + tier wiring lands in Phase E3 once the
extended ``aggregate`` signature and ``tiers.classify_tier`` exist.

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

# These imports are intentionally deferred until Phase E3 wires the aggregator
# extensions and the tiers module. The Phase A skeleton runs in fixture-shape
# mode only.
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
        # Must use layer ids "1".."10" as keys (per plan A5 example).
        missing = [str(j) for j in range(1, 11) if str(j) not in ls]
        if missing:
            errors.append(
                f"fixture #{i} ({f['name']}): layer_scores missing ids {missing}"
            )
    return errors


def main() -> int:
    if not FIXTURES.exists():
        print(f"FAIL: fixtures file not found: {FIXTURES}")
        return 1
    fixtures = json.loads(FIXTURES.read_text(encoding="utf-8"))
    print(f"Loaded {len(fixtures)} fixtures from {FIXTURES.name}")
    errors = _validate_shape(fixtures)
    if errors:
        print("FAIL: fixture shape errors:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("Phase A skeleton: fixture shape OK (50 entries with required keys).")
    print("Phase E3 will wire aggregator + classify_tier comparisons here.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
