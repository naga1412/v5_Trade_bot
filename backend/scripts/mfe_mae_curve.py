"""MFE/MAE curve probe — Phase 1 MFE study for LONG shadow trades.

READ-ONLY. Queries prod postgres + fetches Binance SPOT klines. Produces
the tables the operator requested for the ~60% WR feasibility question.

Population (matches operator's Phase 1 MFE study spec 2026-07-28):
    - closed LONG shadow_trades
    - entry_score >= 0.36  (the live gate)
    - closed 60d window
    - SHADOW_SPOT_BLACKLIST excluded
    - split by TOP-20 (fleet-eligible at time of trade) vs ranks_21_30
      (all-30 dilution comparison per operator's methodology)
    - "top-20 at time of trade" resolves via the asset_universe snapshot
      whose snapshot_at is <= opened_at; BTCUSDT is force-included via
      the fleet's DEFAULT_EXCLUDE handling (singleton coverage).

STUDY 1 — MFE/MAE curve
    For each trade, walk 1h Binance klines from opened_at to closed_at.
    Compute per-bar MFE_R and MAE_R where R = |entry - stop_loss|.
    For each TP_R in {0.5, 0.75, 1.0, 1.25, 1.5, 2.0} determine:
      - implied_WR: share where MFE_R >= TP_R occurred BEFORE
        MAE_R >= 1.0 (i.e., before SL hit).
      - implied_expectancy_R: mean of (win: +TP_R, loss: -1R,
        neither: actual_pnl_pct converted to R).
      - after-fee variant: subtract 0.001 (0.1% round trip) converted to
        R using per-trade R_pct = R / entry_price. Reported as
        avg_expectancy_R_after_fee.

STUDY 2 — SL near-miss autopsy
    Among trades with exit_reason='STOP_LOSS', distribution of MFE_R:
      - share reaching >= 0.5R and >= 0.75R (convertible-loser pool
        for a hypothetical breakeven-stop mechanic).

Same-bar tiebreak: conservative — if MFE and MAE both cross their
thresholds within the SAME 1h bar, treat as SL hit first. Errs toward
under-counting wins. This matches standard backtest hygiene.

Rate-limit hygiene: sequential kline fetches with 200ms spacing (~300
trades × 1 request each = ~5min run). Binance SPOT public klines have
no auth and ~1200 req/min per-IP; well under.

Usage (inside backend container via ops-debug probe):
    docker compose exec -T backend python /app/scripts/mfe_mae_curve.py

Read-only guarantees:
    - No writes to postgres
    - No calls to trading APIs (only public SPOT klines)
    - No mutation of any in-memory state
"""
from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from statistics import mean

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.config import get_settings
from app.db.session import get_session_factory


TP_R_LADDER: list[float] = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
FEE_ROUND_TRIP_PCT: float = 0.001  # 10 bps
BINANCE_SPOT_KLINES: str = "https://api.binance.com/api/v3/klines"
INTERVAL_1H: str = "1h"
FETCH_DELAY_S: float = 0.2
BAR_MS: int = 60 * 60 * 1000


@dataclass(frozen=True)
class TradeRow:
    id: int
    symbol: str  # no-slash form (e.g. BTCUSDT)
    opened_at_ms: int
    closed_at_ms: int
    entry_price: float
    stop_loss: float
    exit_reason: str
    pnl_pct: float
    scope: str  # 'top20' or 'ranks_21_30'


@dataclass(frozen=True)
class TradeMetrics:
    trade_id: int
    scope: str
    exit_reason: str
    r_unit: float                  # entry - stop_loss (LONG, positive)
    r_pct: float                   # r_unit / entry_price
    pnl_r: float                   # pnl_pct as R-multiple
    mfe_r_final: float             # peak MFE in R
    mae_r_final: float             # peak MAE in R
    tp_first_bar: dict[float, int | None]  # per TP_R: 0-indexed bar or None
    sl_first_bar: int | None       # 0-indexed bar or None
    sl_and_tp_same_bar: dict[float, bool]  # per TP_R: were they same-bar
    n_bars: int


def _blacklist() -> set[str]:
    return set(get_settings().SHADOW_SPOT_BLACKLIST)


async def _load_trades() -> list[TradeRow]:
    """Query shadow_trades with the study filter + scope enrichment.

    Scope resolution uses each trade's opened_at to find the closest-
    earlier asset_universe snapshot and looks up the symbol's rank there.
    Symbols at rank <= 20 count as top20; BTCUSDT force-included as
    top20 to mirror the singleton-coverage rule.
    """
    sql = text(
        """
        WITH trades AS (
            SELECT
                s.id,
                s.symbol,
                s.opened_at,
                s.closed_at,
                s.entry_price,
                s.stop_loss,
                s.exit_reason,
                s.pnl_pct,
                (SELECT MAX(snapshot_at) FROM asset_universe
                    WHERE snapshot_at <= s.opened_at) AS rank_snap
            FROM shadow_trades s
            WHERE s.closed_at IS NOT NULL
              AND s.direction = 'LONG'
              AND s.entry_score >= 0.36
              AND s.closed_at >= NOW() - INTERVAL '60 days'
              AND s.entry_price > 0
              AND s.stop_loss > 0
              AND s.stop_loss < s.entry_price
        )
        SELECT t.*, au.rank AS rank_at_trade
        FROM trades t
        LEFT JOIN asset_universe au
          ON au.snapshot_at = t.rank_snap AND au.symbol = t.symbol
        ORDER BY t.opened_at
        """
    )
    bl = _blacklist()
    sf = get_session_factory()
    out: list[TradeRow] = []
    async with sf() as session:
        rows = (await session.execute(sql)).fetchall()
    for r in rows:
        if r.symbol in bl:
            continue
        rank = r.rank_at_trade
        # Fleet rule: top-20 by rank OR singleton BTCUSDT.
        if r.symbol == "BTCUSDT" or (rank is not None and rank <= 20):
            scope = "top20"
        else:
            scope = "ranks_21_30"
        out.append(TradeRow(
            id=int(r.id),
            symbol=str(r.symbol),
            opened_at_ms=int(r.opened_at.timestamp() * 1000),
            closed_at_ms=int(r.closed_at.timestamp() * 1000),
            entry_price=float(r.entry_price),
            stop_loss=float(r.stop_loss),
            exit_reason=str(r.exit_reason),
            pnl_pct=float(r.pnl_pct),
            scope=scope,
        ))
    return out


async def _fetch_bars(
    client: httpx.AsyncClient, symbol: str, start_ms: int, end_ms: int,
) -> list[tuple[int, float, float]]:
    """Return list of (open_time_ms, high, low) 1h bars in [start, end].

    Binance klines returns up to 1000 bars per call. 60 days at 1h = 1440
    bars, so up to 2 pages needed. Handle pagination via startTime cursor.
    """
    bars: list[tuple[int, float, float]] = []
    cursor = start_ms
    while cursor <= end_ms:
        params = {
            "symbol": symbol,
            "interval": INTERVAL_1H,
            "startTime": cursor,
            "endTime": end_ms,
            "limit": 1000,
        }
        try:
            resp = await client.get(BINANCE_SPOT_KLINES, params=params, timeout=15.0)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:  # noqa: BLE001
            print(f"  ! kline fetch failed {symbol} start={cursor}: {e}")
            return bars
        if not payload:
            break
        for row in payload:
            open_time_ms = int(row[0])
            high = float(row[2])
            low = float(row[3])
            bars.append((open_time_ms, high, low))
        last_open = int(payload[-1][0])
        if last_open + BAR_MS > end_ms or len(payload) < 1000:
            break
        cursor = last_open + BAR_MS
        await asyncio.sleep(FETCH_DELAY_S)
    return bars


def _compute_metrics(
    trade: TradeRow, bars: list[tuple[int, float, float]],
) -> TradeMetrics:
    r_unit = trade.entry_price - trade.stop_loss
    r_pct = r_unit / trade.entry_price
    pnl_r = (trade.pnl_pct / 100.0) / r_pct if r_pct > 0 else 0.0

    mfe_r_final = 0.0
    mae_r_final = 0.0
    tp_first_bar: dict[float, int | None] = {tp: None for tp in TP_R_LADDER}
    sl_first_bar: int | None = None
    sl_and_tp_same_bar: dict[float, bool] = {tp: False for tp in TP_R_LADDER}

    for i, (_, high, low) in enumerate(bars):
        # Long-side excursions (in R units, always positive).
        favorable_r = (high - trade.entry_price) / r_unit if r_unit > 0 else 0.0
        adverse_r = (trade.entry_price - low) / r_unit if r_unit > 0 else 0.0
        if favorable_r > mfe_r_final:
            mfe_r_final = favorable_r
        if adverse_r > mae_r_final:
            mae_r_final = adverse_r
        bar_hit_sl = adverse_r >= 1.0
        for tp in TP_R_LADDER:
            bar_hit_tp = favorable_r >= tp
            if bar_hit_tp and tp_first_bar[tp] is None:
                if bar_hit_sl and sl_first_bar is None:
                    # Same-bar tie: mark it for the record. Do NOT set
                    # tp_first_bar this bar; the resolver treats same-bar
                    # as SL-first (conservative).
                    sl_and_tp_same_bar[tp] = True
                else:
                    tp_first_bar[tp] = i
        if bar_hit_sl and sl_first_bar is None:
            sl_first_bar = i

    return TradeMetrics(
        trade_id=trade.id,
        scope=trade.scope,
        exit_reason=trade.exit_reason,
        r_unit=r_unit,
        r_pct=r_pct,
        pnl_r=pnl_r,
        mfe_r_final=mfe_r_final,
        mae_r_final=mae_r_final,
        tp_first_bar=tp_first_bar,
        sl_first_bar=sl_first_bar,
        sl_and_tp_same_bar=sl_and_tp_same_bar,
        n_bars=len(bars),
    )


def _resolve_outcome(m: TradeMetrics, tp: float) -> tuple[str, float]:
    """Return (outcome, r_result) for this trade at this TP_R.

    Outcomes:
      'win' — TP crossed before SL. r_result = +tp
      'loss' — SL crossed before (or same bar as) TP. r_result = -1.0
      'timeout' — neither event fired within the observed bars.
                  r_result = actual pnl_r (bounded by whatever exit hit)
    """
    tp_bar = m.tp_first_bar[tp]
    sl_bar = m.sl_first_bar
    if tp_bar is not None and (sl_bar is None or tp_bar < sl_bar):
        return "win", tp
    if sl_bar is not None:
        return "loss", -1.0
    # Same-bar tie (already handled: tp_first_bar stayed None if same bar
    # as SL) OR truly neither. Fall through to timeout.
    return "timeout", m.pnl_r


def _fee_r_for_trade(m: TradeMetrics) -> float:
    """0.1% round-trip fee expressed in R for this trade.

    fee_pct = 0.001 (round trip = maker+taker or 2x taker); expressed
    as R = 0.001 / r_pct. A trade with r_pct=0.02 (2%) has fee_r =
    0.001/0.02 = 0.05R.
    """
    if m.r_pct <= 0:
        return 0.0
    return FEE_ROUND_TRIP_PCT / m.r_pct


def _report_study1(metrics: list[TradeMetrics]) -> None:
    print("\n===== STUDY 1 — MFE/MAE curve =====")
    for scope in ("top20", "ranks_21_30"):
        subset = [m for m in metrics if m.scope == scope]
        if not subset:
            print(f"\n[{scope}] n=0")
            continue
        print(f"\n[{scope}] n={len(subset)}")
        # Header
        print(
            f"  {'tp_R':>6}  {'impl_WR%':>9}  {'exp_R':>7}  "
            f"{'exp_R_afterfee':>14}  {'timeouts':>9}"
        )
        for tp in TP_R_LADDER:
            outcomes = [_resolve_outcome(m, tp) for m in subset]
            wins = sum(1 for o, _ in outcomes if o == "win")
            n = len(outcomes)
            wr = 100.0 * wins / n if n > 0 else 0.0
            exp_r = mean(r for _, r in outcomes)
            fees_r = [_fee_r_for_trade(m) for m in subset]
            exp_r_af = mean(r - f for (_, r), f in zip(outcomes, fees_r))
            timeouts = sum(1 for o, _ in outcomes if o == "timeout")
            print(
                f"  {tp:>6.2f}  {wr:>8.1f}%  {exp_r:>7.3f}  "
                f"{exp_r_af:>14.3f}  {timeouts:>9d}"
            )


def _report_study2(metrics: list[TradeMetrics]) -> None:
    print("\n===== STUDY 2 — SL near-miss autopsy =====")
    print("  MFE_R distribution among exit_reason='STOP_LOSS' trades")
    for scope in ("top20", "ranks_21_30"):
        sl_trades = [
            m for m in metrics
            if m.scope == scope and m.exit_reason == "STOP_LOSS"
        ]
        if not sl_trades:
            print(f"\n[{scope}] n_sl=0")
            continue
        n = len(sl_trades)
        mfes = [m.mfe_r_final for m in sl_trades]
        n_ge_05 = sum(1 for x in mfes if x >= 0.5)
        n_ge_075 = sum(1 for x in mfes if x >= 0.75)
        avg = mean(mfes)
        print(
            f"\n[{scope}] n_sl={n}  avg_MFE_R={avg:.3f}  "
            f"share_MFE>=0.5R={100.0 * n_ge_05 / n:.1f}%  "
            f"share_MFE>=0.75R={100.0 * n_ge_075 / n:.1f}%"
        )


def _report_coverage(
    metrics: list[TradeMetrics], loaded: list[TradeRow],
) -> None:
    print("\n===== COVERAGE =====")
    print(f"  Total shadow_trades matching filter: {len(loaded)}")
    print(f"  With computed metrics (bars fetched): {len(metrics)}")
    dropped = len(loaded) - len(metrics)
    print(f"  Dropped (fetch failure / zero bars): {dropped}")
    for scope in ("top20", "ranks_21_30"):
        scope_loaded = sum(1 for t in loaded if t.scope == scope)
        scope_metrics = sum(1 for m in metrics if m.scope == scope)
        print(f"  [{scope}] loaded={scope_loaded} computed={scope_metrics}")


async def main() -> None:
    start = time.time()
    print("MFE/MAE curve probe — loading trades …")
    trades = await _load_trades()
    print(f"  loaded {len(trades)} trades ({sum(1 for t in trades if t.scope == 'top20')} top20, "
          f"{sum(1 for t in trades if t.scope == 'ranks_21_30')} ranks_21_30)")

    print("Fetching Binance klines per trade (sequential, ~0.2s each) …")
    metrics: list[TradeMetrics] = []
    async with httpx.AsyncClient() as client:
        for i, t in enumerate(trades, 1):
            # Extend range by 1 bar on each side to include the closing candle.
            start_ms = t.opened_at_ms - BAR_MS
            end_ms = t.closed_at_ms + BAR_MS
            bars = await _fetch_bars(client, t.symbol, start_ms, end_ms)
            if not bars:
                continue
            # Keep only bars whose open falls between the trade window.
            bars = [b for b in bars if t.opened_at_ms <= b[0] <= t.closed_at_ms]
            if not bars:
                continue
            metrics.append(_compute_metrics(t, bars))
            if i % 25 == 0:
                elapsed = time.time() - start
                print(f"  {i}/{len(trades)} processed (elapsed {elapsed:.0f}s)")
            await asyncio.sleep(FETCH_DELAY_S)

    _report_coverage(metrics, trades)
    _report_study1(metrics)
    _report_study2(metrics)

    print(f"\nTotal runtime: {time.time() - start:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
