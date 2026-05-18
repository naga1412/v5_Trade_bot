"""PR9 place_multi_entry_orders — order placement integration tests.

Mocks BinanceLiveClient so tests can run without exchange creds.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.exchanges.binance_live import OrderRejected, OrderResult
from app.trading.multi_entry import (
    _dca_price,
    place_multi_entry_orders,
)


def _mock_client(place_results: list[OrderResult | Exception]):
    client = MagicMock()
    # AsyncMock with side_effect=list cycles through responses;
    # a list element that is an Exception is raised.
    client.place_order = AsyncMock(side_effect=place_results)
    return client


def _order_result(order_id: str, qty: float, price: float = 100.0) -> OrderResult:
    return OrderResult(
        binance_order_id=order_id, symbol="BTCUSDT", side="BUY",
        qty=qty, avg_fill_price=price, status="FILLED", raw={},
    )


# --- _dca_price ---------------------------------------------------------


def test_dca_price_long_first_tranche_below_entry() -> None:
    """LONG with 0.5% band, tranche_idx=1 → LIMIT BUY at -0.5%."""
    assert _dca_price(100.0, "LONG", 0.5, 1) == 99.5


def test_dca_price_short_first_tranche_above_entry() -> None:
    """SHORT with 0.5% band, tranche_idx=1 → LIMIT SELL at +0.5%."""
    # Float arithmetic: 100*(1+0.005) = 100.49999... not 100.5 exact
    assert abs(_dca_price(100.0, "SHORT", 0.5, 1) - 100.5) < 1e-9


def test_dca_price_long_third_tranche_double_band() -> None:
    """tranche_idx=2: two band-steps against direction."""
    assert _dca_price(100.0, "LONG", 0.5, 2) == 99.0


# --- place_multi_entry_orders ------------------------------------------


@pytest.mark.asyncio
async def test_single_tranche_places_market_only() -> None:
    """1-tranche list → 1 MARKET order, no DCA LIMITs."""
    client = _mock_client([_order_result("o1", 0.001)])
    result = await place_multi_entry_orders(
        client=client, symbol="BTCUSDT", side="BUY", direction="LONG",
        tranche_qtys=[0.001], leverage=10, entry_price=100.0,
        dca_band_pct=0.5,
    )
    assert len(result.tranches_placed) == 1
    assert result.tranches_skipped == 0
    # Verify the single call was a MARKET order
    call = client.place_order.call_args_list[0]
    assert call.kwargs["order_type"] == "MARKET"


@pytest.mark.asyncio
async def test_two_tranches_places_market_then_limit() -> None:
    """2 tranches: tranche 1 MARKET, tranche 2 LIMIT at DCA price."""
    client = _mock_client([
        _order_result("o1", 0.0006),
        _order_result("o2", 0.0004),
    ])
    result = await place_multi_entry_orders(
        client=client, symbol="BTCUSDT", side="BUY", direction="LONG",
        tranche_qtys=[0.0006, 0.0004], leverage=10, entry_price=100.0,
        dca_band_pct=0.5,
    )
    assert len(result.tranches_placed) == 2
    assert result.tranches_skipped == 0
    calls = client.place_order.call_args_list
    assert calls[0].kwargs["order_type"] == "MARKET"
    assert calls[1].kwargs["order_type"] == "LIMIT"
    assert calls[1].kwargs["price"] == 99.5  # DCA at -0.5%


@pytest.mark.asyncio
async def test_tranche_2_failure_keeps_tranche_1() -> None:
    """Tranche 2 OrderRejected → tranche 1 stays placed, tranche 2 skipped."""
    client = _mock_client([
        _order_result("o1", 0.0006),
        OrderRejected("rate limit"),
    ])
    result = await place_multi_entry_orders(
        client=client, symbol="BTCUSDT", side="BUY", direction="LONG",
        tranche_qtys=[0.0006, 0.0004], leverage=10, entry_price=100.0,
        dca_band_pct=0.5,
    )
    assert len(result.tranches_placed) == 1
    assert result.tranches_placed[0].binance_order_id == "o1"
    assert result.tranches_skipped == 1


@pytest.mark.asyncio
async def test_tranche_2_unexpected_exception_still_isolates() -> None:
    """Non-OrderRejected exception on tranche 2 also doesn't roll back tranche 1."""
    client = _mock_client([
        _order_result("o1", 0.0006),
        ConnectionError("network blip"),
    ])
    result = await place_multi_entry_orders(
        client=client, symbol="BTCUSDT", side="BUY", direction="LONG",
        tranche_qtys=[0.0006, 0.0004], leverage=10, entry_price=100.0,
        dca_band_pct=0.5,
    )
    assert len(result.tranches_placed) == 1
    assert result.tranches_skipped == 1


@pytest.mark.asyncio
async def test_tranche_1_failure_propagates() -> None:
    """Tranche 1 failure → exception propagates (caller handles as blocked_error)."""
    client = _mock_client([OrderRejected("Binance 400: bad qty")])
    with pytest.raises(OrderRejected):
        await place_multi_entry_orders(
            client=client, symbol="BTCUSDT", side="BUY", direction="LONG",
            tranche_qtys=[0.0006, 0.0004], leverage=10, entry_price=100.0,
            dca_band_pct=0.5,
        )


@pytest.mark.asyncio
async def test_empty_tranche_qtys_no_call() -> None:
    """Empty list → no orders placed."""
    client = _mock_client([])
    result = await place_multi_entry_orders(
        client=client, symbol="BTCUSDT", side="BUY", direction="LONG",
        tranche_qtys=[], leverage=10, entry_price=100.0,
        dca_band_pct=0.5,
    )
    assert result.tranches_placed == []
    assert result.tranches_skipped == 0
    client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_short_signal_dca_price_above_entry() -> None:
    """SHORT signal: tranche 2 LIMIT at entry +0.5%."""
    client = _mock_client([
        _order_result("o1", 0.0006),
        _order_result("o2", 0.0004, price=100.5),
    ])
    result = await place_multi_entry_orders(
        client=client, symbol="BTCUSDT", side="SELL", direction="SHORT",
        tranche_qtys=[0.0006, 0.0004], leverage=10, entry_price=100.0,
        dca_band_pct=0.5,
    )
    assert len(result.tranches_placed) == 2
    calls = client.place_order.call_args_list
    assert abs(calls[1].kwargs["price"] - 100.5) < 1e-9  # DCA at +0.5%
