# Breakeven-stop mechanic — design draft

**Status: AMENDED 2026-07-31.** Docs → implementation. See
"Amendment 3" below for the current architecture (variant lane,
separate table, dual triggers). Sections below "Amendment 3" and
above "Persistence & schema (AMENDED)" are the original 2026-07-30
draft, retained for context but superseded on the persistence path
and the measurement plan.

## Amendment 3 — variant lane, separate table, dual triggers (2026-07-31)

Operator has ruled and authorized this shape:

1. **Variant lane, not before/after cutover.** For every base signal
   the shadow engine ordinarily produces, the same base signal is
   ALSO simulated under one or more breakeven-armed alternative exit
   rules. Base row still lands in `shadow_trades`; variant rows land
   in a NEW table `shadow_trade_variants`. Removes regime confound
   inherent in a temporal A/B (measurement lens vs strategy change).

2. **Separate table `shadow_trade_variants`.** Not additional rows on
   `shadow_trades`. Reason: adding rows to `shadow_trades` silently
   corrupts every consumer that does not filter on variant (analytics,
   dashboards, universe snapshots, exit-reason histograms, reference-
   cohort computations). Separate table keeps blast radius zero on
   existing readers.

3. **Two triggers ship together: 0.40R AND 0.50R.** Each base signal
   produces TWO variant rows (one per trigger). This settles the
   trigger-selection question in the same measurement cycle instead
   of another 2-3 month wait (0.43 trades/day live shadow throughput
   → ~93 days for n=40 comparable pairs on a single trigger). Cost
   is one additional row + one additional pure-function call per base
   close. Reversible: drop 0.40R lane later by setting its config to
   empty list.

4. **Variant lane writes NO cooldowns** and does **NOT touch
   `open_shadow_positions`** — confirmations required by operator
   (destroying the pairing otherwise). Implementation constraint:
   the variant compute path must not call `set_cooldown`, must not
   read/write `open_shadow_positions`, and must not participate in
   any per-user position counter. Shadow already has no per-user
   `max_concurrent_positions` (unlike live), so this is trivially
   satisfied for position limits; cooldown avoidance is enforced by
   architecture (variant persist is a separate function that only
   touches `shadow_trade_variants`).

### Variant lane architecture

**Simulation timing**: at BASE-close time, not at each candle. When
the base position closes normally (persist_closed_trade path in
`shadow/worker.py::_maybe_close_position`), the worker replays the
same bar history through a pure `simulate_variant_exit(...)` function
for each active trigger. Each variant produces its own
(exit_price, exit_reason, exit_ts, bars_held, pnl_pct) tuple.

**Bar history buffer**: `ShadowPosition` gains an in-memory-only
`bar_history: list[BarSnapshot]` cleared on close. Each candle
processed by `_maybe_close_position` (whether it triggers an exit or
not) appends a snapshot `(ts, high, low, close)`. Size cap: 500
entries (15m × 500 = ~5 days, exceeds any practical hold before
timeout). Not persisted; not audit-chained; not touched on
worker restart (positions loaded from DB have empty buffer and
therefore emit no variants — acceptable, since the pairing only
needs to hold for positions that open+close in the same process
lifetime).

**Pure simulator function**: `app/shadow/breakeven_variant.py::
simulate_variant_exit(entry_price, initial_stop_loss, take_profit,
direction, trigger_r, bar_history) -> VariantOutcome`. Walks the
buffer in order, tracks peak MFE in R units, arms breakeven when
peak crosses `trigger_r`, then continues with `stop_loss = entry`.
Same-bar tiebreak: SL-first on the ORIGINAL stop (matches production
convention at `exit_monitor.py:55`). Returns
`VariantOutcome(exit_price, exit_reason, exit_ts, bars_held,
pnl_pct, armed_bar_index | None)`.

**Persist at base close**: after `persist_closed_trade` + `set_cooldown`
succeed, the worker computes variants and inserts them in a separate
best-effort try/except so a variant persistence failure never breaks
the base close. Variant rows carry `base_shadow_trade_id` FK back
to the base row for join-based analysis.

### `shadow_trade_variants` table (schema)

```
id                   BIGSERIAL PRIMARY KEY
base_shadow_trade_id BIGINT NOT NULL REFERENCES shadow_trades(id)
                       ON DELETE CASCADE
                     -- FK back to the base row that generated this variant
variant_name         TEXT NOT NULL
                     -- 'breakeven_0.40R' / 'breakeven_0.50R' initially
trigger_r            NUMERIC(6,4) NOT NULL
                     -- 0.4000 / 0.5000
armed                BOOLEAN NOT NULL
                     -- True iff peak MFE crossed trigger_r before exit
exit_price           NUMERIC(20,10) NOT NULL
exit_reason          TEXT NOT NULL
                     -- 'TAKE_PROFIT' | 'STOP_LOSS' | 'TIMEOUT' | 'BREAKEVEN'
exit_ts              TIMESTAMPTZ NOT NULL
bars_held            INT NOT NULL
pnl_pct              NUMERIC(10,6) NOT NULL
                     -- gross % vs entry, same formula as shadow_trades.pnl_pct
created_at           TIMESTAMPTZ NOT NULL DEFAULT now()

UNIQUE (base_shadow_trade_id, variant_name)  -- one row per (base, variant)
INDEX ON (base_shadow_trade_id)
```

**Hash-chain status**: NOT chained. Rationale: variants are derived
data from bar history the audit chain doesn't cover anyway, and the
base row IS chained (`shadow_trades` in `HASHED_TABLES`). The
integrity of the base signal (entry/SL/TP/direction/inputs_hash) is
already protected. Recomputing variants from bar history is not
supported (buffer is in-memory only), so a hash chain would only
detect tampering, not enable replay. Analytics-only table → skip.

### `ExitReason.BREAKEVEN` still added, but only in the variant path

The `ExitReason` enum in `app/shadow/exit_monitor.py` still gains
`BREAKEVEN` (variant-lane emits it). The `shadow_trades.exit_reason`
CHECK constraint does NOT need extending because base rows never
carry `BREAKEVEN` (base lane behavior is unchanged). The
`shadow_trade_variants.exit_reason` column uses its own CHECK
that includes `BREAKEVEN`.

### Settings

```python
# Turns the variant lane on/off wholesale (default OFF).
SHADOW_BREAKEVEN_VARIANTS_ENABLED: bool = False

# List of trigger R values to simulate. Default: dual lane.
# Empty list is equivalent to _ENABLED=False.
SHADOW_BREAKEVEN_VARIANT_TRIGGERS_R: list[float] = [0.40, 0.50]
```

Live-side is unchanged from the original design — this amendment
covers shadow-only. Live parity remains a separate follow-up
gated on the confirmations from FU-37 and LIVE_COOLDOWN_ENABLED.

### Measurement plan (replaces "Before/after measurement plan")

**No pre-flip / post-flip cutover.** The variant lane runs
continuously the moment `SHADOW_BREAKEVEN_VARIANTS_ENABLED=True`.
Analysis is paired-comparison:

- For each base row `b` in the observation window: retrieve
  `shadow_trade_variants` rows keyed by `b.id`.
- Compute per-variant Δ_pnl vs base: `pnl_pct_variant - pnl_pct_base`.
  These are PAIRED observations on the same signal → no regime
  confound → SE shrinks vs. two independent means.
- Report mean Δ, SE, n, and share_armed per (variant, TF, window).
- Ship-or-keep decision: whichever trigger's cumulative Δ exceeds
  its SE with the right sign becomes the recommended live-side
  trigger. If both survive, prefer higher n_armed × Δ product.

Sample-size acceleration: because paired deltas have lower SE than
independent means, meaningful measurement is expected at n≈40 pairs
per variant (~2 months at current 0.43 trades/day live shadow rate),
vs. the ~6 months required for two-sample comparison at similar
power.

### Open questions from original design — RESOLVED

1. **Option A vs Option B same-bar tiebreak**: **Option B** (SL-first
   on original stop) — matches production convention, no code
   change to `check_exit`.
2. **`stop_loss_original` column**: N/A — variant rows carry
   `trigger_r` and `armed` directly; base rows are unchanged.
3. **Live parity in same PR**: **Separate follow-up**. Ship shadow
   variants first, accumulate ~2 months of paired data, then live.
4. **Trigger config hardcoded vs per-TF**: **Global list** in
   `SHADOW_BREAKEVEN_VARIANT_TRIGGERS_R`. Per-TF is a future
   refinement once we know which trigger wins on which TF.
5. **WR denominator for BE exits**: BREAKEVEN counts as **neither
   win nor loss** in dashboards. The paired-Δ methodology above
   doesn't touch WR; it works on avg_pnl_pct directly.

### Non-goals for this amendment

- Trailing after breakeven: still explicitly out of scope.
- Multi-tier breakeven (0.5R then 1.0R): still out; the dual-lane
  0.40R vs 0.50R is a TRIGGER selection experiment, not a multi-tier
  mechanic.

### Cross-references

- `docs/superpowers/decisions/2026-07-29-study-1-flat-geometry-ladder.md`
- `backend/docs/KNOWN_ISSUES.md` FU-37 (live-side prerequisite)
- `backend/scripts/mfe_mae_curve.py` (v5 native-TF probe that
  produced the +0.338% Δ_af at 0.50R, n=174)
- Operator ruling 2026-07-31 (this session): "TABLE DECISION IS
  SETTLED: separate table (shadow_trade_variants) ... variant-lane
  exits write NO cooldowns and do NOT count toward position limits."

---

## Original draft (2026-07-30) — superseded on persistence and measurement paths

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

## What the mechanic does

**Current exit behavior** (from `backend/app/shadow/exit_monitor.py:34-79`,
`check_exit`): each closed candle checks `bar_low ≤ pos.stop_loss` (SL)
and `bar_high ≥ pos.take_profit` (TP). Same-bar tie: SL first.
`pos.stop_loss` is set at open-time by `SignalEvaluator` in
`backend/app/shadow/engine.py:159-166` as `max(entry − 1.5·ATR,
entry × 0.95)` and never mutated.

**Proposed change**: once the position's peak favorable excursion
(highest observed price − entry, expressed in R units) reaches
`BREAKEVEN_TRIGGER_R × R_unit` (initially `R_unit = |entry − initial_SL|`,
0.50R), mutate `pos.stop_loss` to `pos.entry_price` (breakeven). The
TP stays unchanged. All subsequent bars check the new stop; if
`bar_low ≤ entry` after the mutation, exit at entry price (0R P&L,
no fees-on-fees since it's a stop-market close). If TP hits first,
exit at TP as before.

**Same-bar SL-first rule survives unchanged** — in the same-bar case
where MFE crosses trigger AND MAE reaches 1R original stop, the
original SL fires (production convention).

**Sacrificed-winner is real**: if MFE reaches trigger, price then
retraces to entry BEFORE hitting TP, and would have reached TP given
more bars, we exit at 0R instead of TP. The v3 probe accounted for
this explicitly at n=27 sacrificed winners per 166 top-20 trades.

## Hook location

Minimal-surface implementation:

**Site A — track peak MFE on `ShadowPosition`** (currently ephemeral):
`backend/app/shadow/engine.py:64-73` `ShadowPosition` dataclass. Add
one attribute `peak_mfe_r: float = 0.0`. Update inside
`shadow/worker.py:405-450` `_maybe_close_position` — before the
`check_exit` call, compute `favorable_r = (candle.high − pos.entry_price)
/ (pos.entry_price − pos.initial_stop_loss)` (needs `initial_stop_loss`
preserved separately since we're about to mutate `stop_loss`). Update
`peak_mfe_r = max(peak_mfe_r, favorable_r)`.

**Site B — arm the breakeven on trigger crossing**: same location, right
after peak update:
```python
if pos.stop_loss < pos.entry_price:  # still on original stop
    if peak_mfe_r >= settings.SHADOW_BREAKEVEN_TRIGGER_R:
        pos.stop_loss = pos.entry_price
        log.info("shadow_worker: breakeven armed on %s/%s at MFE %.2fR",
                 pos.symbol, pos.timeframe, peak_mfe_r)
```

The `check_exit` call downstream now sees `bar_low ≤ pos.stop_loss` at
the entry price and exits accordingly. **Zero change to `check_exit`
itself** — the existing logic is correct once `stop_loss` is mutated.

**Same-bar tiebreak preserved**: because we update `peak_mfe_r` from
`candle.high` and then check trigger BEFORE `check_exit`, a bar where
MFE crosses trigger AND the low touches original SL will arm the
breakeven, then `check_exit` will see `bar_low ≤ new stop_loss = entry`
and exit at entry. If the original SL check would have fired anyway
(low ≤ original SL), the SL-first rule applies to the ORIGINAL stop
first — which means the mutation-then-check sequence needs care. See
"Same-bar edge case" below.

## Same-bar edge case

Order of operations within a single bar where MFE crosses trigger AND
low touches original SL:

**Option A — mutate first, then check_exit sees the new stop.**
Because entry > original SL, `bar_low ≤ original SL` implies
`bar_low ≤ entry` too, so we'd still exit — but at the new stop (entry
= 0R) instead of the original (−1R). Bias toward the operator's
mechanic ("save the SL from same-bar oscillation that ends within
1R of entry").

**Option B — SL-first tiebreak on the original stop.** If original SL
would have fired same-bar, honor it. The mechanic only activates for
subsequent bars.

Both are defensible. **Option B is the production-convention-matching
choice** (`exit_monitor.py:55` — "assume SL hit first"). The v3 probe
uses this convention. Recommend Option B for parity, but call it out
as a design decision that measurably changes the outcome — Option A
would increase converted-losers by the same-bar-conflict rate (~5% at
2R geometry).

## Persistence & schema

**shadow_trades**: no schema change. Existing columns (`entry_price`,
`stop_loss`, `take_profit`, `exit_price`, `exit_reason`, `pnl_pct`)
cover all data. When breakeven fires, `exit_price = entry_price`,
`exit_reason = 'BREAKEVEN'` (new enum value — see below), `pnl_pct = 0`.

**`ExitReason` enum** at `backend/app/shadow/exit_monitor.py`: add a
new value `BREAKEVEN`. This is a hash-chained enum — the CHECK
constraint on `shadow_trades.exit_reason` (via migration) needs
extending. Add an alembic migration extending the CHECK constraint to
include `'BREAKEVEN'`. Standard pattern from the 2026-05-26 lesson
(FU-35 companion in code review: `HYBRID_AUTO_SCORE_THRESHOLD` /
`APPROVAL_MAX_PRICE_DRIFT` migrations).

**Optional observability**: add `stop_loss_original` column preserving
the entry-time SL for post-hoc analysis (was it a converted loser vs
a sacrificed winner?). Nullable, defaults to `NULL` for legacy rows.
NON_HASHED_ALLOW_LIST per audit chain rules — analytics-only.

## Settings

New config in `backend/app/config.py`:

```python
SHADOW_BREAKEVEN_ENABLED: bool = False
SHADOW_BREAKEVEN_TRIGGER_R: float = 0.50
```

Default OFF so shipping this is a zero-behavior-change deploy.
Operator flips `SHADOW_BREAKEVEN_ENABLED=True` in prod `.env` for the
first live run after soak.

Live-side parity: analogous env vars `LIVE_BREAKEVEN_ENABLED` and
`LIVE_BREAKEVEN_TRIGGER_R`, wired into `live_exit_monitor`. **DO NOT
enable live breakeven until FU-37 (cross-TF cooldown gap) is fixed AND
LIVE_COOLDOWN_ENABLED is flipped ON** — combining a stop-mutation
mechanic with no cooldown protection is a real-money risk multiplier.

## Before/after measurement plan

**Cannot ship this before the Aug 3-10 validation window** — contaminates
the window that measures the CURRENT strategy. Sequence:

1. **2026-08-03 to 2026-08-10**: Aug validation runs on CURRENT logic
   (breakeven OFF). Fresh 60d trailing baseline computed on 2026-08-03,
   plus the 8-day observed window figure. Both quoted with n and
   dispersion (30d/60d/90d range) per methodology rule.

2. **After 2026-08-10 window closes**: implement + ship this mechanic
   behind `SHADOW_BREAKEVEN_ENABLED=False`. Standard behavior-changing
   soak (~4-6h dev), then dev→main via cherry-pick prod-promotion.

3. **Operator flips `SHADOW_BREAKEVEN_ENABLED=True`** on a defined
   date. Record the flip timestamp in the decision doc.

4. **Post-flip observation window (~14 days)**:
   - Compute rolling top-20 avg_pnl_pct pre-flip and post-flip.
   - Compare `n`, `avg`, `share_exit_reason='BREAKEVEN'`, WR (with new
     denominator including BE exits as wins? losses? neither? — a
     design decision, recommend "neither" so WR reflects TP hits only).
   - Statistical test: paired-cohort comparison on same-symbol trades
     before/after the flip, controlling for signal volume drift.

5. **Ship-or-revert decision**: if the observed post-flip after-fee
   avg is worse than pre-flip by more than 1 standard error (of the
   30d rolling avg), REVERT (flip `SHADOW_BREAKEVEN_ENABLED=False`).
   If better by more than 1 SE, keep. If within noise, extend
   observation to 30 days before deciding.

## Risks & caveats

**Sacrificed-winner accounting is upper-bound-conservative in the
probe.** The v3 model uses 1h bars; intra-hour breakeven arming +
subsequent same-hour retracement to entry is aggregated at 1h
granularity. Real 1h check_exit behavior matches, but the model can't
distinguish "trigger armed at 10:15 then price dropped to entry at
10:45" from "trigger armed at 10:15 and price stayed above entry all
hour". Both are collapsed to "same-bar → breakeven" per the SL-first
rule. This is likely a small effect (~0-3% of triggered trades) but
not zero.

**Baseline drift**: the +0.212% net delta was measured on the
corrected 1h model whose baseline was −0.28% (2026-07-29 snapshot).
Applying to 2026-07-30's live baseline (−0.018% after fee) projects to
~+0.194% after-fee. Direction unchanged; magnitude will drift with
the rolling window.

**n = 166 top-20**: statistical confidence in the specific delta is
modest. Direction (positive) is well supported.

**No cross-TF cooldown** (FU-37): breakeven doesn't help a symbol that
gets stopped 4× in 18h across TF lanes — each stop still happens
before its trigger fires. The mechanic amplifies signal from good
setups; it does not defend against structural cooldown gaps.

**Live path considerations**: STOP_MARKET at Binance requires a
separate `stopPrice`. Moving the stop means CANCELING the existing
STOP_MARKET and placing a new one — which introduces a brief unhedged
window. `binance_live.py` has the plumbing; live-side implementation
needs a `modify_stop_order` helper. Not required for the shadow-only
first ship.

## Estimated effort

- Shadow-only implementation (recommended first ship):
  - `engine.py` ShadowPosition attribute + initial_stop_loss preservation: ~2h
  - `worker.py` peak-MFE update + trigger + mutation: ~2h
  - `exit_monitor.py` ExitReason.BREAKEVEN enum + tests: ~1h
  - Alembic migration for CHECK constraint: ~1h
  - `payload_builders.py` optional stop_loss_original column: ~0.5h
  - Unit tests for arm/trigger/same-bar/retrace/timeout cases: ~4h
  - Total: ~1.5 days
- Live-side parity (separate PR):
  - `live_exit_monitor.py` peak-MFE tracking: ~2h
  - `binance_live.py` cancel-and-replace STOP_MARKET helper: ~4h
  - Integration test against Binance testnet: ~2h
  - Total: ~1 day
- Documentation + decision doc amendment: ~2h

**Grand total**: ~3 days of shadow-only work + ~1 day of live parity +
soak windows. Compressible to 2 days if the live-side parity is
deferred to a follow-up PR after shadow validation.

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

## Open questions for operator review

1. **Option A vs Option B** for same-bar tiebreak — pick before ship.
2. **`stop_loss_original` optional column** — worth the schema change
   for post-hoc analysis, or skip and derive from `entry_price −
   1.5 × entry_atr` at analysis time?
3. **Live parity in same PR or separate follow-up?** Recommendation:
   separate. Ship shadow-only first, validate ~14 days, then live.
4. **Trigger config: hardcoded 0.50R or per-TF (like SHADOW_COOLDOWN_HOURS)?**
   Recommendation: hardcoded initially, per-TF once we have data.
5. **WR denominator treatment for breakeven exits** — count as "not a
   win, not a loss" (my recommendation), as losses (conservative), or
   exclude from WR (data-cleanest). Affects dashboard/report semantics
   only.

## Cross-references

- `docs/superpowers/decisions/2026-07-29-study-1-flat-geometry-ladder.md`
  — STUDY 1 verdict this mechanic addresses.
- `backend/docs/KNOWN_ISSUES.md` FU-37 — the cross-TF cooldown gap
  that limits this mechanic's protection.
- `backend/scripts/mfe_mae_curve.py` — v3 probe that generated the
  path-aware +0.212% delta estimate.
