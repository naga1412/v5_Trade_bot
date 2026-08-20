"""Unit tests for SP-9 Phase D2 news ingest worker.

The worker is exercised entirely via dependency injection — both adapters
are passed in (the production code path uses ``_build_adapters`` which is
covered by a smoke test) and ``sleep_fn`` is replaced so we can break out
of the loop after exactly N iterations.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

import app.news.ingest_worker as iw
from app.news.adapters._base import NewsArticle
from app.news.sentiment import SentimentResult


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeAdapter:
    """Records the cursor passed to ``fetch_recent`` and returns a script."""

    def __init__(
        self,
        name: str,
        scripted: list[list[NewsArticle]] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.name = name
        self._scripted = scripted or []
        self._raises = raises
        self.calls: list[datetime] = []

    async def fetch_recent(self, *, since: datetime) -> list[NewsArticle]:
        self.calls.append(since)
        if self._raises is not None:
            raise self._raises
        if not self._scripted:
            return []
        return self._scripted.pop(0)


class _FakeSession:
    def __init__(self, store: list[Any]) -> None:
        self._store = store
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


def _session_factory_factory(captured: list[Any]) -> Any:
    """Returns an ``async_sessionmaker``-shaped callable used by the worker."""

    @asynccontextmanager
    async def _ctx() -> AsyncIterator[_FakeSession]:
        s = _FakeSession(captured)
        yield s
        captured.append(("session_closed", s.commits))

    def _factory() -> Any:
        return _ctx()

    return _factory


def _make_article(
    *,
    source: str = "cryptopanic",
    url: str = "https://example.com/x",
    title: str = "Bitcoin rallies",
    published_at: datetime | None = None,
) -> NewsArticle:
    return NewsArticle(
        source=source,
        url=url,
        title=title,
        body=None,
        published_at=published_at or datetime(2026, 5, 6, 12, tzinfo=timezone.utc),
        category=None,
        affected_assets=("BTC",),
    )


@pytest.fixture(autouse=True)
def _reset_cursors() -> None:
    iw._last_fetch_ts.clear()
    yield
    iw._last_fetch_ts.clear()


@pytest.fixture(autouse=True)
def _stub_universe_tickers(monkeypatch: pytest.MonkeyPatch) -> None:
    """2026-08-20: _run_one_iteration now always fetches the current
    universe's ticker set (on its own session, before persist) to pass
    into persist_news_items as dynamic_tickers. Default every test in
    this file to the pre-2026-08-20 behavior (empty set) so none of them
    need updating for a concern they aren't testing -- _FakeSession
    below only implements .commit(), not .execute()/.rollback(), so an
    unpatched real load_universe_tickers would blow up against it.
    Tests that specifically exercise the ticker-fetch wiring override
    this via monkeypatch.setattr in their own body."""
    async def _empty(*_a: object, **_k: object) -> frozenset[str]:
        return frozenset()
    monkeypatch.setattr(
        "app.news.persistence.load_universe_tickers", _empty,
    )


# ---------------------------------------------------------------------------
# _run_one_iteration — the unit we lean on most
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_iteration_persists_articles_and_updates_cursors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    art1 = _make_article(
        url="https://example.com/c1",
        published_at=datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc),
    )
    art2 = _make_article(
        url="https://example.com/c2",
        published_at=datetime(2026, 5, 6, 12, 5, tzinfo=timezone.utc),
    )
    art_macro = _make_article(
        source="yahoo_rss",
        url="https://example.com/m1",
        published_at=datetime(2026, 5, 6, 12, 30, tzinfo=timezone.utc),
    )
    crypto = _FakeAdapter("cryptopanic", scripted=[[art1, art2]])
    yahoo = _FakeAdapter("yahoo_rss", scripted=[[art_macro]])

    persisted: list[tuple[list[NewsArticle], list[SentimentResult]]] = []

    async def _fake_persist(
        session: Any,
        articles: list[NewsArticle],
        sentiments: list[SentimentResult],
        **_kw: Any,
    ) -> int:
        persisted.append((list(articles), list(sentiments)))
        return len(articles)

    monkeypatch.setattr(
        "app.news.persistence.persist_news_items", _fake_persist,
    )

    classify_calls: list[list[str]] = []

    def _fake_classify(titles: list[str]) -> list[SentimentResult]:
        classify_calls.append(list(titles))
        return [
            SentimentResult(score=0.5, label="positive", confidence=0.9)
            for _ in titles
        ]

    monkeypatch.setattr("app.news.sentiment.classify_batch", _fake_classify)

    captured: list[Any] = []
    factory = _session_factory_factory(captured)

    # Force the macro branch by setting last_macro_run far in the past.
    last_macro_run = datetime(2026, 5, 6, 0, 0, tzinfo=timezone.utc)
    await iw._run_one_iteration(factory, crypto, yahoo, last_macro_run)

    # Both adapters were polled; persistence saw all 3 articles.
    assert len(persisted) == 1
    persisted_articles, persisted_sents = persisted[0]
    assert len(persisted_articles) == 3
    assert len(persisted_sents) == 3
    # Classification ran on every title in one batch.
    assert len(classify_calls) == 1
    assert len(classify_calls[0]) == 3
    # Cursors updated to the *latest* published_at per source.
    assert iw._last_fetch_ts["cryptopanic"] == datetime(
        2026, 5, 6, 12, 5, tzinfo=timezone.utc,
    )
    assert iw._last_fetch_ts["yahoo_rss"] == datetime(
        2026, 5, 6, 12, 30, tzinfo=timezone.utc,
    )
    # Session was committed inside the worker (separate from persist's commit).
    assert ("session_closed", 1) in captured


@pytest.mark.asyncio
async def test_one_iteration_no_articles_skips_persist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crypto = _FakeAdapter("cryptopanic", scripted=[[]])
    yahoo = _FakeAdapter("yahoo_rss", scripted=[[]])

    persist_calls = 0

    async def _fake_persist(*_a: Any, **_k: Any) -> int:
        nonlocal persist_calls
        persist_calls += 1
        return 0

    classify_calls = 0

    def _fake_classify(titles: list[str]) -> list[SentimentResult]:
        nonlocal classify_calls
        classify_calls += 1
        return []

    monkeypatch.setattr(
        "app.news.persistence.persist_news_items", _fake_persist,
    )
    monkeypatch.setattr("app.news.sentiment.classify_batch", _fake_classify)

    factory = _session_factory_factory([])

    last_macro_run = datetime(2026, 5, 6, 0, 0, tzinfo=timezone.utc)
    await iw._run_one_iteration(factory, crypto, yahoo, last_macro_run)

    assert persist_calls == 0
    assert classify_calls == 0
    # Cursor stays empty because no article gave us a max(published_at).
    assert "cryptopanic" not in iw._last_fetch_ts
    assert "yahoo_rss" not in iw._last_fetch_ts


@pytest.mark.asyncio
async def test_one_iteration_skips_macro_until_30min_elapsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Within 30 min of the last macro run, the yahoo adapter is NOT called."""
    crypto = _FakeAdapter("cryptopanic", scripted=[[]])
    yahoo = _FakeAdapter("yahoo_rss", scripted=[[]])

    monkeypatch.setattr(
        "app.news.persistence.persist_news_items",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not run")),
    )

    factory = _session_factory_factory([])
    # last_macro_run is 5 minutes ago → 25 min until next macro run.
    last_macro_run = datetime.now(timezone.utc) - timedelta(minutes=5)

    await iw._run_one_iteration(factory, crypto, yahoo, last_macro_run)

    assert len(crypto.calls) == 1
    assert len(yahoo.calls) == 0


@pytest.mark.asyncio
async def test_one_iteration_uses_backfill_window_on_first_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No prior cursor → cutoff = now - BACKFILL_HOURS."""
    crypto = _FakeAdapter("cryptopanic", scripted=[[]])
    yahoo = _FakeAdapter("yahoo_rss", scripted=[[]])

    factory = _session_factory_factory([])
    last_macro_run = datetime(2026, 5, 6, 0, 0, tzinfo=timezone.utc)

    before = datetime.now(timezone.utc)
    await iw._run_one_iteration(factory, crypto, yahoo, last_macro_run)
    after = datetime.now(timezone.utc)

    assert len(crypto.calls) == 1
    cutoff = crypto.calls[0]
    expected_min = before - timedelta(hours=iw.BACKFILL_HOURS)
    expected_max = after - timedelta(hours=iw.BACKFILL_HOURS)
    assert expected_min <= cutoff <= expected_max


@pytest.mark.asyncio
async def test_one_iteration_threads_dynamic_tickers_into_persist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2026-08-20: whatever load_universe_tickers returns must reach
    persist_news_items's dynamic_tickers kwarg unmodified, on its own
    session opened before the persist session."""
    crypto = _FakeAdapter("cryptopanic", scripted=[[_make_article()]])
    yahoo = _FakeAdapter("yahoo_rss", scripted=[[]])

    async def _fake_tickers(*_a: Any, **_k: Any) -> frozenset[str]:
        return frozenset({"ONDO", "KAITO"})

    monkeypatch.setattr(
        "app.news.persistence.load_universe_tickers", _fake_tickers,
    )

    received_kwargs: list[dict[str, Any]] = []

    async def _fake_persist(
        _session: Any, _articles: list[NewsArticle],
        _sentiments: list[SentimentResult], **kw: Any,
    ) -> int:
        received_kwargs.append(kw)
        return 1

    monkeypatch.setattr(
        "app.news.persistence.persist_news_items", _fake_persist,
    )
    monkeypatch.setattr(
        "app.news.sentiment.classify_batch",
        lambda titles: [
            SentimentResult(score=0.0, label="neutral", confidence=0.5)
            for _ in titles
        ],
    )

    factory = _session_factory_factory([])
    last_macro_run = datetime(2026, 5, 6, 0, 0, tzinfo=timezone.utc)
    await iw._run_one_iteration(factory, crypto, yahoo, last_macro_run)

    assert len(received_kwargs) == 1
    assert received_kwargs[0]["dynamic_tickers"] == frozenset({"ONDO", "KAITO"})


# ---------------------------------------------------------------------------
# run_news_ingest_loop — outer driver
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_runs_iteration_then_sleeps_then_cancels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    iter_calls = 0

    async def _fake_iter(*_a: Any, **_k: Any) -> None:
        nonlocal iter_calls
        iter_calls += 1

    monkeypatch.setattr(iw, "_run_one_iteration", _fake_iter)

    sleep_intervals: list[float] = []

    async def _sleep(s: float) -> None:
        sleep_intervals.append(s)
        if len(sleep_intervals) >= 2:
            raise asyncio.CancelledError()

    factory = _session_factory_factory([])
    monkeypatch.setattr(
        iw, "_build_adapters",
        lambda *_a, **_k: (_FakeAdapter("c"), _FakeAdapter("y")),
    )

    with pytest.raises(asyncio.CancelledError):
        await iw.run_news_ingest_loop(factory, sleep_fn=_sleep)

    # 2 sleep calls means 2 iterations completed, all with CRYPTO_INTERVAL.
    assert iter_calls == 2
    assert sleep_intervals == [iw.CRYPTO_INTERVAL_S, iw.CRYPTO_INTERVAL_S]


@pytest.mark.asyncio
async def test_loop_swallows_iteration_exception_and_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exception inside _run_one_iteration must not break the loop."""
    iter_count = 0

    async def _flaky_iter(*_a: Any, **_k: Any) -> None:
        nonlocal iter_count
        iter_count += 1
        if iter_count == 1:
            raise RuntimeError("adapter blew up")

    monkeypatch.setattr(iw, "_run_one_iteration", _flaky_iter)

    sleep_calls = 0

    async def _sleep(s: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= 3:
            raise asyncio.CancelledError()

    factory = _session_factory_factory([])
    monkeypatch.setattr(
        iw, "_build_adapters",
        lambda *_a, **_k: (_FakeAdapter("c"), _FakeAdapter("y")),
    )

    with pytest.raises(asyncio.CancelledError):
        await iw.run_news_ingest_loop(factory, sleep_fn=_sleep)

    # 3 sleeps → 3 iterations, even though the first raised.
    assert iter_count == 3


@pytest.mark.asyncio
async def test_loop_propagates_cancellation_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CancelledError raised inside _run_one_iteration must propagate."""

    async def _cancelling_iter(*_a: Any, **_k: Any) -> None:
        raise asyncio.CancelledError()

    monkeypatch.setattr(iw, "_run_one_iteration", _cancelling_iter)

    sleep_calls = 0

    async def _sleep(s: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1

    factory = _session_factory_factory([])
    monkeypatch.setattr(
        iw, "_build_adapters",
        lambda *_a, **_k: (_FakeAdapter("c"), _FakeAdapter("y")),
    )

    with pytest.raises(asyncio.CancelledError):
        await iw.run_news_ingest_loop(factory, sleep_fn=_sleep)

    # Cancellation propagated before any sleep call.
    assert sleep_calls == 0


# ---------------------------------------------------------------------------
# Optional CryptoPanic — graceful degradation when api_key is empty
# ---------------------------------------------------------------------------


def test_build_adapters_returns_none_for_crypto_when_no_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty CRYPTOPANIC_API_KEY should not crash the worker.

    Production was crashing the entire news_ingest task at startup because
    the CryptoPanicAdapter constructor raised ValueError on the missing
    key. With the fix, _build_adapters logs a warning and returns
    (None, YahooRssAdapter) — the worker keeps running with macro news.
    """
    from app.config import Settings

    monkeypatch.setattr(
        "app.news.ingest_worker.get_settings",
        lambda: Settings(
            database_url="postgresql+asyncpg://x:x@h/d",
            redis_url="redis://h:0/0",
            cryptopanic_api_key="",
        ),
    )
    crypto, yahoo = iw._build_adapters()
    assert crypto is None
    assert yahoo is not None
    assert yahoo.name == "yahoo_rss"


def test_build_adapters_constructs_crypto_when_api_key_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import Settings

    monkeypatch.setattr(
        "app.news.ingest_worker.get_settings",
        lambda: Settings(
            database_url="postgresql+asyncpg://x:x@h/d",
            redis_url="redis://h:0/0",
            cryptopanic_api_key="real-key",
        ),
    )
    crypto, yahoo = iw._build_adapters()
    assert crypto is not None
    assert crypto.name == "cryptopanic"
    assert yahoo.name == "yahoo_rss"


@pytest.mark.asyncio
async def test_one_iteration_handles_none_crypto_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When crypto_adapter is None, only yahoo is polled and persisted."""
    yahoo = _FakeAdapter("yahoo_rss", scripted=[[_make_article(
        source="yahoo_rss", url="https://y.example/1",
        published_at=datetime(2026, 5, 6, 12, tzinfo=timezone.utc),
    )]])

    captured: list[Any] = []
    persisted: list[list[NewsArticle]] = []

    async def _fake_persist(_session: Any, articles: list[NewsArticle],
                            _sentiments: list[SentimentResult],
                            **_kw: Any) -> int:
        persisted.append(list(articles))
        return len(articles)

    def _fake_classify(titles: list[str]) -> list[SentimentResult]:
        return [
            SentimentResult(score=0.0, label="neutral", confidence=0.5)
            for _ in titles
        ]

    monkeypatch.setattr(
        "app.news.persistence.persist_news_items", _fake_persist,
    )
    monkeypatch.setattr("app.news.sentiment.classify_batch", _fake_classify)

    factory = _session_factory_factory(captured)

    # last_macro_run far enough in the past that yahoo IS polled.
    last_macro_run = datetime(2026, 5, 6, 0, 0, tzinfo=timezone.utc)
    await iw._run_one_iteration(factory, None, yahoo, last_macro_run)

    # Yahoo was polled, persistence happened, only yahoo articles in the batch.
    assert len(yahoo.calls) == 1
    assert len(persisted) == 1
    assert all(a.source == "yahoo_rss" for a in persisted[0])
    # Crypto cursor stays empty since crypto was skipped entirely.
    assert "cryptopanic" not in iw._last_fetch_ts
