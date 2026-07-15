"""Tests for direction-aware promotion gate filtering (PR C).

Contracts:
  - compute_gates_from_db adds AND direction = :direction_filter to SQL when
    direction_filter is set; omits the clause when direction_filter is None.
  - Blended fixture (LONG winners + SHORT losers) → with direction_filter='LONG',
    metrics equal LONG-only stats; without filter, metrics equal blended stats.
  - auto_promote.evaluate_user passes direction_filter='LONG' when
    DISABLE_SHORT_SIGNALS=True, and direction_filter=None when False.
"""
from __future__ import annotations

from unittest import mock

import pytest


def _row(pnl_pct: float):
    r = mock.MagicMock()
    r.pnl_pct = pnl_pct
    return r


def _session_returning(rows: list) -> mock.AsyncMock:
    result = mock.MagicMock()
    result.all.return_value = rows
    session = mock.AsyncMock()
    session.execute = mock.AsyncMock(return_value=result)
    return session


def _direction_aware_session(
    long_rows: list, blended_rows: list,
) -> mock.AsyncMock:
    """Returns LONG rows when direction_filter param is present; blended otherwise."""
    session = mock.AsyncMock()

    async def _execute(sql_text, params):
        result = mock.MagicMock()
        if "direction_filter" in params:
            result.all.return_value = long_rows
        else:
            result.all.return_value = blended_rows
        return result

    session.execute = mock.AsyncMock(side_effect=_execute)
    return session


# ---------------------------------------------------------------------------
# SQL predicate tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_direction_filter_wired_into_sql_and_params() -> None:
    """When direction_filter='LONG', SQL must contain a direction predicate
    and the params dict must carry direction_filter='LONG'."""
    from app.trading.promotion import compute_gates_from_db

    session = _session_returning([])
    await compute_gates_from_db(session, direction_filter="LONG")

    call_args = session.execute.call_args
    sql_text = str(call_args[0][0])
    params = call_args[0][1]

    assert "direction_filter" in params
    assert params["direction_filter"] == "LONG"
    assert "direction" in sql_text


@pytest.mark.asyncio
async def test_no_direction_predicate_when_filter_is_none() -> None:
    """When direction_filter=None, the direction predicate must NOT appear
    in either the SQL or the params dict."""
    from app.trading.promotion import compute_gates_from_db

    session = _session_returning([])
    await compute_gates_from_db(session, direction_filter=None)

    call_args = session.execute.call_args
    params = call_args[0][1]

    assert "direction_filter" not in params


# ---------------------------------------------------------------------------
# Metric correctness tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_long_only_metrics_when_direction_filter_long() -> None:
    """With direction_filter='LONG', gate metrics reflect LONG-only trades.

    Fixture: 4 LONG winners (pnl=+2%) + 4 SHORT losers (pnl=-1.5%).
    - LONG-only: win_rate=1.0, n_trades=4
    - Blended:   win_rate=0.5, n_trades=8
    """
    from app.trading.promotion import compute_gates_from_db

    long_winners = [_row(2.0) for _ in range(4)]
    short_losers = [_row(-1.5) for _ in range(4)]
    blended = long_winners + short_losers

    snap_long = await compute_gates_from_db(
        _session_returning(long_winners), direction_filter="LONG"
    )
    assert snap_long.win_rate == pytest.approx(1.0)
    assert snap_long.n_trades == 4

    snap_blended = await compute_gates_from_db(
        _session_returning(blended), direction_filter=None
    )
    assert snap_blended.win_rate == pytest.approx(0.5)
    assert snap_blended.n_trades == 8


@pytest.mark.asyncio
async def test_direction_aware_session_routes_rows_correctly() -> None:
    """When the mock session is direction-aware, the filtered path returns
    LONG metrics and the unfiltered path returns blended metrics."""
    from app.trading.promotion import compute_gates_from_db

    long_winners = [_row(3.0) for _ in range(10)]
    blended = long_winners + [_row(-2.0) for _ in range(10)]
    session = _direction_aware_session(long_rows=long_winners, blended_rows=blended)

    snap_filtered = await compute_gates_from_db(session, direction_filter="LONG")
    assert snap_filtered.n_trades == 10
    assert snap_filtered.win_rate == pytest.approx(1.0)

    snap_blended = await compute_gates_from_db(session, direction_filter=None)
    assert snap_blended.n_trades == 20
    assert snap_blended.win_rate == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# auto_promote.evaluate_user caller tests
# ---------------------------------------------------------------------------


def _make_settings(*, disable_shorts: bool):
    from app.config import Settings

    return Settings(  # type: ignore[call-arg]
        database_url="postgresql://x",
        redis_url="redis://x",
        DISABLE_SHORT_SIGNALS=disable_shorts,
    )


@pytest.mark.asyncio
async def test_evaluate_user_passes_long_filter_when_shorts_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """evaluate_user must pass direction_filter='LONG' when DISABLE_SHORT_SIGNALS=True."""
    from app.trading import auto_promote

    captured_filter: list = []

    async def fake_compute_gates(session, *, window_days, now, direction_filter=None):
        captured_filter.append(direction_filter)
        from app.trading.promotion import compute_stats
        return compute_stats([], span_days=window_days, now=now)

    monkeypatch.setattr(auto_promote, "compute_gates_from_db", fake_compute_gates)
    monkeypatch.setattr(
        auto_promote, "get_settings",
        lambda: _make_settings(disable_shorts=True),
    )
    monkeypatch.setattr(
        auto_promote, "get_mode", mock.AsyncMock(return_value="manual"),
    )

    cfg = auto_promote.AutoPromoteConfig(
        to_telegram_enabled=True,
        to_fullyauto_enabled=False,
        consecutive_days=1,
    )
    await auto_promote.evaluate_user(mock.AsyncMock(), user_id=1, cfg=cfg)

    assert len(captured_filter) >= 1
    assert captured_filter[0] == "LONG"


@pytest.mark.asyncio
async def test_evaluate_user_no_filter_when_shorts_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """evaluate_user must pass direction_filter=None when DISABLE_SHORT_SIGNALS=False."""
    from app.trading import auto_promote

    captured_filter: list = []

    async def fake_compute_gates(session, *, window_days, now, direction_filter=None):
        captured_filter.append(direction_filter)
        from app.trading.promotion import compute_stats
        return compute_stats([], span_days=window_days, now=now)

    monkeypatch.setattr(auto_promote, "compute_gates_from_db", fake_compute_gates)
    monkeypatch.setattr(
        auto_promote, "get_settings",
        lambda: _make_settings(disable_shorts=False),
    )
    monkeypatch.setattr(
        auto_promote, "get_mode", mock.AsyncMock(return_value="manual"),
    )

    cfg = auto_promote.AutoPromoteConfig(
        to_telegram_enabled=True,
        to_fullyauto_enabled=False,
        consecutive_days=1,
    )
    await auto_promote.evaluate_user(mock.AsyncMock(), user_id=1, cfg=cfg)

    assert len(captured_filter) >= 1
    assert captured_filter[0] is None
