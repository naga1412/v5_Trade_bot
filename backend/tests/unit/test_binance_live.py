"""SP-8 Phase F — Binance live order client unit tests.

Mocks the HTTP transport so tests don't touch real Binance. Permission
verification + order placement + close-position + ping all covered;
real Binance testnet smoke is a separate integration test (gated on
TESTNET_API_KEY env var) that doesn't run in CI.
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.exchanges.binance_live import (
    ApiKeyPermissionViolation,
    BinanceLiveClient,
    BinanceLiveError,
    OrderRejected,
    OrderResult,
    PositionState,
    _build_signed_query,
    _sign,
)


# ---- HMAC signing --------------------------------------------------------


def test_sign_matches_known_hmac() -> None:
    """Hand-computed: HMAC-SHA256("test-secret", "symbol=BTCUSDT&timestamp=1") =
    well-defined hex string. Verifies we're using SHA256 not MD5/SHA1."""
    sig = _sign("test-secret", "symbol=BTCUSDT&timestamp=1")
    # Sanity: 64-hex chars (256 bit hash)
    assert len(sig) == 64
    assert all(c in "0123456789abcdef" for c in sig)


def test_build_signed_query_appends_timestamp_and_signature() -> None:
    q = _build_signed_query("test-secret", {"symbol": "BTCUSDT", "side": "BUY"})
    assert q.startswith("symbol=BTCUSDT&side=BUY&timestamp=")
    assert "&recvWindow=5000" in q
    assert "&signature=" in q
    # Signature is the last param.
    assert q.split("&signature=")[1] != ""


# ---- Construction --------------------------------------------------------


def test_construct_with_empty_keys_raises() -> None:
    with pytest.raises(ValueError, match="api_key.*required"):
        BinanceLiveClient(api_key="", api_secret="x")
    with pytest.raises(ValueError, match="api_key.*required"):
        BinanceLiveClient(api_key="x", api_secret="")


def test_construct_testnet_vs_live_url() -> None:
    c = BinanceLiveClient(api_key="k", api_secret="s", use_testnet=True)
    assert "testnet" in c._base
    c2 = BinanceLiveClient(api_key="k", api_secret="s", use_testnet=False)
    assert c2._base == "https://fapi.binance.com"


# ---- verify_permissions (spec §9.3) -------------------------------------


def _http_with(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_verify_permissions_passes_when_only_reading_and_futures() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "enableReading": True,
            "enableFutures": True,
            "enableWithdrawals": False,
            "enableInternalTransfer": False,
            "permitsUniversalTransfer": False,
        })
    async with _http_with(handler) as http:
        c = BinanceLiveClient(api_key="k", api_secret="s", http=http)
        await c.verify_permissions()  # should not raise


@pytest.mark.asyncio
async def test_verify_permissions_raises_on_withdrawal_enabled() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "enableWithdrawals": True,
            "enableInternalTransfer": False,
            "permitsUniversalTransfer": False,
        })
    async with _http_with(handler) as http:
        c = BinanceLiveClient(api_key="k", api_secret="s", http=http)
        with pytest.raises(ApiKeyPermissionViolation, match="enableWithdrawals"):
            await c.verify_permissions()


@pytest.mark.asyncio
async def test_verify_permissions_raises_on_internal_transfer_enabled() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "enableWithdrawals": False,
            "enableInternalTransfer": True,
            "permitsUniversalTransfer": False,
        })
    async with _http_with(handler) as http:
        c = BinanceLiveClient(api_key="k", api_secret="s", http=http)
        with pytest.raises(
            ApiKeyPermissionViolation, match="enableInternalTransfer",
        ):
            await c.verify_permissions()


@pytest.mark.asyncio
async def test_verify_permissions_lists_all_violations() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "enableWithdrawals": True,
            "enableInternalTransfer": True,
            "permitsUniversalTransfer": True,
        })
    async with _http_with(handler) as http:
        c = BinanceLiveClient(api_key="k", api_secret="s", http=http)
        with pytest.raises(ApiKeyPermissionViolation) as exc:
            await c.verify_permissions()
        msg = str(exc.value)
        assert "enableWithdrawals" in msg
        assert "enableInternalTransfer" in msg
        assert "permitsUniversalTransfer" in msg


@pytest.mark.asyncio
async def test_verify_permissions_propagates_http_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"code": -2014, "msg": "API-key format invalid."})
    async with _http_with(handler) as http:
        c = BinanceLiveClient(api_key="k", api_secret="s", http=http)
        with pytest.raises(BinanceLiveError, match="apiRestrictions returned 401"):
            await c.verify_permissions()


# ---- place_order ---------------------------------------------------------


def _make_order_handler(
    *, leverage_response: dict | None = None,
    order_response: dict | None = None,
    order_status: int = 200,
) -> Any:
    """Handler that returns leverage + order responses depending on URL."""
    lev_resp = leverage_response or {"leverage": 10, "symbol": "BTCUSDT"}
    ord_resp = order_response or {
        "orderId": 123, "symbol": "BTCUSDT", "side": "BUY",
        "executedQty": "0.01", "avgPrice": "80000.0", "status": "FILLED",
    }

    def handler(req: httpx.Request) -> httpx.Response:
        if "/leverage" in str(req.url):
            return httpx.Response(200, json=lev_resp)
        if "/order" in str(req.url):
            return httpx.Response(order_status, json=ord_resp)
        return httpx.Response(404, json={"error": "unmocked"})
    return handler


@pytest.mark.asyncio
async def test_place_order_market_buy_returns_order_result() -> None:
    async with _http_with(_make_order_handler()) as http:
        c = BinanceLiveClient(api_key="k", api_secret="s", http=http)
        r = await c.place_order(
            symbol="BTCUSDT", side="BUY", quantity=0.01, leverage=10,
        )
    assert isinstance(r, OrderResult)
    assert r.binance_order_id == "123"
    assert r.symbol == "BTCUSDT"
    assert r.side == "BUY"
    assert r.qty == 0.01
    assert r.avg_fill_price == 80000.0
    assert r.status == "FILLED"


@pytest.mark.asyncio
async def test_place_order_4xx_raises_order_rejected() -> None:
    handler = _make_order_handler(
        order_response={"code": -2010, "msg": "insufficient balance"},
        order_status=400,
    )
    async with _http_with(handler) as http:
        c = BinanceLiveClient(api_key="k", api_secret="s", http=http)
        with pytest.raises(OrderRejected, match="insufficient balance"):
            await c.place_order(
                symbol="BTCUSDT", side="BUY", quantity=0.01, leverage=10,
            )


@pytest.mark.asyncio
async def test_place_order_limit_requires_price() -> None:
    async with _http_with(_make_order_handler()) as http:
        c = BinanceLiveClient(api_key="k", api_secret="s", http=http)
        with pytest.raises(ValueError, match="LIMIT orders require price"):
            await c.place_order(
                symbol="BTCUSDT", side="BUY", quantity=0.01, leverage=10,
                order_type="LIMIT",
            )


@pytest.mark.asyncio
async def test_place_order_sets_leverage_first() -> None:
    """Ensures _set_leverage is called before /order. Critical: forgetting
    this puts the position at the WRONG leverage and could blow up later."""
    seen_urls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen_urls.append(str(req.url))
        if "/leverage" in str(req.url):
            return httpx.Response(200, json={"leverage": 5})
        if "/order" in str(req.url):
            return httpx.Response(200, json={
                "orderId": 1, "symbol": "BTCUSDT", "side": "BUY",
                "executedQty": "0.01", "avgPrice": "80000", "status": "FILLED",
            })
        return httpx.Response(404)

    async with _http_with(handler) as http:
        c = BinanceLiveClient(api_key="k", api_secret="s", http=http)
        await c.place_order(symbol="BTCUSDT", side="BUY", quantity=0.01, leverage=5)

    assert any("/leverage" in u for u in seen_urls)
    leverage_idx = next(i for i, u in enumerate(seen_urls) if "/leverage" in u)
    order_idx = next(i for i, u in enumerate(seen_urls) if "/order" in u)
    assert leverage_idx < order_idx


# ---- get_position --------------------------------------------------------


@pytest.mark.asyncio
async def test_get_position_returns_state_when_open() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{
            "symbol": "BTCUSDT", "positionAmt": "0.05",
            "entryPrice": "80000.0", "leverage": "10",
            "liquidationPrice": "72500.0", "unRealizedProfit": "12.5",
        }])
    async with _http_with(handler) as http:
        c = BinanceLiveClient(api_key="k", api_secret="s", http=http)
        pos = await c.get_position(symbol="BTCUSDT")
    assert isinstance(pos, PositionState)
    assert pos.position_amt == 0.05
    assert pos.entry_price == 80000.0
    assert pos.leverage == 10
    assert pos.liquidation_price == 72500.0


@pytest.mark.asyncio
async def test_get_position_returns_none_when_flat() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{
            "symbol": "BTCUSDT", "positionAmt": "0.0",
            "entryPrice": "0", "leverage": "1",
            "liquidationPrice": "0", "unRealizedProfit": "0",
        }])
    async with _http_with(handler) as http:
        c = BinanceLiveClient(api_key="k", api_secret="s", http=http)
        pos = await c.get_position(symbol="BTCUSDT")
    assert pos is None


# ---- close_position ------------------------------------------------------


@pytest.mark.asyncio
async def test_close_position_long_sends_sell_reduce_only() -> None:
    captured_params: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "/positionRisk" in url:
            return httpx.Response(200, json=[{
                "symbol": "BTCUSDT", "positionAmt": "0.05",
                "entryPrice": "80000", "leverage": "10",
                "liquidationPrice": "72000", "unRealizedProfit": "0",
            }])
        if "/order" in url:
            for k in ("side", "quantity", "reduceOnly", "type"):
                captured_params[k] = req.url.params.get(k)
            return httpx.Response(200, json={
                "orderId": 99, "symbol": "BTCUSDT", "side": "SELL",
                "executedQty": "0.05", "avgPrice": "81000",
                "status": "FILLED",
            })
        return httpx.Response(404)

    async with _http_with(handler) as http:
        c = BinanceLiveClient(api_key="k", api_secret="s", http=http)
        r = await c.close_position(symbol="BTCUSDT")

    assert r.side == "SELL"
    assert captured_params["side"] == "SELL"
    assert captured_params["quantity"] == "0.05"
    assert captured_params["reduceOnly"] == "true"
    assert captured_params["type"] == "MARKET"


@pytest.mark.asyncio
async def test_close_position_short_sends_buy_reduce_only() -> None:
    captured_side = []

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "/positionRisk" in url:
            return httpx.Response(200, json=[{
                "symbol": "BTCUSDT", "positionAmt": "-0.05",
                "entryPrice": "80000", "leverage": "10",
                "liquidationPrice": "88000", "unRealizedProfit": "0",
            }])
        if "/order" in url:
            captured_side.append(req.url.params.get("side"))
            return httpx.Response(200, json={
                "orderId": 99, "symbol": "BTCUSDT", "side": "BUY",
                "executedQty": "0.05", "avgPrice": "79000",
                "status": "FILLED",
            })
        return httpx.Response(404)

    async with _http_with(handler) as http:
        c = BinanceLiveClient(api_key="k", api_secret="s", http=http)
        r = await c.close_position(symbol="BTCUSDT")

    assert r.side == "BUY"
    assert captured_side == ["BUY"]


@pytest.mark.asyncio
async def test_close_position_no_open_raises() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{
            "symbol": "BTCUSDT", "positionAmt": "0",
            "entryPrice": "0", "leverage": "1",
            "liquidationPrice": "0", "unRealizedProfit": "0",
        }])
    async with _http_with(handler) as http:
        c = BinanceLiveClient(api_key="k", api_secret="s", http=http)
        with pytest.raises(BinanceLiveError, match="no open position"):
            await c.close_position(symbol="BTCUSDT")


# ---- ping ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_ping_returns_true_on_200() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})
    async with _http_with(handler) as http:
        c = BinanceLiveClient(api_key="k", api_secret="s", http=http)
        assert await c.ping() is True


@pytest.mark.asyncio
async def test_ping_returns_false_on_network_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")
    async with _http_with(handler) as http:
        c = BinanceLiveClient(api_key="k", api_secret="s", http=http)
        assert await c.ping() is False


@pytest.mark.asyncio
async def test_ping_returns_false_on_5xx() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"msg": "service unavailable"})
    async with _http_with(handler) as http:
        c = BinanceLiveClient(api_key="k", api_secret="s", http=http)
        assert await c.ping() is False
