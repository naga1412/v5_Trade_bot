"""MFE/MAE curve probe v3 — corrected model + path-aware breakeven.

READ-ONLY. Answers ITEM 1 + ITEM 2 of the operator's 2026-07-29 direction
after the v2 flat-ladder verdict was ratified:

ITEM 1 — per-trade actual TP/SL rewrite
    The v2 probe used a hypothetical TP_R multiplier of R=|entry-stop_loss|.
    This v3 replaces that with each trade's ACTUAL take_profit and
    stop_loss from shadow_trades. On top-20 1h this should close most of
    the ~0.36-0.41R reality gap between the v2 model (exp_R -0.259) and
    STUDY 3's actual +0.228%/trade (~+0.10 to +0.15R).

    Report:
      - Directional agreement (confusion matrix) — target >>91.6% v2
      - exp_pnl_pct — target near STUDY 3's actual +0.228%/trade
      - After-fee variant using 0.10% round-trip taker (verified 2026-07-29
        in ITEM 3: MARKET entry + STOP_MARKET/TAKE_PROFIT_MARKET exit).

ITEM 2 — path-aware breakeven simulation on the CORRECTED 1h model
    For triggers in {0.4R, 0.5R, 0.75R, 1.0R}:

    Baseline: use the corrected per-trade outcome from ITEM 1.

    Breakeven mechanic: once MFE crosses trigger, the stop moves to
    entry. From that bar onward, exit at ENTRY on first bar with
    low<=entry (breakeven), or exit at actual take_profit on first bar
    with high>=take_profit — same-bar SL-first tiebreak matches
    production check_exit (2026-07-28 verified).

    HONEST accounting of BOTH sides:
      - Converted-losers: baseline was a loss (SL hit); trigger fires
        before SL; after trigger, breakeven wins the SL-first race → 0R.
      - Sacrificed-winners: baseline was a win (TP hit); trigger fires
        before TP; after trigger, breakeven wins the SL-first race
        BEFORE the TP bar → 0R instead of TP.

    Report per trigger: triggered, converted-losers, sacrificed-winners,
    unchanged, new exp_R_after_fee, delta vs baseline. That delta is the
    number that decides whether the mechanic ships.

Production geometry parity (verified 2026-07-28):
    - check_exit uses (bar_low <= stop_loss, bar_high >= take_profit) — wick
    - Same-bar SL-first tiebreak

Fee model (verified 2026-07-29 ITEM 3):
    - Entry: MARKET (taker) → 0.05%
    - Exit: STOP_MARKET or TAKE_PROFIT_MARKET (taker) → 0.05%
    - Round-trip: 0.10%

Rate-limit hygiene: sequential 1h kline fetches at 100ms spacing.
~300 trades × 1 request = ~30s + Binance latency, so ~2-3min total.

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
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.config import get_settings
from app.db.session import get_session_factory


BREAKEVEN_TRIGGERS: list[float] = [0.4, 0.5, 0.75, 1.0]
FEE_ROUND_TRIP_PCT: float = 0.001  # 10 bps taker × 2
BINANCE_SPOT_KLINES: str = "https://api.binance.com/api/v3/klines"
INTERVAL_1H: str = "1h"
FETCH_DELAY_S: float = 0.1
BAR_MS_1H: int = 60 * 60 * 1000


@dataclass(frozen=True)
class TradeRow:
    id: int
    symbol: str
    timeframe: str      # v5: fetch bars at this trade's own TF
    opened_at_ms: int
    closed_at_ms: int
    entry_price: float
    stop_loss: float
    take_profit: float
    entry_atr: float
    exit_reason: str
    pnl_pct: float
    scope: str          # 'top20' | 'ranks_21_30'
    atr_class: str      # 'atr_bound' | 'cap_bound'  (per 1.5*ATR > 5% of entry)


@dataclass
class Bar:
    ts_ms: int
    high: float
    low: float


@dataclass
class TradeMetrics:
    trade_id: int
    scope: str
    atr_class: str
    symbol: str
    exit_reason: str
    actual_pnl_pct: float
    entry_price: float
    stop_loss: float
    take_profit: float
    bars: list[Bar]
    r_unit: float
    r_pct: float

    # Corrected 1h model outcome using actual TP/SL:
    model_outcome: str = "unknown"  # "win" | "loss" | "timeout"
    model_pnl_pct: float = 0.0      # pnl_pct if outcome fires; else DB actual

    # For breakeven simulation:
    tp_first_bar: int | None = None
    sl_first_bar: int | None = None
    # trigger_first_bar[trg] = index of first bar where MFE crosses trg
    trigger_first_bar: dict[float, int | None] = field(default_factory=dict)

    # MFE / MAE finals for STUDY-2 style reporting:
    mfe_r_final: float = 0.0
    mae_r_final: float = 0.0


def _blacklist() -> set[str]:
    return set(get_settings().SHADOW_SPOT_BLACKLIST)


async def _load_trades() -> list[TradeRow]:
    sql = text(
        """
        WITH trades AS (
            SELECT
                s.id,
                s.symbol,
                s.timeframe,
                s.opened_at,
                s.closed_at,
                s.entry_price,
                s.stop_loss,
                s.take_profit,
                s.entry_atr,
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
              AND s.take_profit > 0
              AND s.entry_atr > 0
              AND s.stop_loss < s.entry_price
              AND s.take_profit > s.entry_price
              AND s.timeframe IN ('1h', '15m')
        )
        SELECT t.*, au.rank AS rank_at_trade
        FROM trades t
        LEFT JOIN asset_universe au
          ON au.snapshot_at = t.rank_snap AND au.symbol = t.symbol
        ORDER BY t.opened_at
        """
    )
    bl = _blacklist()
    out: list[TradeRow] = []
    sf = get_session_factory()
    async with sf() as session:
        rows = (await session.execute(sql)).fetchall()
    for r in rows:
        if r.symbol in bl:
            continue
        rank = r.rank_at_trade
        if r.symbol == "BTCUSDT" or (rank is not None and rank <= 20):
            scope = "top20"
        else:
            scope = "ranks_21_30"
        entry_atr = float(r.entry_atr)
        entry_price = float(r.entry_price)
        # Match engine.py convention: cap_bound iff 1.5*ATR > 5% of entry.
        atr_class = "cap_bound" if (1.5 * entry_atr) > (0.05 * entry_price) else "atr_bound"
        out.append(TradeRow(
            id=int(r.id),
            symbol=str(r.symbol),
            timeframe=str(r.timeframe),
            opened_at_ms=int(r.opened_at.timestamp() * 1000),
            closed_at_ms=int(r.closed_at.timestamp() * 1000),
            entry_price=entry_price,
            stop_loss=float(r.stop_loss),
            take_profit=float(r.take_profit),
            entry_atr=entry_atr,
            exit_reason=str(r.exit_reason),
            pnl_pct=float(r.pnl_pct),
            scope=scope,
            atr_class=atr_class,
        ))
    return out


_TF_TO_INTERVAL: dict[str, tuple[str, int]] = {
    "1h": (INTERVAL_1H, BAR_MS_1H),
    "15m": ("15m", 15 * 60 * 1000),
}


async def _fetch_bars_at_tf(
    client: httpx.AsyncClient,
    symbol: str,
    start_ms: int,
    end_ms: int,
    *,
    timeframe: str,
) -> list[Bar]:
    """v5: fetch bars at the trade's OWN timeframe (15m or 1h).

    Same production interval as check_exit would have used for that
    position — dissolves the v3/v4 model-vs-reality resolution mismatch.
    """
    if timeframe not in _TF_TO_INTERVAL:
        return []
    interval, bar_ms = _TF_TO_INTERVAL[timeframe]
    bars: list[Bar] = []
    cursor = start_ms
    while cursor <= end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": cursor,
            "endTime": end_ms,
            "limit": 1000,
        }
        try:
            resp = await client.get(BINANCE_SPOT_KLINES, params=params, timeout=15.0)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:  # noqa: BLE001
            print(f"  ! kline fetch failed {symbol}@{interval} start={cursor}: {e}")
            return bars
        if not payload:
            break
        for row in payload:
            bars.append(Bar(ts_ms=int(row[0]), high=float(row[2]), low=float(row[3])))
        last_open = int(payload[-1][0])
        if last_open + bar_ms > end_ms or len(payload) < 1000:
            break
        cursor = last_open + bar_ms
        await asyncio.sleep(FETCH_DELAY_S)
    return bars


def _pnl_pct_for_exit(entry: float, exit_price: float) -> float:
    return (exit_price - entry) / entry * 100.0


def _compute_metrics(trade: TradeRow, bars: list[Bar]) -> TradeMetrics:
    r_unit = trade.entry_price - trade.stop_loss
    r_pct = r_unit / trade.entry_price if trade.entry_price > 0 else 0.0
    m = TradeMetrics(
        trade_id=trade.id, scope=trade.scope, atr_class=trade.atr_class,
        symbol=trade.symbol,
        exit_reason=trade.exit_reason, actual_pnl_pct=trade.pnl_pct,
        entry_price=trade.entry_price, stop_loss=trade.stop_loss,
        take_profit=trade.take_profit,
        bars=bars, r_unit=r_unit, r_pct=r_pct,
        trigger_first_bar={t: None for t in BREAKEVEN_TRIGGERS},
    )

    # Baseline outcome using ACTUAL stop_loss and take_profit.
    # Production check_exit: bar_low <= stop_loss OR bar_high >= take_profit,
    # same-bar SL-first tiebreak.
    for i, b in enumerate(bars):
        favorable_r = (b.high - trade.entry_price) / r_unit if r_unit > 0 else 0.0
        adverse_r = (trade.entry_price - b.low) / r_unit if r_unit > 0 else 0.0
        if favorable_r > m.mfe_r_final:
            m.mfe_r_final = favorable_r
        if adverse_r > m.mae_r_final:
            m.mae_r_final = adverse_r
        bar_hit_sl = b.low <= trade.stop_loss
        bar_hit_tp = b.high >= trade.take_profit
        # Trigger tracking (unaffected by tiebreak)
        for trg in BREAKEVEN_TRIGGERS:
            if favorable_r >= trg and m.trigger_first_bar[trg] is None:
                m.trigger_first_bar[trg] = i
        if bar_hit_sl and m.sl_first_bar is None:
            m.sl_first_bar = i
        if bar_hit_tp and m.tp_first_bar is None:
            if bar_hit_sl and m.sl_first_bar == i:
                # Same-bar tiebreak: SL wins (matches production).
                pass
            else:
                m.tp_first_bar = i

    # Resolve outcome:
    tp_b = m.tp_first_bar
    sl_b = m.sl_first_bar
    if tp_b is not None and (sl_b is None or tp_b < sl_b):
        m.model_outcome = "win"
        m.model_pnl_pct = _pnl_pct_for_exit(trade.entry_price, trade.take_profit)
    elif sl_b is not None:
        m.model_outcome = "loss"
        m.model_pnl_pct = _pnl_pct_for_exit(trade.entry_price, trade.stop_loss)
    else:
        m.model_outcome = "timeout"
        m.model_pnl_pct = trade.pnl_pct
    return m


def _fee_pct_round_trip() -> float:
    return FEE_ROUND_TRIP_PCT * 100.0  # in percent


def _slices(metrics: list[TradeMetrics]) -> list[tuple[str, list[TradeMetrics]]]:
    """Yield (label, subset) for every scope × atr_class breakdown."""
    slices: list[tuple[str, list[TradeMetrics]]] = []
    for scope in ("top20", "ranks_21_30"):
        for ac in ("atr_bound", "cap_bound"):
            subset = [m for m in metrics if m.scope == scope and m.atr_class == ac]
            slices.append((f"{scope}/{ac}", subset))
        # Also the full scope (atr+cap combined) for backward compat
        subset_all = [m for m in metrics if m.scope == scope]
        slices.append((f"{scope}/all", subset_all))
    return slices


def _report_item1_agreement(metrics: list[TradeMetrics]) -> None:
    print("\n===== ITEM 1 — CORRECTED 1h MODEL (per-trade actual TP/SL) =====")
    print("  Confusion matrix vs actual pnl sign at each trade's own TP/SL")
    for label, subset in _slices(metrics):
        scope = label
        if not subset:
            continue
        cm = {
            ("win", "actual_win"): 0, ("win", "actual_loss"): 0,
            ("loss", "actual_win"): 0, ("loss", "actual_loss"): 0,
            ("timeout", "actual_win"): 0, ("timeout", "actual_loss"): 0,
        }
        for m in subset:
            actual_key = "actual_win" if m.actual_pnl_pct > 0 else "actual_loss"
            cm[(m.model_outcome, actual_key)] += 1
        n = len(subset)
        mismatch = cm[("win", "actual_loss")] + cm[("loss", "actual_win")]
        header = "model \\ actual"
        print(f"\n[{scope}] n={n}")
        print(f"  {header:<20}  {'actual_win':>10}  {'actual_loss':>11}")
        for row in ("win", "loss", "timeout"):
            print(
                f"  {row:<20}  {cm[(row, 'actual_win')]:>10}  "
                f"{cm[(row, 'actual_loss')]:>11}"
            )
        print(f"  n_actual_wins  = {sum(1 for m in subset if m.actual_pnl_pct > 0)}")
        print(f"  n_actual_losses = {sum(1 for m in subset if m.actual_pnl_pct <= 0)}")
        print(f"  WIN/LOSS mismatches = {mismatch}/{n} = {100.0 * mismatch / n:.1f}%")
        print(f"  Directional agreement = {100.0 * (n - mismatch) / n:.1f}%")

        # Expectancy comparison in ACTUAL PNL_PCT units.
        model_avg_pct = mean(m.model_pnl_pct for m in subset)
        actual_avg_pct = mean(m.actual_pnl_pct for m in subset)
        fee_pct = _fee_pct_round_trip()
        model_avg_pct_af = model_avg_pct - fee_pct
        actual_avg_pct_af = actual_avg_pct - fee_pct
        print(
            f"  MODEL avg_pnl_pct  = {model_avg_pct:+.4f}%  "
            f"(after-fee {model_avg_pct_af:+.4f}%)"
        )
        print(
            f"  ACTUAL avg_pnl_pct = {actual_avg_pct:+.4f}%  "
            f"(after-fee {actual_avg_pct_af:+.4f}%)"
        )
        gap = actual_avg_pct - model_avg_pct
        print(f"  Model-vs-actual gap in gross pnl_pct = {gap:+.4f}%")


def _report_study2_style(metrics: list[TradeMetrics]) -> None:
    print("\n===== STUDY 2 (with corrected model) — SL near-miss @ 1h =====")
    print("  MFE_R distribution among trades whose ACTUAL exit_reason='STOP_LOSS'")
    for label, subset in _slices(metrics):
        scope = label
        sl_trades = [m for m in subset if m.exit_reason == "STOP_LOSS"]
        if not sl_trades:
            print(f"\n[{scope}] n_sl=0")
            continue
        n = len(sl_trades)
        mfes = [m.mfe_r_final for m in sl_trades]
        n_04 = sum(1 for x in mfes if x >= 0.4)
        n_05 = sum(1 for x in mfes if x >= 0.5)
        n_075 = sum(1 for x in mfes if x >= 0.75)
        avg = mean(mfes)
        print(
            f"\n[{scope}] n_sl={n}  avg_MFE_R={avg:.3f}  "
            f"share>=0.4R={100.0 * n_04 / n:.1f}%  "
            f"share>=0.5R={100.0 * n_05 / n:.1f}%  "
            f"share>=0.75R={100.0 * n_075 / n:.1f}%"
        )


def _simulate_breakeven(m: TradeMetrics, trigger: float) -> tuple[str, float]:
    """Path-aware breakeven simulation for one trade + one trigger.

    Returns (outcome, pnl_pct) where outcome ∈
      {"unchanged_win", "unchanged_loss", "unchanged_timeout",
       "converted_loser", "sacrificed_winner", "still_win_after_trigger",
       "breakeven_from_timeout"}.

    Algorithm:
      1. If trigger never fired → outcome unchanged from baseline.
      2. If trigger fired at bar t_T:
         - Walk bars AFTER t_T (inclusive of t_T for same-bar breakeven).
         - Find first bar with low <= entry (breakeven exit at 0%).
         - Find first bar with high >= take_profit (actual TP exit).
         - Same-bar tiebreak: SL wins (matches production).
         - Result:
             * If neither event fires within remaining bars → breakeven-
               from-timeout (exit at actual pnl_pct floored at 0
               because the stop is now at entry).
             * If breakeven wins the race → 0% pnl.
                 - If baseline was 'loss' → converted_loser (positive delta).
                 - If baseline was 'win'  → sacrificed_winner (negative delta).
                 - If baseline was 'timeout' → depends: if actual pnl > 0,
                   we sacrificed a partial win; if <= 0, we neutralized.
             * If TP wins the race → still_win_after_trigger (exit at TP).
    """
    trg_bar = m.trigger_first_bar.get(trigger)
    if trg_bar is None:
        # Unchanged from baseline
        if m.model_outcome == "win":
            return "unchanged_win", m.model_pnl_pct
        if m.model_outcome == "loss":
            return "unchanged_loss", m.model_pnl_pct
        return "unchanged_timeout", m.model_pnl_pct

    # Trigger fired. Walk bars from trg_bar onward.
    entry = m.entry_price
    tp = m.take_profit
    be_bar: int | None = None
    tp_bar_after: int | None = None
    for i in range(trg_bar, len(m.bars)):
        b = m.bars[i]
        hit_be = b.low <= entry
        hit_tp = b.high >= tp
        if hit_be and be_bar is None:
            if hit_tp and tp_bar_after is None:
                # Same-bar tie: SL (breakeven now) wins.
                be_bar = i
                break
            be_bar = i
            break
        if hit_tp and tp_bar_after is None:
            tp_bar_after = i
            break

    if be_bar is not None:
        # Breakeven exit — 0%
        if m.model_outcome == "win":
            return "sacrificed_winner", 0.0
        if m.model_outcome == "loss":
            return "converted_loser", 0.0
        # timeout: floor negative timeouts at 0; keep positive timeouts
        # if breakeven fires after MFE peak but before drop, we've
        # accepted 0 by the stop-at-entry rule
        return "converted_loser" if m.model_pnl_pct <= 0 else "sacrificed_winner", 0.0
    if tp_bar_after is not None:
        # TP wins the race after trigger
        return "still_win_after_trigger", _pnl_pct_for_exit(entry, tp)
    # Neither event within bars — trade times out with actual pnl
    # but stop is at entry, so pnl >= 0.
    floored = max(0.0, m.model_pnl_pct)
    if m.model_outcome == "win":
        return "unchanged_win", m.model_pnl_pct
    return ("breakeven_from_timeout", floored)


def _report_item2_breakeven(metrics: list[TradeMetrics]) -> None:
    print("\n===== ITEM 2 — PATH-AWARE BREAKEVEN ON CORRECTED 1h MODEL =====")
    fee_pct = _fee_pct_round_trip()
    for label, subset in _slices(metrics):
        scope = label
        if not subset:
            continue
        # Baseline expectancy
        base_avg = mean(m.model_pnl_pct for m in subset)
        base_avg_af = base_avg - fee_pct
        n = len(subset)
        print(
            f"\n[{scope}] n={n}  baseline_1h_actualTP_exp_pnl_pct={base_avg:+.4f}%  "
            f"(after-fee {base_avg_af:+.4f}%)"
        )
        cols = (
            f"  {'trigger':>8}  {'triggered':>10}  {'conv_loss':>10}  "
            f"{'sacr_win':>10}  {'still_win':>10}  {'be_timeout':>11}  "
            f"{'exp%':>7}  {'exp%_afterfee':>13}  {'delta_af':>10}"
        )
        print(cols)
        for trg in BREAKEVEN_TRIGGERS:
            triggered = 0
            conv = 0
            sacr = 0
            still_win = 0
            be_to = 0
            new_pnls: list[float] = []
            for m in subset:
                outcome, pnl_pct = _simulate_breakeven(m, trg)
                if outcome.startswith("unchanged"):
                    new_pnls.append(pnl_pct)
                    continue
                triggered += 1
                if outcome == "converted_loser":
                    conv += 1
                elif outcome == "sacrificed_winner":
                    sacr += 1
                elif outcome == "still_win_after_trigger":
                    still_win += 1
                elif outcome == "breakeven_from_timeout":
                    be_to += 1
                new_pnls.append(pnl_pct)
            new_avg = mean(new_pnls)
            new_avg_af = new_avg - fee_pct
            delta_af = new_avg_af - base_avg_af
            print(
                f"  {trg:>8.2f}  {triggered:>10d}  {conv:>10d}  "
                f"{sacr:>10d}  {still_win:>10d}  {be_to:>11d}  "
                f"{new_avg:>+7.4f}  {new_avg_af:>+13.4f}  {delta_af:>+10.4f}"
            )


async def _fetch_all_native_tf(
    trades: list[TradeRow], client: httpx.AsyncClient,
) -> list[TradeMetrics]:
    """v5: fetch each trade on its OWN timeframe's bars.

    A 15m trade uses 15m bars; a 1h trade uses 1h bars. This matches
    exactly what production `check_exit` would have seen for the
    position — dissolves the v4 resolution-mismatch artifact where a
    mostly-15m population was simulated on 1h bars.
    """
    metrics: list[TradeMetrics] = []
    start = time.time()
    for i, t in enumerate(trades, 1):
        _interval, bar_ms = _TF_TO_INTERVAL.get(t.timeframe, (INTERVAL_1H, BAR_MS_1H))
        start_ms = t.opened_at_ms - bar_ms
        end_ms = t.closed_at_ms + bar_ms
        bars = await _fetch_bars_at_tf(
            client, t.symbol, start_ms, end_ms, timeframe=t.timeframe,
        )
        bars = [b for b in bars if t.opened_at_ms <= b.ts_ms <= t.closed_at_ms]
        if not bars:
            continue
        metrics.append(_compute_metrics(t, bars))
        if i % 50 == 0:
            print(f"  {i}/{len(trades)} processed (elapsed {time.time() - start:.0f}s)")
        await asyncio.sleep(FETCH_DELAY_S)
    return metrics


async def main() -> None:
    t0 = time.time()
    print("MFE/MAE curve probe v5 — native-TF (each trade on its OWN timeframe's bars)")
    trades = await _load_trades()
    top = sum(1 for t in trades if t.scope == "top20")
    bot = sum(1 for t in trades if t.scope == "ranks_21_30")
    tf_counts: dict[str, int] = {}
    for t in trades:
        tf_counts[t.timeframe] = tf_counts.get(t.timeframe, 0) + 1
    print(f"  loaded {len(trades)} trades ({top} top20, {bot} ranks_21_30)")
    print(f"  timeframe breakdown: {tf_counts}")

    print(f"\nFetching Binance klines per trade at native TF (~{FETCH_DELAY_S}s each) …")
    async with httpx.AsyncClient() as client:
        metrics = await _fetch_all_native_tf(trades, client)
    print(f"  fetched: {len(metrics)} trades with computed metrics")

    print("\n===== COVERAGE =====")
    print(f"  Total shadow_trades matching filter: {len(trades)}")
    for scope in ("top20", "ranks_21_30"):
        loaded = sum(1 for t in trades if t.scope == scope)
        computed = sum(1 for m in metrics if m.scope == scope)
        print(f"  [{scope}] loaded={loaded} computed={computed}")

    _report_item1_agreement(metrics)
    _report_study2_style(metrics)
    _report_item2_breakeven(metrics)

    print(f"\nTotal runtime: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
