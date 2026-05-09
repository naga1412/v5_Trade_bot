"""SP-8 Phase F — Binance Futures (USDT-margined) live order client.

Spec §13 (live execution) + §9.3 (API key permission validation).

This is the WRITE path — places real orders on Binance Futures. The
read path (klines, exchangeInfo, ticker) lives in
``app/data/adapters/binance.py`` and is unchanged.

Endpoints used:
    GET    /fapi/v1/exchangeInfo                — symbol + filters
    GET    /fapi/v1/ping                        — liveness
    POST   /fapi/v1/order                       — place order (LONG/SHORT)
    DELETE /fapi/v1/order                       — cancel order
    GET    /fapi/v1/positionRisk                — current position state
    POST   /fapi/v1/leverage                    — set leverage
    GET    /sapi/v1/account/apiRestrictions     — verify key permissions

Authentication: HMAC-SHA256 over the query string + timestamp +
signature. Keys come from the SP-8 vault (Phase E); this module takes
plaintext key + secret as args so it stays vault-agnostic.

Failure modes:
- API key has withdrawal/transfer enabled → verify_permissions raises
  ApiKeyPermissionViolation; caller refuses to start live mode (spec §9.3).
- 429/418 rate limit → caller respects Retry-After header.
- 5xx Binance side → bubble up; caller decides retry policy.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx


log = logging.getLogger(__name__)


_TESTNET_URL = "https://testnet.binancefuture.com"
_LIVE_URL = "https://fapi.binance.com"
_RECV_WINDOW_MS = 5_000


class BinanceLiveError(Exception):
    """Base for all live-trading errors."""


class ApiKeyPermissionViolation(BinanceLiveError):
    """Spec §9.3 violation — withdrawal or transfer permissions enabled."""


class OrderRejected(BinanceLiveError):
    """Binance returned 4xx on a place_order call."""


@dataclass(frozen=True)
class OrderResult:
    """Successful order placement response (subset of Binance fields)."""

    binance_order_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    qty: float
    avg_fill_price: float
    status: str          # NEW / FILLED / PARTIALLY_FILLED / CANCELED / ...
    raw: dict[str, Any]  # full response for audit


@dataclass(frozen=True)
class PositionState:
    """Current open position on a symbol (one row from positionRisk)."""

    symbol: str
    position_amt: float       # signed; positive = long, negative = short
    entry_price: float
    leverage: int
    liquidation_price: float
    unrealized_pnl: float


def _sign(secret: str, query: str) -> str:
    """HMAC-SHA256 hex digest used for Binance API signature."""
    return hmac.new(
        secret.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _build_signed_query(secret: str, params: dict[str, Any]) -> str:
    """Append timestamp + recvWindow + signature; return ready-to-send query."""
    params = dict(params)
    params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = _RECV_WINDOW_MS
    base = "&".join(f"{k}={v}" for k, v in params.items())
    sig = _sign(secret, base)
    return f"{base}&signature={sig}"


class BinanceLiveClient:
    """Binance Futures live + testnet order client.

    Construct with:
        client = BinanceLiveClient(
            api_key="...", api_secret="...",
            use_testnet=True,
            http=shared_httpx_client,  # optional; new one created if None
        )

    Always call ``await client.verify_permissions()`` once at startup
    BEFORE any place_order. Spec §9.3 — refuses to operate if the
    keys have withdrawal/transfer enabled.
    """

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        use_testnet: bool = True,
        http: httpx.AsyncClient | None = None,
    ):
        if not api_key or not api_secret:
            raise ValueError("api_key + api_secret required")
        self._key = api_key
        self._secret = api_secret
        self._base = _TESTNET_URL if use_testnet else _LIVE_URL
        self._http = http if http is not None else httpx.AsyncClient(timeout=15.0)
        self._owns_http = http is None

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    @property
    def headers(self) -> dict[str, str]:
        return {"X-MBX-APIKEY": self._key}

    # ---- Permission verification (spec §9.3) ----------------------------

    async def verify_permissions(self) -> None:
        """Spec §9.3: refuse to operate if withdrawals or transfers
        are enabled on the API key.

        Calls /sapi/v1/account/apiRestrictions. The response includes:
            enableWithdrawals
            enableInternalTransfer
            permitsUniversalTransfer

        Any of those being true → ApiKeyPermissionViolation. The
        operator must regenerate keys with the correct permissions
        before live trading is allowed.
        """
        # Note: /sapi/v1 lives on api.binance.com, NOT fapi. Use the
        # spot host for permission queries, regardless of testnet/live
        # for futures (testnet doesn't expose /sapi).
        host = (
            "https://api.binance.com" if not self._base.endswith(".com") or "fapi" in self._base
            else "https://api.binance.com"
        )
        query = _build_signed_query(self._secret, {})
        r = await self._http.get(
            f"{host}/sapi/v1/account/apiRestrictions?{query}",
            headers=self.headers,
        )
        if r.status_code != 200:
            raise BinanceLiveError(
                f"apiRestrictions returned {r.status_code}: {r.text[:200]}"
            )
        data = r.json()
        bad = []
        if data.get("enableWithdrawals"):
            bad.append("enableWithdrawals")
        if data.get("enableInternalTransfer"):
            bad.append("enableInternalTransfer")
        if data.get("permitsUniversalTransfer"):
            bad.append("permitsUniversalTransfer")
        if bad:
            raise ApiKeyPermissionViolation(
                f"API key has dangerous permissions enabled: {', '.join(bad)}. "
                "Regenerate with Reading + Futures only; disable all withdrawal "
                "and transfer scopes; restrict to Hetzner IP."
            )

    # ---- Order placement ------------------------------------------------

    async def place_order(
        self,
        *,
        symbol: str,                 # e.g. "BTCUSDT" (Binance native)
        side: Literal["BUY", "SELL"],
        quantity: float,
        leverage: int,
        order_type: Literal["MARKET", "LIMIT"] = "MARKET",
        price: float | None = None,
        stop_loss_price: float | None = None,
        take_profit_price: float | None = None,
    ) -> OrderResult:
        """Place a futures order. Sets leverage first if it's not already.

        Returns the OrderResult on 200; raises OrderRejected on 4xx
        with the response body in the message for the caller's audit.
        """
        # 1. Ensure leverage is set on the symbol (idempotent — Binance
        # accepts repeat calls with the same value).
        await self._set_leverage(symbol=symbol, leverage=leverage)

        # 2. Place the entry order.
        params: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "quantity": quantity,
        }
        if order_type == "LIMIT":
            if price is None:
                raise ValueError("LIMIT orders require price=")
            params["price"] = price
            params["timeInForce"] = "GTC"
        query = _build_signed_query(self._secret, params)
        r = await self._http.post(
            f"{self._base}/fapi/v1/order?{query}",
            headers=self.headers,
        )
        if r.status_code >= 400:
            raise OrderRejected(
                f"Binance {r.status_code}: {r.text[:500]}"
            )
        body = r.json()
        return OrderResult(
            binance_order_id=str(body["orderId"]),
            symbol=body["symbol"],
            side=body["side"],
            qty=float(body.get("executedQty") or body.get("origQty") or quantity),
            avg_fill_price=float(body.get("avgPrice") or body.get("price") or 0),
            status=body.get("status", "NEW"),
            raw=body,
        )

    async def _set_leverage(self, *, symbol: str, leverage: int) -> None:
        params = {"symbol": symbol, "leverage": leverage}
        query = _build_signed_query(self._secret, params)
        r = await self._http.post(
            f"{self._base}/fapi/v1/leverage?{query}",
            headers=self.headers,
        )
        if r.status_code >= 400:
            # Common cause: leverage exceeds Binance's per-symbol max.
            raise OrderRejected(
                f"set_leverage({symbol}, {leverage}x) failed "
                f"{r.status_code}: {r.text[:300]}"
            )

    async def cancel_order(self, *, symbol: str, order_id: str) -> dict:
        """Cancel an open order by Binance's orderId."""
        params = {"symbol": symbol, "orderId": order_id}
        query = _build_signed_query(self._secret, params)
        r = await self._http.delete(
            f"{self._base}/fapi/v1/order?{query}",
            headers=self.headers,
        )
        if r.status_code >= 400:
            raise OrderRejected(
                f"cancel_order({symbol}, {order_id}) "
                f"{r.status_code}: {r.text[:300]}"
            )
        return r.json()

    # ---- Position queries -----------------------------------------------

    async def get_position(self, *, symbol: str) -> PositionState | None:
        """Return current position state for a symbol, or None if flat."""
        query = _build_signed_query(self._secret, {"symbol": symbol})
        r = await self._http.get(
            f"{self._base}/fapi/v2/positionRisk?{query}",
            headers=self.headers,
        )
        if r.status_code >= 400:
            raise BinanceLiveError(
                f"positionRisk({symbol}) {r.status_code}: {r.text[:200]}"
            )
        rows = r.json()
        # Binance returns one row per position-mode (one-way / hedge).
        # Pick the row with non-zero positionAmt; otherwise we're flat.
        for row in rows:
            amt = float(row.get("positionAmt") or 0)
            if abs(amt) > 1e-12:
                return PositionState(
                    symbol=row["symbol"],
                    position_amt=amt,
                    entry_price=float(row["entryPrice"]),
                    leverage=int(row["leverage"]),
                    liquidation_price=float(row.get("liquidationPrice") or 0),
                    unrealized_pnl=float(row.get("unRealizedProfit") or 0),
                )
        return None

    async def close_position(self, *, symbol: str) -> OrderResult:
        """Market-close any open position on the symbol.

        Issues a reduce-only MARKET order with the opposite side.
        Returns the closing fill; if no position is open, raises
        BinanceLiveError so the caller doesn't silently no-op.
        """
        pos = await self.get_position(symbol=symbol)
        if pos is None:
            raise BinanceLiveError(
                f"close_position({symbol}): no open position"
            )
        side = "SELL" if pos.position_amt > 0 else "BUY"
        params = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": abs(pos.position_amt),
            "reduceOnly": "true",
        }
        query = _build_signed_query(self._secret, params)
        r = await self._http.post(
            f"{self._base}/fapi/v1/order?{query}",
            headers=self.headers,
        )
        if r.status_code >= 400:
            raise OrderRejected(
                f"close_position({symbol}) {r.status_code}: {r.text[:300]}"
            )
        body = r.json()
        return OrderResult(
            binance_order_id=str(body["orderId"]),
            symbol=body["symbol"],
            side=body["side"],
            qty=float(body.get("executedQty") or body.get("origQty") or 0),
            avg_fill_price=float(body.get("avgPrice") or 0),
            status=body.get("status", "NEW"),
            raw=body,
        )

    async def ping(self) -> bool:
        """Liveness check — used by the kill_switches network_outage probe."""
        try:
            r = await self._http.get(f"{self._base}/fapi/v1/ping", timeout=5.0)
            return r.status_code == 200
        except (httpx.HTTPError, OSError):
            return False


__all__ = [
    "ApiKeyPermissionViolation",
    "BinanceLiveClient",
    "BinanceLiveError",
    "OrderRejected",
    "OrderResult",
    "PositionState",
]
