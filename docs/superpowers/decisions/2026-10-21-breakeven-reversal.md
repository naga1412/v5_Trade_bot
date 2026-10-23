# Breakeven-variant lane — the read REVERSED. Recommendation withdrawn.

**Status: the n=129 read banked on 2026-09-08 is SUPERSEDED.** The
operator-facing recommendation to adopt the breakeven stop is
**withdrawn**. The mechanic was never shipped, so nothing needs
reverting — but it must not be shipped on the strength of the banked
read.

## What changed

The 2026-09-08 read (n=129, +0.643%/+0.659%, 2.76σ/2.84σ, PR #561) was
**entirely pre-universe-expansion data**. Re-run on 2026-10-20 with six
more weeks of pairs, same pre-registered population
(`timeframe='1h' AND direction='LONG' AND entry_score>=0.36`), split on
`opened_at` against the 2026-08-31 expansion:

| variant | cohort | n_paired | n_armed | mean Δ | SE | Δ/SE |
|---|---|---|---|---|---|---|
| breakeven_0.40R | pre-expansion | 100 | 61 | **+0.7384%** | 0.282 | +2.62 |
| breakeven_0.40R | post-expansion | 153 | 84 | **−0.6257%** | 0.395 | −1.59 |
| breakeven_0.40R | **pooled** | **253** | 145 | **−0.0865%** | 0.266 | **−0.32** |
| breakeven_0.50R | pre-expansion | 100 | 60 | **+0.7585%** | 0.280 | +2.71 |
| breakeven_0.50R | post-expansion | 153 | 81 | **−0.5953%** | 0.389 | −1.53 |
| breakeven_0.50R | **pooled** | **253** | 141 | **−0.0602%** | 0.263 | **−0.23** |

**The halves are 2.8σ apart** (0.50R: difference 1.354, SE_diff 0.480).
On 2026-09-08 they were 0.93 SE apart, which is why the doc of the day
called the split a refinement rather than a gate. That framing is now
wrong: the split decides whether the effect exists on the universe the
bot actually trades, not merely which population the figure describes.

## It is mechanistic, not dilution

Unarmed pairs are **exactly Δ=0.0000 in every cell** — correct by
construction, since a variant whose stop never arms *is* the base
trade. So the pooled move cannot be explained by a changing armed/
unarmed mix. Inside ARMED, where the mechanism actually operates:

| variant | pre-expansion ARMED | post-expansion ARMED |
|---|---|---|
| breakeven_0.40R | +1.2105% (n=61, +2.67σ) | **−1.1397%** (n=84, −1.59σ) |
| breakeven_0.50R | +1.2642% (n=60, +2.77σ) | **−1.1244%** (n=81, −1.54σ) |

A **2.39pp swing** inside the subset the effect was supposed to live in;
the armed pre-vs-post difference is itself **2.77σ**.

**The noise mechanism.** Post-expansion SE is far larger (0.73 vs 0.46
on the armed cells) — the new cohort is substantially noisier, which is
consistent with the universe self-selecting newly-listed high-volatility
symbols. A stop moved to entry in a high-noise regime gets touched
before the move develops, converting would-be winners into scratches.
That is a coherent account of a sign flip rather than a fade, and it
predicts the effect should be worse the noisier the symbol.

## What this does and does not say

- **Does not say** the original measurement was wrong. It was a correct
  read of the data available, on a population that then changed.
- **Does say** that a result banked immediately before a population
  change is specifically fragile, and that "the question is answered"
  was too strong a phrasing for any lane still accruing.
- **The operator's manual application is unaffected.** It has run since
  2026-08-17 on hand-picked, largely established symbols — the cohort
  where the mechanic still measures positive.

## The established-cohort option, and why it is NOT pre-registered here

The obvious move is to scope the mechanic to the established cohort,
where it still measures +1.26% armed. Phase 4's cohort machinery
(`cohort_baseline_symbols`, synchronous tagging at position-open) makes
it buildable. It is deliberately **not** pre-registered, for two
reasons:

1. **It is a post-hoc subgroup of a pre-registration that has now
   failed** — the weakest possible footing for a forward claim.
2. **The timeline is prohibitive.** Pre-expansion accrual was 100
   pairs over the ~27 days from the lane's 2026-08-04 start to the
   08-31 expansion, i.e. ~3.7 pairs/day, of which ~60% arm → ~2.2 armed
   pairs/day. But the established cohort is now a *minority* of a
   ~100-symbol universe, and the ~21 pairs that accrued in the weeks
   before the 09-08 read came almost entirely from the new universe.
   At the observed armed-cell SE of 0.46% on n=60, reaching 3σ on
   +1.26% needs SE ≈ 0.42%, i.e. roughly n_armed ≈ 72 — but on
   *established symbols only*, at a small fraction of the fleet's
   current pair rate. On the pre-expansion rate it would be ~5 weeks;
   on the established-only share of the current fleet it is **many
   months**, plausibly a year.

**Cost it before proposing it** was the instruction, and the cost is
the answer: a year of accrual to confirm a post-hoc subgroup of a
failed pre-registration is not worth the lane. Leave it.

## THE REVERSAL SURVIVED A NAMED, PRE-COMMITTED CHALLENGE (2026-10-23)

**This is the useful part for whoever reads it next, and it is why this
closure is stronger than one that was never challenged.**

A specific mechanism was proposed by which the reversal could have been
a measurement artifact rather than a real effect:

> The breakeven arming threshold is **R-denominated** (0.40R / 0.50R).
> Shadow caps `R` at 5% of entry; live does not. So on cap-bound trades
> shadow armed breakeven at a **tighter price level** than live would
> have — and a tighter arming level is more likely to be touched out. If
> the post-expansion cohort were higher-ATR, it would be more
> cap-bound, so shadow would have armed even earlier on exactly the
> cohort that produced the negative read.

**The decision rule was written before the data was pulled:** materially
higher cap-bind post-expansion would mean the reversal had an artifact
explanation and the withdrawal was premature; similar rates across
cohorts would mean the reversal stands.

**Measured, 1h lane:** cap-bind **11.84% pre-expansion** against
**8.54% post**. On 15m, **2.45%** against **1.34%**. **Lower
post-expansion on both lanes — the mechanism runs OPPOSITE to the
hypothesis.**

More cap-binding *pre*-expansion means shadow armed breakeven earlier on
the **pre** cohort, which biases the **+0.65%** figure downward rather
than the post figure. Correcting for it would, if anything, **widen**
the gap the reversal reports.

**The premise also failed at its first link.** The challenge assumed the
post-expansion cohort was noisier. Per-trade `pnl_pct` sd on the 1h lane
is **5.048 pre** against **5.050 post** — identical to three figures.
The SE difference that motivated "noisier" (0.73 vs 0.46) comes from
**n, not variance**: 684 rows against 480, differing by sqrt(n) alone.

**So: a result that has defeated a specific, named, pre-committed
attempt to explain it away is worth more than one nobody tried to
explain away.** The withdrawal stands, and it now stands on a stronger
footing than when it was made.
