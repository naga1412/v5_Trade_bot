# Restrict a counterfactual to the subset where the baseline event actually happened

**Status: standing rule.** Sibling to
`2026-09-08-checks-that-cannot-fail.md`. Not a check that cannot fail —
a **counterfactual evaluated where the baseline never occurred**, which
measures the model's own error rate and dresses it as an effect.

## The rule

**Before asking "what would have happened if we had done X instead",
confirm that the thing you are replacing actually happened.** A
counterfactual over a population that includes cases where the baseline
event never occurred does not measure the intervention. It measures the
difference between your model and reality, and that difference has a
sign and a magnitude and will look exactly like a finding.

## The instance, with both numbers, because the contrast is what teaches it

Question: of the trades that stopped out, how many would have reached
their take-profit had the stop been wider? Intervention: widen the stop
to 1.25x / 1.5x / 2.0x. Cost side: every remaining loss gets
proportionally deeper. Net is conversions against bigger losses.

**Run on ALL trades (n=685), selecting stop-outs as the MODEL resolved
them:**

| width | converted | still_stop | timeout | exp_R |
|---|---|---|---|---|
| 1.0 (baseline) | — | 596 | — | −0.5587 |
| 2.0 | **150 (25.2%)** | 378 | 68 | **−0.2402** |

A **+0.32R** improvement — roughly 4.5x the entire TP ladder's range.
That reads as the one real lever in the system.

**Run on DB-confirmed stop-outs only (`exit_reason` recorded as
STOP_LOSS), n=501:**

| width | converted | still_stop | timeout | exp_R |
|---|---|---|---|---|
| 1.0 (baseline) | — | 496 | — | −0.9887 |
| 2.0 | **71 (14.3%)** | 365 | 60 | **−1.1190** |

**Monotonically WORSE. −0.13R.** Both cohorts agree.

**Same data. One filter apart. +0.32R "real lever" versus −0.13R "do not
touch it".**

### Why the first number was wrong

The model resolved **87.0%** of trades as stop-outs; the database
records **70.5%** as stop-loss exits. That 16.5pp over-call means the
"baseline stop-out set" contained trades production actually closed at
take-profit or on timeout. For those, the baseline event — being stopped
out — **never happened**. So a "conversion at a wider stop" was the
model undoing its own false stop.

16.5/87.0 = 19% of the set, against 25.2% observed conversions: the
confound accounted for most of the effect, and reversed its sign.

### How to tell you are exposed

Any time the intervention is defined relative to an event your *model*
identified rather than one your *records* confirm. The tell is a
baseline-event rate that disagrees with the recorded rate. **Check that
agreement first, and state it, before reading any counterfactual built
on it.** The deconfounded slice also self-validates here: on DB-confirmed
stops the baseline reads **−0.9887R**, and a stop-out is −1R by
construction, so the filter is provably selecting what it claims.

## What nearly happened

**Two independent artifacts, both inflating the same false positive, in
one test.**

1. **Clipped bars.** The bar window was the trade's own holding window,
   ending at the original exit. A widened stop had no price data to be
   followed into, fell through to the timeout branch, and was handed the
   trade's *realised* pnl — which came from the exit at the original
   stop. So a widened stop inherited the narrow stop's loss (≈−0.83R)
   instead of either a real conversion or a real −2.0R loss. Timeouts
   grew **186 → 440** as width rose; the entire apparent improvement was
   −1.0R losses being replaced by ≈−0.83R fabrications.
2. **The baseline-subset confound above.**

Each one on its own produced "wider stops improve expectancy". Fixing
the first and not the second still produced it, at +0.32R. **Had either
survived, we would have widened stops on production.** That is the
closest call in this project.

Neither was caught by review. The first was caught by noticing that the
timeout column grew monotonically with the intervention — a column
nobody was looking at, which had no business moving that way. The second
was caught by comparing the model's baseline-event rate against the
database's.

## The two questions to add

1. **Did the thing I am replacing actually happen, in the records?**
   Restrict to where it did.
2. **Does my simulation have the data to follow the intervention to its
   own conclusion?** If the window ends at the baseline's exit, a
   counterfactual that postpones that exit has nothing to read.

## Related

- `2026-09-08-checks-that-cannot-fail.md` — the parent rule. This is the
  same family: a measurement that cannot fail for the reason it exists,
  here because its population guarantees a non-zero answer.
- `2026-07-29-study-1-flat-geometry-ladder.md` — the amendment that
  closed the take-profit term; the stop-width test closed the other.
