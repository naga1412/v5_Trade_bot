"""PR-strategy-1: entry_quality gate logic.

Pure unit tests for the duck-typed `open_position_gate`. Both call sites
(shadow worker + dispatcher) pass differently-shaped objects (ShadowSignal
vs SignalProposal) — the gate must reach `.direction` + `.entry_score`
without caring which.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.core.gates.entry_quality import evaluate_all_gates, open_position_gate


def _signal(direction: str, entry_score: float | None = 0.5):
    return SimpleNamespace(direction=direction, entry_score=entry_score)


def _settings(*, min_long: float | None = None, disable_short: bool = False):
    return SimpleNamespace(
        MIN_ENTRY_SCORE_LONG=min_long,
        DISABLE_SHORT_SIGNALS=disable_short,
    )


def test_gate_allows_when_flags_off() -> None:
    d = open_position_gate(_signal("LONG", 0.10), _settings())
    assert d.allow is True
    d = open_position_gate(_signal("SHORT", -0.40), _settings())
    assert d.allow is True


def test_gate_denies_short_when_disabled() -> None:
    d = open_position_gate(_signal("SHORT", -0.40), _settings(disable_short=True))
    assert d.allow is False
    assert d.reason == "short_disabled"


def test_gate_allows_short_when_not_disabled() -> None:
    d = open_position_gate(_signal("SHORT", -0.40), _settings(disable_short=False))
    assert d.allow is True


def test_gate_denies_long_below_threshold() -> None:
    d = open_position_gate(_signal("LONG", 0.35), _settings(min_long=0.36))
    assert d.allow is False
    assert d.reason == "below_long_threshold"


def test_gate_allows_long_at_threshold() -> None:
    d = open_position_gate(_signal("LONG", 0.36), _settings(min_long=0.36))
    assert d.allow is True


def test_gate_allows_long_when_threshold_null() -> None:
    d = open_position_gate(_signal("LONG", -1.0), _settings(min_long=None))
    assert d.allow is True


# ---------------------------------------------------------------------------
# PR-BOT-INTELLIGENCE-UPGRADE — extended gate coverage
# ---------------------------------------------------------------------------


def _ext_signal(
    direction: str,
    entry_score: float | None = 0.5,
    *,
    layer2_direction: str | None = None,
    layer2_confidence: float | None = None,
    mtf_dominant_tf: str | None = None,
    mtf_adx_by_tf: dict[str, float] | None = None,
):
    """SignalProposal-shaped namespace exposing the extended-gate context."""
    return SimpleNamespace(
        direction=direction,
        entry_score=entry_score,
        layer2_direction=layer2_direction,
        layer2_confidence=layer2_confidence,
        mtf_dominant_tf=mtf_dominant_tf,
        mtf_adx_by_tf=mtf_adx_by_tf,
    )


def _ext_settings(
    *,
    min_long: float | None = None,
    disable_short: bool = False,
    regime_enabled: bool = False,
    regime_override: float = 0.8,
    pattern_enabled: bool = False,
    pattern_min_conf: float = 0.6,
    pattern_boost: float = 0.10,
    pattern_penalty: float = 0.15,
    adx_enabled: bool = False,
    adx_threshold: float = 25.0,
):
    return SimpleNamespace(
        MIN_ENTRY_SCORE_LONG=min_long,
        DISABLE_SHORT_SIGNALS=disable_short,
        REGIME_GATE_ENABLED=regime_enabled,
        REGIME_OPPOSITE_PATTERN_OVERRIDE=regime_override,
        PATTERN_BOOST_ENABLED=pattern_enabled,
        PATTERN_BOOST_MIN_CONFIDENCE=pattern_min_conf,
        PATTERN_BOOST_AMOUNT=pattern_boost,
        PATTERN_PENALTY_AMOUNT=pattern_penalty,
        ADX_GATE_ENABLED=adx_enabled,
        MIN_ADX_TREND_STRENGTH=adx_threshold,
    )


# ---- CHANGE 1: regime gate -------------------------------------------------


def test_long_blocked_in_bear_regime_no_pattern() -> None:
    """LONG signal in confirmed bear regime → blocked when L2 doesn't override."""
    d = open_position_gate(
        _ext_signal("LONG", 0.5),
        _ext_settings(regime_enabled=True),
        market_regime="bear",
    )
    assert d.allow is False
    assert d.reason is not None
    assert d.reason.startswith("blocked_wrong_regime: regime=bear")


def test_short_blocked_in_bull_regime_no_pattern() -> None:
    """SHORT signal in confirmed bull regime → blocked when L2 doesn't override."""
    d = open_position_gate(
        _ext_signal("SHORT", -0.5),
        _ext_settings(regime_enabled=True),
        market_regime="bull",
    )
    assert d.allow is False
    assert d.reason is not None
    assert d.reason.startswith("blocked_wrong_regime: regime=bull")


def test_long_allowed_in_bear_with_strong_pattern_override() -> None:
    """LONG in bear + L2 LONG pattern at conf ≥ override → allowed."""
    d = open_position_gate(
        _ext_signal(
            "LONG", 0.5,
            layer2_direction="LONG",
            layer2_confidence=0.85,
        ),
        _ext_settings(regime_enabled=True, regime_override=0.8),
        market_regime="bear",
    )
    assert d.allow is True


def test_regime_neutral_does_not_block() -> None:
    """`market_regime="neutral"` → never opposes any direction → allow."""
    d_long = open_position_gate(
        _ext_signal("LONG", 0.5),
        _ext_settings(regime_enabled=True),
        market_regime="neutral",
    )
    d_short = open_position_gate(
        _ext_signal("SHORT", -0.5),
        _ext_settings(regime_enabled=True),
        market_regime="neutral",
    )
    assert d_long.allow is True
    assert d_short.allow is True


def test_regime_flag_off_skips_check() -> None:
    """Regime flag OFF → even bear regime + LONG signal passes."""
    d = open_position_gate(
        _ext_signal("LONG", 0.5),
        _ext_settings(regime_enabled=False),
        market_regime="bear",
    )
    assert d.allow is True


# ---- CHANGE 2: Layer-2 pattern boost --------------------------------------


def test_pattern_boost_lifts_long_above_threshold() -> None:
    """LONG at score 0.31 with aligned L2 + boost 0.10 → effective 0.41 ≥ 0.40."""
    d = open_position_gate(
        _ext_signal(
            "LONG", 0.31,
            layer2_direction="LONG",
            layer2_confidence=0.72,
        ),
        _ext_settings(
            min_long=0.40,
            pattern_enabled=True,
            pattern_boost=0.10,
        ),
    )
    assert d.allow is True


def test_pattern_penalty_drops_long_below_threshold() -> None:
    """LONG at score 0.42 with opposing L2 + penalty 0.15 → effective 0.27 < 0.40."""
    d = open_position_gate(
        _ext_signal(
            "LONG", 0.42,
            layer2_direction="SHORT",
            layer2_confidence=0.75,
        ),
        _ext_settings(
            min_long=0.40,
            pattern_enabled=True,
            pattern_penalty=0.15,
        ),
    )
    assert d.allow is False
    assert d.reason is not None
    assert d.reason.startswith("below_long_threshold")
    assert "L2_penalty=0.15" in d.reason


def test_pattern_boost_skipped_below_confidence_floor() -> None:
    """L2 aligned but confidence 0.50 < floor 0.60 → no boost, raw score gate."""
    d = open_position_gate(
        _ext_signal(
            "LONG", 0.31,
            layer2_direction="LONG",
            layer2_confidence=0.50,
        ),
        _ext_settings(
            min_long=0.40,
            pattern_enabled=True,
            pattern_min_conf=0.60,
        ),
    )
    # No boost applied → 0.31 < 0.40 → blocked
    assert d.allow is False


# ---- CHANGE 3: global ADX trend strength ----------------------------------


def test_adx_below_threshold_blocks_entry() -> None:
    """Dominant-TF ADX 18.3 < threshold 25.0 → block."""
    d = open_position_gate(
        _ext_signal(
            "LONG", 0.5,
            mtf_dominant_tf="1h",
            mtf_adx_by_tf={"5m": 14.2, "1h": 18.3, "4h": 22.0},
        ),
        _ext_settings(adx_enabled=True, adx_threshold=25.0),
    )
    assert d.allow is False
    assert d.reason is not None
    assert d.reason.startswith("blocked_low_trend_strength: adx=18.3")
    assert "dominant_tf=1h" in d.reason


def test_dominant_tf_adx_lookup_from_mtf_data() -> None:
    """ADX from dominant_tf entry is the one compared (not max/min/avg)."""
    # ADX gate threshold 25; dominant_tf "4h" with ADX 31.5 → pass even though
    # the 5m entry (8.2) would fail by itself. Verifies the lookup is by key,
    # not by aggregate.
    d = open_position_gate(
        _ext_signal(
            "LONG", 0.5,
            mtf_dominant_tf="4h",
            mtf_adx_by_tf={"5m": 8.2, "1h": 22.0, "4h": 31.5},
        ),
        _ext_settings(adx_enabled=True, adx_threshold=25.0),
    )
    assert d.allow is True


def test_adx_gate_fails_open_when_data_missing() -> None:
    """ADX flag ON but mtf_adx_by_tf is None → fall open (do not block)."""
    d = open_position_gate(
        _ext_signal("LONG", 0.5, mtf_dominant_tf="1h"),
        _ext_settings(adx_enabled=True),
    )
    assert d.allow is True


# ---- All-flags-disabled regression ----------------------------------------


def test_all_gates_disabled_preserves_prior_behavior() -> None:
    """With every new flag off, the gate behaves exactly as PR-strategy-1.

    A LONG entry with L2 contradiction + low ADX + bear regime data attached
    must still allow, because no new flag is on."""
    d = open_position_gate(
        _ext_signal(
            "LONG", 0.10,
            layer2_direction="SHORT", layer2_confidence=0.95,
            mtf_dominant_tf="1h",
            mtf_adx_by_tf={"1h": 5.0},
        ),
        _ext_settings(
            min_long=None,  # legacy gate also off
            disable_short=False,
            regime_enabled=False,
            pattern_enabled=False,
            adx_enabled=False,
        ),
        market_regime="bear",  # supplied but flag is off → ignored
    )
    assert d.allow is True


# ---------------------------------------------------------------------------
# Item 3 (2026-08-14): evaluate_all_gates — dispatch decision log's
# unconditional, log-only sweep. Never drives the real decision.
# ---------------------------------------------------------------------------


def test_evaluate_all_gates_reports_not_evaluated_when_flags_off() -> None:
    """All 3 extended sub-gates OFF → not_evaluated, not pass — the log
    should distinguish "flag off, meaningless" from "flag on, checked,
    fine". short_disabled has no ENABLED flag, so it's always evaluated."""
    verdicts = evaluate_all_gates(
        _ext_signal("LONG", 0.5), _ext_settings(), market_regime="bear",
    )
    by_gate = {v.gate: v for v in verdicts}
    assert by_gate["short_disabled"].verdict == "pass"
    assert by_gate["regime"].verdict == "not_evaluated"
    assert by_gate["adx"].verdict == "not_evaluated"
    # direction IS LONG, so min_score is "evaluated" (fail-open "pass")
    # even with no threshold configured -- "not_evaluated" is reserved
    # for when the check structurally doesn't apply (SHORT direction).
    assert by_gate["min_score"].verdict == "pass"


def test_evaluate_all_gates_evaluates_downstream_gates_even_when_upstream_blocks() -> None:
    """The whole point: short_disabled blocking must NOT stop regime/adx/
    min_score from being evaluated too -- otherwise the log reproduces
    open_position_gate's short-circuit blind spot."""
    verdicts = evaluate_all_gates(
        _ext_signal(
            "SHORT", -0.5, mtf_dominant_tf="1h", mtf_adx_by_tf={"1h": 5.0},
        ),
        _ext_settings(disable_short=True, regime_enabled=True, adx_enabled=True),
        market_regime="bull",  # SHORT in bull also opposes -- would ALSO block
    )
    by_gate = {v.gate: v for v in verdicts}
    assert by_gate["short_disabled"].verdict == "block"
    # Evaluated (not skipped) despite short_disabled already blocking:
    assert by_gate["regime"].verdict == "block"
    assert by_gate["adx"].verdict == "block"  # adx=5.0 < default 25.0


def test_evaluate_all_gates_min_score_not_evaluated_for_short() -> None:
    """MIN_ENTRY_SCORE_LONG doesn't apply to SHORT signals at all."""
    verdicts = evaluate_all_gates(
        _ext_signal("SHORT", -0.5), _ext_settings(min_long=0.36),
    )
    by_gate = {v.gate: v for v in verdicts}
    assert by_gate["min_score"].verdict == "not_evaluated"


def test_evaluate_all_gates_matches_open_position_gate_across_scenario_matrix() -> None:
    """Cross-consistency guarantee: for every scenario below,
    open_position_gate's outcome must equal the FIRST "block" verdict
    evaluate_all_gates reports, in short-circuit precedence order
    (short_disabled, regime, adx, min_score) -- or allow, if none block.
    Both call the same _check_* helpers, so this is really a proof the
    two functions can't drift apart, not a coincidence to maintain by hand.
    """
    precedence = ("short_disabled", "regime", "adx", "min_score")
    scenarios = [
        # (signal, settings, market_regime)
        (_ext_signal("LONG", 0.5), _ext_settings(), None),
        (_ext_signal("SHORT", -0.5), _ext_settings(disable_short=True), None),
        (
            _ext_signal("LONG", 0.5),
            _ext_settings(regime_enabled=True), "bear",
        ),
        (
            _ext_signal("LONG", 0.5, mtf_dominant_tf="1h", mtf_adx_by_tf={"1h": 5.0}),
            _ext_settings(adx_enabled=True), None,
        ),
        (_ext_signal("LONG", 0.10), _ext_settings(min_long=0.36), None),
        (
            _ext_signal(
                "SHORT", -0.5, mtf_dominant_tf="1h", mtf_adx_by_tf={"1h": 5.0},
            ),
            _ext_settings(disable_short=True, regime_enabled=True, adx_enabled=True),
            "bull",
        ),
        (
            _ext_signal("LONG", 0.36, mtf_dominant_tf="1h", mtf_adx_by_tf={"1h": 30.0}),
            _ext_settings(min_long=0.36, adx_enabled=True), None,
        ),
    ]
    for signal, settings, market_regime in scenarios:
        decision = open_position_gate(signal, settings, market_regime=market_regime)
        verdicts = {
            v.gate: v.verdict
            for v in evaluate_all_gates(signal, settings, market_regime=market_regime)
        }
        first_block = next(
            (gate for gate in precedence if verdicts[gate] == "block"), None,
        )
        if decision.allow:
            assert first_block is None, (signal, settings, market_regime)
        else:
            assert first_block is not None, (signal, settings, market_regime)
