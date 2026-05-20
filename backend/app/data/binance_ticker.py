"""Best-effort batch fetch of Binance SPOT mid prices for a set of symbols.

Used by the Bot Status `/open-positions` REST endpoint to populate the
`current_price` / `unrealized_pnl_pct` fields at cold-load time. Without
this, the dashboard shows "—" for those columns until the live
`shadow_pnl_tick` WS stream fires its first per-symbol update (which
only happens on each candle close — up to 1h of waiting for 1h-timeframe
positions).

Design notes:
- One HTTP call per request when batch succeeds; per-symbol fallback
  when the batch is rejected. Binance's
  ``/api/v3/ticker/price?symbols=[…]`` rejects the WHOLE batch if ANY
  symbol is invalid (futures-only, delisted, regionally restricted).
  PR10.7 added the fallback so one bad symbol doesn't blank the whole
  Open Positions card.
- Input symbols are deduped + blacklist-filtered upfront.
- Best-effort: any HTTP error, timeout, parse failure, or missing symbol
  returns a PARTIAL dict (skipping the bad symbols). Caller must tolerate
  missing keys. Pre-PR10.7 returned an empty dict on any failure.
- No caching here — the endpoint is called only when the user hits the
  Bot Status tab (or refreshes), so the volume is tiny.
"""
from __future__ import annotations

import json
import logging

import httpx

from app.config import get_settings


log = logging.getLogger(__name__)

# Default Binance SPOT base. Overridable in tests via the kwarg below.
_DEFAULT_BASE_URL: str = "https://api.binance.com"
_REQUEST_TIMEOUT_SECONDS: float = 5.0


# PR10.7: in-memory metrics. Surfaced via inspection (grep + log scans) only;
# no Prometheus exposure today. Reset on process restart.
_metrics: dict[str, object] = {
    "spot_price_fetch_batch_rejected_total": 0,
    "spot_price_fetch_per_symbol_fallback_total": 0,
    "spot_price_fetch_per_symbol_failed": {},  # type: ignore[dict-item]
}


def get_metrics_snapshot() -> dict[str, object]:
    """Return a copy of the in-memory metrics dict. For tests + probes."""
    return {
        "spot_price_fetch_batch_rejected_total":
            _metrics["spot_price_fetch_batch_rejected_total"],
        "spot_price_fetch_per_symbol_fallback_total":
            _metrics["spot_price_fetch_per_symbol_fallback_total"],
        "spot_price_fetch_per_symbol_failed":
            dict(_metrics["spot_price_fetch_per_symbol_failed"]),  # type: ignore[arg-type]
    }


def _reset_metrics_for_tests() -> None:
    _metrics["spot_price_fetch_batch_rejected_total"] = 0
    _metrics["spot_price_fetch_per_symbol_fallback_total"] = 0
    _metrics["spot_price_fetch_per_symbol_failed"] = {}


async def _fetch_one_symbol(
    http: httpx.AsyncClient, base_url: str, symbol: str,
) -> float | None:
    """Fetch a single SPOT price. Returns None on any failure."""
    try:
        resp = await http.get(
            f"{base_url}/api/v3/ticker/price", params={"symbol": symbol},
        )
        resp.raise_for_status()
        body = resp.json()
        return float(body["price"])
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        failed = _metrics["spot_price_fetch_per_symbol_failed"]
        if isinstance(failed, dict):
            failed[symbol] = failed.get(symbol, 0) + 1
        log.warning(
            "fetch_spot_prices fallback: skip %s: %s", symbol, e,
        )
        return None


async def fetch_spot_prices(
    symbols: list[str],
    *,
    http: httpx.AsyncClient | None = None,
    base_url: str = _DEFAULT_BASE_URL,
) -> dict[str, float]:
    """Return {symbol: latest_price} for ``symbols``. Partial dict on failure.

    ``symbols`` should be in the no-slash Binance format (``BTCUSDT``,
    not ``BTC/USDT``). The caller is responsible for that normalization
    — most callers in this codebase already store DB symbols in the
    SPOT-API form.

    PR10.7 behavior:
    - Deduplicate the input list (a position table with multiple open
      positions on BTCUSDT would otherwise pass BTCUSDT twice — Binance
      400-rejects the whole batch on duplicates).
    - Filter SPOT-blacklisted symbols (Settings.SHADOW_SPOT_BLACKLIST)
      upfront — they're known-bad on SPOT (futures-only, delisted, etc.).
    - On batch HTTP error, fall back to per-symbol fetches. Each
      individual failure is logged + counted but skipped (partial dict).
    """
    if not symbols:
        return {}

    # Dedupe + sort for stable order + filter blacklist.
    try:
        blacklist = set(get_settings().SHADOW_SPOT_BLACKLIST)
    except Exception:  # noqa: BLE001 — defensive; never block pricing on settings issues
        blacklist = set()
    deduped = sorted({s for s in symbols if s not in blacklist})
    if not deduped:
        return {}

    close_client = http is None
    if http is None:
        http = httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS)

    try:
        # Try the batch endpoint first. The query param is a JSON-encoded
        # array of strings, NOT a comma-separated list. Binance is strict
        # AND rejects the array if json.dumps' default separators inject a
        # space after each comma — use compact separators.
        params = {"symbols": json.dumps(deduped, separators=(",", ":"))}
        try:
            resp = await http.get(
                f"{base_url}/api/v3/ticker/price", params=params,
            )
            resp.raise_for_status()
            body = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError) as e:
            # Batch rejected — most likely one of the symbols is invalid on
            # SPOT. Fall back to per-symbol fetches. Slower (N HTTP calls
            # vs 1) but resilient — one bad symbol doesn't blank the whole
            # Open Positions card.
            _metrics["spot_price_fetch_batch_rejected_total"] = (
                int(_metrics["spot_price_fetch_batch_rejected_total"]) + 1  # type: ignore[arg-type]
            )
            log.warning(
                "fetch_spot_prices batch rejected (%d symbols): %s — "
                "falling back to per-symbol fetch",
                len(deduped), e,
            )
            return await _fetch_per_symbol(http, base_url, deduped)

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
    finally:
        if close_client:
            await http.aclose()


async def _fetch_per_symbol(
    http: httpx.AsyncClient, base_url: str, symbols: list[str],
) -> dict[str, float]:
    """PR10.7: batch-failure fallback path."""
    _metrics["spot_price_fetch_per_symbol_fallback_total"] = (
        int(_metrics["spot_price_fetch_per_symbol_fallback_total"]) + 1  # type: ignore[arg-type]
    )
    out: dict[str, float] = {}
    for sym in symbols:
        price = await _fetch_one_symbol(http, base_url, sym)
        if price is not None:
            out[sym] = price
    return out


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


__all__ = [
    "compute_unrealized_pnl_pct",
    "fetch_spot_prices",
    "get_metrics_snapshot",
]
