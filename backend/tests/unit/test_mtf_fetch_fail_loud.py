"""The MTF kline fetch fails OPEN. Make that observable, not silent.

``_fetch_one_tf`` returns ``None`` on any HTTP error, and
``compute_mtf_confluence`` turns ``None`` into a ``(0, 0.0)`` vote. So a
symbol whose fetch always fails scores as though no higher timeframe agreed
with it -- which is **indistinguishable from genuine disagreement** in every
downstream consumer, including the dispatch gate's veto path.

MEASURED ON PROD 2026-10-26, 1h predictions, per cohort:

    established_top20     mtf_agreement NULL in 26.6% of 89,546 rows
    liquidity_added_spot  mtf_agreement NULL in 25.0% of 50,189 rows
    futures_poll          mtf_agreement NULL in 88.8% of 16,791 rows

and the futures_poll rate is flat across its whole existence (88.5% in
2026-09 from its first prediction on 2026-09-03, 89.1% in 2026-10), so this
is not drift -- it is the cohort's entire history.

NO BEHAVIOUR CHANGE HERE. The return value and the fail-open contract are
untouched; only the severity and a counter are added. Fail-open-silently is
the defect independently of what the correct value turns out to be, which is
why the observability ships before the routing fix is measured.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest

from app.core.scoring import mtf_confluence as mc


class _FailingClient:
    """An http client whose every GET raises, like a 400 for a missing pair."""

    def __init__(self, exc: Exception | None = None) -> None:
        self.exc = exc or httpx.ConnectError("boom")
        self.calls = 0

    async def get(self, *_: Any, **__: Any) -> Any:
        self.calls += 1
        raise self.exc


@pytest.fixture(autouse=True)
def _clean_counter() -> Any:
    mc._reset_fetch_failures_for_tests()
    yield
    mc._reset_fetch_failures_for_tests()


@pytest.mark.asyncio
async def test_a_failed_fetch_is_counted_per_symbol() -> None:
    """A counter, because a log line alone has no retrieval path.

    This repo has shipped log-only instrumentation several times and then
    had no way to answer the question it was added for -- and a container's
    log buffer is truncated on every redeploy, so the history is gone
    exactly when it is wanted.
    """
    http = _FailingClient()
    await mc._fetch_one_tf(http, "PERPONLYUSDT", "1h",
                           cache_get=lambda *_: None, cache_set=lambda *_: None)
    assert mc.get_fetch_failure_counts() == {"PERPONLYUSDT": 1}


@pytest.mark.asyncio
async def test_repeated_failures_accumulate_so_permanent_differs_from_transient(
) -> None:
    """One failure is a hiccup; a count that tracks the cycles is a dead path.

    The distinction is the entire operational point: a futures-only symbol
    on a spot endpoint can never succeed, so its count rises forever, while
    a real network blip leaves a small constant.
    """
    http = _FailingClient()
    for tf in ("5m", "15m", "1h", "4h"):
        await mc._fetch_one_tf(http, "PERPONLYUSDT", tf,
                               cache_get=lambda *_: None, cache_set=lambda *_: None)
    assert mc.get_fetch_failure_counts()["PERPONLYUSDT"] == 4


@pytest.mark.asyncio
async def test_the_log_is_ERROR_and_names_the_fail_open_consequence(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Severity AND content, because a WARNING nobody reads is still silent.

    The message has to say that the value is now a zero-vote, otherwise a
    reader sees a fetch error and assumes a retry covers it.
    """
    http = _FailingClient()
    with caplog.at_level(logging.DEBUG, logger=mc.log.name):
        await mc._fetch_one_tf(http, "PERPONLYUSDT", "1h",
                               cache_get=lambda *_: None, cache_set=lambda *_: None)
    recs = [r for r in caplog.records if "mtf_kline_fetch_failed" in r.getMessage()]
    assert recs, (
        "no record carried the greppable marker 'mtf_kline_fetch_failed'; "
        f"saw {[r.getMessage()[:60] for r in caplog.records]}"
    )
    assert recs[0].levelno == logging.ERROR, (
        f"logged at {recs[0].levelname}, expected ERROR. This path silently "
        "degrades a scoring input for the whole cohort, which is not a warning."
    )
    msg = recs[0].getMessage()
    assert "FAIL-OPEN" in msg and "zero-agreement" in msg, (
        f"message does not state the consequence: {msg!r}"
    )


@pytest.mark.asyncio
async def test_a_cache_hit_neither_counts_nor_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The degenerate case with a known answer.

    If a cache hit counted, the counter would measure traffic rather than
    failure and would rise on a perfectly healthy symbol -- a metric that
    cannot distinguish the thing it is named after.
    """
    http = _FailingClient()
    with caplog.at_level(logging.DEBUG, logger=mc.log.name):
        got = await mc._fetch_one_tf(
            http, "BTCUSDT", "1h",
            cache_get=lambda *_: [[1, "1", "1", "1", "1", "1"]],
            cache_set=lambda *_: None,
        )
    assert got is not None
    assert http.calls == 0, "a cache hit must make no request"
    assert mc.get_fetch_failure_counts() == {}
    assert not [r for r in caplog.records
                if "mtf_kline_fetch_failed" in r.getMessage()]


@pytest.mark.asyncio
async def test_a_successful_fetch_neither_counts_nor_logs() -> None:
    """The other direction: the counter must be able to stay at zero."""
    class _OK:
        calls = 0

        async def get(self, *_: Any, **__: Any) -> Any:
            class _R:
                @staticmethod
                def raise_for_status() -> None:
                    return None

                @staticmethod
                def json() -> list[list[Any]]:
                    return [[1, "1", "1", "1", "1", "1"]]
            return _R()

    stored: dict[str, Any] = {}
    got = await mc._fetch_one_tf(
        _OK(), "BTCUSDT", "1h",
        cache_get=lambda *_: None,
        cache_set=lambda s, t, k: stored.update({f"{s}:{t}": k}),
    )
    assert got is not None
    assert stored, "a successful fetch must populate the cache"
    assert mc.get_fetch_failure_counts() == {}


@pytest.mark.asyncio
async def test_the_return_value_is_unchanged_so_behaviour_is_unchanged() -> None:
    """None on failure, exactly as before -- this change is observability only.

    If this returned anything else, the caller's (0, 0.0) vote would change
    and an 'observability' commit would have moved live scoring.
    """
    got = await mc._fetch_one_tf(
        _FailingClient(), "PERPONLYUSDT", "1h",
        cache_get=lambda *_: None, cache_set=lambda *_: None,
    )
    assert got is None
