# When the target accumulates over a window, check whether the treatment sets the window

**Status: standing rule.** Sibling to
`2026-10-21-counterfactual-baseline-subset.md` and
`2026-09-08-checks-that-cannot-fail.md`. Same family, one layer further
in: there the **population** was wrong, here the **measurement window**
is. The estimate is computed correctly, on the right rows, from real
prices — and it is still not the quantity it claims to be.

## The rule

**Before reporting a target that is a maximum, a minimum, a sum, a
count, or any other accumulation over a window, ask what sets the
window's length. If the treatment influences it, the target is
post-treatment and the comparison is contaminated.**

And the part that makes it actionable rather than merely cautionary:

**No amount of stratifying ON window length fixes it. Only a window
fixed by design does.** Stratifying on the window conditions on the
collider instead of removing it — inside a stratum of "cases that
happened to run 20 bars", the two groups are no longer comparable,
because *being* a 20-bar case means something different in each group.

## The instance

Question: does `mtf_agreement >= 4` predict how far a trade travels?
Target: `MFE_R`, the maximum favourable excursion in R units. Population:
n=2,128 LONG shadow trades, `entry_score >= 0.30`, 60d.

`MFE_R` was computed over each trade's **realised holding window** — from
entry to its actual exit. That window ends when a barrier was hit, which
is an **outcome**. And the treatment moves it: mtf>=4 trades are held
**13.04 bars** against mtf<4's **9.60**. So the high cell was handed more
bars in which to register a maximum, and a maximum over more bars is
larger for free.

Recomputing the same target over **windows fixed by design** — 5, 10, 20
and 40 bars from entry, identical for every trade, decided before any
price is read:

| horizon | mfe mtf<4 | mfe mtf>=4 | diff | SE | sigma |
|---|---|---|---|---|---|
| 5 | 0.9576 | 1.0833 | +0.1256 | 0.0531 | +2.36 |
| 10 | 1.3896 | 1.5660 | +0.1764 | 0.0867 | +2.03 |
| 20 | 1.9323 | 2.1976 | +0.2653 | 0.1115 | +2.38 |
| 40 | 2.7448 | 2.9696 | +0.2247 | 0.1529 | +1.47 |

```
realised-window diff  +0.3358 R  (+5.78 sigma)
fixed-10-bar diff     +0.1764 R  (+2.03 sigma)
window length         +0.1594 R  = 47% of the headline
```

**A +5.78σ finding became +2.03σ, below even the uncorrected 3σ bar.**

### The asymmetry is the tell

Equalising the window **raises the low cell and barely touches the high
one**:

- mtf<4: 1.2124 -> 1.3896 (**+0.1772**) when given a full 10 bars
- mtf>=4: 1.5482 -> 1.5660 (**+0.0178**), because it already averaged 13

The gap closed **from below**. The low cell was not weaker; it was
measured for less time. If a confound is real, this is what fixing it
looks like — the cell that was being short-changed comes up, rather than
both cells moving together.

### The dose-response was the collider talking

Stratified on deciles of realised `bars_observed`, the within-stratum
diff rose monotonically with hold length: −0.17, +0.14, +0.16, +0.28,
+0.09, +0.32, +0.40, +0.59, +0.64. That reads as a mechanism: a
multi-timeframe trend signal whose advantage **compounds with time
held**. It was reported as the interesting part of the finding.

At fixed horizons the advantage is proportionally **flat** — 13.1%,
12.7%, 13.7%, 8.2%. There is no compounding. The apparent dose-response
was the stratifier sorting on the same contamination it was meant to
remove.

## How the rule gets defeated: a plausible-sounding reassurance

**AN ADJUSTMENT THAT LEAVES THE ESTIMATE WHERE IT WAS HAS TWO READINGS:
*the confound was small*, or *the adjustment did not address the
confound*. The agreement cannot distinguish them.** That is the rule,
and it is the most useful thing in this document. A sensitivity analysis
whose reassurance is structurally guaranteed is **a check that cannot
fail wearing a sensitivity analysis as a costume** — see
`2026-09-08-checks-that-cannot-fail.md`, which is where it is filed.

The instance. The decile-stratified estimate came out at **+0.3125 R**
against the unadjusted **+0.3357 R** — agreement to within 7%. That
closeness was stated as evidence that duration accounted for only ~7% of
the effect, i.e. as a **bound on the collider bias**.

**It bounds nothing.** Both estimates are computed on
**outcome-dependent windows**. The adjustment redistributed rows between
strata; it never changed what was being measured inside them. Two
estimates that share a contamination agree with each other about as
closely as their shared contamination is stable — so their agreement
measures that stability, not its absence.

**Agreement between two contaminated estimates is not evidence of
cleanliness.** To break the tie you need a measurement that does not
share the defect, and here that was a window fixed by design.

**Worth recording plainly, because it is the whole lesson:** the
reassurance was produced in the *same turn* that correctly flagged
`bars_held` as post-treatment in the **stratifier**. The collider was
spotted one layer up and missed in the **target**, and the miss was then
dressed as a bound. It went into the report unchallenged. Spotting a
confound in one role does not mean you have found all its roles — the
same variable can be a bad control *and* the thing that defines your
outcome, and fixing the first makes the second feel handled.

## A second trap in the fix: nested windows are not replications

The four fixed horizons agree in sign and magnitude. **That is one ~2σ
observation measured four ways, not four independent same-sign
results.**

The 5-bar window is contained in the 10-bar window, which is contained
in the 20, which is contained in the 40 — **on the same trades**. The
maxima are therefore very highly correlated by construction: a trade's
40-bar MFE *is* its 5-bar MFE unless price went further later. Treating
the four as corroboration would multiply one piece of evidence by four.

Left unqualified, "consistent at every horizon" will be read later —
including by the people who produced it — as strength it does not have.
**This is the same shape as the dose-response it replaced:** an apparent
pattern that is really one quantity viewed repeatedly. Having just
removed a false pattern of that kind, the fix reintroduced the identical
shape in its own output.

**So: when reporting one target at several nested windows, state the
dependence next to the numbers.** The independent replication axis is
**temporal halves and out-of-sample windows**, not nested horizons on the
same rows.

## The questions to add

1. **What sets the length of my measurement window?** If the treatment
   does, the target is post-treatment.
2. **Is my "adjustment" measuring something different, or the same thing
   rearranged?** If it is the latter, its agreement with the unadjusted
   estimate is not a bound.
3. **Are my several views of this target independent, or nested?** If
   nested, say so beside the figures.

## Related

- `2026-10-21-counterfactual-baseline-subset.md` — the sibling. There the
  counterfactual ran where the baseline event never happened. Here the
  measurement window was set by the outcome. Both produce a real number,
  with a sign and a magnitude, that looks exactly like a finding.
- `2026-09-08-checks-that-cannot-fail.md` — the parent rule.
- `2026-10-22-phase-a-result.md` — the result this downgraded, with the
  surviving estimate and the n a future re-test would require.
