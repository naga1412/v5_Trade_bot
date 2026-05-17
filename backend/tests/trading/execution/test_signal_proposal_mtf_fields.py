"""PR2 SignalProposal extension — 3 new optional MTF fields.

Locks in the design's §4.3 contract: every existing call site that
doesn't set the new fields continues to receive ``None`` (bit-identical
to PR1). New call sites (post-PR2 `proposal_from_prediction` in
glue.py) populate them from `LivePredictionOut`.
"""
from __future__ import annotations

from app.trading.execution.dispatcher import SignalProposal


def _minimal_proposal_kwargs() -> dict:
    """Minimal kwargs that satisfy the existing required fields of
    SignalProposal as defined in dispatcher.py at PR1 (post-merge)."""
    return dict(
        symbol="BTC/USDT",
        timeframe="1h",
        direction="LONG",
        entry_price=80000.0,
        stop_loss_price=78000.0,
        take_profit_price=84000.0,
        confidence_pct=72.5,
        layer_summary={"L1": {}, "L2": {}, "L3": {}},
        inputs_hash="a" * 64,
    )


def test_mtf_fields_default_none() -> None:
    p = SignalProposal(**_minimal_proposal_kwargs())
    assert p.mtf_agreement is None
    assert p.mtf_dominant_tf is None
    assert p.mtf_directions is None


def test_mtf_fields_can_be_populated() -> None:
    p = SignalProposal(
        **_minimal_proposal_kwargs(),
        mtf_agreement=4,
        mtf_dominant_tf="1h",
        mtf_directions={"5m": 1, "15m": 1, "1h": 1, "4h": 1, "1d": -1, "1w": 0},
    )
    assert p.mtf_agreement == 4
    assert p.mtf_dominant_tf == "1h"
    assert p.mtf_directions is not None
    assert p.mtf_directions["1d"] == -1
