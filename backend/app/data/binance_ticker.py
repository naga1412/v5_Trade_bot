"""Best-effort batch fetch of Binance SPOT mid prices for a set of symbols.

Used by the Bot Status `/open-positions` REST endpoint to populate the
`current_price` / `unrealized_pnl_pct` fields at cold-load time. Without
this, the dashboard shows "—" for those columns until the live
`shadow_pnl_tick` WS stream fires its first per-symbol update (which
only happens on each candle close — up to 1h of waiting for 1h-timeframe
positions).

Design notes:
- One HTTP call per request, not per symbol. Binance's
  ``/api/v3/ticker/price?symbols=[…]`` batch endpoint takes a JSON array
  and returns the matching tickers in a single round-trip.
- Best-effort: any HTTP error, timeout, parse failure, or missing symbol
  returns an empty / partial dict. Caller must tolerate missing keys.
- No caching here — the endpoint is called only when the user hits the
  Bot Status tab (or refreshes), so the volume is tiny. The WS path
  (which can fire every few seconds during volatile periods) does its
  own caching at the worker layer.
"""
from __future__ import annotations

import json
import logging

import httpx


log = logging.getLogger(__name__)

# Default Binance SPOT base. Overridable in tests via the kwarg below.
_DEFAULT_BASE_URL: str = "https://api.binance.com"
_REQUEST_TIMEOUT_SECONDS: float = 5.0


async def fetch_spot_prices(
    symbols: list[str],
    *,
    http: httpx.AsyncClient | None = None,
    base_url: str = _DEFAULT_BASE_URL,
) -> dict[str, float]:
    """Return {symbol: latest_price} for ``symbols``. Empty dict on failure.

    ``symbols`` should be in the no-slash Binance format (``BTCUSDT``,
    not ``BTC/USDT``). The caller is responsible for that normalization
    — most callers in this codebase already store DB symbols in the
    SPOT-API form.

    The endpoint accepts symbols as a JSON array via the ``symbols`` query
    parameter. When the array is empty (no open positions) we skip the
    HTTP call entirely.
    """
    if not symbols:
        return {}

    close_client = http is None
    if http is None:
        http = httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS)

    try:
        # The query param is a JSON-encoded array of strings, NOT a
        # comma-separated list. Binance is strict about this — AND it
        # rejects the array if json.dumps' default separators inject a
        # space after each comma (URL-encodes to `+`/`%20`). Use
        # compact separators so the wire form is `["BTCUSDT","ETHUSDT"]`
        # not `["BTCUSDT", "ETHUSDT"]`.
        params = {"symbols": json.dumps(symbols, separators=(",", ":"))}
        resp = await http.get(
            f"{base_url}/api/v3/ticker/price", params=params,
        )
        resp.raise_for_status()
        body = resp.json()
        if not isinstance(body, list):
            log.warning(
                "fetch_spot_prices: unexpected response shape: %r", body,
            )
            return {}
        out: dict[str, float] = {}
        for entry in body:
            try:
                sym = str(entry["symbol"])
                price = float(entry["price"])
                out[sym] = price
            except (KeyError, TypeError, ValueError) as e:
                log.debug("fetch_spot_prices: skip malformed entry %r: %s", entry, e)
        return out
    except (httpx.HTTPError, json.JSONDecodeError) as e:
        log.warning("fetch_spot_prices failed for %d symbols: %s", len(symbols), e)
        return {}
    finally:
        if close_client:
            await http.aclose()


def compute_unrealized_pnl_pct(
    direction: str, entry_price: float, current_price: float,
) -> float | None:
    """Compute unrealized P&L as a signed percent of entry.

    Returns None if entry_price is non-positive (invalid trade state).
    LONG  → (current − entry) / entry × 100
    SHORT → (entry − current) / entry × 100
    """
    if entry_price <= 0:
        return None
    if direction == "LONG":
        return (current_price - entry_price) / entry_price * 100.0
    if direction == "SHORT":
        return (entry_price - current_price) / entry_price * 100.0
    return None


__all__ = ["fetch_spot_prices", "compute_unrealized_pnl_pct"]
