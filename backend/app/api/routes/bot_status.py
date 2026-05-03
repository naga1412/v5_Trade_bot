"""REST endpoints powering the SP-0.5 Bot Status tab.

All endpoints are read-only aggregations over `shadow_trades`,
`shadow_open_positions`, and `asset_universe`. Auth is enforced at the
router level via `require_cf_user`.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    BotOverviewOut,
    GateMetricOut,
    OpenPositionOut,
    PerAssetStatOut,
    PromotionGateOut,
    RecentTradeOut,
    WindowStatsOut,
)
from app.db.session import get_session
from app.deps import require_cf_user
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
    dependencies=[Depends(require_cf_user)],
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
    session: AsyncSession, *, since: datetime, direction: str | None = None,
) -> list[Any]:
    """Return shadow_trades rows closed at/after `since`. Optional direction filter."""
    sql = (
        "SELECT direction, entry_price, stop_loss, take_profit, "
        "position_size_usdt, pnl_pct, pnl_usdt, closed_at "
        "FROM shadow_trades "
        "WHERE closed_at >= :since AND closed_at IS NOT NULL "
    )
    params: dict[str, Any] = {"since": since.isoformat()}
    if direction is not None:
        sql += "AND direction = :direction "
        params["direction"] = direction
    sql += "ORDER BY closed_at ASC"
    result = await session.execute(sa.text(sql), params)
    return list(result.all())


# --- Endpoints --------------------------------------------------------------


@router.get("/overview", response_model=BotOverviewOut)
async def overview(
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
        rows = await _select_trades_since(session, since=since)
        trades = [_row_to_trade(r) for r in rows]
        blocks[key] = _build_window_stats(
            window=label, trades=trades, rows=rows, window_days=days,
        )

    long_rows = await _select_trades_since(
        session, since=now - timedelta(days=30), direction="LONG",
    )
    short_rows = await _select_trades_since(
        session, since=now - timedelta(days=30), direction="SHORT",
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
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> PromotionGateOut:
    """Return rolling-30-day promotion-gate state for telegram-approve mode."""
    now = datetime.now(UTC)
    since = now - timedelta(days=30)
    rows = await _select_trades_since(session, since=since)
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
    return PromotionGateOut(
        target_mode="telegram-approve",
        metrics=metrics,
        all_passing=all_passing,
        distance_summary=distance_summary,
    )


@router.get("/open-positions", response_model=list[OpenPositionOut])
async def open_positions(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[OpenPositionOut]:
    """Return the open shadow positions.

    `current_price`, `unrealized_pnl_pct`, and `unrealized_pnl_usdt` are
    intentionally returned as `None`. The Bot Status tab subscribes to the
    WebSocket `shadow_pnl_tick` stream for live mark prices; this REST
    endpoint exists only for cold-load / refresh fallback so we avoid an
    expensive Binance round-trip on every page load.
    """
    sql = (
        "SELECT symbol, direction, entry_price, stop_loss, take_profit, "
        "position_size_usdt, bars_held, opened_at, signal_id "
        "FROM shadow_open_positions ORDER BY opened_at ASC"
    )
    result = await session.execute(sa.text(sql))
    out: list[OpenPositionOut] = []
    for r in result:
        opened_at = r.opened_at
        if isinstance(opened_at, str):
            opened_at = datetime.fromisoformat(opened_at)
        out.append(OpenPositionOut(
            symbol=r.symbol,
            direction=r.direction,  # type: ignore[arg-type]
            entry_price=r.entry_price,
            stop_loss=r.stop_loss,
            take_profit=r.take_profit,
            position_size_usdt=r.position_size_usdt,
            bars_held=r.bars_held,
            opened_at=opened_at,
            signal_id=r.signal_id,
            current_price=None,
            unrealized_pnl_pct=None,
            unrealized_pnl_usdt=None,
        ))
    return out


@router.get("/per-asset", response_model=list[PerAssetStatOut])
async def per_asset(
    days: int = Query(default=30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[PerAssetStatOut]:
    """Per-asset rolling stats over the last `days` days, sorted by pnl_usdt desc."""
    now = datetime.now(UTC)
    since = now - timedelta(days=days)
    sql = (
        "SELECT symbol, direction, entry_price, stop_loss, take_profit, "
        "position_size_usdt, pnl_pct, pnl_usdt, closed_at "
        "FROM shadow_trades "
        "WHERE closed_at >= :since AND closed_at IS NOT NULL"
    )
    result = await session.execute(sa.text(sql), {"since": since.isoformat()})
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


def _normalize_symbol_path(s: str) -> str:
    """BTC-USDT (URL-safe) -> BTC/USDT. Idempotent."""
    return s.replace("-", "/").upper()


@router.get("/recent-trades", response_model=list[RecentTradeOut])
async def recent_trades(
    limit: int = Query(default=100, ge=1, le=500),
    symbol: str | None = Query(default=None),
    direction: Literal["LONG", "SHORT"] | None = Query(default=None),
    result: Literal["win", "loss"] | None = Query(default=None),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[RecentTradeOut]:
    """Paginated, filterable closed-trade history (closed_at DESC)."""
    where: list[str] = ["closed_at IS NOT NULL"]
    params: dict[str, Any] = {}
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
