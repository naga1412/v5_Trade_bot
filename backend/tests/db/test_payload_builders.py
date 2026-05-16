"""Golden-dict tests for backend/app/db/payload_builders.py.

PR1 Phase 2.2 — each builder is exercised against a frozen expected dict to
confirm bit-identical output with the original inline literals at the call
sites. Tests use SimpleNamespace to duck-type the production objects so we
don't need a running DB, real Pydantic models, or live infrastructure.

Coverage per operator spec §6:
  * Both LONG and SHORT direction cases where direction is involved.
  * Optional ghost_payload path for predictions (None + 8-key dict).
  * Telegram-style vs auto-style live_trades confirm divergent fields differ.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.db.payload_builders import (
    build_live_trade_payload,
    build_predictions_payload,
    build_shadow_trade_payload,
)
from app.shadow.engine import Direction
from app.shadow.exit_monitor import ExitReason


# ── Shared fixtures ──────────────────────────────────────────────────────────

_TS = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)
_CLOSED_AT = datetime(2026, 5, 16, 18, 0, 0, tzinfo=timezone.utc)
_OPENED_AT = datetime(2026, 5, 16, 10, 0, 0, tzinfo=timezone.utc)


def _make_layer_score_obj(score: float) -> SimpleNamespace:
    """Minimal object with a model_dump() method (Pydantic-like duck type)."""
    return SimpleNamespace(model_dump=lambda: {"score": score})


def _make_pred(*, direction: str = "LONG") -> SimpleNamespace:
    """Duck-typed LivePredictionOut for build_predictions_payload."""
    return SimpleNamespace(
        symbol="BTC/USDT",
        timeframe="1h",
        ts=_TS,
        inputs_hash="abc123hash",
        cold_start=False,
        layer_scores={
            "L1": _make_layer_score_obj(0.75),
            "L3": _make_layer_score_obj(0.60),
        },
        prediction_extras=None,
        final=SimpleNamespace(
            score=0.42,
            direction=direction,
            confidence=0.71,
        ),
    )


def _build_layer_payload(pred: SimpleNamespace) -> dict[str, object]:
    """Construct the same `_layer_payload` dict the production call site builds
    before calling build_predictions_payload — used in tests to mirror the
    caller-side responsibility post-refactor.
    """
    layer_payload: dict[str, object] = {
        k: (v.model_dump() if v else None)
        for k, v in pred.layer_scores.items()
    }
    if pred.prediction_extras is not None:
        layer_payload.update(pred.prediction_extras)
    return layer_payload


def _make_pos(*, direction: Direction = Direction.LONG) -> SimpleNamespace:
    """Duck-typed ShadowPosition for build_shadow_trade_payload."""
    return SimpleNamespace(
        symbol="BTC/USDT",
        direction=direction,
        entry_price=80_000.0,
        stop_loss=78_800.0,
        take_profit=82_400.0,
        position_size_usdt=100.0,
        entry_score=0.42,
        entry_confidence=0.71,
        entry_atr=600.0,
        layer_scores={"L1": 0.75, "L3": 0.60},
        entry_atr_mult=1.5,
        opened_at=_OPENED_AT,
        signal_id="sig-abc-1",
    )


# ── build_predictions_payload ────────────────────────────────────────────────


class TestBuildPredictionsPayload:
    def test_long_no_ghost(self) -> None:
        pred = _make_pred(direction="LONG")
        result = build_predictions_payload(
            pred, user_id=1,
            layer_payload=_build_layer_payload(pred),
            ghost_payload=None,
        )

        expected_layer_scores = json.dumps({
            "L1": {"score": 0.75},
            "L3": {"score": 0.60},
        })

        expected = {
            "user_id": 1,
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "ts": _TS,
            "layer_scores": expected_layer_scores,
            "final_score": 0.42,
            "direction": "LONG",
            "confidence": 0.71,
            "inputs_hash": "abc123hash",
            "model_version": "sp-0",
            "cold_start": False,
        }
        assert result == expected

    def test_short_no_ghost(self) -> None:
        pred = _make_pred(direction="SHORT")
        result = build_predictions_payload(
            pred, user_id=1,
            layer_payload=_build_layer_payload(pred),
            ghost_payload=None,
        )

        expected_layer_scores = json.dumps({
            "L1": {"score": 0.75},
            "L3": {"score": 0.60},
        })

        expected = {
            "user_id": 1,
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "ts": _TS,
            "layer_scores": expected_layer_scores,
            "final_score": 0.42,
            "direction": "SHORT",
            "confidence": 0.71,
            "inputs_hash": "abc123hash",
            "model_version": "sp-0",
            "cold_start": False,
        }
        assert result == expected

    def test_long_with_ghost_payload(self) -> None:
        pred = _make_pred(direction="LONG")
        ghost_payload = {
            "ghost_open": 80_100.0,
            "ghost_high": 80_300.0,
            "ghost_low": 79_900.0,
            "ghost_close": 80_200.0,
            "ghost_p5_low": 79_500.0,
            "ghost_p95_high": 80_800.0,
            "ghost_uncertainty": 0.005,
            "model_checkpoint_id": 42,
        }
        result = build_predictions_payload(
            pred, user_id=1,
            layer_payload=_build_layer_payload(pred),
            ghost_payload=ghost_payload,
        )

        expected_layer_scores = json.dumps({
            "L1": {"score": 0.75},
            "L3": {"score": 0.60},
        })

        expected = {
            "user_id": 1,
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "ts": _TS,
            "layer_scores": expected_layer_scores,
            "final_score": 0.42,
            "direction": "LONG",
            "confidence": 0.71,
            "inputs_hash": "abc123hash",
            "model_version": "sp-0",
            "cold_start": False,
            # ghost keys merged in
            "ghost_open": 80_100.0,
            "ghost_high": 80_300.0,
            "ghost_low": 79_900.0,
            "ghost_close": 80_200.0,
            "ghost_p5_low": 79_500.0,
            "ghost_p95_high": 80_800.0,
            "ghost_uncertainty": 0.005,
            "model_checkpoint_id": 42,
        }
        assert result == expected

    def test_empty_ghost_payload_not_merged(self) -> None:
        """Empty dict is falsy — builder must NOT call update with it."""
        pred = _make_pred(direction="LONG")
        layer_payload = _build_layer_payload(pred)
        result_none = build_predictions_payload(
            pred, user_id=1, layer_payload=layer_payload, ghost_payload=None,
        )
        result_empty = build_predictions_payload(
            pred, user_id=1, layer_payload=layer_payload, ghost_payload={},
        )
        # Both should be identical (11 keys, no ghost keys)
        assert result_none == result_empty
        assert "ghost_open" not in result_empty

    def test_prediction_extras_merged_into_layer_scores(self) -> None:
        """When the caller pre-merges prediction_extras into layer_payload,
        the merged keys must appear inside the JSON-serialized layer_scores
        column. Builder no longer does the merge itself (post-refactor) —
        the caller (live_prediction.py) is responsible.
        """
        extras = {"traps_fired": ["L4"], "tier": "A"}
        pred = _make_pred(direction="LONG")
        pred = SimpleNamespace(
            symbol=pred.symbol,
            timeframe=pred.timeframe,
            ts=pred.ts,
            inputs_hash=pred.inputs_hash,
            cold_start=pred.cold_start,
            layer_scores=pred.layer_scores,
            prediction_extras=extras,
            final=pred.final,
        )
        layer_payload = _build_layer_payload(pred)  # helper does the merge
        # Confirm helper merged extras into layer_payload
        assert layer_payload["traps_fired"] == ["L4"]
        assert layer_payload["tier"] == "A"
        result = build_predictions_payload(
            pred, user_id=1, layer_payload=layer_payload, ghost_payload=None,
        )
        decoded = json.loads(result["layer_scores"])
        assert decoded["traps_fired"] == ["L4"]
        assert decoded["tier"] == "A"
        assert decoded["L1"] == {"score": 0.75}


# ── build_shadow_trade_payload ───────────────────────────────────────────────


class TestBuildShadowTradePayload:
    def test_long_positive_pnl(self) -> None:
        """LONG: exit > entry → positive pnl."""
        pos = _make_pos(direction=Direction.LONG)
        exit_price = 82_400.0  # hit take-profit
        result = build_shadow_trade_payload(
            pos,
            user_id=1,
            exit_price=exit_price,
            exit_reason=ExitReason.TAKE_PROFIT,
            closed_at=_CLOSED_AT,
            bars_held=4,
            inputs_hash="hash-long",
        )

        pnl_pct = (exit_price - pos.entry_price) / pos.entry_price * 100.0
        pnl_usdt = pos.position_size_usdt * pnl_pct / 100.0

        expected = {
            "user_id": 1,
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "direction": "LONG",
            "entry_price": 80_000.0,
            "stop_loss": 78_800.0,
            "take_profit": 82_400.0,
            "position_size_usdt": 100.0,
            "entry_score": 0.42,
            "entry_confidence": 0.71,
            "layer_scores": json.dumps({"L1": 0.75, "L3": 0.60}),
            "entry_atr": 600.0,
            "exit_price": 82_400.0,
            "exit_reason": "TAKE_PROFIT",
            "pnl_pct": pnl_pct,
            "pnl_usdt": pnl_usdt,
            "bars_held": 4,
            "opened_at": _OPENED_AT,
            "closed_at": _CLOSED_AT,
            "inputs_hash": "hash-long",
            "model_version": "sp-0.5",
            "signal_id": "sig-abc-1",
        }
        assert result == expected
        assert result["pnl_pct"] > 0

    def test_long_negative_pnl(self) -> None:
        """LONG: exit < entry → negative pnl (stop loss)."""
        pos = _make_pos(direction=Direction.LONG)
        exit_price = 78_800.0  # hit stop-loss
        result = build_shadow_trade_payload(
            pos,
            user_id=1,
            exit_price=exit_price,
            exit_reason=ExitReason.STOP_LOSS,
            closed_at=_CLOSED_AT,
            bars_held=2,
            inputs_hash="hash-long-sl",
        )

        pnl_pct = (exit_price - pos.entry_price) / pos.entry_price * 100.0
        assert result["pnl_pct"] == pytest.approx(pnl_pct)
        assert result["pnl_pct"] < 0

    def test_short_positive_pnl(self) -> None:
        """SHORT: entry > exit → positive pnl."""
        pos = _make_pos(direction=Direction.SHORT)
        exit_price = 78_000.0  # price fell: profit for short
        result = build_shadow_trade_payload(
            pos,
            user_id=2,
            exit_price=exit_price,
            exit_reason=ExitReason.TAKE_PROFIT,
            closed_at=_CLOSED_AT,
            bars_held=6,
            inputs_hash="hash-short",
        )

        pnl_pct = (pos.entry_price - exit_price) / pos.entry_price * 100.0
        pnl_usdt = pos.position_size_usdt * pnl_pct / 100.0

        expected = {
            "user_id": 2,
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "direction": "SHORT",
            "entry_price": 80_000.0,
            "stop_loss": 78_800.0,
            "take_profit": 82_400.0,
            "position_size_usdt": 100.0,
            "entry_score": 0.42,
            "entry_confidence": 0.71,
            "layer_scores": json.dumps({"L1": 0.75, "L3": 0.60}),
            "entry_atr": 600.0,
            "exit_price": 78_000.0,
            "exit_reason": "TAKE_PROFIT",
            "pnl_pct": pnl_pct,
            "pnl_usdt": pnl_usdt,
            "bars_held": 6,
            "opened_at": _OPENED_AT,
            "closed_at": _CLOSED_AT,
            "inputs_hash": "hash-short",
            "model_version": "sp-0.5",
            "signal_id": "sig-abc-1",
        }
        assert result == expected
        assert result["pnl_pct"] > 0

    def test_short_negative_pnl(self) -> None:
        """SHORT: exit > entry → negative pnl (stop loss on a short)."""
        pos = _make_pos(direction=Direction.SHORT)
        exit_price = 82_400.0  # price rose: loss for short
        result = build_shadow_trade_payload(
            pos,
            user_id=2,
            exit_price=exit_price,
            exit_reason=ExitReason.STOP_LOSS,
            closed_at=_CLOSED_AT,
            bars_held=3,
            inputs_hash="hash-short-sl",
        )

        pnl_pct = (pos.entry_price - exit_price) / pos.entry_price * 100.0
        assert result["pnl_pct"] == pytest.approx(pnl_pct)
        assert result["pnl_pct"] < 0

    def test_22_keys_present(self) -> None:
        """Output must have exactly 22 keys (matches schema)."""
        pos = _make_pos(direction=Direction.LONG)
        result = build_shadow_trade_payload(
            pos,
            user_id=1,
            exit_price=82_400.0,
            exit_reason=ExitReason.TAKE_PROFIT,
            closed_at=_CLOSED_AT,
            bars_held=4,
            inputs_hash="hash-22",
        )
        assert len(result) == 22


# ── build_live_trade_payload ─────────────────────────────────────────────────


class TestBuildLiveTradePayload:
    def _auto_kwargs(self, *, direction: str = "LONG") -> dict:
        return dict(
            user_id=1,
            symbol="BTC/USDT",
            direction=direction,
            margin_usdt=30.0,
            leverage=5,
            entry_price=80_100.0,
            stop_loss=78_498.0,
            take_profit=83_304.0,
            binance_order_id="ord-auto-1",
            opened_at=_OPENED_AT,
            mode_at_open="fully-auto",
            approved_via="auto",
            reasoning_json=json.dumps({
                "confidence_pct": 72,
                "layer_summary": {"L1": 0.75},
                "signal_id": "sig-auto-1",
            }),
            inputs_hash="auto-hash-abc",
        )

    def _telegram_kwargs(self, *, direction: str = "LONG") -> dict:
        return dict(
            user_id=1,
            symbol="BTC/USDT",
            direction=direction,
            margin_usdt=30.0,
            leverage=5,
            entry_price=80_100.0,
            stop_loss=78_498.0,
            take_profit=83_304.0,
            binance_order_id="ord-tg-1",
            opened_at=_OPENED_AT,
            mode_at_open="telegram-approve",
            approved_via="telegram",
            reasoning_json=json.dumps({
                "signal_id": "sig-tg-1",
                "confidence_pct": 72,
            }),
            inputs_hash="tg-hash-abc",
        )

    def test_auto_long(self) -> None:
        kwargs = self._auto_kwargs(direction="LONG")
        result = build_live_trade_payload(**kwargs)

        expected = {
            "user_id": 1,
            "symbol": "BTC/USDT",
            "direction": "LONG",
            "margin_usdt": 30.0,
            "leverage": 5,
            "position_value_usdt": 150.0,  # 30 * 5
            "entry_price": 80_100.0,
            "stop_loss": 78_498.0,
            "take_profit": 83_304.0,
            "binance_order_id": "ord-auto-1",
            "opened_at": _OPENED_AT,
            "mode_at_open": "fully-auto",
            "approved_via": "auto",
            "reasoning": json.dumps({
                "confidence_pct": 72,
                "layer_summary": {"L1": 0.75},
                "signal_id": "sig-auto-1",
            }),
            "inputs_hash": "auto-hash-abc",
        }
        assert result == expected

    def test_auto_short(self) -> None:
        kwargs = self._auto_kwargs(direction="SHORT")
        result = build_live_trade_payload(**kwargs)
        assert result["direction"] == "SHORT"
        assert result["position_value_usdt"] == 150.0
        assert result["approved_via"] == "auto"
        assert result["mode_at_open"] == "fully-auto"
        assert len(result) == 15

    def test_telegram_long(self) -> None:
        kwargs = self._telegram_kwargs(direction="LONG")
        result = build_live_trade_payload(**kwargs)

        expected = {
            "user_id": 1,
            "symbol": "BTC/USDT",
            "direction": "LONG",
            "margin_usdt": 30.0,
            "leverage": 5,
            "position_value_usdt": 150.0,
            "entry_price": 80_100.0,
            "stop_loss": 78_498.0,
            "take_profit": 83_304.0,
            "binance_order_id": "ord-tg-1",
            "opened_at": _OPENED_AT,
            "mode_at_open": "telegram-approve",
            "approved_via": "telegram",
            "reasoning": json.dumps({
                "signal_id": "sig-tg-1",
                "confidence_pct": 72,
            }),
            "inputs_hash": "tg-hash-abc",
        }
        assert result == expected

    def test_telegram_short(self) -> None:
        kwargs = self._telegram_kwargs(direction="SHORT")
        result = build_live_trade_payload(**kwargs)
        assert result["direction"] == "SHORT"
        assert result["approved_via"] == "telegram"
        assert result["mode_at_open"] == "telegram-approve"
        assert len(result) == 15

    def test_auto_vs_telegram_divergent_fields(self) -> None:
        """Auto and telegram payloads differ in the 5 divergent fields."""
        auto_result = build_live_trade_payload(**self._auto_kwargs())
        tg_result = build_live_trade_payload(**self._telegram_kwargs())

        # Identical structure — same 15 keys
        assert set(auto_result.keys()) == set(tg_result.keys())
        assert len(auto_result) == 15

        # Divergent: mode_at_open
        assert auto_result["mode_at_open"] == "fully-auto"
        assert tg_result["mode_at_open"] == "telegram-approve"

        # Divergent: approved_via
        assert auto_result["approved_via"] == "auto"
        assert tg_result["approved_via"] == "telegram"

        # Divergent: reasoning (key ordering differs between call sites)
        auto_reasoning = json.loads(auto_result["reasoning"])
        tg_reasoning = json.loads(tg_result["reasoning"])
        assert "layer_summary" in auto_reasoning
        assert "layer_summary" not in tg_reasoning

        # Divergent: inputs_hash (different values per call site)
        assert auto_result["inputs_hash"] == "auto-hash-abc"
        assert tg_result["inputs_hash"] == "tg-hash-abc"

        # Divergent: binance_order_id (different per test — not a divergence spec
        # field but confirms different orders placed)
        assert auto_result["binance_order_id"] != tg_result["binance_order_id"]

    def test_position_value_usdt_computed_internally(self) -> None:
        """Builder computes position_value_usdt = margin * leverage internally."""
        result = build_live_trade_payload(
            user_id=1, symbol="ETH/USDT", direction="LONG",
            margin_usdt=50.0, leverage=10,
            entry_price=2_000.0, stop_loss=1_900.0, take_profit=2_200.0,
            binance_order_id="ord-eth-1", opened_at=_OPENED_AT,
            mode_at_open="fully-auto", approved_via="auto",
            reasoning_json=json.dumps({"signal_id": "x"}),
            inputs_hash="h",
        )
        assert result["position_value_usdt"] == 500.0  # 50 * 10

    def test_15_keys_present(self) -> None:
        """Output must have exactly 15 keys."""
        result = build_live_trade_payload(**self._auto_kwargs())
        assert len(result) == 15

    def test_reasoning_column_not_reasoning_json(self) -> None:
        """Column name is 'reasoning', NOT 'reasoning_json'."""
        result = build_live_trade_payload(**self._auto_kwargs())
        assert "reasoning" in result
        assert "reasoning_json" not in result
