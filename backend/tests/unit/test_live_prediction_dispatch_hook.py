"""SP-8 Phase J — live_prediction worker → dispatcher wiring."""
from __future__ import annotations

from dataclasses import dataclass
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


class _FakeSession:
    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def commit(self) -> None:
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
