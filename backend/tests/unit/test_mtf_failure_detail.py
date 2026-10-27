"""The (symbol, timeframe, failure) counter — the actual discriminator.

#610's counter accepted `tf` and discarded it, so classifying ARBUSDT as
permanent-vs-transient took two manual reads of consecutive hourly
cycles, and "is it a timeout?" stayed unanswerable. The shape these keys
expose:

  routing failure -> every timeframe fails, one status code, persistently
  timeout         -> concentrates on the longest fetches, or scatters
"""
from __future__ import annotations

import datetime as _dt

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.scoring import mtf_confluence as mtf
from app.ops import check_events as ce


T0 = _dt.datetime(2026, 10, 26, 12, 0, 0, tzinfo=_dt.timezone.utc)


def _http_status_exc(code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://api.binance.com/api/v3/klines")
    resp = httpx.Response(code, request=req)
    return httpx.HTTPStatusError("boom", request=req, response=resp)


@pytest.fixture(autouse=True)
def _reset():
    mtf._reset_fetch_failures_for_tests()
    ce._reset_for_tests()
    yield
    mtf._reset_fetch_failures_for_tests()
    ce._reset_for_tests()


# ---------------------------------------------------------------------------
# _failure_kind -- the label IS the discriminator
# ---------------------------------------------------------------------------


def test_the_http_STATUS_is_part_of_the_label() -> None:
    """Hand-derived. 400 and 404 mean "Binance does not have this symbol
    on this market" (routing); 429/418 mean the request never got a
    verdict (transport). Collapsing them to "HTTPStatusError" would throw
    the answer away a second time, which is the defect being fixed."""
    assert mtf._failure_kind(_http_status_exc(400)) == "HTTPStatusError_400"
    assert mtf._failure_kind(_http_status_exc(404)) == "HTTPStatusError_404"
    assert mtf._failure_kind(_http_status_exc(429)) == "HTTPStatusError_429"


def test_a_transport_exception_keeps_its_class_name() -> None:
    """A timeout has no status code -- its CLASS is the signal."""
    assert mtf._failure_kind(httpx.ReadTimeout("slow")) == "ReadTimeout"
    assert mtf._failure_kind(httpx.ConnectTimeout("slow")) == "ConnectTimeout"
    assert mtf._failure_kind(httpx.ConnectError("down")) == "ConnectError"


def test_a_missing_exception_is_labelled_not_guessed() -> None:
    """The two-argument call shape stays valid, and its label says so
    rather than silently attributing the failure to something."""
    assert mtf._failure_kind(None) == "unknown"


# ---------------------------------------------------------------------------
# the counter
# ---------------------------------------------------------------------------


def test_the_detail_counter_keys_on_symbol_timeframe_AND_failure() -> None:
    """Hand-derived: three records, two of them the same key, so the
    result is 2 keys with counts 2 and 1 -- not 3 keys and not 1."""
    mtf._record_fetch_failure("ARBUSDT", "1w", _http_status_exc(400))
    mtf._record_fetch_failure("ARBUSDT", "1w", _http_status_exc(400))
    mtf._record_fetch_failure("ARBUSDT", "1d", httpx.ReadTimeout("slow"))

    detail = mtf.get_fetch_failure_detail()
    assert detail == {
        ("ARBUSDT", "1w", "HTTPStatusError_400"): 2,
        ("ARBUSDT", "1d", "ReadTimeout"): 1,
    }


def test_the_SYMBOL_level_counter_is_unchanged_by_the_new_key() -> None:
    """The 2026-10-26 permanent/transient classification was made from
    the symbol-level series, so re-keying it would have broken that
    comparison. Hand-derived: 3 failures on one symbol -> 3, regardless
    of how they split across timeframes."""
    mtf._record_fetch_failure("BNBUSDT", "1w", _http_status_exc(400))
    mtf._record_fetch_failure("BNBUSDT", "1d", httpx.ReadTimeout("x"))
    mtf._record_fetch_failure("BNBUSDT", "4h", httpx.ReadTimeout("x"))
    assert mtf.get_fetch_failure_counts() == {"BNBUSDT": 3}


def test_the_two_series_disagree_in_the_way_that_carries_the_ANSWER() -> None:
    """The point of the whole change, as one assertion.

    Two symbols with IDENTICAL symbol-level counts (6 each) and
    completely different diagnoses. The old counter could not tell them
    apart at all; the new one does it in a single read, with no second
    cycle required.
    """
    for tf in ("5m", "15m", "1h", "4h", "1d", "1w"):
        mtf._record_fetch_failure("ROUTED", tf, _http_status_exc(400))
    for tf in ("4h", "1d", "1w"):
        mtf._record_fetch_failure("SLOW", tf, httpx.ReadTimeout("x"))
        mtf._record_fetch_failure("SLOW", tf, httpx.ReadTimeout("x"))

    counts = mtf.get_fetch_failure_counts()
    assert counts["ROUTED"] == counts["SLOW"] == 6, counts

    detail = mtf.get_fetch_failure_detail()
    routed = {k[1]: v for k, v in detail.items() if k[0] == "ROUTED"}
    slow = {k[1]: v for k, v in detail.items() if k[0] == "SLOW"}
    # ROUTED: all six timeframes, one status code -> a routing failure.
    assert set(routed) == {"5m", "15m", "1h", "4h", "1d", "1w"}
    assert {k[2] for k in detail if k[0] == "ROUTED"} == {"HTTPStatusError_400"}
    # SLOW: only the three longest fetches, a transport exception -> timeout.
    assert set(slow) == {"4h", "1d", "1w"}
    assert {k[2] for k in detail if k[0] == "SLOW"} == {"ReadTimeout"}


def test_the_reset_clears_BOTH_series() -> None:
    """A reset that cleared only one would leak state across tests and
    make the next test's hand-derived counts wrong."""
    mtf._record_fetch_failure("X", "1h", _http_status_exc(400))
    assert mtf.get_fetch_failure_counts() and mtf.get_fetch_failure_detail()
    mtf._reset_fetch_failures_for_tests()
    assert mtf.get_fetch_failure_counts() == {}
    assert mtf.get_fetch_failure_detail() == {}


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------

_MIG = (
    "CREATE TABLE check_events ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, check_name TEXT NOT NULL, "
    "check_kind TEXT NOT NULL, status TEXT NOT NULL, "
    "granularity TEXT NOT NULL, bucket_date TEXT, "
    "subject TEXT NOT NULL DEFAULT '', observed_count INTEGER NOT NULL DEFAULT 1, "
    "detail TEXT, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL)"
)


@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(_MIG))
        await conn.execute(sa.text(
            "CREATE UNIQUE INDEX ce_uq ON check_events "
            "(check_name, subject, bucket_date)"
        ))
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_flusher_persists_the_detail_series_under_its_OWN_name(
    factory,
) -> None:
    """Separate check_name, so the symbol-level series keeps continuity.

    Hand-derived subject format: "<SYMBOL>/<tf>/<failure>".
    """
    async with factory() as s:
        await ce.flush_mtf_fetch_failures(
            s, now=T0,
            counts={"ARBUSDT": 2},
            detail={
                ("ARBUSDT", "1w", "HTTPStatusError_400"): 2,
                ("ARBUSDT", "1d", "ReadTimeout"): 1,
            },
        )
        await s.commit()
        rows = (await s.execute(sa.text(
            "SELECT check_name, subject, observed_count FROM check_events "
            "ORDER BY check_name, subject"
        ))).all()

    got = {(r.check_name, r.subject): r.observed_count for r in rows}
    assert ("mtf_kline_fetch_failed", "ARBUSDT") in got, got
    assert got[("mtf_kline_fetch_failed_detail",
                "ARBUSDT/1w/HTTPStatusError_400")] == 2, got
    assert got[("mtf_kline_fetch_failed_detail",
                "ARBUSDT/1d/ReadTimeout")] == 1, got


@pytest.mark.asyncio
async def test_the_detail_flush_writes_DELTAS_too(factory) -> None:
    """Same cumulative-counter contract as the symbol series.

    Hand-derived: flush 1 sees 2, flush 2 sees 5. Stored total is 5, NOT
    2 + 5 = 7. Writing the absolute value each time inflates by the
    number of flushes.
    """
    key = ("ARBUSDT", "1w", "HTTPStatusError_400")
    async with factory() as s:
        await ce.flush_mtf_fetch_failures(
            s, now=T0, counts={}, detail={key: 2})
        await ce.flush_mtf_fetch_failures(
            s, now=T0 + _dt.timedelta(minutes=5), counts={}, detail={key: 5})
        await s.commit()
        total = (await s.execute(sa.text(
            "SELECT observed_count FROM check_events "
            "WHERE check_name = 'mtf_kline_fetch_failed_detail'"
        ))).scalar_one()
    assert total == 5


@pytest.mark.asyncio
async def test_an_unchanged_detail_counter_WRITES_NOTHING(factory) -> None:
    """Idempotence, asserted on the WRITE rather than the count.

    A mutant SURVIVED the first version of this test, which asserted
    only `observed_count == 4`. Removing the `delta <= 0` guard does not
    change the count: the unchanged key falls through to
    `record_daily(increment=0)`, and `observed_count + 0` is identical.
    The count was blind to the defect.

    The real consequence of dropping the guard is a WRITE for every
    unchanged key on every flush -- write amplification, which at
    symbols x 6 timeframes x failure kinds every 5 minutes is exactly
    the per-event cost the daily aggregation exists to avoid. The
    observable signature is `last_seen_at` advancing, so that is what is
    asserted.
    """
    key = ("X", "1h", "ReadTimeout")
    t1 = T0 + _dt.timedelta(minutes=5)
    async with factory() as s:
        await ce.flush_mtf_fetch_failures(s, now=T0, counts={}, detail={key: 4})
        await ce.flush_mtf_fetch_failures(s, now=t1, counts={}, detail={key: 4})
        await s.commit()
        rows = (await s.execute(sa.text(
            "SELECT observed_count, first_seen_at, last_seen_at "
            "FROM check_events "
            "WHERE check_name = 'mtf_kline_fetch_failed_detail'"
        ))).all()

    assert len(rows) == 1, rows
    assert rows[0].observed_count == 4
    assert rows[0].last_seen_at == T0.isoformat(sep=" "), (
        "last_seen_at ADVANCED, so the second flush wrote a row for an "
        "unchanged counter -- the delta guard is not holding, and the "
        "count alone cannot see that because increment=0 is a no-op"
    )
    assert rows[0].first_seen_at == T0.isoformat(sep=" ")
