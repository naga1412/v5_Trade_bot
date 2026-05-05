"""SP-5 Phase A — programmatic builder for ``sp5_fixtures.json``.

Per the SP-5 plan A5 design note, the 50 fixtures must be INDEPENDENT of
``app.core.scoring.aggregator``: if the cross-check loaded fixture inputs
through the aggregator itself, it could not detect aggregator regressions.

This builder is that second source of truth: it re-implements the SP-5
FINAL_SCORE formula in plain Python (no ``app.*`` imports), generates a
hand-curated 50-input matrix covering every branch of the spec section 5
categories, and emits ``sp5_fixtures.json`` with ``expected_*`` fields
computed from the local formula.

Categories produced (matches plan A5 design note):
  - 8 fixtures: all long, no traps; varying number of active layers
  - 8 fixtures: all short, no traps; same layer counts (asymmetric SHORT 0.95 penalty)
  - 8 fixtures: mixed long/short; verifying weighted-average sign
  - 8 fixtures: 1, 2, 3, 4, 5 traps fired (verifying ``(1-0.15)^min(n,4)`` cap)
  - 6 fixtures: tier boundaries (54.9/55.1/64.9/65.1 LONG, two SHORT corners)
  - 4 fixtures: SHORT direction with the asymmetric +10 percentage-point tier bias
  - 4 fixtures: brain_adjust != 1.0 (0.5, 0.8, 1.2, 1.5)
  - 4 fixtures: news_multiplier != 1.0 (0.5, 0.8, 1.2, 1.5)

Trade-off (per task brief): hand-crafting 50 distinct numerical fixtures one
by one is tedious and error-prone. A programmatic builder that re-implements
the formula independently gives the same drift-detection guarantee with
better coverage. The fixture file is checked in; this builder is run only
when categories or counts change.

Run from the worktree root::

    python tools/validation/build_sp5_fixtures.py
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# --- SP-5 FINAL_SCORE formula (independent re-implementation) ---------------
# Mirrors spec section 5 + plan Phase E1. Kept in lockstep by hand; if either
# drifts the cross-check fails and a human must reconcile.

_BASE_WEIGHT: float = 1.0 / 9
_NEUTRAL_BAND: float = 0.05
_TRAP_PENALTY: float = 0.15
_TRAP_CAP: int = 4
_SHORT_DIRECTION_PENALTY: float = 0.95
_LAYER_10_EXCLUDED: int = 10
EXPECTED_FIXTURE_COUNT: int = 50


def _signed(direction: str, strength: float) -> float:
    if direction == "LONG":
        return strength
    if direction == "SHORT":
        return -strength
    return 0.0


def compute_expected(
    layer_scores: dict[str, dict[str, Any] | None],
    trap_fires: list[dict[str, str]],
    brain_adjust: float,
    news_multiplier: float,
) -> dict[str, Any]:
    """Re-implement the SP-5 aggregator formula. Independent of app.*."""
    present = {
        int(k): v for k, v in layer_scores.items()
        if v is not None and int(k) != _LAYER_10_EXCLUDED
    }
    if not present:
        return {
            "static": 0.0, "raw_final": 0.0, "final": 0.0,
            "direction": "NEUTRAL", "tier": "NO_SIGNAL",
        }

    raw_total_weight = _BASE_WEIGHT * len(present)
    rescale = 1.0 / raw_total_weight if raw_total_weight > 0 else 1.0
    static = 0.0
    for layer in present.values():
        s = _signed(layer["d"], float(layer["s"])) * float(layer["c"])
        static += _BASE_WEIGHT * rescale * s
    static = max(-1.0, min(1.0, static))

    effective_count = min(len(trap_fires), _TRAP_CAP)
    trap_factor = (1.0 - _TRAP_PENALTY) ** effective_count
    raw_final = static * brain_adjust * trap_factor * news_multiplier

    if raw_final > _NEUTRAL_BAND:
        direction = "LONG"
    elif raw_final < -_NEUTRAL_BAND:
        direction = "SHORT"
    else:
        direction = "NEUTRAL"

    direction_penalty = _SHORT_DIRECTION_PENALTY if direction == "SHORT" else 1.0
    final = raw_final * direction_penalty
    final = max(-1.0, min(1.0, final))

    tier = _classify_tier(final, direction)
    return {
        "static": round(static, 6),
        "raw_final": round(raw_final, 6),
        "final": round(final, 6),
        "direction": direction,
        "tier": tier,
    }


def _classify_tier(final_score: float, direction: str) -> str:
    if direction == "NEUTRAL":
        return "NO_SIGNAL"
    pct = abs(final_score) * 100.0
    bias = 10.0 if direction == "SHORT" else 0.0
    for threshold, tier in (
        (85.0, "A+"), (75.0, "STANDARD"), (65.0, "SMALL"), (55.0, "PAPER"),
    ):
        if pct >= threshold + bias:
            return tier
    return "NO_SIGNAL"


# --- Helpers ---------------------------------------------------------------

def _layers_none() -> dict[str, Any]:
    return {str(i): None for i in range(1, 11)}


def _set(layers: dict[str, Any], idx: int, d: str, s: float, c: float) -> None:
    layers[str(idx)] = {"d": d, "s": s, "c": c}


def _trap(trap_id: str, severity: str = "high", side: str = "both") -> dict[str, str]:
    return {"trap_id": trap_id, "severity": severity, "side": side}


def _make(
    name: str,
    layer_scores: dict[str, Any],
    trap_fires: list[dict[str, str]] | None = None,
    brain_adjust: float = 1.0,
    news_multiplier: float = 1.0,
) -> dict[str, Any]:
    fires = trap_fires or []
    expected = compute_expected(
        layer_scores, fires, brain_adjust, news_multiplier,
    )
    return {
        "name": name,
        "layer_scores": layer_scores,
        "trap_fires": fires,
        "brain_adjust": brain_adjust,
        "news_multiplier": news_multiplier,
        "expected_static": expected["static"],
        "expected_raw_final": expected["raw_final"],
        "expected_final": expected["final"],
        "expected_direction": expected["direction"],
        "expected_tier": expected["tier"],
    }


def _build_all_long() -> list[dict[str, Any]]:
    """Cat 1: All LONG, no traps, varying active layer count (8 fixtures)."""
    out: list[dict[str, Any]] = []

    layers = _layers_none()
    _set(layers, 1, "LONG", 0.6, 0.7)
    out.append(_make("all_long_1_layer", layers))

    layers = _layers_none()
    for i in (1, 3, 5):
        _set(layers, i, "LONG", 0.7, 0.8)
    out.append(_make("all_long_3_layers", layers))

    layers = _layers_none()
    for i in (1, 2, 3, 4, 5):
        _set(layers, i, "LONG", 0.6, 0.7)
    out.append(_make("all_long_5_layers", layers))

    layers = _layers_none()
    for i in (1, 2, 3, 4, 5, 6, 7):
        _set(layers, i, "LONG", 0.5, 0.6)
    out.append(_make("all_long_7_layers", layers))

    layers = _layers_none()
    for i in range(1, 10):
        _set(layers, i, "LONG", 0.7, 0.8)
    out.append(_make("all_long_9_layers", layers))

    layers = _layers_none()
    for i in range(1, 10):
        _set(layers, i, "LONG", 1.0, 1.0)
    out.append(_make("all_long_9_layers_max", layers))

    layers = _layers_none()
    for i in (1, 3, 5):
        _set(layers, i, "LONG", 0.3, 0.5)
    out.append(_make("all_long_3_layers_weak", layers))

    layers = _layers_none()
    for i in (2, 4, 6, 8):
        _set(layers, i, "LONG", 0.85, 0.9)
    out.append(_make("all_long_4_even_layers", layers))

    return out


def _build_all_short() -> list[dict[str, Any]]:
    """Cat 2: All SHORT, no traps, same layer counts (8 fixtures)."""
    out: list[dict[str, Any]] = []

    layers = _layers_none()
    _set(layers, 1, "SHORT", 0.6, 0.7)
    out.append(_make("all_short_1_layer", layers))

    layers = _layers_none()
    for i in (1, 3, 5):
        _set(layers, i, "SHORT", 0.7, 0.8)
    out.append(_make("all_short_3_layers", layers))

    layers = _layers_none()
    for i in (1, 2, 3, 4, 5):
        _set(layers, i, "SHORT", 0.6, 0.7)
    out.append(_make("all_short_5_layers", layers))

    layers = _layers_none()
    for i in (1, 2, 3, 4, 5, 6, 7):
        _set(layers, i, "SHORT", 0.5, 0.6)
    out.append(_make("all_short_7_layers", layers))

    layers = _layers_none()
    for i in range(1, 10):
        _set(layers, i, "SHORT", 0.7, 0.8)
    out.append(_make("all_short_9_layers", layers))

    layers = _layers_none()
    for i in range(1, 10):
        _set(layers, i, "SHORT", 1.0, 1.0)
    out.append(_make("all_short_9_layers_max", layers))

    layers = _layers_none()
    for i in (1, 3, 5):
        _set(layers, i, "SHORT", 0.3, 0.5)
    out.append(_make("all_short_3_layers_weak", layers))

    layers = _layers_none()
    for i in (2, 4, 6, 8):
        _set(layers, i, "SHORT", 0.85, 0.9)
    out.append(_make("all_short_4_even_layers", layers))

    return out


def _build_mixed() -> list[dict[str, Any]]:
    """Cat 3: Mixed long/short verifying weighted-average sign (8 fixtures)."""
    out: list[dict[str, Any]] = []

    layers = _layers_none()
    _set(layers, 1, "LONG", 0.8, 0.9)
    _set(layers, 2, "SHORT", 0.4, 0.6)
    out.append(_make("mixed_dominant_long", layers))

    layers = _layers_none()
    _set(layers, 1, "SHORT", 0.8, 0.9)
    _set(layers, 2, "LONG", 0.4, 0.6)
    out.append(_make("mixed_dominant_short", layers))

    layers = _layers_none()
    _set(layers, 1, "LONG", 0.6, 0.7)
    _set(layers, 3, "SHORT", 0.6, 0.7)
    out.append(_make("mixed_balanced_cancel", layers))

    layers = _layers_none()
    _set(layers, 1, "LONG", 0.9, 0.9)
    _set(layers, 2, "LONG", 0.7, 0.8)
    _set(layers, 3, "SHORT", 0.3, 0.5)
    out.append(_make("mixed_2long_1short_net_long", layers))

    layers = _layers_none()
    _set(layers, 1, "SHORT", 0.9, 0.9)
    _set(layers, 2, "SHORT", 0.7, 0.8)
    _set(layers, 3, "LONG", 0.3, 0.5)
    out.append(_make("mixed_2short_1long_net_short", layers))

    layers = _layers_none()
    for i in (1, 3, 5):
        _set(layers, i, "LONG", 0.7, 0.7)
    for i in (2, 4):
        _set(layers, i, "SHORT", 0.5, 0.6)
    out.append(_make("mixed_3long_2short_net_long", layers))

    layers = _layers_none()
    for i in (1, 3, 5):
        _set(layers, i, "SHORT", 0.7, 0.7)
    for i in (2, 4):
        _set(layers, i, "LONG", 0.5, 0.6)
    out.append(_make("mixed_3short_2long_net_short", layers))

    layers = _layers_none()
    _set(layers, 1, "LONG", 0.5, 0.5)
    _set(layers, 2, "SHORT", 0.5, 0.5)
    _set(layers, 3, "LONG", 0.5, 0.5)
    _set(layers, 4, "SHORT", 0.5, 0.5)
    out.append(_make("mixed_perfect_cancel", layers))

    return out


def _build_traps() -> list[dict[str, Any]]:
    """Cat 4: Trap counts 1, 2, 3, 4, 5 — verify (1-0.15)^n + cap (8 fixtures)."""
    out: list[dict[str, Any]] = []
    base_long = _layers_none()
    for i in range(1, 6):
        _set(base_long, i, "LONG", 0.8, 0.8)

    out.append(_make(
        "trap_long_1_fired", {**base_long}, [_trap("pre_news_event")],
    ))
    out.append(_make(
        "trap_long_2_fired", {**base_long},
        [_trap("pre_news_event"), _trap("liquidity_sweep")],
    ))
    out.append(_make(
        "trap_long_3_fired", {**base_long},
        [
            _trap("friday_weekend"),
            _trap("counter_weekly", side="long"),
            _trap("pattern_in_pattern"),
        ],
    ))
    out.append(_make(
        "trap_long_4_fired_at_cap", {**base_long},
        [
            _trap("friday_weekend"),
            _trap("counter_weekly"),
            _trap("pattern_in_pattern"),
            _trap("thin_orderbook"),
        ],
    ))
    out.append(_make(
        "trap_long_5_fired_above_cap", {**base_long},
        [
            _trap("friday_weekend"),
            _trap("counter_weekly"),
            _trap("pattern_in_pattern"),
            _trap("thin_orderbook"),
            _trap("price_extreme"),
        ],
    ))

    base_short = _layers_none()
    for i in range(1, 6):
        _set(base_short, i, "SHORT", 0.8, 0.8)
    out.append(_make(
        "trap_short_1_fired", {**base_short},
        [_trap("short_squeeze_cascade", side="short")],
    ))
    out.append(_make(
        "trap_short_3_fired", {**base_short},
        [
            _trap("short_squeeze_cascade", side="short"),
            _trap("funding_rate_decay", side="short"),
            _trap("borrow_rate", side="short"),
        ],
    ))
    out.append(_make(
        "trap_short_5_fired_above_cap", {**base_short},
        [
            _trap("short_squeeze_cascade", side="short"),
            _trap("funding_rate_decay", side="short"),
            _trap("borrow_rate", side="short"),
            _trap("unlimited_upside_risk", side="short"),
            _trap("regulatory_short_ban", side="short"),
        ],
    ))

    return out


def _build_tier_boundaries() -> list[dict[str, Any]]:
    """Cat 5: Tier boundaries (6 fixtures).

    For LONG with 1 layer at confidence=1.0, ``static = strength``, so picking
    strength = target_pct/100 lands the fixture exactly on the boundary.
    For SHORT, the 0.95 direction penalty applies post-static, so we pre-divide.
    """
    out: list[dict[str, Any]] = []

    layers = _layers_none()
    _set(layers, 1, "LONG", 0.549, 1.0)
    out.append(_make("tier_boundary_long_54p9_no_signal", layers))

    layers = _layers_none()
    _set(layers, 1, "LONG", 0.551, 1.0)
    out.append(_make("tier_boundary_long_55p1_paper", layers))

    layers = _layers_none()
    _set(layers, 1, "LONG", 0.649, 1.0)
    out.append(_make("tier_boundary_long_64p9_paper", layers))

    layers = _layers_none()
    _set(layers, 1, "LONG", 0.651, 1.0)
    out.append(_make("tier_boundary_long_65p1_small", layers))

    # SHORT: target |final| via static = -target/0.95.
    layers = _layers_none()
    _set(layers, 1, "SHORT", 0.683158, 1.0)  # -> |final| ~ 0.6490 -> NO_SIGNAL (bias 65.0)
    out.append(_make("tier_boundary_short_static_0p683_no_signal", layers))

    layers = _layers_none()
    _set(layers, 1, "SHORT", 0.685263, 1.0)  # -> |final| ~ 0.6510 -> PAPER (bias 65.0)
    out.append(_make("tier_boundary_short_static_0p685_paper", layers))

    return out


def _build_asymmetric() -> list[dict[str, Any]]:
    """Cat 6: SHORT with asymmetric +10pp bias active (4 fixtures)."""
    out: list[dict[str, Any]] = []

    layers = _layers_none()
    _set(layers, 1, "LONG", 0.7, 1.0)
    out.append(_make("asym_long_at_70pct_small", layers))

    layers = _layers_none()
    _set(layers, 1, "SHORT", 0.7, 1.0)
    out.append(_make("asym_short_at_70pct_paper_after_0p95", layers))

    layers = _layers_none()
    _set(layers, 1, "LONG", 0.9, 1.0)
    out.append(_make("asym_long_at_90pct_a_plus", layers))

    layers = _layers_none()
    _set(layers, 1, "SHORT", 0.9, 1.0)
    out.append(_make("asym_short_at_90pct", layers))

    return out


def _build_brain_adjust() -> list[dict[str, Any]]:
    """Cat 7: brain_adjust != 1.0 (4 fixtures)."""
    base = _layers_none()
    for i in (1, 3, 5):
        _set(base, i, "LONG", 0.7, 0.8)
    return [
        _make("brain_adjust_0p5", {**base}, brain_adjust=0.5),
        _make("brain_adjust_0p8", {**base}, brain_adjust=0.8),
        _make("brain_adjust_1p2", {**base}, brain_adjust=1.2),
        _make("brain_adjust_1p5", {**base}, brain_adjust=1.5),
    ]


def _build_news_multiplier() -> list[dict[str, Any]]:
    """Cat 8: news_multiplier != 1.0 (4 fixtures)."""
    base = _layers_none()
    for i in (1, 3, 5):
        _set(base, i, "LONG", 0.7, 0.8)
    return [
        _make("news_multiplier_0p5", {**base}, news_multiplier=0.5),
        _make("news_multiplier_0p8", {**base}, news_multiplier=0.8),
        _make("news_multiplier_1p2", {**base}, news_multiplier=1.2),
        _make("news_multiplier_1p5", {**base}, news_multiplier=1.5),
    ]


def build() -> list[dict[str, Any]]:
    """Concatenate all category builders."""
    return [
        *_build_all_long(),
        *_build_all_short(),
        *_build_mixed(),
        *_build_traps(),
        *_build_tier_boundaries(),
        *_build_asymmetric(),
        *_build_brain_adjust(),
        *_build_news_multiplier(),
    ]


def main() -> int:
    fixtures = build()
    if len(fixtures) != EXPECTED_FIXTURE_COUNT:
        print(f"WARN: built {len(fixtures)} fixtures, expected {EXPECTED_FIXTURE_COUNT}")
    out_path = Path(__file__).resolve().parent / "sp5_fixtures.json"
    out_path.write_text(json.dumps(fixtures, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(fixtures)} fixtures to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
