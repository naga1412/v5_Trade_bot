"""Integration tests for /api/v1/admin/test-trade/{open,close-all}.

The endpoints are the operator's smoke test for the live dispatch path.
They MUST:
  - Hard-force ``use_testnet=True`` regardless of env/config (a regression
    here would risk live-money trades from an admin endpoint).
  - Reject symbols not in the whitelist.
  - Cap the notional at MAX_TEST_NOTIONAL_USDT.
  - 503 cleanly when the vault isn't initialised.
  - Force mode=telegram-approve so the operator sees the full flow.

Network is fully mocked: no calls to Binance testnet, no calls to
Telegram. We monkey-patch the dispatcher (and the mark-price fetch)
so the test stays hermetic.
"""

from __future__ import annotations

from typing import Any

import pytest

import app.api.routes.admin_test_trade as ate
from app.trading.execution.dispatcher import DispatchResult


# ---------------------------------------------------------------------------
# Helpers


def _stub_vault(monkeypatch: Any) -> None:
    """Pretend the vault has been initialised with placeholder keys."""
    from app.trading.execution.glue import VaultKeys

    monkeypatch.setattr(
        ate, "vault_keys",
        lambda: VaultKeys(binance_api_key="test-key", binance_api_secret="test-secret"),
    )


async def _stub_mark_price(_: str) -> float:
    return 100_000.0


# ---------------------------------------------------------------------------
# open endpoint


@pytest.mark.asyncio
async def test_open_happy_path_dispatches_with_testnet_and_telegram_mode(
    bot_status_client: Any, monkeypatch: Any,
) -> None:
    """The dispatch call must see use_testnet=True and mode=telegram-approve.

    We capture the UserContext the dispatcher received and assert on those
    fields — the safety contract is that the endpoint cannot accidentally
    hit live Binance.
    """
    _stub_vault(monkeypatch)
    monkeypatch.setattr(ate, "_fetch_testnet_mark_price", _stub_mark_price)

    captured: dict[str, Any] = {}

    async def _fake_dispatch(session, *, proposal, user, now=None):
        captured["proposal"] = proposal
        captured["user"] = user
        return DispatchResult(
            outcome="sent_telegram",
            detail="Telegram message sent — awaiting Approve",
            signal_id="sig-test-123",
        )

    monkeypatch.setattr(ate, "dispatch", _fake_dispatch)

    r = await bot_status_client.post(
        "/api/v1/admin/test-trade/open",
        json={"symbol": "BTCUSDT", "direction": "LONG", "notional_usdt": 20.0},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["outcome"] == "sent_telegram"
    assert body["signal_id"] == "sig-test-123"

    # CRITICAL safety asserts on what the dispatcher actually saw:
    user = captured["user"]
    assert user.use_testnet is True, "endpoint must hard-force testnet"
    assert user.mode == "telegram-approve", (
        "endpoint must force the full Telegram approval flow"
    )
    assert user.fixed_size_usdt == 20.0
    # entry/sl/tp wrap the mark price correctly for LONG
    p = captured["proposal"]
    assert p.entry_price == pytest.approx(100_000.0)
    assert p.stop_loss_price < p.entry_price < p.take_profit_price


@pytest.mark.asyncio
async def test_open_short_inverts_sl_tp(
    bot_status_client: Any, monkeypatch: Any,
) -> None:
    """SHORT must invert the SL/TP layout — SL above entry, TP below."""
    _stub_vault(monkeypatch)
    monkeypatch.setattr(ate, "_fetch_testnet_mark_price", _stub_mark_price)

    captured: dict[str, Any] = {}

    async def _fake_dispatch(session, *, proposal, user, now=None):
        captured["proposal"] = proposal
        return DispatchResult(outcome="sent_telegram", detail="ok", signal_id="x")

    monkeypatch.setattr(ate, "dispatch", _fake_dispatch)

    r = await bot_status_client.post(
        "/api/v1/admin/test-trade/open",
        json={"symbol": "ETHUSDT", "direction": "SHORT"},
    )
    assert r.status_code == 200
    p = captured["proposal"]
    assert p.stop_loss_price > p.entry_price > p.take_profit_price


@pytest.mark.asyncio
async def test_open_rejects_non_whitelisted_symbol(
    bot_status_client: Any, monkeypatch: Any,
) -> None:
    """Symbols outside {BTCUSDT, ETHUSDT} must 422 (pydantic rejects at
    body parse) or 400 (we re-check inside the handler as belt+braces)."""
    _stub_vault(monkeypatch)
    r = await bot_status_client.post(
        "/api/v1/admin/test-trade/open",
        json={"symbol": "DOGEUSDT", "direction": "LONG"},
    )
    assert r.status_code in (400, 422), r.text


@pytest.mark.asyncio
async def test_open_caps_notional(bot_status_client: Any, monkeypatch: Any) -> None:
    """Pydantic Field(le=MAX_TEST_NOTIONAL_USDT) must reject > cap."""
    _stub_vault(monkeypatch)
    r = await bot_status_client.post(
        "/api/v1/admin/test-trade/open",
        json={"symbol": "BTCUSDT", "direction": "LONG", "notional_usdt": 5000.0},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_open_503_when_vault_unset(
    bot_status_client: Any, monkeypatch: Any,
) -> None:
    """If autonomous trading isn't enabled, vault_keys() returns None.
    Endpoint must 503 cleanly rather than crash."""
    monkeypatch.setattr(ate, "vault_keys", lambda: None)
    r = await bot_status_client.post(
        "/api/v1/admin/test-trade/open",
        json={"symbol": "BTCUSDT", "direction": "LONG"},
    )
    assert r.status_code == 503
    assert "vault" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_open_surfaces_dispatcher_failure_in_next_step(
    bot_status_client: Any, monkeypatch: Any,
) -> None:
    """If the dispatcher returns a non-sent_telegram outcome, the response
    must steer the operator to inspect logs rather than silently passing."""
    _stub_vault(monkeypatch)
    monkeypatch.setattr(ate, "_fetch_testnet_mark_price", _stub_mark_price)

    async def _bad_dispatch(session, *, proposal, user, now=None):
        return DispatchResult(
            outcome="error", detail="Binance: invalid signature",
            signal_id=None,
        )

    monkeypatch.setattr(ate, "dispatch", _bad_dispatch)

    r = await bot_status_client.post(
        "/api/v1/admin/test-trade/open",
        json={"symbol": "BTCUSDT", "direction": "LONG"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["outcome"] == "error"
    assert "logs" in body["next_step"].lower()


# ---------------------------------------------------------------------------
# close-all endpoint


@pytest.mark.asyncio
async def test_close_all_503_when_vault_unset(
    bot_status_client: Any, monkeypatch: Any,
) -> None:
    monkeypatch.setattr(ate, "vault_keys", lambda: None)
    r = await bot_status_client.post("/api/v1/admin/test-trade/close-all")
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_close_all_uses_testnet_client(
    bot_status_client: Any, monkeypatch: Any,
) -> None:
    """The BinanceLiveClient must be constructed with use_testnet=True.
    Same hard-coded-true safety contract as /open."""
    _stub_vault(monkeypatch)

    constructed: list[dict[str, Any]] = []

    class _FakeClient:
        def __init__(self, **kwargs):
            constructed.append(kwargs)

        async def get_position(self, *, symbol):
            return None  # no positions to close

        async def aclose(self):
            pass

    monkeypatch.setattr(ate, "BinanceLiveClient", _FakeClient)

    r = await bot_status_client.post("/api/v1/admin/test-trade/close-all")
    assert r.status_code == 200
    body = r.json()
    assert body["closed"] == []
    assert {"symbol": "BTCUSDT", "reason": "no open position"} in body["skipped"]
    assert {"symbol": "ETHUSDT", "reason": "no open position"} in body["skipped"]

    # The safety contract: testnet=True must always be passed.
    assert len(constructed) == 1
    assert constructed[0]["use_testnet"] is True
