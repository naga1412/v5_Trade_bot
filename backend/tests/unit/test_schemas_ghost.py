"""SP-1 Phase D2: GhostOut schema + LivePredictionOut.ghost optional field."""
from __future__ import annotations

from app.api.schemas import GhostOut, LivePredictionOut


def test_ghost_out_required_fields() -> None:
    g = GhostOut(
        open=1, high=2, low=0.5, close=1.5,
        p5_low=0.1, p95_high=2.5, uncertainty=0.05,
    )
    assert g.uncertainty == 0.05


def test_live_prediction_out_ghost_optional() -> None:
    """LivePredictionOut.ghost must be Optional (None when no model loaded)."""
    fields = LivePredictionOut.model_fields
    assert "ghost" in fields
    # Default must be None so existing payloads without ghost still parse.
    assert fields["ghost"].default is None
