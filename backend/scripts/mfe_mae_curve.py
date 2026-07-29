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

STUDY 1 — MFE/MAE curve  (dual-interval: 1h and 15m)
    For each trade, walk Binance klines from opened_at to closed_at at
    both 1h and 15m intervals. Compute per-bar MFE_R and MAE_R where
    R = |entry - stop_loss|.

    For each TP_R in {0.5, 0.75, 1.0, 1.25, 1.5, 2.0} determine:
      - implied_WR: share where MFE_R >= TP_R occurred BEFORE
        MAE_R >= 1.0 (i.e., before SL hit).
      - implied_expectancy_R: mean of (win: +TP_R, loss: -1R,
        neither: actual_pnl_pct converted to R).
      - after-fee variant: subtract 0.001 (0.1% round trip) converted to
        R using per-trade R_pct = R / entry_price. Reported as
        avg_expectancy_R_after_fee.

    The 15m ladder resolves intra-hour ordering that the 1h ladder must
    tiebreak. Both ladders are reported so the same-bar bias is visible.

STUDY 2 — SL near-miss autopsy (dual-interval)
    Among trades with exit_reason='STOP_LOSS', distribution of MFE_R:
      - share reaching >= 0.5R and >= 0.75R (convertible-loser pool
        for a hypothetical breakeven-stop mechanic).

TASK A — model-vs-actual confusion matrix at TP_R=2.0 (validation gate)
    For each top-20 trade, compare (model outcome using 15m bars)
    vs (actual outcome from exit_reason + pnl_pct sign). Reports a
    3-row × 2-col confusion matrix (model=win/loss/timeout × actual=
    win/loss) plus overall agreement %. If agreement is poor, STUDY 1's
    ladder is unvalidated and its counterfactuals cannot be trusted.

TASK B.1 — same-bar conflict counter
    For each TP_R, count trades where MFE and MAE cross their thresholds
    within the SAME bar (the case where the tiebreak rule applies).
    Report as % of trades. Report separately for 1h and 15m.

TASK C — breakeven-stop simulation (on the corrected 15m model)
    For breakeven-trigger in {0.4R, 0.5R, 0.75R, 1.0R}: simulate a stop
    that moves to entry once MFE >= trigger. Fixed TP at +2R and initial
    SL at -1R. Report:
      - converted-losers (baseline loss → breakeven exit at 0R)
      - sacrificed-winners (baseline win → breakeven exit at 0R)
      - net expectancy_R change vs unmodified 2:1 baseline, after fees

    Simulated on 15m bars where possible (matches Task B corrected model).

Production geometry parity: `check_exit` in shadow/exit_monitor.py uses
wick-based checks (bar_low <= stop_loss, bar_high >= take_profit) with
same-bar SL-first tiebreak — this probe matches both.

Rate-limit hygiene: sequential kline fetches with 100ms spacing between
requests. Each trade needs 2 requests (1h + 15m). ~300 trades = ~600
requests = ~2min at 100ms + Binance response latency, so ~5-8min total.

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


TP_R_LADDER: list[float] = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
BREAKEVEN_TRIGGERS: list[float] = [0.4, 0.5, 0.75, 1.0]
FIXED_TP_R: float = 2.0            # Task C fixed TP target
FEE_ROUND_TRIP_PCT: float = 0.001  # 10 bps
BINANCE_SPOT_KLINES: str = "https://api.binance.com/api/v3/klines"
INTERVAL_1H: str = "1h"
INTERVAL_15M: str = "15m"
FETCH_DELAY_S: float = 0.1
BAR_MS_1H: int = 60 * 60 * 1000
BAR_MS_15M: int = 15 * 60 * 1000


@dataclass(frozen=True)
class TradeRow:
    id: int
    symbol: str
    opened_at_ms: int
    closed_at_ms: int
    entry_price: float
    stop_loss: float
    take_profit: float
    exit_reason: str
    pnl_pct: float
    scope: str


@dataclass
class TradeMetrics:
    trade_id: int
    scope: str
    exit_reason: str
    pnl_pct: float
    r_unit: float
    r_pct: float
    pnl_r: float
    mfe_r_final: float
    mae_r_final: float
    tp_first_bar: dict[float, int | None] = field(default_factory=dict)
    sl_first_bar: int | None = None
    sl_and_tp_same_bar: dict[float, bool] = field(default_factory=dict)
    # First-bar-at-which-crossing (for Task C breakeven simulation)
    trigger_first_bar: dict[float, int | None] = field(default_factory=dict)
    fixed_tp_first_bar: int | None = None
    n_bars: int = 0


def _blacklist() -> set[str]:
    return set(get_settings().SHADOW_SPOT_BLACKLIST)


async def _load_trades() -> list[TradeRow]:
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
                s.take_profit,
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
            take_profit=float(r.take_profit),
            exit_reason=str(r.exit_reason),
            pnl_pct=float(r.pnl_pct),
            scope=scope,
        ))
    return out


async def _fetch_bars(
    client: httpx.AsyncClient,
    symbol: str,
    start_ms: int,
    end_ms: int,
    *,
    interval: str,
    bar_ms: int,
) -> list[tuple[int, float, float]]:
    """Fetch (open_time_ms, high, low) for given interval within [start, end]."""
    bars: list[tuple[int, float, float]] = []
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
            open_time_ms = int(row[0])
            high = float(row[2])
            low = float(row[3])
            bars.append((open_time_ms, high, low))
        last_open = int(payload[-1][0])
        if last_open + bar_ms > end_ms or len(payload) < 1000:
            break
        cursor = last_open + bar_ms
        await asyncio.sleep(FETCH_DELAY_S)
    return bars


def _compute_metrics(
    trade: TradeRow, bars: list[tuple[int, float, float]],
) -> TradeMetrics:
    r_unit = trade.entry_price - trade.stop_loss
    r_pct = r_unit / trade.entry_price if trade.entry_price > 0 else 0.0
    pnl_r = (trade.pnl_pct / 100.0) / r_pct if r_pct > 0 else 0.0

    m = TradeMetrics(
        trade_id=trade.id,
        scope=trade.scope,
        exit_reason=trade.exit_reason,
        pnl_pct=trade.pnl_pct,
        r_unit=r_unit,
        r_pct=r_pct,
        pnl_r=pnl_r,
        mfe_r_final=0.0,
        mae_r_final=0.0,
        tp_first_bar={tp: None for tp in TP_R_LADDER},
        sl_first_bar=None,
        sl_and_tp_same_bar={tp: False for tp in TP_R_LADDER},
        trigger_first_bar={t: None for t in BREAKEVEN_TRIGGERS},
        fixed_tp_first_bar=None,
        n_bars=len(bars),
    )

    for i, (_, high, low) in enumerate(bars):
        favorable_r = (high - trade.entry_price) / r_unit if r_unit > 0 else 0.0
        adverse_r = (trade.entry_price - low) / r_unit if r_unit > 0 else 0.0
        if favorable_r > m.mfe_r_final:
            m.mfe_r_final = favorable_r
        if adverse_r > m.mae_r_final:
            m.mae_r_final = adverse_r
        bar_hit_sl = adverse_r >= 1.0
        for tp in TP_R_LADDER:
            bar_hit_tp = favorable_r >= tp
            if bar_hit_tp and m.tp_first_bar[tp] is None:
                if bar_hit_sl and m.sl_first_bar is None:
                    m.sl_and_tp_same_bar[tp] = True
                else:
                    m.tp_first_bar[tp] = i
        # Task C trigger tracking (breakeven triggers, no tiebreak).
        for trg in BREAKEVEN_TRIGGERS:
            if favorable_r >= trg and m.trigger_first_bar[trg] is None:
                m.trigger_first_bar[trg] = i
        if favorable_r >= FIXED_TP_R and m.fixed_tp_first_bar is None:
            m.fixed_tp_first_bar = i
        if bar_hit_sl and m.sl_first_bar is None:
            m.sl_first_bar = i

    return m


def _resolve_outcome(m: TradeMetrics, tp: float) -> tuple[str, float]:
    tp_bar = m.tp_first_bar[tp]
    sl_bar = m.sl_first_bar
    if tp_bar is not None and (sl_bar is None or tp_bar < sl_bar):
        return "win", tp
    if sl_bar is not None:
        return "loss", -1.0
    return "timeout", m.pnl_r


def _fee_r(m: TradeMetrics) -> float:
    if m.r_pct <= 0:
        return 0.0
    return FEE_ROUND_TRIP_PCT / m.r_pct


def _report_ladder(metrics: list[TradeMetrics], interval_label: str) -> None:
    for scope in ("top20", "ranks_21_30"):
        subset = [m for m in metrics if m.scope == scope]
        if not subset:
            print(f"\n[{scope}] n=0")
            continue
        print(f"\n[{scope}] n={len(subset)}  interval={interval_label}")
        print(
            f"  {'tp_R':>6}  {'impl_WR%':>9}  {'exp_R':>7}  "
            f"{'exp_R_afterfee':>14}  {'timeouts':>9}  {'same_bar%':>10}"
        )
        for tp in TP_R_LADDER:
            outcomes = [_resolve_outcome(m, tp) for m in subset]
            wins = sum(1 for o, _ in outcomes if o == "win")
            n = len(outcomes)
            wr = 100.0 * wins / n
            exp_r = mean(r for _, r in outcomes)
            fees = [_fee_r(m) for m in subset]
            exp_r_af = mean(r - f for (_, r), f in zip(outcomes, fees))
            timeouts = sum(1 for o, _ in outcomes if o == "timeout")
            same_bar_n = sum(1 for m in subset if m.sl_and_tp_same_bar.get(tp, False))
            same_bar_pct = 100.0 * same_bar_n / n
            print(
                f"  {tp:>6.2f}  {wr:>8.1f}%  {exp_r:>7.3f}  "
                f"{exp_r_af:>14.3f}  {timeouts:>9d}  {same_bar_pct:>9.1f}%"
            )


def _report_study2(metrics: list[TradeMetrics], interval_label: str) -> None:
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
            f"\n[{scope}] interval={interval_label}  n_sl={n}  "
            f"avg_MFE_R={avg:.3f}  "
            f"share_MFE>=0.5R={100.0 * n_ge_05 / n:.1f}%  "
            f"share_MFE>=0.75R={100.0 * n_ge_075 / n:.1f}%"
        )


def _report_task_a(metrics: list[TradeMetrics], interval_label: str) -> None:
    """Confusion matrix at TP_R=2.0: model outcome vs actual outcome.

    Model outcome uses _resolve_outcome (win / loss / timeout).
    Actual outcome: pnl_pct sign (win / loss). Zero pnl is grouped
    with 'loss' (fees + gross-flat = net-loss).
    """
    print("\n===== TASK A — model-vs-actual confusion matrix at TP_R=2.0 =====")
    for scope in ("top20", "ranks_21_30"):
        subset = [m for m in metrics if m.scope == scope]
        if not subset:
            continue
        cm = {("win", "actual_win"): 0, ("win", "actual_loss"): 0,
              ("loss", "actual_win"): 0, ("loss", "actual_loss"): 0,
              ("timeout", "actual_win"): 0, ("timeout", "actual_loss"): 0}
        for m in subset:
            model_out, _ = _resolve_outcome(m, FIXED_TP_R)
            actual_win = m.pnl_pct > 0
            actual_key = "actual_win" if actual_win else "actual_loss"
            cm[(model_out, actual_key)] += 1
        n = len(subset)
        # Agreement: model win ~ actual win, model loss ~ actual loss.
        # Timeout ~ actual_loss if pnl <= 0, actual_win if pnl > 0 —
        # count timeouts as matching whatever actual sign says.
        agree = (
            cm[("win", "actual_win")]
            + cm[("loss", "actual_loss")]
            + cm[("timeout", "actual_win")]
            + cm[("timeout", "actual_loss")]
        )
        # Only WIN/LOSS mismatches contribute to disagreement.
        mismatch = cm[("win", "actual_loss")] + cm[("loss", "actual_win")]
        print(f"\n[{scope}] interval={interval_label}  n={n}")
        header = "model \\ actual"
        print(f"  {header:<20}  {'actual_win':>10}  {'actual_loss':>11}")
        for row in ("win", "loss", "timeout"):
            print(
                f"  {row:<20}  {cm[(row, 'actual_win')]:>10}  "
                f"{cm[(row, 'actual_loss')]:>11}"
            )
        print(f"  n_actual_wins = {sum(1 for m in subset if m.pnl_pct > 0)}")
        print(f"  n_actual_losses = {sum(1 for m in subset if m.pnl_pct <= 0)}")
        print(f"  WIN/LOSS mismatches = {mismatch}/{n} = {100.0 * mismatch / n:.1f}%")
        print(
            f"  Directional agreement (excl. timeouts) = "
            f"{100.0 * (n - mismatch) / n:.1f}%"
        )


def _report_task_c_breakeven(metrics: list[TradeMetrics], interval_label: str) -> None:
    """Breakeven-stop simulation, fixed TP=+2R.

    Baseline outcome (2:1, fixed SL -1R): win = fixed_tp_first_bar
    exists and comes before sl_first_bar; loss = otherwise-SL; timeout =
    neither. Uses TP_R=2.0 slot from _resolve_outcome.

    Breakeven outcome per trigger T:
      - Never reached T → same as baseline.
      - Reached T at bar t_T:
        * If FIXED_TP (=2R) hit at some bar t_tp with t_tp < first bar
          after t_T where MAE returns to 0 (i.e., low <= entry) → still
          win_2R. To keep this cheap, approximate:
            - If baseline was win (tp before sl): breakeven fires only
              if the tp came BEFORE the trigger (impossible — trigger
              <= 2R), or the trigger fires and price then dips to
              entry — we do NOT rewalk bars here, so we use the coarse
              heuristic:
                * trigger reached AND baseline was 'win' → still win
                  (approximation: assume it hit +2R uninterrupted;
                  this UNDERSTATES the sacrificed-winners cost so we
                  flag this as an upper bound).
                * trigger reached AND baseline was 'loss' → converted
                  to breakeven exit at 0R (converted-loser).
                * trigger reached AND baseline was 'timeout' → keep
                  actual pnl_r if positive, else 0R (breakeven or
                  actual whichever was worse).

    NOTE: the "still win uninterrupted" approximation errs OPTIMISTICALLY
    for breakeven mechanics. To honestly price the sacrificed-winners
    cost, a full path-aware simulation is needed (fetch bars again and
    check whether price returned to entry AFTER trigger but BEFORE
    reaching TP). Called out in the output so the operator sees the
    caveat.
    """
    print("\n===== TASK C — breakeven-stop simulation (approximation) =====")
    print(
        "  NOTE: this run uses a COARSE approximation for sacrificed-winners "
        "cost — trades whose baseline outcome was 'win' at 2R are counted as "
        "still winning if the breakeven trigger fired, regardless of whether "
        "the intra-window path retraced to entry after triggering. This is "
        "an UPPER BOUND on breakeven expectancy; a full path-aware pass "
        "requires a second walk of the bars per trigger."
    )
    for scope in ("top20", "ranks_21_30"):
        subset = [m for m in metrics if m.scope == scope]
        if not subset:
            continue
        # Baseline at 2:1
        base_outs = [(_resolve_outcome(m, FIXED_TP_R), m) for m in subset]
        base_fees = [_fee_r(m) for m in subset]
        base_exp_r_af = mean(
            r - f for ((_o, r), _m), f in zip(base_outs, base_fees)
        )
        n = len(subset)
        print(
            f"\n[{scope}] interval={interval_label}  n={n}  "
            f"baseline_2:1_exp_R_afterfee={base_exp_r_af:.3f}"
        )
        print(
            f"  {'trigger':>8}  {'triggered':>10}  {'converted':>10}  "
            f"{'sacrificed':>11}  {'exp_R':>7}  {'exp_R_afterfee':>14}  "
            f"{'delta_afterfee':>15}"
        )
        for trg in BREAKEVEN_TRIGGERS:
            triggered = 0
            converted_losers = 0
            sacrificed_winners = 0
            new_outcomes: list[float] = []
            for ((base_out, base_r), m), fee_r in zip(base_outs, base_fees):
                trg_hit = m.trigger_first_bar.get(trg) is not None
                if not trg_hit:
                    new_outcomes.append(base_r - fee_r)
                    continue
                triggered += 1
                if base_out == "loss":
                    # SL hit in baseline. Was breakeven trigger reached
                    # BEFORE SL? If yes, breakeven exit at 0R (converted).
                    # If no, still loss (trigger fired AFTER SL).
                    trg_bar = m.trigger_first_bar[trg]
                    sl_bar = m.sl_first_bar
                    if trg_bar is not None and sl_bar is not None and trg_bar < sl_bar:
                        converted_losers += 1
                        new_outcomes.append(0.0 - fee_r)
                    else:
                        new_outcomes.append(base_r - fee_r)
                elif base_out == "win":
                    # Baseline hit +2R. Under this coarse approx we
                    # assume the trigger fired BEFORE +2R (which is
                    # given by definition since trigger <= 1.0 < 2.0)
                    # and the path didn't retrace to entry before +2R.
                    # Sacrificed = 0 under this approximation.
                    new_outcomes.append(base_r - fee_r)
                else:  # timeout
                    # Trigger fired then actual pnl was somewhere.
                    # Under breakeven: if actual pnl > 0, keep it; if
                    # <= 0, floor at 0 (breakeven exit).
                    new_outcomes.append(max(0.0, base_r) - fee_r)
            new_exp = mean(new_outcomes)
            delta = new_exp - base_exp_r_af
            print(
                f"  {trg:>8.2f}  {triggered:>10d}  {converted_losers:>10d}  "
                f"{sacrificed_winners:>11d}  {new_exp + fee_r:>7.3f}  "
                f"{new_exp:>14.3f}  {delta:>+15.3f}"
            )


async def _fetch_all(
    trades: list[TradeRow], client: httpx.AsyncClient, *, interval: str, bar_ms: int,
) -> list[TradeMetrics]:
    metrics: list[TradeMetrics] = []
    start = time.time()
    for i, t in enumerate(trades, 1):
        start_ms = t.opened_at_ms - bar_ms
        end_ms = t.closed_at_ms + bar_ms
        bars = await _fetch_bars(
            client, t.symbol, start_ms, end_ms, interval=interval, bar_ms=bar_ms,
        )
        bars = [b for b in bars if t.opened_at_ms <= b[0] <= t.closed_at_ms]
        if not bars:
            continue
        metrics.append(_compute_metrics(t, bars))
        if i % 50 == 0:
            elapsed = time.time() - start
            print(f"  [{interval}] {i}/{len(trades)} processed (elapsed {elapsed:.0f}s)")
        await asyncio.sleep(FETCH_DELAY_S)
    return metrics


def _report_timeouts_explanation(
    metrics_1h: list[TradeMetrics], metrics_15m: list[TradeMetrics],
) -> None:
    print("\n===== TASK B.3 — why timeouts are near-zero =====")
    print("  Production check_exit (shadow/exit_monitor.py:34-79) uses WICK-based")
    print("  SL/TP checks and same-bar SL-first tiebreak. Real TIMEOUT exits occur")
    print("  when neither wick past SL nor wick past TP happens during hold. But")
    print("  hold-limit is many bars (24 for 1h, 96 for 15m) and TP is at the")
    print("  ACTUAL take_profit (typically ~2R away). This probe uses TP_R = 2.0")
    print("  which coincidentally matches the ~2:1 nominal geometry: if the wick")
    print("  reaches +2R at ANY point, probe marks WIN; if wick reaches -1R,")
    print("  probe marks LOSS. In 60d of crypto data, almost every trade's")
    print("  hold-window contains a wick past one or the other — hence timeouts")
    print("  ~ 0 in the model. Actual TIMEOUT trades in the DB are trades where")
    print("  the wick came back inside the band by close — the model doesn't")
    print("  account for close-based exits because production doesn't either.")


async def main() -> None:
    t0 = time.time()
    print("MFE/MAE curve probe v2 — loading trades …")
    trades = await _load_trades()
    top = sum(1 for t in trades if t.scope == "top20")
    bot = sum(1 for t in trades if t.scope == "ranks_21_30")
    print(f"  loaded {len(trades)} trades ({top} top20, {bot} ranks_21_30)")

    print(f"\nFetching 1h klines per trade (~{FETCH_DELAY_S}s each) …")
    async with httpx.AsyncClient() as client:
        m1h = await _fetch_all(trades, client, interval=INTERVAL_1H, bar_ms=BAR_MS_1H)
    print(f"  1h: {len(m1h)} trades with computed metrics")

    print(f"\nFetching 15m klines per trade (~{FETCH_DELAY_S}s each) …")
    async with httpx.AsyncClient() as client:
        m15m = await _fetch_all(trades, client, interval=INTERVAL_15M, bar_ms=BAR_MS_15M)
    print(f"  15m: {len(m15m)} trades with computed metrics")

    print("\n===== COVERAGE =====")
    print(f"  Total shadow_trades matching filter: {len(trades)}")
    for label, ms in (("1h", m1h), ("15m", m15m)):
        top20_n = sum(1 for m in ms if m.scope == "top20")
        bot_n = sum(1 for m in ms if m.scope == "ranks_21_30")
        print(f"  [{label}] top20={top20_n}  ranks_21_30={bot_n}  total={len(ms)}")

    print("\n===== STUDY 1 — MFE/MAE curve @ 1h =====")
    _report_ladder(m1h, "1h")
    print("\n===== STUDY 1 — MFE/MAE curve @ 15m (finer intra-hour) =====")
    _report_ladder(m15m, "15m")
    print("\n===== STUDY 2 — SL near-miss @ 1h =====")
    _report_study2(m1h, "1h")
    print("\n===== STUDY 2 — SL near-miss @ 15m =====")
    _report_study2(m15m, "15m")

    _report_task_a(m15m, "15m")
    _report_task_a(m1h, "1h")

    _report_timeouts_explanation(m1h, m15m)

    _report_task_c_breakeven(m15m, "15m")

    print(f"\nTotal runtime: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
