"""The one place a bar fetch chooses its endpoint.

FU-53. Before this module there was no single bar-fetch entry point, so
every caller re-made the spot-or-futures choice, and the spot endpoint is
the one that looks like the default. That choice was made wrong three
times in three separate files:

1. **#539** (root-caused 2026-09-01) -- ``run_live_prediction`` seeded
   history from spot for every symbol, so the entire ``futures_poll``
   cohort produced **zero predictions for its whole existence**. See
   ``docs/superpowers/decisions/2026-09-01-futures-poll-seed-was-spot-only.md``.
2. ``app.ml.validator`` -- called the cohort-blind
   :meth:`BinanceAdapter.fetch_klines`, so outcome validation silently
   skipped every futures-only symbol.
3. ``backend/scripts/mfe_mae_curve``'s range fetcher -- cost the A0
   escalation **60 of its 279 symbols**.

A ``futures_poll`` symbol is a USDT perpetual with **no spot pair at
all**, so ``/api/v3/klines`` for it is not a degraded answer: it is a
permanent empty result, on every call, forever. Both the adapter and the
range fetchers swallow that into an empty list rather than raising, which
is why all three instances ran unnoticed for weeks.

THE SILENT DEFAULT IS THE DEFECT, not the endpoint. So
:func:`resolve_klines_url` **raises** on a cohort it was not taught,
rather than falling back to spot. A cohort added to
``app.shadow.live_fleet_universe.Cohort`` without being taught here fails
loudly at the first fetch instead of inheriting the #539 behaviour.

The load-bearing half of this fix is not this module -- a helper callers
can bypass is a convention, and the convention has already failed three
times. It is ``backend/tests/unit/test_bar_fetch_endpoint_guard.py``,
which asserts an exact allowlist of files permitted to name a klines
endpoint or call the cohort-blind fetcher, so that the fourth instance
fails in CI the moment it is written.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from app.data.adapters.binance import Candle


SPOT_KLINES_URL: str = "https://api.binance.com/api/v3/klines"
FUTURES_KLINES_URL: str = "https://fapi.binance.com/fapi/v1/klines"

# Cohort values are defined by app.shadow.live_fleet_universe.Cohort.
# They are restated rather than imported because that module pulls in the
# DB session factory, and this one is imported by offline scripts that
# have no database.  The guard test enumerates the Literal and asserts
# every member routes, so a drift between the two fails CI.
FUTURES_ONLY_COHORTS: frozenset[str] = frozenset({"futures_poll"})
SPOT_BACKED_COHORTS: frozenset[str] = frozenset(
    {"established_top20", "liquidity_added_spot"}
)


def resolve_klines_url(symbol_source: str) -> str:
    """The klines endpoint for one cohort, or ``ValueError``.

    Deliberately total over the cohorts it knows and loud over the ones it
    does not. Returning ``SPOT_KLINES_URL`` for an unrecognised cohort
    would reproduce #539 exactly: a futures-only symbol quietly fetching
    from a market it is not listed on, and an empty bar list that reads
    like "no data yet".
    """
    if symbol_source in FUTURES_ONLY_COHORTS:
        return FUTURES_KLINES_URL
    if symbol_source in SPOT_BACKED_COHORTS:
        return SPOT_KLINES_URL
    raise ValueError(
        f"unknown cohort {symbol_source!r}: cannot choose a klines endpoint. "
        "Teach app.data.bar_fetch which market this cohort is listed on. "
        "Do NOT default to spot -- a futures-only cohort fetching spot klines "
        "returns an empty list forever and reads like 'no data yet', which is "
        "how #539 cost the futures_poll cohort every prediction it should have "
        "made."
    )


def is_futures_only(symbol_source: str) -> bool:
    """True when this cohort has no spot listing. Raises on unknown cohorts."""
    return resolve_klines_url(symbol_source) == FUTURES_KLINES_URL


def resolve_klines_url_for_symbol(binance_symbol: str) -> str | None:
    """Endpoint for a symbol whose caller has no cohort tag, or ``None``.

    Some call sites genuinely do not carry a cohort: ``app.ml.validator``
    reads ``prediction_validations``, which has no ``symbol_source``
    column. They can still ask the question that actually matters -- is
    this symbol futures-only? -- from the futures-only cache, a
    near-static Binance listing property refreshed on a 24h cadence.

    ``None`` means NOT CLASSIFIABLE (the cache has never been populated),
    and callers must **skip** rather than fetch. That follows
    ``app.shadow.cohort_cache``'s NO-DEFAULT-ON-FAILURE rule, and here it
    is the whole point: guessing "spot" is #539, and guessing is what a
    returned URL would invite.
    """
    from app.shadow.cohort_cache import get_futures_only_cache

    cache = get_futures_only_cache()
    if cache is None:
        return None
    return FUTURES_KLINES_URL if binance_symbol in cache else SPOT_KLINES_URL


def _to_candles(rows: list[list[object]]) -> list[Candle]:
    """Parse klines rows. Spot and futures share an identical 12-element row."""
    from app.data.adapters.binance import Candle

    return [
        Candle(
            ts=datetime.fromtimestamp(int(r[0]) / 1000, tz=timezone.utc),  # type: ignore[arg-type]
            open=float(r[1]),  # type: ignore[arg-type]
            high=float(r[2]),  # type: ignore[arg-type]
            low=float(r[3]),  # type: ignore[arg-type]
            close=float(r[4]),  # type: ignore[arg-type]
            volume=float(r[5]),  # type: ignore[arg-type]
        )
        for r in rows
    ]


async def fetch_bars(
    symbol: str,
    timeframe: str,
    *,
    symbol_source: str,
    rate_client: object | None = None,
    limit: int = 500,
) -> list[Candle]:
    """Fetch bars for one symbol from the market its cohort is listed on.

    One code path for both markets: the endpoint comes from
    :func:`resolve_klines_url` and nothing else varies, because spot and
    futures klines share an identical 12-element row schema.

    ``rate_client`` SHOULD BE PASSED by anything on a live worker, and
    that is not a style preference. ``RateLimitedClient`` holds the
    Binance weight budget as *instance* state, so constructing a fresh
    one per call shares no budget with the app's own -- which is FU-40,
    where fire-and-forget flow-feature writes bypassed the limiter and
    burst 90-138 calls an hour. When omitted (offline scripts, one-shot
    analysis) a short-lived unlimited client is used instead.

    NOTE ON WHY THIS DOES NOT CALL THE ADAPTERS: both
    :class:`BinanceAdapter` and :class:`BinanceFuturesAdapter` expose a
    method named ``fetch_klines``, on different markets, so a reader
    cannot tell from a call line which endpoint it reaches -- part of why
    this went wrong three times. Resolving a URL from the cohort makes
    the choice visible at the one place it is made.
    """
    return await _fetch_at(
        resolve_klines_url(symbol_source), symbol, timeframe,
        rate_client=rate_client, limit=limit,
    )


async def fetch_bars_by_symbol(
    symbol: str,
    timeframe: str,
    *,
    rate_client: object | None = None,
    http: httpx.AsyncClient | None = None,
    limit: int = 500,
) -> list[Candle] | None:
    """:func:`fetch_bars` for callers with no cohort tag. ``None`` = skip.

    Returns ``None`` -- never an empty list -- when the market cannot be
    determined, so a caller cannot confuse "unclassifiable" with "no bars
    returned". That distinction is the one #539 destroyed: a spot fetch
    for a futures-only symbol returned ``[]``, which read as "no data
    yet" for the cohort's entire existence.
    """
    url = resolve_klines_url_for_symbol(symbol.replace("/", ""))
    if url is None:
        return None
    return await _fetch_at(url, symbol, timeframe,
                           rate_client=rate_client, limit=limit, http=http)


async def _fetch_at(
    url: str,
    symbol: str,
    timeframe: str,
    *,
    rate_client: object | None,
    limit: int,
    http: httpx.AsyncClient | None = None,
) -> list[Candle]:
    params = {"symbol": symbol.replace("/", ""), "interval": timeframe,
              "limit": str(limit)}
    if rate_client is not None:
        resp = await rate_client.request(  # type: ignore[attr-defined]
            "GET", url, endpoint_key="klines", params=params, timeout=15.0,
        )
        resp.raise_for_status()
        return _to_candles(resp.json())
    if http is not None:
        resp = await http.get(url, params=params, timeout=15.0)
        resp.raise_for_status()
        return _to_candles(resp.json())
    async with httpx.AsyncClient() as owned:
        resp = await owned.get(url, params=params, timeout=15.0)
        resp.raise_for_status()
        return _to_candles(resp.json())


__all__ = [
    "FUTURES_KLINES_URL",
    "FUTURES_ONLY_COHORTS",
    "SPOT_BACKED_COHORTS",
    "SPOT_KLINES_URL",
    "fetch_bars",
    "fetch_bars_by_symbol",
    "is_futures_only",
    "resolve_klines_url",
    "resolve_klines_url_for_symbol",
]
