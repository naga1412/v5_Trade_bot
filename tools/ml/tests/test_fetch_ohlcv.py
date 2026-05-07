"""Smoke tests for tools/ml/fetch_ohlcv.py.

Covers what's mockable without a network round-trip: pagination cursor
math, Binance kline-row decoding, sort+dedup, output Parquet shape.

Network-dependent end-to-end fetch is intentionally NOT tested here —
that's a manual smoke (``python -m tools.ml.fetch_ohlcv ...``) the
operator runs once before training.
"""
from __future__ import annotations

import io
import zipfile
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


# ---- Archive (data.binance.vision) tests ---------------------------------


def _build_fake_archive_zip(rows: list[list]) -> bytes:
    """Create an in-memory ZIP holding a 12-col CSV that matches Binance's
    monthly archive format. Returns the raw ZIP bytes."""
    csv_lines = []
    for r in rows:
        csv_lines.append(",".join(str(x) for x in r))
    csv_body = "\n".join(csv_lines).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("BTCUSDT-1h-2024-04.csv", csv_body)
    return buf.getvalue()


def test_month_iter_inclusive_of_endpoints() -> None:
    months = F._month_iter(
        datetime(2024, 1, 15, tzinfo=timezone.utc),
        datetime(2024, 4, 5, tzinfo=timezone.utc),
    )
    assert months == [(2024, 1), (2024, 2), (2024, 3), (2024, 4)]


def test_month_iter_handles_year_boundary() -> None:
    months = F._month_iter(
        datetime(2023, 11, 1, tzinfo=timezone.utc),
        datetime(2024, 2, 1, tzinfo=timezone.utc),
    )
    assert months == [(2023, 11), (2023, 12), (2024, 1), (2024, 2)]


def test_fetch_archive_month_returns_dataframe_on_200() -> None:
    rows = [_kline_row(1714000000000 + i * 3_600_000) for i in range(5)]
    zip_bytes = _build_fake_archive_zip(rows)
    sess = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.content = zip_bytes
    resp.raise_for_status.return_value = None
    sess.get.return_value = resp

    df = F.fetch_archive_month(
        symbol="BTCUSDT", interval="1h", year=2024, month=4, session=sess,
    )
    assert df is not None
    assert len(df) == 5
    assert list(df.columns)[:6] == [
        "open_ms", "open", "high", "low", "close", "volume",
    ]
    assert df["open"].iloc[0] == 100.0


def test_fetch_archive_month_returns_none_on_404() -> None:
    """Months that don't exist (pre-listing or current month) return None."""
    sess = MagicMock()
    resp = MagicMock()
    resp.status_code = 404
    sess.get.return_value = resp

    df = F.fetch_archive_month(
        symbol="BTCUSDT", interval="1h", year=2015, month=1, session=sess,
    )
    assert df is None


def test_fetch_archive_month_url_pattern() -> None:
    sess = MagicMock()
    resp = MagicMock()
    resp.status_code = 404
    sess.get.return_value = resp

    F.fetch_archive_month(
        symbol="BTCUSDT", interval="1h", year=2024, month=4, session=sess,
    )
    url, = sess.get.call_args[0]
    assert "data.binance.vision" in url
    assert "/spot/monthly/klines/BTCUSDT/1h/" in url
    assert url.endswith("BTCUSDT-1h-2024-04.zip")


def test_fetch_all_archive_concatenates_months() -> None:
    """Two months of fake data → 10-row dataframe sorted + indexed."""
    feb_rows = [_kline_row(1706745600000 + i * 3_600_000) for i in range(5)]
    mar_rows = [_kline_row(1709251200000 + i * 3_600_000) for i in range(5)]
    feb_zip = _build_fake_archive_zip(feb_rows)
    mar_zip = _build_fake_archive_zip(mar_rows)

    def fake_get(url, timeout=None):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        if "2024-02" in url:
            resp.status_code = 200
            resp.content = feb_zip
        elif "2024-03" in url:
            resp.status_code = 200
            resp.content = mar_zip
        else:
            resp.status_code = 404
        return resp

    with patch("requests.Session") as mock_sess_cls:
        sess = MagicMock()
        sess.get.side_effect = fake_get
        sess.headers = {}
        mock_sess_cls.return_value = sess

        df = F.fetch_all_archive(
            symbol="BTCUSDT", interval="1h",
            start=datetime(2024, 2, 1, tzinfo=timezone.utc),
            end=datetime(2024, 3, 31, tzinfo=timezone.utc),
        )
    assert len(df) == 10
    assert df.index.is_monotonic_increasing
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]


def test_fetch_all_archive_raises_on_no_data() -> None:
    """All months 404 → no frames → RuntimeError."""
    with patch("requests.Session") as mock_sess_cls:
        sess = MagicMock()
        resp = MagicMock()
        resp.status_code = 404
        sess.get.return_value = resp
        sess.headers = {}
        mock_sess_cls.return_value = sess

        with pytest.raises(RuntimeError, match="no archive data"):
            F.fetch_all_archive(
                symbol="DOESNOTEXIST", interval="1h",
                start=datetime(2024, 2, 1, tzinfo=timezone.utc),
                end=datetime(2024, 2, 28, tzinfo=timezone.utc),
            )
