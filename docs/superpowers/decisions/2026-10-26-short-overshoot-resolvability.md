# When the SHORT overshoot becomes resolvable — computed, with a date

**Date:** 2026-10-26
**Status:** DO NOT RE-ESTIMATE BEFORE 2026-11-03. No watch set, no reminder.

## The estimator as it stands

SHORT cards/day, armed-exposure-weighted per UTC day, clustered by day:

| | |
|---|---|
| day rates | 53.63, 43.00, 6.00, 78.67 |
| G | 4 |
| mean | 45.33/day |
| between-day SD | 30.18 |
| cluster SE | 15.09 |
| effect vs 16/day projection | **+29.33/day** |
| **z** | **+1.943** |
| design effect vs naive Poisson | 42.7 |

## G required for 3σ

Holding the observed SD and effect:

```
G >= (3 * SD / effect)^2 = (3 * 30.18 / 29.33)^2 = 9.53  ->  G = 10
```

That is the **naive** answer. It uses a normal critical value on an SE
estimated from 4 points, which understates what is needed. With the
small-sample t correction — requiring |effect| / SE ≥ t(0.9987, G−1), the
t-equivalent of two-sided 3σ — the first G that clears it is:

| criterion | G | more clusters needed |
|---|---|---|
| naive 3σ | 10 | 6 |
| **t-corrected (the honest one)** | **15** | **11** |

## The calendar date

Observed rate of distinct armed regime-days: 4 clusters in 3.001 days =
**1.333 clusters/day** (the first and last are partial days, which is why it
exceeds 1.0).

| criterion | date |
|---|---|
| naive 3σ (G=10) | 2026-10-30 |
| **t-corrected (G=15)** | **2026-11-03** |

**The operative date is 2026-11-03.** Before then, accumulating events is not
progress — that is the entire content of the clustered number. The event
count will have roughly tripled by 2026-10-30 and the question will be no
closer to settled.

## Does the nominal G overstate the effective one? YES, twice over

**(1) Unequal exposure.** The four clusters carry armed-exposure weights
0.709, 1.000, 1.000, 0.292. The Kish effective count is

```
G_eff = (sum w)^2 / sum(w^2) = 3.48   against a nominal 4
```

So the honest G is **3.48**, and the G=15 target is correspondingly further
away than the arithmetic above suggests.

**(2) The non-result rests on ONE cluster.** Leave-one-out:

| dropped day | remaining mean | SE | z |
|---|---|---|---|
| 53.63 | 42.56 | 20.98 | +1.27 |
| 43.00 | 46.10 | 21.31 | +1.41 |
| **6.00** | **58.43** | **10.57** | **+4.01** |
| 78.67 | 34.21 | 14.43 | +1.26 |

Dropping the single low day (2026-10-25, 6 cards) takes z from **+1.94 to
+4.01** — through the bar. Dropping any of the other three leaves z ≈ +1.3.

**This is the same shape as every "point measurement read as a general fact"
error on file, with the sign reversed: the NON-result is as
single-cluster-dominated as a result would be.** It would be exactly as wrong
to conclude "the overshoot is real, one day is dragging it down" as to
conclude "there is no overshoot". Both readings are one cluster deep.

So the correct statement is not "the overshoot is not significant". It is:
**with G_eff = 3.48 and a leave-one-out range of z = +1.26 to +4.01, the data
does not yet distinguish the hypotheses, and no amount of further events
inside the same few days will.**

## Standing instruction

Do not re-estimate before **2026-11-03**. If the question comes up sooner,
the answer is this document. No watch is set and no reminder is scheduled, by
the operator's standing rule — a future session can read the required G, the
effective G, and the date, and decide deliberately.

When it is re-run, re-derive **G_eff**, not G: if exposure stays uneven the
nominal count will keep overstating it.
