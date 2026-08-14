"""SP-8 Phase J — live_prediction worker → dispatcher wiring."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.trading.execution import glue
from app.ws import live_prediction


@dataclass
class _FakeTradeSetup:
    entry: float | None
    stop_loss: float | None
    take_profit: float | None


@dataclass
class _FakeFinal:
    direction: str
    confidence: float
    score: float = 0.7


@dataclass
class _FakePrediction:
    symbol: str
    timeframe: str
    inputs_hash: str
    trade_setup: _FakeTradeSetup | None
    final: _FakeFinal
    # PR2: live_prediction now forwards these to proposal_from_prediction
    # so the dispatcher gate can read MTF state. Default None preserves
    # pre-PR2 fixture behaviour (gate fail-open).
    mtf_agreement: int | None = None
    mtf_dominant_tf: str | None = None
    mtf_directions_json: str | None = None
    # PR-PLUMBING-1: live_prediction now forwards the per-day funding rate
    # to proposal_from_prediction so the dispatcher's funding-rate
    # kill-switch evaluates real values. Default None matches predictor's
    # behavior when the intermarket lookup fails (gate fail-open).
    funding_rate_daily: float | None = None
    # Item 3 (2026-08-14): the decision log records the candle ts each
    # dispatch attempt is for, matching pred.ts used elsewhere (e.g.
    # anchor_ts in _persist_prediction_and_schedule_validation).
    ts: datetime = field(
        default_factory=lambda: datetime(2026, 8, 14, tzinfo=timezone.utc),
    )
    mtf_adx_by_tf_json: str | None = None


class _FakeSession:
    def __init__(self) -> None:
        self.executed: list[Any] = []

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def execute(self, stmt: Any, params: Any = None) -> None:
        # Item 3 (2026-08-14): record_dispatch_decision's INSERT lands
        # here too, via the same session_factory as dispatch_if_eligible.
        self.executed.append((stmt, params))
        return None


def _factory() -> Any:
    return lambda: _FakeSession()


@pytest.fixture(autouse=True)
def _reset_vault():
    glue.reset_vault_cache_for_tests()
    yield
    glue.reset_vault_cache_for_tests()


def _vault_loaded() -> None:
    glue._vault_keys.update(  # noqa: SLF001 — intentional test poke
        {"binance_api_key": "k", "binance_api_secret": "s"},
    )


def _pred(*, direction: str = "LONG", entry: float = 80_000) -> _FakePrediction:
    return _FakePrediction(
        symbol="BTC/USDT", timeframe="1h", inputs_hash="abc",
        trade_setup=_FakeTradeSetup(
            entry=entry, stop_loss=entry * 0.98, take_profit=entry * 1.04,
        ),
        final=_FakeFinal(direction=direction, confidence=0.72),
    )


@pytest.mark.asyncio
async def test_skips_when_vault_uninitialised(monkeypatch) -> None:
    called: list[Any] = []

    async def _fake_dispatch(*args, **kwargs):
        called.append(kwargs)

    monkeypatch.setattr(live_prediction, "dispatch_if_eligible", _fake_dispatch)
    await live_prediction._maybe_dispatch(  # noqa: SLF001
        _factory(), pred=_pred(), layer_payload={},
    )
    assert called == []  # vault never loaded → skip


@pytest.mark.asyncio
async def test_skips_when_trade_setup_neutral(monkeypatch) -> None:
    _vault_loaded()
    pred = _FakePrediction(
        symbol="BTC/USDT", timeframe="1h", inputs_hash="abc",
        trade_setup=_FakeTradeSetup(entry=None, stop_loss=None, take_profit=None),
        final=_FakeFinal(direction="NEUTRAL", confidence=0.5),
    )
    called: list[Any] = []

    async def _fake_dispatch(*args, **kwargs):
        called.append(kwargs)

    monkeypatch.setattr(live_prediction, "dispatch_if_eligible", _fake_dispatch)
    await live_prediction._maybe_dispatch(  # noqa: SLF001
        _factory(), pred=pred, layer_payload={},
    )
    assert called == []


@pytest.mark.asyncio
async def test_calls_dispatch_with_proposal_kwargs(monkeypatch) -> None:
    _vault_loaded()
    captured: dict[str, Any] = {}

    async def _fake_dispatch(session, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(outcome="emitted", detail="ok")

    monkeypatch.setattr(live_prediction, "dispatch_if_eligible", _fake_dispatch)
    await live_prediction._maybe_dispatch(  # noqa: SLF001
        _factory(), pred=_pred(direction="LONG", entry=80_000),
        layer_payload={"L1": {"score": 0.85}},
    )
    assert captured["user_id"] == live_prediction.BOOTSTRAP_ADMIN_USER_ID
    assert captured["use_testnet"] is True
    pk = captured["proposal_kwargs"]
    assert pk["symbol"] == "BTC/USDT"
    assert pk["pred_direction"] == "LONG"
    assert pk["entry_price"] == 80_000
    assert pk["stop_loss_price"] == 80_000 * 0.98
    assert pk["take_profit_price"] == 80_000 * 1.04
    assert pk["inputs_hash"] == "abc"
    assert pk["layer_summary"] == {"L1": {"score": 0.85}}


@pytest.mark.asyncio
async def test_dispatch_result_triggers_decision_log_write(monkeypatch) -> None:
    """Item 3 (2026-08-14): a non-None dispatch result must be followed by
    a record_dispatch_decision call carrying the real outcome/detail and
    all 4 unconditionally-evaluated entry-quality gate verdicts."""
    _vault_loaded()

    async def _fake_dispatch(session, **kwargs):
        return SimpleNamespace(outcome="blocked_entry_quality", detail="entry_quality_gate: short_disabled")

    captured: dict[str, Any] = {}

    async def _fake_record(session_factory, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(live_prediction, "dispatch_if_eligible", _fake_dispatch)
    monkeypatch.setattr(live_prediction, "record_dispatch_decision", _fake_record)
    await live_prediction._maybe_dispatch(  # noqa: SLF001
        _factory(), pred=_pred(direction="SHORT", entry=80_000), layer_payload={},
    )

    assert captured["symbol"] == "BTC/USDT"
    assert captured["timeframe"] == "1h"
    assert captured["outcome"] == "blocked_entry_quality"
    assert captured["detail"] == "entry_quality_gate: short_disabled"
    gates = {v.gate for v in captured["gate_verdicts"]}
    assert gates == {"short_disabled", "regime", "adx", "min_score"}


@pytest.mark.asyncio
async def test_decision_log_failure_does_not_propagate_and_logs_distinct_error(
    monkeypatch, caplog,
) -> None:
    """A failure while BUILDING the decision log (upstream of the write
    itself) must not affect the caller and must log a marker distinct
    from "dispatch_if_eligible failed" -- otherwise a telemetry-only
    failure reads as a real dispatch failure."""
    _vault_loaded()

    async def _fake_dispatch(session, **kwargs):
        return SimpleNamespace(outcome="emitted", detail="ok")

    def _boom_evaluate(*args, **kwargs):
        raise RuntimeError("gate evaluation exploded")

    monkeypatch.setattr(live_prediction, "dispatch_if_eligible", _fake_dispatch)
    monkeypatch.setattr(live_prediction, "evaluate_all_gates", _boom_evaluate)
    # Must not raise -- worker survives a bad decision-log attempt too.
    await live_prediction._maybe_dispatch(  # noqa: SLF001
        _factory(), pred=_pred(), layer_payload={},
    )
    assert any(
        "dispatch_decision_log_failed" in r.message for r in caplog.records
    )
    assert not any(
        "dispatch_if_eligible failed" in r.message for r in caplog.records
    )


@pytest.mark.asyncio
async def test_decision_log_db_write_failure_logs_distinct_error(
    monkeypatch, caplog,
) -> None:
    """A real INSERT failure (session lacks the table / DB unreachable)
    is caught inside record_dispatch_decision itself and logged loud,
    not swallowed -- proven here with a session whose execute() raises."""
    _vault_loaded()

    class _BrokenSession(_FakeSession):
        async def execute(self, stmt: Any, params: Any = None) -> None:
            raise RuntimeError("relation \"dispatch_decisions\" does not exist")

    async def _fake_dispatch(session, **kwargs):
        return SimpleNamespace(outcome="emitted", detail="ok")

    monkeypatch.setattr(live_prediction, "dispatch_if_eligible", _fake_dispatch)
    await live_prediction._maybe_dispatch(  # noqa: SLF001
        lambda: _BrokenSession(), pred=_pred(), layer_payload={},
    )

    assert any(
        "dispatch_decision_write_failed" in r.message for r in caplog.records
    )


@pytest.mark.asyncio
async def test_swallows_dispatch_exception(monkeypatch, caplog) -> None:
    _vault_loaded()

    async def _boom(*args, **kwargs):
        raise RuntimeError("binance down")

    monkeypatch.setattr(live_prediction, "dispatch_if_eligible", _boom)
    # Must not raise — worker survives a bad dispatch.
    await live_prediction._maybe_dispatch(  # noqa: SLF001
        _factory(), pred=_pred(), layer_payload={},
    )
    assert any("dispatch_if_eligible failed" in r.message for r in caplog.records)
