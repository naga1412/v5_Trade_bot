"""REST endpoints powering the SP-0.5 Bot Status tab.

All endpoints are read-only aggregations over `shadow_trades`,
`shadow_open_positions`, and `asset_universe`. Auth is resolved per-handler
via `current_user_or_impersonated` so admins viewing through impersonation
see the target user's data lens (Phase E adds the user_id query filters
that complete data isolation; D8 wires the dep only).
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    AssetUniverseEntryOut,
    AssetUniverseOut,
    BotOverviewOut,
    EquityCurveOut,
    EquityCurvePoint,
    GateMetricOut,
    LiveCooldownOut,
    LongShortBreakdownOut,
    OpenPositionOut,
    PerAssetStatOut,
    PromotionGateOut,
    RecentTradeOut,
    SizingPreviewOut,
    SymbolAllowlistOut,
    SymbolSearchHit,
    SymbolSearchOut,
    TimeframeGateStatsOut,
    WindowStatsOut,
)
from app.auth.deps import current_user_or_impersonated
from app.auth.models import User
from app.data.binance_ticker import compute_unrealized_pnl_pct, fetch_spot_prices
from app.db.session import get_session
from app.shadow.stats import (
    Trade,
    compute_avg_rr,
    compute_max_drawdown,
    compute_profit_factor,
    compute_sharpe_annualized,
    compute_win_rate,
)

router = APIRouter(
    prefix="/api/v1/bot-status",
    tags=["bot-status"],
)

# JSON cannot encode infinity. When the strategy has zero losses in a window,
# `compute_profit_factor` returns inf — we cap it for transport.
_PROFIT_FACTOR_INF_CAP: float = 999.0


# --- Helpers ----------------------------------------------------------------


def _risk_reward(direction: str, entry: float, sl: float, tp: float) -> float:
    """RR matches ShadowSignal.risk_reward; returns 0 on degenerate inputs."""
    if direction == "LONG":
        risk = entry - sl
        reward = tp - entry
    else:
        risk = sl - entry
        reward = entry - tp
    return reward / risk if risk > 0 else 0.0


def _row_to_trade(row: Any) -> Trade:
    closed_at = row.closed_at
    if isinstance(closed_at, str):
        closed_at = datetime.fromisoformat(closed_at)
    return Trade(
        pnl_pct=row.pnl_pct,
        pnl_usdt=row.pnl_usdt,
        risk_reward=_risk_reward(
            row.direction, row.entry_price, row.stop_loss, row.take_profit,
        ),
        closed_at=closed_at,
    )


def _build_window_stats(
    *,
    window: Literal["24h", "7d", "30d", "lifetime"],
    trades: list[Trade],
    rows: list[Any],
    window_days: int,
) -> WindowStatsOut:
    n = len(trades)
    pnl_usdt = sum(t.pnl_usdt for t in trades)
    if n == 0:
        pnl_pct: float | None = None
    else:
        total_size = sum(r.position_size_usdt for r in rows)
        pnl_pct = pnl_usdt / total_size if total_size > 0 else None

    pf = compute_profit_factor(trades)
    if math.isinf(pf):
        pf = _PROFIT_FACTOR_INF_CAP

    return WindowStatsOut(
        window=window,
        trades=n,
        pnl_usdt=float(pnl_usdt),
        pnl_pct=pnl_pct,
        win_rate=compute_win_rate(trades),
        sharpe_annualized=compute_sharpe_annualized(trades, window_days),
        max_drawdown=compute_max_drawdown(trades),
        profit_factor=pf,
    )


async def _select_trades_since(
    session: AsyncSession, *, user_id: int, since: datetime,
    direction: str | None = None,
    timeframe: str | None = None,
    exclude_timeframes: list[str] | None = None,
) -> list[Any]:
    """Return shadow_trades rows closed at/after `since`. Optional filters.

    Spec §7 — every per-user table query MUST filter by user_id. The runtime
    query guard (app.auth.query_guard) enforces this at the SQLAlchemy event
    level so any future helper that forgets the predicate raises in dev.

    PR3 Phase 7:
      - ``timeframe``: optional positive filter for the per-TF breakdown
        in /promotion-gate.
      - ``exclude_timeframes``: optional list of TFs to exclude. Used by
        the combined block when ``SHADOW_15M_ELIGIBLE_FOR_PROMOTION=False``
        so 15m trades don't count toward the promotion criterion.
    """
    sql = (
        "SELECT direction, entry_price, stop_loss, take_profit, "
        "position_size_usdt, pnl_pct, pnl_usdt, closed_at "
        "FROM shadow_trades "
        "WHERE user_id = :user_id "
        "AND closed_at >= :since AND closed_at IS NOT NULL "
    )
    params: dict[str, Any] = {"user_id": user_id, "since": since}
    if direction is not None:
        sql += "AND direction = :direction "
        params["direction"] = direction
    if timeframe is not None:
        sql += "AND timeframe = :timeframe "
        params["timeframe"] = timeframe
    if exclude_timeframes:
        # Parametrize each excluded TF so any value flows through asyncpg
        # parameter-binding cleanly (don't string-interpolate).
        placeholders = []
        for i, tf in enumerate(exclude_timeframes):
            key = f"excl_tf_{i}"
            placeholders.append(f":{key}")
            params[key] = tf
        sql += f"AND timeframe NOT IN ({', '.join(placeholders)}) "
    sql += "ORDER BY closed_at ASC"
    result = await session.execute(sa.text(sql), params)
    return list(result.all())


# --- Endpoints --------------------------------------------------------------


@router.get("/overview", response_model=BotOverviewOut)
async def overview(
    current_user: User = Depends(current_user_or_impersonated),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> BotOverviewOut:
    """Aggregate stats over rolling 24h / 7d / 30d (and 30d split by direction)."""
    now = datetime.now(UTC)

    cuts: dict[str, tuple[datetime, int, Literal["24h", "7d", "30d"]]] = {
        "last_24h": (now - timedelta(hours=24), 1, "24h"),
        "last_7d": (now - timedelta(days=7), 7, "7d"),
        "last_30d": (now - timedelta(days=30), 30, "30d"),
    }
    blocks: dict[str, WindowStatsOut] = {}
    for key, (since, days, label) in cuts.items():
        rows = await _select_trades_since(
            session, user_id=current_user.id, since=since,
        )
        trades = [_row_to_trade(r) for r in rows]
        blocks[key] = _build_window_stats(
            window=label, trades=trades, rows=rows, window_days=days,
        )

    long_rows = await _select_trades_since(
        session, user_id=current_user.id,
        since=now - timedelta(days=30), direction="LONG",
    )
    short_rows = await _select_trades_since(
        session, user_id=current_user.id,
        since=now - timedelta(days=30), direction="SHORT",
    )
    blocks["long_only_30d"] = _build_window_stats(
        window="30d",
        trades=[_row_to_trade(r) for r in long_rows],
        rows=long_rows, window_days=30,
    )
    blocks["short_only_30d"] = _build_window_stats(
        window="30d",
        trades=[_row_to_trade(r) for r in short_rows],
        rows=short_rows, window_days=30,
    )

    return BotOverviewOut(**blocks)


# --- Promotion gate (autonomous spec §4.1, telegram-approve target) ---------

# Thresholds for the rolling 30-day promotion-gate metrics.
_GATE_DAYS_REQUIRED: int = 30
_GATE_TRADES_REQUIRED: int = 100
_GATE_SHARPE_MIN: float = 1.0
_GATE_MAX_DD_MAX: float = 0.12
_GATE_WIN_RATE_MIN: float = 0.40
_GATE_PROFIT_FACTOR_MIN: float = 1.5


def _passes(current: float | None, threshold: float, op: Literal[">=", "<="]) -> bool:
    if current is None:
        return False
    if op == ">=":
        return current >= threshold
    return current <= threshold


@router.get("/promotion-gate", response_model=PromotionGateOut)
async def promotion_gate(
    current_user: User = Depends(current_user_or_impersonated),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> PromotionGateOut:
    """Return rolling-30-day promotion-gate state for telegram-approve mode.

    PR3 Phase 7: response also carries `per_timeframe` (informational) with
    the same metric shape per TF. The combined dataset excludes 15m when
    `SHADOW_15M_ELIGIBLE_FOR_PROMOTION=False` so promotion criterion is
    not gambled on the noisier TF until staging validates 15m win-rate.
    """
    from app.config import get_settings as _get_pr3_settings
    _settings = _get_pr3_settings()

    now = datetime.now(UTC)
    since = now - timedelta(days=30)

    # Combined dataset: excludes 15m unless explicitly eligible.
    excluded_tfs: list[str] = []
    if not _settings.SHADOW_15M_ELIGIBLE_FOR_PROMOTION:
        excluded_tfs = ["15m"]
    rows = await _select_trades_since(
        session, user_id=current_user.id, since=since,
        exclude_timeframes=excluded_tfs,
    )
    trades = [_row_to_trade(r) for r in rows]

    n_trades = len(trades)
    if trades:
        # "Days since the first trade in the window, capped at 30." We round
        # generously to the nearest day so wall-clock drift between trade
        # timestamps and request time doesn't shave fractions off the count.
        first_ts = min(t.closed_at for t in trades)
        elapsed_days = (now - first_ts).total_seconds() / 86400.0
        days_active = float(min(30, round(elapsed_days)))
    else:
        days_active = 0.0

    sharpe = compute_sharpe_annualized(trades, 30)
    max_dd = compute_max_drawdown(trades)
    win_rate = compute_win_rate(trades) if trades else 0.0
    pf = compute_profit_factor(trades) if trades else 0.0
    if math.isinf(pf):
        pf = _PROFIT_FACTOR_INF_CAP

    metrics: list[GateMetricOut] = [
        GateMetricOut(
            name="continuous_paper_trading_days",
            current=days_active,
            threshold=float(_GATE_DAYS_REQUIRED),
            operator=">=",
            passing=_passes(days_active, _GATE_DAYS_REQUIRED, ">="),
        ),
        GateMetricOut(
            name="closed_paper_trades",
            current=float(n_trades),
            threshold=float(_GATE_TRADES_REQUIRED),
            operator=">=",
            passing=_passes(float(n_trades), _GATE_TRADES_REQUIRED, ">="),
        ),
        GateMetricOut(
            name="sharpe_annualized",
            current=sharpe,
            threshold=_GATE_SHARPE_MIN,
            operator=">=",
            passing=_passes(sharpe, _GATE_SHARPE_MIN, ">="),
        ),
        GateMetricOut(
            name="max_drawdown",
            current=max_dd,
            threshold=_GATE_MAX_DD_MAX,
            operator="<=",
            passing=_passes(max_dd, _GATE_MAX_DD_MAX, "<="),
        ),
        GateMetricOut(
            name="win_rate",
            current=win_rate,
            threshold=_GATE_WIN_RATE_MIN,
            operator=">=",
            passing=_passes(win_rate, _GATE_WIN_RATE_MIN, ">="),
        ),
        GateMetricOut(
            name="profit_factor",
            current=pf,
            threshold=_GATE_PROFIT_FACTOR_MIN,
            operator=">=",
            passing=_passes(pf, _GATE_PROFIT_FACTOR_MIN, ">="),
        ),
    ]
    all_passing = all(m.passing for m in metrics)
    distance_summary = _build_distance_summary(
        days_short=max(0.0, _GATE_DAYS_REQUIRED - days_active),
        trades_short=max(0, _GATE_TRADES_REQUIRED - n_trades),
        all_passing=all_passing,
    )

    # PR3 Phase 7: per-TF breakdown. One block per configured TF. Empty
    # blocks (no trades on that TF yet) still surface — operator can see
    # the 15m lane accruing toward future-eligibility data.
    per_timeframe: dict[str, TimeframeGateStatsOut] = {}
    for tf in _settings.SHADOW_TIMEFRAMES:
        tf_rows = await _select_trades_since(
            session, user_id=current_user.id, since=since, timeframe=tf,
        )
        tf_trades = [_row_to_trade(r) for r in tf_rows]
        tf_pf: float | None = (
            compute_profit_factor(tf_trades) if tf_trades else None
        )
        if tf_pf is not None and math.isinf(tf_pf):
            tf_pf = _PROFIT_FACTOR_INF_CAP
        per_timeframe[tf] = TimeframeGateStatsOut(
            trades_total=len(tf_trades),
            sharpe=compute_sharpe_annualized(tf_trades, 30) if tf_trades else None,
            max_drawdown=compute_max_drawdown(tf_trades) if tf_trades else None,
            win_rate=compute_win_rate(tf_trades) if tf_trades else None,
            profit_factor=tf_pf,
        )

    return PromotionGateOut(
        target_mode="telegram-approve",
        metrics=metrics,
        all_passing=all_passing,
        distance_summary=distance_summary,
        per_timeframe=per_timeframe,
    )


@router.get("/open-positions", response_model=list[OpenPositionOut])
async def open_positions(
    current_user: User = Depends(current_user_or_impersonated),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[OpenPositionOut]:
    """Return the open shadow positions with current price + unrealized P&L.

    Cold-load fills these from a single Binance SPOT batch ticker call so
    the dashboard shows real numbers immediately. The WebSocket
    `shadow_pnl_tick` stream continues to deliver real-time updates on
    each candle close. Any Binance-side failure here is logged and we
    fall back to None for the price/pnl fields (UI shows "—" as before).
    """
    sql = (
        "SELECT symbol, direction, entry_price, stop_loss, take_profit, "
        "position_size_usdt, bars_held, opened_at, signal_id, "
        # PR3 Phase 8: surface timeframe so the frontend deep-link
        # routes to the correct chart TF (was hardcoded '1h').
        "timeframe "
        "FROM shadow_open_positions "
        "WHERE user_id = :user_id "
        "ORDER BY opened_at ASC"
    )
    result = await session.execute(sa.text(sql), {"user_id": current_user.id})
    rows = list(result)

    # Batch-fetch SPOT prices once for all open symbols. Symbols stored in
    # `shadow_open_positions` are already in Binance no-slash form
    # (BTCUSDT, etc.) — the shadow worker subscribes to that exact form.
    symbols = [r.symbol for r in rows]
    prices = await fetch_spot_prices(symbols)

    out: list[OpenPositionOut] = []
    for r in rows:
        opened_at = r.opened_at
        if isinstance(opened_at, str):
            opened_at = datetime.fromisoformat(opened_at)

        current_price: float | None = prices.get(r.symbol)
        pnl_pct: float | None = None
        pnl_usdt: float | None = None
        if current_price is not None:
            pnl_pct = compute_unrealized_pnl_pct(
                direction=r.direction,
                entry_price=r.entry_price,
                current_price=current_price,
            )
            if pnl_pct is not None:
                pnl_usdt = r.position_size_usdt * pnl_pct / 100.0

        out.append(OpenPositionOut(
            symbol=r.symbol,
            direction=r.direction,  # type: ignore[arg-type]
            # PR3 Phase 8: thread per-position TF. Pre-PR3 rows that lack
            # the column entirely would have raised AttributeError here;
            # the alembic 0021 migration backfills '1h' so post-migration
            # rows always have it. getattr fallback for any defensive case.
            timeframe=getattr(r, "timeframe", None) or "1h",
            entry_price=r.entry_price,
            stop_loss=r.stop_loss,
            take_profit=r.take_profit,
            position_size_usdt=r.position_size_usdt,
            bars_held=r.bars_held,
            opened_at=opened_at,
            signal_id=r.signal_id,
            current_price=current_price,
            unrealized_pnl_pct=pnl_pct,
            unrealized_pnl_usdt=pnl_usdt,
        ))
    return out


@router.get("/cooldowns", response_model=list[LiveCooldownOut])
async def cooldowns(
    current_user: User = Depends(current_user_or_impersonated),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[LiveCooldownOut]:
    """PR8 — active live-trade cooldowns for the current user.

    Returns rows where `cooldown_until > now()`. Each row carries the
    last_exit_reason that triggered the cooldown and the
    last_mtf_agreement that was on the trade at close; the
    `blocked_until_fresh_mtf` flag computes whether the next signal on
    this symbol needs strictly-higher MTF to clear the cooldown.
    """
    from app.config import get_settings as _get_pr8_settings
    settings = _get_pr8_settings()
    require_fresh = settings.LIVE_COOLDOWN_SL_REQUIRES_FRESH_MTF

    rows = (await session.execute(
        sa.text(
            "SELECT symbol, cooldown_until, last_exit_reason, "
            "       last_mtf_agreement "
            "FROM live_cooldowns "
            "WHERE user_id = :uid "
            "  AND cooldown_until > :now "
            "ORDER BY cooldown_until ASC"
        ),
        {"uid": current_user.id, "now": datetime.now(UTC)},
    )).all()

    out: list[LiveCooldownOut] = []
    for r in rows:
        cu = r.cooldown_until
        if isinstance(cu, str):
            cu = datetime.fromisoformat(cu)
        out.append(LiveCooldownOut(
            symbol=r.symbol,
            cooldown_until=cu,
            last_exit_reason=r.last_exit_reason,
            last_mtf_agreement=r.last_mtf_agreement,
            blocked_until_fresh_mtf=(
                require_fresh and r.last_exit_reason == "stop_loss"
            ),
        ))
    return out


@router.get("/sizing", response_model=SizingPreviewOut)
async def sizing_preview(
    confidence_pct: float = Query(default=70.0, ge=0.0, le=100.0),
    current_user: User = Depends(current_user_or_impersonated),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SizingPreviewOut:
    """PR9 — preview what the dispatcher would size the next signal at.

    Read-only diagnostic; computes the same `compute_dynamic_size` the
    dispatcher uses against the current user's portfolio_value_usdt.
    Defaults to confidence_pct=70 (operator's typical signal strength);
    pass ?confidence_pct=N to probe other values.
    """
    from app.config import get_settings as _get_pr9_settings
    from app.trading.dynamic_sizing import (
        classify_balance_tier,
        compute_dynamic_size,
    )

    pr9_settings = _get_pr9_settings()

    # Best-effort read of current portfolio value. Use the user's
    # current row (fixed_size_min_usdt * 5 lower bound matches
    # build_user_context's conservative placeholder until the real
    # Binance wallet API is wired).
    row = (await session.execute(sa.text(
        "SELECT fixed_size_min_usdt FROM users WHERE id = :uid"
    ), {"uid": current_user.id})).first()
    fixed_min = (
        float(row.fixed_size_min_usdt)
        if row is not None and row.fixed_size_min_usdt is not None
        else 20.0
    )
    balance = max(fixed_min * 5.0, 100.0)

    tier = classify_balance_tier(balance, pr9_settings)
    margin = compute_dynamic_size(
        balance_usdt=balance,
        confidence_pct=confidence_pct,
        settings=pr9_settings,
    )

    return SizingPreviewOut(
        dynamic_sizing_enabled=pr9_settings.DYNAMIC_SIZING_ENABLED,
        balance_usdt=balance,
        tier=tier,
        tier_max_fraction=pr9_settings.SIZING_TIER_MAX_FRACTION.get(tier, 0.01),
        fractional_kelly=pr9_settings.SIZING_FRACTIONAL_KELLY,
        sample_confidence_pct=confidence_pct,
        sample_margin_usdt=margin,
    )


@router.get("/symbol-allowlist", response_model=list[SymbolAllowlistOut])
async def symbol_allowlist(
    current_user: User = Depends(current_user_or_impersonated),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[SymbolAllowlistOut]:
    """PR10 — current per-symbol allowlist snapshot. Latest row per symbol,
    sorted by sharpe descending (winners on top, losers at bottom)."""
    rows = (await session.execute(sa.text(
        "SELECT symbol, trades_count, win_rate, sharpe, allowed, computed_at "
        "  FROM symbol_performance_snapshots t1 "
        " WHERE computed_at = ( "
        "       SELECT MAX(computed_at) FROM symbol_performance_snapshots t2 "
        "        WHERE t2.symbol = t1.symbol "
        "     )"
    ))).all()

    out: list[SymbolAllowlistOut] = []
    for r in rows:
        ca = r.computed_at
        if isinstance(ca, str):
            ca = datetime.fromisoformat(ca)
        out.append(SymbolAllowlistOut(
            symbol=r.symbol,
            trades_count=int(r.trades_count),
            win_rate=float(r.win_rate) if r.win_rate is not None else None,
            sharpe=float(r.sharpe) if r.sharpe is not None else None,
            allowed=bool(r.allowed),
            computed_at=ca,
        ))
    # Sort: positive sharpe first (winners), then by sharpe desc; None last.
    out.sort(
        key=lambda e: (e.sharpe is None, -(e.sharpe or 0)),
    )
    return out


@router.get("/per-asset", response_model=list[PerAssetStatOut])
async def per_asset(
    days: int = Query(default=30, ge=1, le=365),
    current_user: User = Depends(current_user_or_impersonated),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[PerAssetStatOut]:
    """Per-asset rolling stats over the last `days` days, sorted by pnl_usdt desc."""
    now = datetime.now(UTC)
    since = now - timedelta(days=days)
    sql = (
        "SELECT symbol, direction, entry_price, stop_loss, take_profit, "
        "position_size_usdt, pnl_pct, pnl_usdt, closed_at "
        "FROM shadow_trades "
        "WHERE user_id = :user_id "
        "AND closed_at >= :since AND closed_at IS NOT NULL"
    )
    result = await session.execute(
        sa.text(sql), {"user_id": current_user.id, "since": since},
    )
    by_symbol: dict[str, list[Any]] = {}
    for r in result:
        by_symbol.setdefault(r.symbol, []).append(r)

    out: list[PerAssetStatOut] = []
    for symbol, sym_rows in by_symbol.items():
        trades = [_row_to_trade(r) for r in sym_rows]
        sharpe = compute_sharpe_annualized(trades, days)
        out.append(PerAssetStatOut(
            symbol=symbol,
            trades=len(trades),
            win_rate=compute_win_rate(trades),
            avg_rr=compute_avg_rr(trades),
            pnl_usdt=float(sum(t.pnl_usdt for t in trades)),
            sharpe_annualized=sharpe,
        ))
    out.sort(key=lambda e: e.pnl_usdt, reverse=True)
    return out


# Window-day labels for the WindowStatsOut.window field.
_DAYS_24H: int = 1
_DAYS_7D: int = 7
_DAYS_30D: int = 30


def _window_label_for_days(days: int) -> Literal["24h", "7d", "30d", "lifetime"]:
    if days <= _DAYS_24H:
        return "24h"
    if days <= _DAYS_7D:
        return "7d"
    if days <= _DAYS_30D:
        return "30d"
    return "lifetime"


def _normalize_symbol_path(s: str) -> str:
    """BTC-USDT (URL-safe) -> BTC/USDT. Idempotent."""
    return s.replace("-", "/").upper()


@router.get("/recent-trades", response_model=list[RecentTradeOut])
async def recent_trades(
    limit: int = Query(default=100, ge=1, le=500),
    symbol: str | None = Query(default=None),
    direction: Literal["LONG", "SHORT"] | None = Query(default=None),
    result: Literal["win", "loss"] | None = Query(default=None),
    current_user: User = Depends(current_user_or_impersonated),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[RecentTradeOut]:
    """Paginated, filterable closed-trade history (closed_at DESC)."""
    where: list[str] = ["user_id = :user_id", "closed_at IS NOT NULL"]
    params: dict[str, Any] = {"user_id": current_user.id}
    if symbol is not None:
        where.append("symbol = :symbol")
        params["symbol"] = _normalize_symbol_path(symbol)
    if direction is not None:
        where.append("direction = :direction")
        params["direction"] = direction
    if result == "win":
        where.append("pnl_pct > 0")
    elif result == "loss":
        where.append("pnl_pct <= 0")

    sql = (
        "SELECT closed_at, symbol, direction, entry_price, exit_price, "
        "pnl_pct, pnl_usdt, exit_reason, bars_held, signal_id "
        "FROM shadow_trades "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY closed_at DESC LIMIT :limit"
    )
    params["limit"] = limit
    rows = await session.execute(sa.text(sql), params)
    out: list[RecentTradeOut] = []
    for r in rows:
        closed_at = r.closed_at
        if isinstance(closed_at, str):
            closed_at = datetime.fromisoformat(closed_at)
        out.append(RecentTradeOut(
            closed_at=closed_at,
            symbol=r.symbol,
            direction=r.direction,  # type: ignore[arg-type]
            entry_price=r.entry_price,
            exit_price=r.exit_price,
            pnl_pct=r.pnl_pct,
            pnl_usdt=r.pnl_usdt,
            exit_reason=r.exit_reason,  # type: ignore[arg-type]
            bars_held=r.bars_held,
            signal_id=r.signal_id,
        ))
    return out


@router.get("/long-vs-short", response_model=LongShortBreakdownOut)
async def long_vs_short(
    days: int = Query(default=30, ge=1, le=365),
    current_user: User = Depends(current_user_or_impersonated),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> LongShortBreakdownOut:
    """Side-by-side LONG vs SHORT stats over the last `days` days."""
    now = datetime.now(UTC)
    since = now - timedelta(days=days)

    label = _window_label_for_days(days)

    long_rows = await _select_trades_since(
        session, user_id=current_user.id, since=since, direction="LONG",
    )
    short_rows = await _select_trades_since(
        session, user_id=current_user.id, since=since, direction="SHORT",
    )
    return LongShortBreakdownOut(
        long=_build_window_stats(
            window=label,
            trades=[_row_to_trade(r) for r in long_rows],
            rows=long_rows, window_days=days,
        ),
        short=_build_window_stats(
            window=label,
            trades=[_row_to_trade(r) for r in short_rows],
            rows=short_rows, window_days=days,
        ),
    )


@router.get("/equity-curve", response_model=EquityCurveOut)
async def equity_curve(
    days: int = Query(default=30, ge=1, le=365),
    current_user: User = Depends(current_user_or_impersonated),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> EquityCurveOut:
    """Cumulative PnL bucketed by UTC midnight day.

    Days with no trades are skipped (sparkline interpolates client-side).
    """
    now = datetime.now(UTC)
    since = now - timedelta(days=days)
    sql = (
        "SELECT pnl_usdt, closed_at FROM shadow_trades "
        "WHERE user_id = :user_id "
        "AND closed_at >= :since AND closed_at IS NOT NULL "
        "ORDER BY closed_at ASC"
    )
    rows = await session.execute(
        sa.text(sql), {"user_id": current_user.id, "since": since},
    )

    by_day: dict[datetime, float] = {}
    for r in rows:
        closed_at = r.closed_at
        if isinstance(closed_at, str):
            closed_at = datetime.fromisoformat(closed_at)
        bucket = datetime(
            closed_at.year, closed_at.month, closed_at.day, tzinfo=UTC,
        )
        by_day[bucket] = by_day.get(bucket, 0.0) + float(r.pnl_usdt)

    points: list[EquityCurvePoint] = []
    cum = 0.0
    for bucket in sorted(by_day):
        cum += by_day[bucket]
        points.append(EquityCurvePoint(date=bucket, cumulative_pnl_usdt=cum))

    return EquityCurveOut(days=days, points=points)


@router.get("/asset-universe", response_model=AssetUniverseOut)
async def asset_universe(
    current_user: User = Depends(current_user_or_impersonated),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> AssetUniverseOut:
    """Return the most recent `asset_universe` snapshot, ordered by rank."""
    sql = (
        "SELECT symbol, quote_volume_usd_24h, rank, snapshot_at "
        "FROM asset_universe "
        "WHERE snapshot_at = (SELECT MAX(snapshot_at) FROM asset_universe) "
        "ORDER BY rank ASC"
    )
    result = await session.execute(sa.text(sql))
    rows = result.all()
    if not rows:
        return AssetUniverseOut(snapshot_at=datetime.now(UTC), entries=[])

    snapshot_at_raw = rows[0].snapshot_at
    if isinstance(snapshot_at_raw, str):
        snapshot_at = datetime.fromisoformat(snapshot_at_raw)
    else:
        snapshot_at = snapshot_at_raw
    entries = [
        AssetUniverseEntryOut(
            symbol=r.symbol,
            rank=r.rank,
            quote_volume_24h_usdt=r.quote_volume_usd_24h,
        )
        for r in rows
    ]
    return AssetUniverseOut(snapshot_at=snapshot_at, entries=entries)


# Symbol limit per query: keeps the response cheap to render in the
# typeahead dropdown + caps any potential SQL-load amplification.
_SEARCH_HARD_LIMIT = 25
# Whitelisted character set for the search query. Symbol names are
# uppercase letters + digits + slash + dash + colon (for some
# exchanges' suffixes). Anything else is filtered out so the LIKE
# can't be tricked into pathological patterns.
_SAFE_QUERY_RE = re.compile(r"[A-Z0-9/_:\-]+")


@router.get("/symbol-search", response_model=SymbolSearchOut)
async def symbol_search(
    q: str = Query(min_length=1, max_length=32),
    limit: int = Query(default=10, ge=1, le=_SEARCH_HARD_LIMIT),
    exchange: str | None = Query(default=None, max_length=32),
    _user: User = Depends(current_user_or_impersonated),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SymbolSearchOut:
    """Typeahead suggestions for the symbol input.

    Matches case-insensitively against ``universe_history.symbol``
    using a prefix-then-substring ranking — exact prefix matches sort
    before substring matches so "SHIB" hits SHIB/USDT before
    SUSHI-perp/USDT. Joined to the latest ``asset_universe`` snapshot
    so liquid pairs surface 24h volume in the suggestion row.

    Active rows only (``delisted_at IS NULL``). Optional ``exchange``
    filter restricts to one venue (default: all).

    Caller is any authenticated user — no admin gate. Defended against
    LIKE-pattern abuse by sanitising the query string to
    [A-Z0-9/_:-] before binding.
    """
    # Sanitise: uppercase + drop anything outside the safe set. This is
    # belt-and-braces — the LIKE binding already escapes wildcards.
    cleaned_parts = _SAFE_QUERY_RE.findall(q.upper())
    cleaned = "".join(cleaned_parts)
    if not cleaned:
        return SymbolSearchOut(query=q, hits=[])

    where: list[str] = ["uh.delisted_at IS NULL"]
    params: dict[str, Any] = {
        "lim": limit,
        "prefix": cleaned + "%",
        "anywhere": "%" + cleaned + "%",
    }
    if exchange is not None:
        where.append("uh.exchange = :ex")
        params["ex"] = exchange[:32]

    # Two passes via UNION ALL — prefix matches first, then substring
    # matches that aren't already prefix matches. SQLite + Postgres
    # both support this; the inner LIMIT prevents runaway scans on
    # universe_history when q is very short.
    sql = (
        "SELECT symbol, exchange, asset_class, "
        "       (SELECT au.quote_volume_usd_24h FROM asset_universe au "
        "         WHERE au.symbol = uh.symbol "
        "         ORDER BY au.snapshot_at DESC LIMIT 1) AS qv "
        "FROM universe_history uh "
        "WHERE " + " AND ".join(where) + " "
        "  AND UPPER(uh.symbol) LIKE :prefix "
        "ORDER BY uh.symbol ASC LIMIT :lim"
    )
    rows = (await session.execute(sa.text(sql), params)).all()

    if len(rows) < limit:
        # Fill remaining slots with substring matches that aren't
        # prefix matches (de-duped by symbol).
        seen = {r.symbol for r in rows}
        sub_params = dict(params)
        sub_params["lim"] = limit - len(rows)
        sub_sql = (
            "SELECT symbol, exchange, asset_class, "
            "       (SELECT au.quote_volume_usd_24h FROM asset_universe au "
            "         WHERE au.symbol = uh.symbol "
            "         ORDER BY au.snapshot_at DESC LIMIT 1) AS qv "
            "FROM universe_history uh "
            "WHERE " + " AND ".join(where) + " "
            "  AND UPPER(uh.symbol) LIKE :anywhere "
            "  AND UPPER(uh.symbol) NOT LIKE :prefix "
            "ORDER BY uh.symbol ASC LIMIT :lim"
        )
        extra = (await session.execute(sa.text(sub_sql), sub_params)).all()
        rows = list(rows) + [r for r in extra if r.symbol not in seen]

    hits = [
        SymbolSearchHit(
            symbol=r.symbol, exchange=r.exchange,
            asset_class=r.asset_class,
            quote_volume_24h_usdt=float(r.qv) if r.qv is not None else None,
        )
        for r in rows[:limit]
    ]
    return SymbolSearchOut(query=q, hits=hits)


def _build_distance_summary(
    *, days_short: float, trades_short: int, all_passing: bool,
) -> str:
    if all_passing:
        return "all gates passing"
    parts: list[str] = []
    if days_short > 0:
        d = int(math.ceil(days_short))
        parts.append(f"{d} day{'s' if d != 1 else ''}")
    if trades_short > 0:
        parts.append(f"{trades_short} trade{'s' if trades_short != 1 else ''}")
    if not parts:
        return "stat thresholds (sharpe / win-rate / drawdown / profit-factor) still failing"
    return f"{' + '.join(parts)} to unlock"
