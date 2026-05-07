"""Smoke tests for tools/ml/fetch_ohlcv.py.

Covers what's mockable without a network round-trip: pagination cursor
math, Binance kline-row decoding, sort+dedup, output Parquet shape.

Network-dependent end-to-end fetch is intentionally NOT tested here —
that's a manual smoke (``python -m tools.ml.fetch_ohlcv ...``) the
operator runs once before training.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from tools.ml import fetch_ohlcv as F


def _kline_row(open_ms: int, *, price: float = 100.0, vol: float = 1000.0) -> list:
    """Construct a Binance-format kline row (12 fields)."""
    return [
        open_ms, str(price), str(price * 1.001), str(price * 0.999),
        str(price * 1.0005), str(vol),
        open_ms + 3_599_999, str(price * vol), 50,
        str(vol / 2), str(price * vol / 2), "0",
    ]


def test_to_ms_naive_datetime_assumes_utc() -> None:
    naive = datetime(2024, 1, 1, 0, 0, 0)
    aware = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert F._to_ms(naive) == F._to_ms(aware)


def test_interval_ms_table_covers_all_advertised_intervals() -> None:
    for k in ("1m", "5m", "15m", "1h", "4h", "1d"):
        assert k in F.INTERVAL_MS
        assert F.INTERVAL_MS[k] > 0


def test_fetch_all_paginates_and_advances_cursor() -> None:
    """Two pages of fake data → 2 dataframe rows per page, cursor advances."""
    interval_ms = F.INTERVAL_MS["1h"]
    base_ms = F._to_ms(datetime(2024, 1, 1, tzinfo=timezone.utc))
    page1 = [_kline_row(base_ms + i * interval_ms) for i in range(2)]
    page2 = [_kline_row(base_ms + (i + 2) * interval_ms) for i in range(2)]
    page3: list = []  # empty → loop terminates

    with patch.object(F, "fetch_klines_page", side_effect=[page1, page2, page3]):
        df = F.fetch_all(
            symbol="BTCUSDT", interval="1h",
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 1, 5, tzinfo=timezone.utc),
        )

    assert len(df) == 4
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df.index.is_monotonic_increasing
    assert df.index.tz is not None


def test_fetch_all_dedups_overlapping_pages() -> None:
    """If two pages share a row, the duplicate is removed."""
    interval_ms = F.INTERVAL_MS["1h"]
    base_ms = F._to_ms(datetime(2024, 1, 1, tzinfo=timezone.utc))
    shared_ms = base_ms + interval_ms
    page1 = [_kline_row(base_ms), _kline_row(shared_ms)]
    page2 = [_kline_row(shared_ms), _kline_row(base_ms + 2 * interval_ms)]
    page3: list = []

    with patch.object(F, "fetch_klines_page", side_effect=[page1, page2, page3]):
        df = F.fetch_all(
            symbol="BTCUSDT", interval="1h",
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 1, 5, tzinfo=timezone.utc),
        )
    assert len(df) == 3, f"expected 3 unique bars after dedup, got {len(df)}"


def test_fetch_all_raises_when_no_rows() -> None:
    with patch.object(F, "fetch_klines_page", return_value=[]):
        with pytest.raises(RuntimeError, match="no bars returned"):
            F.fetch_all(
                symbol="BTCUSDT", interval="1h",
                start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                end=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
            )


def test_unsupported_interval_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported interval"):
        F.fetch_all(
            symbol="BTCUSDT", interval="2h",  # not in INTERVAL_MS
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        )


def test_fetch_klines_page_builds_correct_request() -> None:
    sess = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = []
    resp.raise_for_status.return_value = None
    sess.get.return_value = resp

    F.fetch_klines_page(
        symbol="BTCUSDT", interval="1h",
        start_ms=1_700_000_000_000, end_ms=1_700_003_600_000, session=sess,
    )

    sess.get.assert_called_once()
    call_args = sess.get.call_args
    assert call_args[0][0].endswith("/api/v3/klines")
    params = call_args[1]["params"]
    assert params["symbol"] == "BTCUSDT"
    assert params["interval"] == "1h"
    assert params["startTime"] == 1_700_000_000_000
    assert params["endTime"] == 1_700_003_600_000
    assert params["limit"] == F.MAX_BARS_PER_REQUEST
