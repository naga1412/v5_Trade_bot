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

## AMENDMENT 2026-10-21 — criterion #1 evaluated. Ladder still flat.

**This is the amendment paragraph the ratification promised** ("When the
corrected 1h model lands, this doc gets an amendment paragraph").

**Criterion #1 does not trigger. The flat-ladder verdict stands, now on
the corrected resolver rather than the hypothetical-2R model.**

First, why this took until October: the resolver correction landed on
2026-07-29 (v3, actual TP/SL) and the native-TF fix on 2026-07-30 (v5),
**but the TP_R ladder itself was dropped from the script in the same
evolution.** No `TP_R` constant or ladder loop survived into v5, so from
2026-07-30 onward this document carried a reversal criterion that
nothing could evaluate. Restored in #582.

### Result — PRIMARY, criterion-bearing: top20 @ 1h, n=96

Floor 0.1804R (2x the paired SE of the fixed 0.5R-vs-2.0R endpoint
pair; paired because every rung is evaluated on the same trades).

| TP_R | implied_WR% | exp_R | exp_R_fee |
|---|---|---|---|
| 0.5 | 34.4 | **-0.484** | -0.536 |
| 0.75 | 26.0 | -0.544 | -0.596 |
| 1.0 | 25.0 | -0.500 | -0.552 |
| 1.25 | 19.8 | **-0.555** | -0.607 |
| 1.5 | 19.8 | -0.505 | -0.557 |
| 2.0 | 16.7 | -0.500 | -0.552 |

**Range 0.0703R against the 0.15R criterion -> still FLAT.** Note how
close that is to v2's 0.077R: correcting the resolver did not change the
flatness.

### Threshold discipline applied deliberately

0.15R was set against this doc's n=166. A larger-n population has a
smaller noise floor, so applying the July threshold to the current
~100-symbol fleet would make it easier to clear -- goalpost movement in
our own favour. **The criterion was therefore applied to top20 @ 1h and
nothing else.** The fleet is reported for relevance only, against a floor
computed from its own n.

**SECONDARY (full fleet, n=685)**: floor 0.0639R, range **0.0781R,
marginally exceeding its own floor**, with 0.5R best (-0.531) degrading
to 1.5R (-0.609) and 2.0R recovering (-0.567) -- a shallow hook, not a
monotone gradient. A weak sub-0.1R preference for tighter TP, worth
~0.11%/trade gross, while every rung sits at -0.685R to -0.764R after
fees. Choosing between losses.

### Two caveats that matter more than the verdict

1. **Underpowered relative to its own threshold.** At n=96 the floor
   (0.1804R) **exceeds** the criterion (0.15R), so a true 0.15R effect
   could not have been resolved here. The observed 0.0703R is well under
   both, so the conclusion holds -- but a near-threshold reading at this
   n must not be treated as decisive.
2. **The reality gap did not close.** Directional agreement is **84.7%**
   on the criterion population, *below* the 91.6% this doc cites for v2,
   and the model-vs-actual gross pnl gap runs **+0.83% to +2.91%** by
   slice. The model is ~1%/trade too pessimistic, which is why every
   rung reads about -0.5R while realised population expectancy is
   ~+0.02%. v3 was built to close this and did not. **Absolute levels
   from this probe are not trustworthy. The RANGE is the robust
   statistic, and that is what the criterion uses.**

### Why the ladder is flat — the arithmetic, not an accident

`exp_R = WR*k - (1 - WR)`, with the loss rung pinned at -1R by
construction. The implied win rate falls almost exactly in proportion as
`k` rises: on the fleet, WR goes 31.2% -> 14.5% as TP_R goes 0.5 -> 2.0,
and that arithmetic reproduces the measured `exp_R` to within 0.002R at
both ends. **Moving the take-profit cannot move expectancy, because it
moves the required hit rate and the achieved hit rate together.**

This sharpens the doc's original "the scoring line matters more than the
exit line" into something more specific: **the binding constraint is the
STOP**, which sets R and therefore the hit rate. `exp_R ~ -0.5R` at every
rung means the stop is taken ~70% of the time wherever the TP sits.
Confirmed independently by the exit decomposition (30d to 2026-10-21,
n=2098): STOP_LOSS **70.50%** at **-1.4100%**, TAKE_PROFIT **28.31%** at
**+3.5577%**, TIMEOUT 1.19% at +0.4658% -- expectancy **+0.019%**, against
a break-even hit rate at R=2.523 of **28.38%** versus an actual 28.31%.
The strategy sits on the knife edge to within 0.07pp, by its geometry.

**Criteria #2 and #3 are now also resolved and neither re-opens this**:
the Aug 3-10 validation was cancelled, and the breakeven mechanic
reversed (see `2026-10-21-breakeven-reversal.md`). Criterion #4 (signal
distribution shift) is arguably met by the 31-August universe expansion,
which is why the fleet ladder is reported above -- and it is flat too.

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

## SECOND VINDICATION OF range-NOT-level (2026-10-23)

The criterion was a **range** across rungs, not a level at any rung.
That choice was made for iso-expectancy reasons; it turned out to also
make the criterion immune to a denominator error discovered much later.

Shadow caps `R` at 5% of entry while the live path does not
(`2026-10-23-shadow-live-geometry-divergence.md`), and on the **1h lane
the cap binds on 10.48% of trades** — inflating `MFE_R` for those rows,
because a smaller `R` is a smaller denominator.

**Why the span survives it: the inflation is a property of the ROWS, not
of the RUNG.** A cap-bound trade carries its smaller `R` at *every*
take-profit level tested, so its inflated contribution shifts **every
rung in the same direction by roughly the same amount.** The *levels*
move together; the **range** barely moves.

So the statistic the criterion was built on is **structurally
insensitive to a subset-denominator error**, while a level-based
criterion would not have been. Measured span 0.0703R against a 0.15R
bar — far outside what a ~10% subset could shift a *range* by, however
much it shifts the levels.

**The general form worth carrying: prefer a criterion built on a
DIFFERENCE between conditions over one built on an absolute level,
because a bias shared by all conditions cancels in the difference and
does not cancel in the level.**
