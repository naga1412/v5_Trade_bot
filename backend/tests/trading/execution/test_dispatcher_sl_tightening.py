"""PR2 SHORT SL tightening — `_maybe_tighten_short_sl`.

Spec §4.2: modifies the proposal's stop-loss when (a) flag ON,
(b) direction == "SHORT", (c) mtf_agreement < cutoff. Otherwise returns
the proposal unchanged. Never returns None — this is a modification,
not a block.
"""
from __future__ import annotations

import pytest

from app.config import Settings
from app.trading.execution.dispatcher import (
    SignalProposal,
    _maybe_tighten_short_sl,
)


def _make_short_proposal(
    *,
    entry: float = 100.0,
    stop_loss: float = 110.0,
    mtf_agreement: int | None = 3,
) -> SignalProposal:
    return SignalProposal(
        symbol="DOGE/USDT", timeframe="1h",
        direction="SHORT",
        entry_price=entry, stop_loss_price=stop_loss, take_profit_price=90.0,
        confidence_pct=70.0,
        layer_summary={"L1": {}, "L2": {}, "L3": {}},
        inputs_hash="c" * 64,
        mtf_agreement=mtf_agreement,
    )


def _settings(
    flag: bool = False, cutoff: int = 5, pct: float = 0.20,
) -> Settings:
    return Settings(
        database_url="postgresql://x", redis_url="redis://x",
        SHORT_TIGHTEN_SL_LOW_MTF=flag,
        SHORT_TIGHTEN_SL_MTF_CUTOFF=cutoff,
        SHORT_TIGHTEN_SL_PCT=pct,
    )


def test_flag_off_returns_proposal_unchanged() -> None:
    p = _make_short_proposal(stop_loss=110.0, mtf_agreement=3)
    out = _maybe_tighten_short_sl(p, _settings(flag=False))
    assert out is p  # same object — no modification
    assert out.stop_loss_price == 110.0


def test_long_direction_unchanged() -> None:
    p = SignalProposal(
        symbol="BTC/USDT", timeframe="1h",
        direction="LONG",
        entry_price=100.0, stop_loss_price=90.0, take_profit_price=120.0,
        confidence_pct=70.0,
        layer_summary={"L1": {}, "L2": {}, "L3": {}},
        inputs_hash="d" * 64,
        mtf_agreement=3,
    )
    out = _maybe_tighten_short_sl(p, _settings(flag=True))
    assert out is p
    assert out.stop_loss_price == 90.0


def test_high_agreement_unchanged() -> None:
    """mtf_agreement >= cutoff → no tightening."""
    p = _make_short_proposal(stop_loss=110.0, mtf_agreement=5)
    out = _maybe_tighten_short_sl(p, _settings(flag=True, cutoff=5))
    assert out is p
    assert out.stop_loss_price == 110.0


def test_low_agreement_tightens_sl_20pct() -> None:
    """SHORT entry=100, SL=110 → SL distance 10. Tighten 20% → new SL=108."""
    p = _make_short_proposal(
        entry=100.0, stop_loss=110.0, mtf_agreement=3,
    )
    out = _maybe_tighten_short_sl(p, _settings(flag=True, cutoff=5, pct=0.20))
    assert out.stop_loss_price == pytest.approx(108.0)
    # Returned proposal is a new instance (frozen dataclass) — original
    # untouched.
    assert p.stop_loss_price == 110.0


def test_low_agreement_with_50pct_tightens_more() -> None:
    """SL distance 10 with 50% tightening → new distance 5 → new SL=105."""
    p = _make_short_proposal(
        entry=100.0, stop_loss=110.0, mtf_agreement=3,
    )
    out = _maybe_tighten_short_sl(p, _settings(flag=True, cutoff=5, pct=0.50))
    assert out.stop_loss_price == pytest.approx(105.0)


def test_none_agreement_unchanged_fail_open() -> None:
    """mtf_agreement=None → no tightening (we don't know it's low)."""
    p = _make_short_proposal(stop_loss=110.0, mtf_agreement=None)
    out = _maybe_tighten_short_sl(p, _settings(flag=True))
    assert out is p
    assert out.stop_loss_price == 110.0


def test_other_fields_preserved_when_tightening() -> None:
    """The dataclass.replace must preserve every other field exactly."""
    p = _make_short_proposal(
        entry=100.0, stop_loss=110.0, mtf_agreement=3,
    )
    out = _maybe_tighten_short_sl(p, _settings(flag=True, cutoff=5))
    # Every field except stop_loss_price is identical.
    assert out.symbol == p.symbol
    assert out.timeframe == p.timeframe
    assert out.direction == p.direction
    assert out.entry_price == p.entry_price
    assert out.take_profit_price == p.take_profit_price
    assert out.confidence_pct == p.confidence_pct
    assert out.inputs_hash == p.inputs_hash
    assert out.mtf_agreement == p.mtf_agreement
