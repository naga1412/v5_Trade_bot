"""News ingest + cleanup background loops (SP-9 Phase D2/D3).

Two long-running coroutines live here:

* :func:`run_news_ingest_loop` — every 5 minutes, polls CryptoPanic for
  fresh crypto headlines; every 30 minutes, also polls the Yahoo Finance
  RSS feeds for macro news. Whatever it gets is classified through
  FinBERT in one batch and persisted via :func:`persist_news_items`.
  Per-source cursors live in module state so we only ever ask each
  adapter for "what's new since last time".
* :func:`run_news_cleanup_loop` — wakes once a day at 04:00 UTC and
  deletes any ``news_items`` row older than 20 days.

Both loops:

* Accept an injected ``sleep_fn`` so unit tests can drive them
  deterministically (raise :class:`asyncio.CancelledError` to break out).
* Re-raise :class:`asyncio.CancelledError` cleanly so the surrounding
  FastAPI lifespan can shut them down.
* Catch + log every other ``Exception`` so a transient adapter failure
  doesn't take down the worker.

The helpers in this module are intentionally small + injectable so each
one can be exercised in isolation.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import get_settings
from app.news.adapters._base import NewsArticle
from app.news.adapters.cryptopanic import CryptoPanicAdapter
from app.news.adapters.yahoo_rss import YahooRssAdapter


log = logging.getLogger(__name__)


# Per-adapter cursor: the published_at of the most recently ingested
# article from that source. Persists for the life of the process; reset
# to "now - BACKFILL_HOURS" on cold start.
_last_fetch_ts: dict[str, datetime] = {}


CRYPTO_INTERVAL_S: int = 5 * 60      # poll cryptopanic every 5 min
MACRO_INTERVAL_S: int = 30 * 60      # poll yahoo_rss every 30 min
BACKFILL_HOURS: int = 1               # cold-start window


def _build_adapters(
    rate_limited_client_factory: Callable[..., httpx.AsyncClient] | None = None,
) -> tuple[CryptoPanicAdapter, YahooRssAdapter]:
    """Construct the production adapter pair.

    The ``rate_limited_client_factory`` hook is currently unused — kept
    in the signature so a future test can swap the underlying client
    without monkey-patching this whole function.
    """
    settings = get_settings()
    crypto = CryptoPanicAdapter(api_key=settings.cryptopanic_api_key)
    yahoo = YahooRssAdapter()
    return crypto, yahoo


async def _run_one_iteration(
    session_factory: Any,
    crypto_adapter: CryptoPanicAdapter | Any,
    yahoo_adapter: YahooRssAdapter | Any,
    last_macro_run: datetime,
) -> None:
    """One fetch round: poll, classify, persist, advance cursors.

    Extracted from :func:`run_news_ingest_loop` so it can be unit-tested
    in isolation. The macro adapter is only polled if at least
    :data:`MACRO_INTERVAL_S` seconds have elapsed since ``last_macro_run``.
    """
    now = datetime.now(timezone.utc)
    cutoff_crypto = _last_fetch_ts.get(
        crypto_adapter.name, now - timedelta(hours=BACKFILL_HOURS),
    )
    crypto_articles = await crypto_adapter.fetch_recent(since=cutoff_crypto)

    macro_articles: list[NewsArticle] = []
    if datetime.now(timezone.utc) - last_macro_run >= timedelta(seconds=MACRO_INTERVAL_S):
        cutoff_macro = _last_fetch_ts.get(
            yahoo_adapter.name, now - timedelta(hours=BACKFILL_HOURS),
        )
        macro_articles = await yahoo_adapter.fetch_recent(since=cutoff_macro)

    all_articles: list[NewsArticle] = list(crypto_articles) + list(macro_articles)
    if not all_articles:
        return

    # Imported here so the heavy ``transformers`` / ``torch`` stack only
    # loads when the worker actually has something to classify.
    from app.news.sentiment import classify_batch
    from app.news.persistence import persist_news_items

    titles = [a.title for a in all_articles]
    sentiments = classify_batch(titles)

    async with session_factory() as session:
        await persist_news_items(session, all_articles, sentiments)
        await session.commit()

    if crypto_articles:
        _last_fetch_ts[crypto_adapter.name] = max(
            a.published_at for a in crypto_articles
        )
    if macro_articles:
        _last_fetch_ts[yahoo_adapter.name] = max(
            a.published_at for a in macro_articles
        )


async def run_news_ingest_loop(
    session_factory: Any,
    *,
    rate_limited_client_factory: Callable[..., httpx.AsyncClient] | None = None,
    sleep_fn: Callable[[float], Awaitable[None]] | None = None,
) -> None:
    """Async background task: 5-min crypto / 30-min macro ingest.

    ``sleep_fn`` defaults to :func:`asyncio.sleep`; unit tests pass a
    fake that raises :class:`asyncio.CancelledError` after N iterations
    to break out of the otherwise-infinite loop.
    """
    sleep = sleep_fn or asyncio.sleep
    crypto_adapter, yahoo_adapter = _build_adapters(rate_limited_client_factory)
    last_macro_run = datetime.now(timezone.utc) - timedelta(hours=BACKFILL_HOURS)

    while True:
        try:
            await _run_one_iteration(
                session_factory, crypto_adapter, yahoo_adapter, last_macro_run,
            )
            now = datetime.now(timezone.utc)
            if now - last_macro_run >= timedelta(seconds=MACRO_INTERVAL_S):
                last_macro_run = now
        except asyncio.CancelledError:
            log.info("news ingest loop cancelled")
            raise
        except Exception:
            log.exception("news ingest loop iteration failed")

        await sleep(float(CRYPTO_INTERVAL_S))


# ---------------------------------------------------------------------------
# Phase D3: nightly cleanup loop (implemented in D3 commit)
# ---------------------------------------------------------------------------


async def run_news_cleanup_loop(
    session_factory: Any,
    *,
    sleep_fn: Callable[[float], Awaitable[None]] | None = None,
) -> None:
    raise NotImplementedError("SP-9 Phase D3")


def start_news_ingest_task(session_factory: Any) -> asyncio.Task[None]:
    """Spawn :func:`run_news_ingest_loop` as a background task.

    Wired into ``app.main:lifespan`` in Phase D4.
    """
    return asyncio.create_task(run_news_ingest_loop(session_factory))


def start_news_cleanup_task(session_factory: Any) -> asyncio.Task[None]:
    """Spawn :func:`run_news_cleanup_loop` as a background task."""
    return asyncio.create_task(run_news_cleanup_loop(session_factory))


__all__ = [
    "BACKFILL_HOURS",
    "CRYPTO_INTERVAL_S",
    "MACRO_INTERVAL_S",
    "_build_adapters",
    "_last_fetch_ts",
    "_run_one_iteration",
    "run_news_cleanup_loop",
    "run_news_ingest_loop",
    "start_news_cleanup_task",
    "start_news_ingest_task",
]
