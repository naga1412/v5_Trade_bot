# STUDY 1 verdict — flat geometry ladder, 2:1 retained by default

**Ratified: 2026-07-29** as the closeout of the Phase 1 MFE study (probe
`mfe-mae-curve` v2 run 30436098755, model-vs-actual validation gate
passed at 91.6% directional agreement on the 1h ladder for the top-20
population).

## Decision

**The 1h TP_R ladder is flat within noise: 2:1 is retained by default,
not by demonstrated superiority.** The recorded verdict is NOT "2:1 is
optimal" — it is that no fixed TP_R in {0.5, 0.75, 1.0, 1.25, 1.5, 2.0}
is distinguishable from any other in the 60d top-20 LONG-at-gate
population.

## Evidence

Top-20 @ 1h, n=166 (probe v2 run 30436098755):

| TP_R | implied_WR% | exp_R | exp_R_after_fee | same_bar% |
|---|---|---|---|---|
| 0.50 | 48.8 | -0.268 | -0.466 | 22.9 |
| 0.75 | 42.2 | -0.262 | -0.460 | 15.1 |
| 1.00 | 36.7 | -0.265 | -0.463 | 13.3 |
| 1.25 | 29.5 | -0.336 | -0.534 | 7.8 |
| 1.50 | 27.7 | -0.307 | -0.505 | 5.4 |
| 2.00 | 24.7 | -0.259 | -0.457 | 4.8 |

Span of `exp_R` across the entire ladder: **0.077R** (max -0.259
at 2.0R, min -0.336 at 1.25R). At the neighbor cells the operator
called out: **TP_R=2.0 beats TP_R=0.75 by 0.003R** — noise at n=166.

Under this ladder, no fixed geometry is superior on expectancy. The
choice among them is a scoreboard/design preference, not an
expectancy-optimization decision.

## Corollary — the ~60% WR question

The operator's original question was whether LONG WR can honestly
reach ~60%. Answer: **yes at TP_R≈0.5**, where implied_WR is 48.8%
biased low by a **22.9% same-bar conflict rate** (the highest in the
ladder) — the tighter-TP geometry gets more of its wins tie-broken to
losses by the SL-first rule. Correcting for tiebreak bias, TP_R≈0.5's
honest WR is likely in the mid-50s to low-60s.

But **its expectancy is identical to 2:1's** (exp_R -0.268 vs -0.259).
So it's a better scoreboard for the same money: the trader sees more
wins but takes home the same expected R.

## Consequence — where the edge actually is

Expectancy is not distinguishable across TP_R because the SIGNAL LEVEL
does not differ enough at the fixed-geometry granularity. The scoring
line (which trade to take) matters more than the exit line (where to
take profit). Redirect effort accordingly:

- **STUDY 2's near-miss finding is the real lever.** Top-20 SL exits
  have avg MFE_R = 0.957 — the average loser came within a whisker of
  1R before reversing. A breakeven-stop mechanic that converts a
  meaningful share of these losers to 0R is where positive expectancy
  is available in this data. The v2 upper-bound estimate at 15m
  (trigger 0.40R → +0.288R after fees) is inflated by 15m over-
  counting; the honest 1h path-aware estimate (Item 2 of the operator's
  2026-07-29 direction) is the number that decides whether it ships.
- **Entry scoring / signal selection** — mtf_agreement ≥ 4 lifts
  avg_pnl_pct from +0.228 to +0.481 (STUDY 3 retest 2026-07-28). The
  operator's Aug 3-10 validation runs on both 0.36-only and 0.36+mtf≥4
  gates; this is the entry-side counterpart to the exit-side breakeven
  work.

## What is NOT decided by this ratification

- The 2:1 geometry is not FINAL. Rewriting the outcome resolver to use
  each trade's ACTUAL take_profit and stop_loss (Item 1 of the 2026-07-29
  direction) may change the ladder. When the corrected 1h model lands,
  this doc gets an amendment paragraph — the flat-ladder conclusion is
  provisional on the current probe's hypothetical-2R model.
- The breakeven-stop mechanic is not APPROVED. The 15m upper-bound
  says +0.288R; the 1h path-aware run may say +0.05R after honest
  sacrificed-winners accounting. Ratification of any exit-side change
  requires that number.
- The Aug 3-10 dual-axis validation (entry_score deciles × mtf bands)
  proceeds unchanged by this ratification — it's an entry-side
  question, orthogonal to the exit geometry.

## Fee context (2026-07-29 investigation)

All numbers above are **GROSS of Binance fees**. Confirmed 2026-07-29:
`shadow_trades.pnl_pct` is `(exit - entry) / entry × 100` with no fee
subtraction. Round-trip taker fee for the bot's configured order types
(MARKET entry + STOP_MARKET / TAKE_PROFIT_MARKET exit) is **0.10%**.

**AMENDED 2026-07-30 — the +0.228% figure was a positive-window snapshot.**
Re-running STUDY 3's exact SQL 45 hours later showed top-20 avg drifted
to **+0.0822% gross** (n=179, ~45h of new closes added 11 trades whose
cluster including 3× COTI −5% floor hits dragged the average down by
~15 percentage points of sum PnL). Corrected as-of-2026-07-30 headline
under the same accounting:

| snapshot | n | gross avg | after-fee avg (0.10% RT) |
|---|---|---|---|
| STUDY 3 (2026-07-28 06:34 UTC) | 168 | +0.228% | +0.128% |
| v3 probe (2026-07-29 09:52 UTC) | 166 | +0.0466% | −0.0534% |
| **as of 2026-07-30 03:24 UTC** | **179** | **+0.0822%** | **−0.0178%** |

The current honest read: **bot is breakeven-with-friction at the 0.36
gate — after fees, top-20 expected value is ~zero, drifting inside the
observed noise of the rolling window.** The 2:1-retained-by-default
ratification holds under either accounting (the flat-ladder finding is
about geometry, not baseline expectancy magnitude).

**Methodology rule adopted 2026-07-30 (operator ruling):** stop quoting
point-estimate expectancy. Three measurements of the same population
gave +0.228% / +0.047% / +0.082% within 45 hours. Every expectancy
figure going forward must carry `n` and a dispersion measure (std
error or the range across a few trailing windows, e.g. 30d/60d/90d).
Applies to the Aug 3-10 report too — an 8-day window will be even
noisier.

**Aug 3-10 baseline methodology (operator ruling 2026-07-30):** compute
a fresh 60d trailing baseline on 2026-08-03 (baseline for pre/post
comparison), plus the 8-day observed window figure. Do NOT quote the
+0.228% snapshot as the current expected value.

## Reversal criteria

Re-open the geometry question if any of the following changes:

1. **The Item 1 corrected 1h model** produces an `exp_R` ladder whose
   range exceeds 0.15R (about 2× today's 0.077R noise floor).
2. **Aug 3-10 validation** produces per-decile WR that differs
   materially from the current ladder's implied numbers.
3. **The exit-side breakeven mechanic ships** and lifts expectancy
   enough that the residual geometry choice becomes cheaper to
   optimize.
4. **Signal distribution shifts** — a new scoring layer or gate change
   substantially reshapes the top-20 entry population.

Without one of those, re-testing this ladder on the same data is
churn.

## Cross-references

- [[pr368_c3_narrow_scope_prod_verified]] — the fleet-cap ratification
  that defines the top-20 population this study rests on.
- [[aug_validation_top_20_only]] — Aug 3-10 methodology memo.
- `docs/superpowers/decisions/2026-07-28-fleet-cap-top-20-ratified.md`
  — the fleet-cap decision.
