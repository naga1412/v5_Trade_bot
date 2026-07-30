# Breakeven-stop mechanic — design (variant-lane spec, AUTHORIZED)

**Status: AUTHORIZED for SHADOW-ONLY implementation (2026-07-30 later
same day), amended to PARALLEL VARIANT LANE spec.**

The original v1 of this doc (2026-07-30 earlier) proposed a BEFORE/AFTER
replacement of the exit logic. The operator's implementation ruling
2026-07-30 rejected that as strictly-worse than a paired same-signal
comparison and mandated the variant-lane approach below. Reason: a
paired same-entry comparison eliminates the regime confound that a
before/after cannot (before/after mixes the mechanic's effect with any
market-regime drift between the two windows), and it delivers a
definitive answer at n=100 in ~55 days with NO model assumptions.

Prerequisite CLEARED 2026-07-30: v5 native-TF probe (run 30568470369)
re-ran the 0.50R breakeven simulation with each trade on its own
timeframe's bars. Top-20/atr_bound n=174, Δ_af = +0.338% (up ~12% from
v4's 1h-only-bars result of +0.302%). Trigger ranking unchanged
(0.50R still winner). All 4 tested triggers positive. Delta materially
positive → operator's authorization gate cleared.

**Live enablement remains NOT authorized.** Standing rule:
`live_capacity_expansion_rule` — live expansion requires shadow
demonstrating positive net edge worth capturing; this doc's variant lane
IS that demonstration.

## Origin

Phase 1 MFE study (2026-07-28 → 2026-07-29) surfaced two findings:

1. STUDY 1 — fixed TP_R geometry is flat across {0.5R…2.0R} at n=166:
   `exp_R` span is only 0.077R. 2:1 is retained by default, not by
   demonstrated superiority. Recorded in `docs/superpowers/decisions/
   2026-07-29-study-1-flat-geometry-ladder.md`.

2. STUDY 2 — SL near-miss autopsy showed top-20 SL-exit trades have
   `avg_MFE_R = 0.957` — the average loser came within a whisker of 1R
   before reversing. `share_MFE ≥ 0.5R = 69.2%`.

The mechanic proposed here converts a fraction of those "convertible
losers" from −1R to 0R by moving the stop to entry once MFE crosses a
trigger threshold. Path-aware v3 probe simulation on the corrected 1h
model (probe run 30441229244, 2026-07-29) gave the following net
after-fee expectancy deltas for top-20:

| trigger | triggered | conv_loss | sacr_win | Δ after-fee |
|---|---|---|---|---|
| 0.40R | 137 | 99 | 30 | +0.169% |
| **0.50R** | **130** | **92** | **27** | **+0.212% ← candidate** |
| 0.75R | 106 | 68 | 26 | +0.019% |
| 1.00R | 94 | 56 | 25 | −0.036% |

Chosen candidate: **0.50R trigger**. Highest net delta after honest
sacrificed-winners accounting. 0.40R fires more often but sacrifices 3
more winners; 0.75R and 1.00R let too many convertibles slip.

## What the mechanic does — PARALLEL VARIANT LANE

**Current exit behavior** (from `backend/app/shadow/exit_monitor.py:34-79`,
`check_exit`): each closed candle checks `bar_low ≤ pos.stop_loss` (SL)
and `bar_high ≥ pos.take_profit` (TP). Same-bar tie: SL first.
`pos.stop_loss` is set at open-time by `SignalEvaluator` in
`backend/app/shadow/engine.py:159-166` as `max(entry − 1.5·ATR,
entry × 0.95)` and never mutated.

**Variant lane design (operator ruling 2026-07-30):** every qualifying
shadow signal is booked TWICE simultaneously against the same entry:
- **BASELINE variant** — current exit logic unchanged (`stop_loss` never
  mutated, existing `check_exit` semantics).
- **BREAKEVEN-0.50R variant** — after MFE >= 0.50R, `stop_loss`
  mutates to `entry_price`; otherwise identical to baseline.

Both variants share entry_price, take_profit, opened_at, and receive the
same bar events. They diverge only on which SL applies. The result is
a **paired same-signal comparison**: for every trade in the population,
we get both `(baseline_pnl, variant_pnl)`, so any regime drift affects
both equally and the delta isolates the mechanic's effect exclusively.

**Why this is strictly better than before/after** (superseding the
original v1 plan): before/after mixes the mechanic's effect with any
market-regime drift between the pre-flip and post-flip windows. A hot
volatility week before-flip vs a calm week post-flip would confound
the reading regardless of the actual mechanic quality. Paired same-
entry eliminates this. At the reference-cohort rate (15m atr_bound ~1.7
trades/day) the paired delta reaches n=100 in ~55 days with SE tight
enough to decide.

**Independence guarantees the variant lane MUST maintain:**
- The BASELINE lane's `shadow_trades` rows, cooldowns, and downstream
  reporting are BIT-IDENTICAL to today's behavior. A reader who queries
  `SELECT * FROM shadow_trades WHERE variant IS NULL OR variant = 'baseline'`
  sees exactly what production sees today.
- The BREAKEVEN variant lane writes its own `shadow_trades` rows with
  a distinct `variant` value; these rows do NOT participate in
  `shadow_cooldowns` (cooldown remains scoped to baseline outcomes so
  the two lanes don't inhibit each other via cross-lane blocking).
- The LIVE path (`live_prediction.py`, `dispatcher.py`, `live_trades`)
  is UNAFFECTED. Live cooldown continues to key on baseline `shadow_trades`
  data alone.

**Same-bar tiebreak preserved**: for both lanes independently the SL-first
convention holds. See "Same-bar edge case" below.

**Sacrificed-winner accounting is FREE under the variant lane**: no
model estimate needed. Every trade whose baseline was a TP win but whose
variant exits at breakeven is directly observable in the data as
`(variant='baseline' → TP, variant='breakeven_0.5r' → BREAKEVEN)`.

## Hook location

**Site A — dual-track exits in `_maybe_close_position`**
(`backend/app/shadow/worker.py:405-450`):

Each `ShadowPosition` gains a lightweight variant descriptor. When a
new candle arrives:

1. Update `peak_mfe_r = max(peak_mfe_r, (candle.high − pos.entry_price)
   / (pos.entry_price − pos.initial_stop_loss))` — a single per-position
   float held in memory.
2. For each variant in `{baseline, breakeven_0.5r}` — call `check_exit`
   with the variant-appropriate stop_loss:
   - baseline: `stop_loss = pos.initial_stop_loss` (unchanged)
   - breakeven_0.5r: `stop_loss = pos.entry_price if peak_mfe_r >= 0.50
     else pos.initial_stop_loss`
3. Each variant's exit decision (or None if still open) is recorded
   independently. When either variant fires an exit, persist that
   variant's `shadow_trades` row with `variant = <variant_name>`.
4. A variant continues to be tracked until IT exits, regardless of
   whether the OTHER variant has already exited.

**Site B — `check_exit` gains a stop_loss parameter**
(`backend/app/shadow/exit_monitor.py:34`): current signature takes
`pos`. New signature: `check_exit(pos, *, bar_high, bar_low, bar_close,
override_stop_loss: float | None = None)`. When `override_stop_loss`
is None (all existing callers), behavior is bit-identical. When provided,
that value is used in place of `pos.stop_loss` for the SL check only.
`pos.stop_loss` itself is NEVER mutated.

This means:
- No mutation of shared position state → no risk of variant lanes
  interfering with each other.
- Existing tests + call sites remain valid without changes.
- Variant logic lives entirely inside the worker, not in `check_exit`.

**Site C — `ShadowPosition` gets one new field: `initial_stop_loss`**
(`backend/app/shadow/engine.py:64-73`). Currently `stop_loss` is
immutable-in-practice. Add `initial_stop_loss: float` populated at
`from_signal` time from `stop_loss`. Legacy call sites unchanged
(`stop_loss` value is unchanged). This field is used ONLY by the
variant-lane logic; baseline continues to reference `stop_loss`.

## Same-bar edge case (both lanes)

For the BASELINE lane: convention unchanged — SL-first tiebreak per
`exit_monitor.py:55`.

For the BREAKEVEN variant lane: if in one bar MFE crosses trigger AND
low touches original SL AND high touches TP — process in this order:
1. Update `peak_mfe_r` from `candle.high` first.
2. Because `peak_mfe_r >= 0.50` is now true, override_stop_loss = entry.
3. `check_exit` with `override_stop_loss = entry`: `bar_low ≤ entry`
   fires SL exit at entry price (0R).
4. TP check is subordinate to SL check via the existing SL-first rule.

This means variant SL fires BEFORE variant TP in same-bar contention,
matching production convention. The v5 probe uses this exact rule; the
+0.338% delta is measured under it.

## Persistence & schema

**shadow_trades — one new column: `variant TEXT`** (nullable, defaults
to NULL for legacy rows). Values: `'baseline'` or `'breakeven_0.5r'`.
NULL means pre-variant-lane row (all existing history).

Reporting queries filter by variant:
- Baseline behavior: `WHERE variant IS NULL OR variant = 'baseline'`
- Variant comparison: `WHERE variant IN ('baseline', 'breakeven_0.5r')`
  joined on `signal_id` for the paired comparison.

Standard queries + memory (`v5-trade-bot` skill's Standard Diagnostic
Queries block) must be amended to filter by `variant` — a follow-up
docs PR bundling the skill update with the implementation ship.

**Column class**: `NON_HASHED_ALLOW_LIST` per `app/db/audit.py` — the
variant discriminator does NOT participate in the audit hash chain
(same class as the PR1 analytics columns). Rationale: variant is a
scope tag, not a data value; hashing it would break chains on legacy
rows where variant is NULL.

Alembic migration `NNNN_shadow_trades_variant_column`:
```sql
ALTER TABLE shadow_trades ADD COLUMN variant TEXT;
-- No CHECK constraint on values initially — a later PR can add one
-- once we're sure the enum is stable. Nullable is intentional: legacy
-- rows have no variant.
```

**`ExitReason` enum** at `backend/app/shadow/exit_monitor.py`: add a
new value `BREAKEVEN`. Alembic migration extending the CHECK constraint
on `shadow_trades.exit_reason` to include `'BREAKEVEN'`. Standard pattern
per the 2026-05-26 lesson (`HYBRID_AUTO_SCORE_THRESHOLD` /
`APPROVAL_MAX_PRICE_DRIFT` migrations).

Combined alembic migration: single file adding both `variant` column
and extending the `exit_reason` CHECK.

## Settings

New config in `backend/app/config.py`:

```python
SHADOW_BREAKEVEN_VARIANT_LANE_ENABLED: bool = False
SHADOW_BREAKEVEN_TRIGGER_R: float = 0.50
```

Default OFF so shipping this is a zero-behavior-change deploy. When
False, the shadow worker's exit path is bit-identical to today
(baseline lane only, `variant='baseline'` set on new rows post-migration
but not written when the flag is off — see "Verified-promotion named
check" below).

Operator flips `SHADOW_BREAKEVEN_VARIANT_LANE_ENABLED=True` post-soak.
No live-side setting exists. No live-side parity in this design;
`live_capacity_expansion_rule` requires shadow to demonstrate a
positive net edge FIRST.

## Measurement plan — paired same-signal comparison

**Definitive comparison at n≥100 in ~55 days at 15m atr_bound top-20
rate**, per operator ruling:

1. **Post-flip (SHADOW_BREAKEVEN_VARIANT_LANE_ENABLED=True)** — every
   new closed shadow position writes TWO rows: `variant='baseline'`
   and `variant='breakeven_0.5r'`. Both share `signal_id` and
   `entry_price`.
2. **Continuous measurement query**:
   ```sql
   SELECT
     COUNT(*) AS n,
     AVG(b.pnl_pct) AS baseline_avg,
     AVG(v.pnl_pct) AS variant_avg,
     AVG(v.pnl_pct - b.pnl_pct) AS delta_avg,
     STDDEV(v.pnl_pct - b.pnl_pct) / SQRT(COUNT(*)) AS delta_se
   FROM shadow_trades b
   JOIN shadow_trades v ON b.signal_id = v.signal_id
   WHERE b.variant = 'baseline'
     AND v.variant = 'breakeven_0.5r'
     AND b.direction = 'LONG'
     AND b.entry_score >= 0.36
     AND b.closed_at >= NOW() - INTERVAL '60 days'
     AND b.symbol NOT IN (<SHADOW_SPOT_BLACKLIST>)
   ```
3. **Split by cap_bound / atr_bound per methodology memo**.
4. **Report weekly**: rolling n, `delta_avg`, `delta_se`. Once n≥100
   on the reference cohort (15m atr_bound top-20) AND `delta_avg -
   1*delta_se > 0`, the mechanic has demonstrated positive net edge
   worth capturing. Only then propose live enablement per the standing
   `live_capacity_expansion_rule`.
5. **Ship-or-revert on shadow**: if at n≥100 the `delta_avg + 1*delta_se
   < 0`, revert (flip flag OFF; keep the code path for future re-test).
   If SE overlaps zero at n=100, extend observation to n=200 before
   deciding.

**Aug 3-10 concern gone**: no before/after regime confound to worry
about, so the variant lane can be enabled the day the code ships. No
window contamination.

## Risks & caveats

**Sacrificed-winner accounting is now FREE.** Under variant-lane paired
comparison, every sacrificed-winner is directly observable in the data
(a signal_id with `baseline=TAKE_PROFIT` and `variant=BREAKEVEN` on the
matched row). No model estimate needed. This corner of the v1 spec that
required probe upper-bound approximation is dissolved.

**Same-bar resolution (v5 native-TF)**: v5 measures at 15m/1h native
bars, delta +0.338% at 0.50R top-20/atr_bound n=174. The variant lane
uses the SAME check_exit logic on the SAME bars in prod, so any residual
same-bar tiebreak effect is exactly reproduced (not merely approximated).
Zero model uncertainty carries forward from probe to prod.

**No cross-TF cooldown** (FU-37): breakeven doesn't help a symbol that
gets stopped 4× in 18h across TF lanes — each stop still happens
before its trigger fires. The mechanic amplifies signal from good
setups; it does not defend against structural cooldown gaps. FU-37
remains a separate follow-up item.

**Baseline drift (mitigated by variant lane)**: variant delta measured
per-signal is regime-invariant. A hot week affects both lanes equally.
The paired `AVG(v.pnl_pct - b.pnl_pct)` is the honest delta regardless
of what the baseline drifts to.

**n=100 target in ~55 days**: reference cohort (15m atr_bound top-20)
runs at 1.7 trades/day. Both lanes book on every trade so paired-n
accumulates at the same rate.

**Live path unaffected**: live continues to key on baseline
`shadow_trades` rows only. No `live_exit_monitor` change in this design.
Live enablement of the mechanic (if ever) requires:
1. Shadow variant lane demonstrates positive net edge (n≥100 with SE
   clear of zero).
2. FU-37 cross-TF cooldown gap addressed OR argued moot for live's
   1h-only lane.
3. `binance_live.py` `modify_stop_order` helper (cancel + replace
   STOP_MARKET). Not needed for shadow-only ship.

## Estimated effort

- Shadow variant-lane implementation:
  - `engine.py` `ShadowPosition.initial_stop_loss` field + `peak_mfe_r`
    tracker: ~2h
  - `exit_monitor.py` `override_stop_loss` kwarg + `ExitReason.BREAKEVEN`
    enum: ~2h
  - `worker.py` dual-track exit loop + variant-tagged persistence: ~3h
  - `payload_builders.py` `variant` column threading: ~1h
  - Alembic migration (add `variant` col + extend `exit_reason` CHECK): ~1h
  - Standard-diagnostic-query skill memory amend: ~1h
  - Unit tests for baseline-untouched + variant paths + same-bar tie: ~4h
  - Integration test for paired-row persistence: ~2h
  - Total: **~2 days**
- Documentation (this doc amendment + decision-doc appendix): ~1h

**Grand total**: ~2 days shadow-only + soak window (24h behavior-
changing on shadow, must span a nightly cycle).

**Verified-promotion named check** (per operator ruling): within the
first hour post-deploy AND flag flip, `SELECT count(*), variant FROM
shadow_trades WHERE opened_at > <deploy_time> GROUP BY variant` must
return exactly one row per baseline signal with `variant='baseline'`
AND one row per baseline signal with `variant='breakeven_0.5r'`,
matched on `signal_id`. Divergent `exit_reason` and `pnl_pct` values
between the two rows within the hour prove the mechanic is active
and producing paired data. If either side is silent or the match rate
< 100%, revert immediately.

## Non-goals for this design

- Adjustable trigger per symbol / per volatility class — 0.50R is a
  single global constant. Per-symbol calibration is a follow-up study
  worth ≥ 60d of shadow data with breakeven ON.
- Trailing stop after breakeven — this design fixes the stop at entry
  once armed. Trailing is a separate mechanic that requires its own
  path-aware simulation.
- Multi-tier breakeven (arm at 0.5R then 1.0R then 1.5R) — 0.50R is
  the single-step candidate. Multi-tier is a variance-reducing
  extension worth testing AFTER single-step ships and stabilizes.

## Open questions for operator review (variant-lane spec)

Most of the v1 open questions are now MOOT under the variant-lane
approach:
- Same-bar tiebreak: use production convention (SL-first), matches v5
  probe, no design decision needed.
- `stop_loss_original` optional column: NOT NEEDED — variant-lane data
  makes converted-vs-sacrificed directly readable from the paired
  `shadow_trades` rows.
- Live parity: NOT IN SCOPE — shadow-only per operator ruling. Live
  requires shadow-first demonstration of positive net edge.
- Trigger config: hardcoded 0.50R.
- WR denominator for breakeven exits: apply to variant lane only;
  baseline lane's WR semantics unchanged. Design decision: `BREAKEVEN`
  exits count as NEITHER (WR reflects TP-hit fraction on the variant
  lane; a BE exit is a "neutralized loss," different class).

Remaining question:
1. **Cutover for cooldown attribution**: the design says shadow
   cooldowns key on baseline outcomes so the two lanes don't
   cross-inhibit. Alternative: cool down separately per-variant.
   Recommend baseline-only — simpler, and cooldowns are a downstream
   consumer of "what happened" (baseline behavior is the reference).

## Cross-references

- `docs/superpowers/decisions/2026-07-29-study-1-flat-geometry-ladder.md`
  — STUDY 1 verdict this mechanic addresses.
- `backend/docs/KNOWN_ISSUES.md` FU-37 — the cross-TF cooldown gap
  that limits this mechanic's protection (SHADOW-side, per FU-37
  correction 2026-07-30).
- `backend/scripts/mfe_mae_curve.py` — v5 probe (run 30568470369) that
  measured the +0.338% path-aware delta at 0.50R on 15m atr_bound n=174.
- `docs/superpowers/decisions/2026-07-28-fleet-cap-top-20-ratified.md`
  — the fleet-cap ratification that defines the reference cohort.
- Memory `aug_validation_top_20_only` — the CANCELLED Aug validation
  and the continuous-shadow measurement replacement plan.
- Memory `live_capacity_expansion_rule` — the standing rule that gates
  live enablement of this mechanic.
- Memory `fee_lever_maker_entry_adverse_selection` — the parallel fee-
  side lever; not addressed in this design.
