"""Tests for timeframe-aware promotion gate filtering.

TIER 3 (defect sweep 2026-08-06): SHADOW_15M_ELIGIBLE_FOR_PROMOTION only
ever gated the read-only /promotion-gate dashboard display
(app/api/routes/bot_status.py) — the real auto-promotion engine
(compute_gates_from_db) had no timeframe parameter at all, so mode
upgrades (manual -> telegram-approve -> fully-auto) always included 15m
trades regardless of the flag. A real authorization-path gap, not just a
display inconsistency.

Contracts (mirrors tests/unit/test_promotion_direction_filter.py exactly):
  - compute_gates_from_db adds AND timeframe NOT IN (...) to SQL when
    exclude_timeframes is set; omits the clause when it's None/empty.
  - Blended fixture (1h + 15m trades) -> with exclude_timeframes=['15m'],
    metrics equal 1h-only stats; without it, metrics equal blended stats.
  - auto_promote.evaluate_user passes exclude_timeframes=['15m'] when
    SHADOW_15M_ELIGIBLE_FOR_PROMOTION=False, and None when True.
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


def _timeframe_aware_session(
    hourly_rows: list, blended_rows: list,
) -> mock.AsyncMock:
    """Returns 1h-only rows when an exclude_tf param is present; blended otherwise."""
    session = mock.AsyncMock()

    async def _execute(sql_text, params):
        result = mock.MagicMock()
        if any(k.startswith("excl_tf_") for k in params):
            result.all.return_value = hourly_rows
        else:
            result.all.return_value = blended_rows
        return result

    session.execute = mock.AsyncMock(side_effect=_execute)
    return session


# ---------------------------------------------------------------------------
# SQL predicate tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exclude_timeframes_wired_into_sql_and_params() -> None:
    """When exclude_timeframes=['15m'], SQL must contain a NOT IN predicate
    and the params dict must carry the excluded value."""
    from app.trading.promotion import compute_gates_from_db

    session = _session_returning([])
    await compute_gates_from_db(session, exclude_timeframes=["15m"])

    call_args = session.execute.call_args
    sql_text = str(call_args[0][0])
    params = call_args[0][1]

    assert any(v == "15m" for v in params.values())
    assert "timeframe" in sql_text
    assert "NOT IN" in sql_text


@pytest.mark.asyncio
async def test_no_timeframe_predicate_when_exclude_is_none() -> None:
    """When exclude_timeframes=None, the NOT IN predicate must NOT appear
    in either the SQL or the params dict."""
    from app.trading.promotion import compute_gates_from_db

    session = _session_returning([])
    await compute_gates_from_db(session, exclude_timeframes=None)

    call_args = session.execute.call_args
    sql_text = str(call_args[0][0])
    params = call_args[0][1]

    assert not any(k.startswith("excl_tf_") for k in params)
    assert "NOT IN" not in sql_text


@pytest.mark.asyncio
async def test_no_timeframe_predicate_when_exclude_is_empty_list() -> None:
    from app.trading.promotion import compute_gates_from_db

    session = _session_returning([])
    await compute_gates_from_db(session, exclude_timeframes=[])

    call_args = session.execute.call_args
    sql_text = str(call_args[0][0])
    assert "NOT IN" not in sql_text


# ---------------------------------------------------------------------------
# Metric correctness tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_1h_only_metrics_when_15m_excluded() -> None:
    """With exclude_timeframes=['15m'], gate metrics reflect 1h-only trades.

    Fixture: 4 1h winners (pnl=+2%) + 4 15m losers (pnl=-1.5%).
    - 1h-only:  win_rate=1.0, n_trades=4
    - Blended:  win_rate=0.5, n_trades=8
    """
    from app.trading.promotion import compute_gates_from_db

    hourly_winners = [_row(2.0) for _ in range(4)]
    fifteen_min_losers = [_row(-1.5) for _ in range(4)]
    blended = hourly_winners + fifteen_min_losers

    snap_1h = await compute_gates_from_db(
        _session_returning(hourly_winners), exclude_timeframes=["15m"],
    )
    assert snap_1h.win_rate == pytest.approx(1.0)
    assert snap_1h.n_trades == 4

    snap_blended = await compute_gates_from_db(
        _session_returning(blended), exclude_timeframes=None,
    )
    assert snap_blended.win_rate == pytest.approx(0.5)
    assert snap_blended.n_trades == 8


@pytest.mark.asyncio
async def test_timeframe_aware_session_routes_rows_correctly() -> None:
    from app.trading.promotion import compute_gates_from_db

    hourly_winners = [_row(3.0) for _ in range(10)]
    blended = hourly_winners + [_row(-2.0) for _ in range(10)]
    session = _timeframe_aware_session(hourly_rows=hourly_winners, blended_rows=blended)

    snap_filtered = await compute_gates_from_db(session, exclude_timeframes=["15m"])
    assert snap_filtered.n_trades == 10
    assert snap_filtered.win_rate == pytest.approx(1.0)

    snap_blended = await compute_gates_from_db(session, exclude_timeframes=None)
    assert snap_blended.n_trades == 20
    assert snap_blended.win_rate == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_direction_and_timeframe_filters_compose() -> None:
    """Both filters must be usable together — the real caller applies both
    direction_filter (from DISABLE_SHORT_SIGNALS) and exclude_timeframes
    (from SHADOW_15M_ELIGIBLE_FOR_PROMOTION) simultaneously."""
    from app.trading.promotion import compute_gates_from_db

    session = _session_returning([])
    await compute_gates_from_db(
        session, direction_filter="LONG", exclude_timeframes=["15m"],
    )

    call_args = session.execute.call_args
    sql_text = str(call_args[0][0])
    params = call_args[0][1]
    assert params.get("direction_filter") == "LONG"
    assert any(v == "15m" for v in params.values())
    assert "direction" in sql_text
    assert "NOT IN" in sql_text


# ---------------------------------------------------------------------------
# auto_promote.evaluate_user caller tests
# ---------------------------------------------------------------------------


def _make_settings(*, fifteen_m_eligible: bool):
    from app.config import Settings

    return Settings(  # type: ignore[call-arg]
        database_url="postgresql://x",
        redis_url="redis://x",
        SHADOW_15M_ELIGIBLE_FOR_PROMOTION=fifteen_m_eligible,
    )


@pytest.mark.asyncio
async def test_evaluate_user_excludes_15m_when_flag_is_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """evaluate_user must pass exclude_timeframes=['15m'] when
    SHADOW_15M_ELIGIBLE_FOR_PROMOTION=False."""
    from app.trading import auto_promote

    captured: list = []

    async def fake_compute_gates(
        session, *, window_days, now, direction_filter=None, exclude_timeframes=None,
    ):
        captured.append(exclude_timeframes)
        from app.trading.promotion import compute_stats
        return compute_stats([], span_days=window_days, now=now)

    monkeypatch.setattr(auto_promote, "compute_gates_from_db", fake_compute_gates)
    monkeypatch.setattr(
        auto_promote, "get_settings",
        lambda: _make_settings(fifteen_m_eligible=False),
    )
    monkeypatch.setattr(
        auto_promote, "get_mode", mock.AsyncMock(return_value="manual"),
    )

    cfg = auto_promote.AutoPromoteConfig(
        to_telegram_enabled=True, to_fullyauto_enabled=False, consecutive_days=1,
    )
    await auto_promote.evaluate_user(mock.AsyncMock(), user_id=1, cfg=cfg)

    assert len(captured) >= 1
    assert captured[0] == ["15m"]


@pytest.mark.asyncio
async def test_evaluate_user_no_exclusion_when_flag_is_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """evaluate_user must pass exclude_timeframes=None when
    SHADOW_15M_ELIGIBLE_FOR_PROMOTION=True."""
    from app.trading import auto_promote

    captured: list = []

    async def fake_compute_gates(
        session, *, window_days, now, direction_filter=None, exclude_timeframes=None,
    ):
        captured.append(exclude_timeframes)
        from app.trading.promotion import compute_stats
        return compute_stats([], span_days=window_days, now=now)

    monkeypatch.setattr(auto_promote, "compute_gates_from_db", fake_compute_gates)
    monkeypatch.setattr(
        auto_promote, "get_settings",
        lambda: _make_settings(fifteen_m_eligible=True),
    )
    monkeypatch.setattr(
        auto_promote, "get_mode", mock.AsyncMock(return_value="manual"),
    )

    cfg = auto_promote.AutoPromoteConfig(
        to_telegram_enabled=True, to_fullyauto_enabled=False, consecutive_days=1,
    )
    await auto_promote.evaluate_user(mock.AsyncMock(), user_id=1, cfg=cfg)

    assert len(captured) >= 1
    assert captured[0] is None
