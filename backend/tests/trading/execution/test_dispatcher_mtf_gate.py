"""PR2 dispatcher MTF gate — `_apply_mtf_gate` decision logic.

Locks in spec §4.2:
  - gate fires when ``mtf_agreement < MTF_MIN_AGREEMENT_1H``
  - gate passes when agreement ≥ threshold
  - ``mtf_agreement=None`` → gate PASSES (PR1 fail-open, R6)
  - ``MTF_MIN_AGREEMENT_1H=0`` → any agreement passes (rollback path, §8)
  - higher-TF veto fires only when 1d AND 1w both oppose the signal
"""
from __future__ import annotations

from app.config import Settings
from app.trading.execution.dispatcher import SignalProposal, _apply_mtf_gate


def _make_proposal(
    *,
    direction: str = "LONG",
    mtf_agreement: int | None = None,
    mtf_directions: dict[str, int] | None = None,
) -> SignalProposal:
    return SignalProposal(
        symbol="BTC/USDT", timeframe="1h",
        direction=direction,  # type: ignore[arg-type]
        entry_price=80_000.0, stop_loss_price=78_000.0, take_profit_price=84_000.0,
        confidence_pct=72.0,
        layer_summary={"L1": {}, "L2": {}, "L3": {}},
        inputs_hash="a" * 64,
        mtf_agreement=mtf_agreement,
        mtf_dominant_tf="1h",
        mtf_directions=mtf_directions,
    )


def _settings(min_agreement: int = 3, higher_tf_veto: bool = True) -> Settings:
    return Settings(
        database_url="postgresql://x", redis_url="redis://x",
        MTF_MIN_AGREEMENT_1H=min_agreement,
        MTF_HIGHER_TF_VETO=higher_tf_veto,
    )


def test_gate_passes_when_agreement_above_threshold() -> None:
    p = _make_proposal(mtf_agreement=4)
    assert _apply_mtf_gate(p, _settings(min_agreement=3)) is None


def test_gate_blocks_when_agreement_below_threshold() -> None:
    p = _make_proposal(mtf_agreement=2)
    result = _apply_mtf_gate(p, _settings(min_agreement=3))
    assert result is not None
    assert result.outcome == "blocked_mtf_low_agreement"


def test_gate_passes_when_agreement_equals_threshold() -> None:
    """Threshold is a `<` check, not `<=` — agreement == threshold passes."""
    p = _make_proposal(mtf_agreement=3)
    assert _apply_mtf_gate(p, _settings(min_agreement=3)) is None


def test_gate_passes_when_agreement_is_none_fail_open() -> None:
    """PR1 compute returned None (cold cache, fetch failure) → gate PASSES."""
    p = _make_proposal(mtf_agreement=None)
    assert _apply_mtf_gate(p, _settings()) is None


def test_gate_threshold_zero_is_rollback_bypass() -> None:
    """MTF_MIN_AGREEMENT_1H=0 → any agreement >= 0 passes (spec §8 rollback)."""
    p = _make_proposal(mtf_agreement=0)
    assert _apply_mtf_gate(p, _settings(min_agreement=0)) is None


def test_higher_tf_veto_fires_when_both_1d_and_1w_oppose_long() -> None:
    p = _make_proposal(
        direction="LONG", mtf_agreement=5,
        mtf_directions={"5m": 1, "15m": 1, "1h": 1, "4h": 1, "1d": -1, "1w": -1},
    )
    result = _apply_mtf_gate(p, _settings(higher_tf_veto=True))
    assert result is not None
    assert result.outcome == "blocked_mtf_higher_tf_veto"


def test_higher_tf_veto_does_not_fire_when_only_one_opposes() -> None:
    p = _make_proposal(
        direction="LONG", mtf_agreement=5,
        mtf_directions={"5m": 1, "15m": 1, "1h": 1, "4h": 1, "1d": -1, "1w": 1},
    )
    assert _apply_mtf_gate(p, _settings(higher_tf_veto=True)) is None


def test_higher_tf_veto_disabled_by_flag() -> None:
    p = _make_proposal(
        direction="LONG", mtf_agreement=5,
        mtf_directions={"5m": 1, "15m": 1, "1h": 1, "4h": 1, "1d": -1, "1w": -1},
    )
    assert _apply_mtf_gate(p, _settings(higher_tf_veto=False)) is None


def test_higher_tf_veto_no_directions_fails_open() -> None:
    """mtf_directions=None → veto cannot evaluate → fail-open."""
    p = _make_proposal(direction="LONG", mtf_agreement=5, mtf_directions=None)
    assert _apply_mtf_gate(p, _settings(higher_tf_veto=True)) is None


def test_higher_tf_veto_short_direction_inverted() -> None:
    """SHORT signal: 1d AND 1w must both be POSITIVE (opposite of SHORT)."""
    p = _make_proposal(
        direction="SHORT", mtf_agreement=5,
        mtf_directions={"5m": -1, "15m": -1, "1h": -1, "4h": -1, "1d": 1, "1w": 1},
    )
    result = _apply_mtf_gate(p, _settings(higher_tf_veto=True))
    assert result is not None
    assert result.outcome == "blocked_mtf_higher_tf_veto"


def test_higher_tf_veto_short_partial_does_not_fire() -> None:
    """SHORT signal: only 1d positive → veto does NOT fire."""
    p = _make_proposal(
        direction="SHORT", mtf_agreement=5,
        mtf_directions={"5m": -1, "15m": -1, "1h": -1, "4h": -1, "1d": 1, "1w": -1},
    )
    assert _apply_mtf_gate(p, _settings(higher_tf_veto=True)) is None


def test_higher_tf_veto_missing_keys_treats_as_zero() -> None:
    """When 1d / 1w keys are missing from mtf_directions, they default to 0
    (neutral) — neither positive nor negative → veto never fires."""
    p = _make_proposal(
        direction="LONG", mtf_agreement=5,
        mtf_directions={"5m": 1, "15m": 1},  # 1d, 1w missing
    )
    assert _apply_mtf_gate(p, _settings(higher_tf_veto=True)) is None
