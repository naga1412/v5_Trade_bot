# The LONG collapse and the SHORT overshoot are TWO causes, not one

**Date:** 2026-10-26
**Status:** measured, read-only. One action follows: do **not** retune the SHORT bar yet.

## The pre-registered test

One location-shift parameter had to reconcile **both** the 0.284× LONG
dispatch ratio and the 2.29× SHORT overshoot. Fit it, report the residual; if
no single shift fits both, there are two causes.

**Verdict: the single-shift model is CONFIRMED at the scoring layer and
FALSIFIED at the dispatch layer.**

## Part 1 — at the scoring layer, one shift fits almost perfectly

`predictions`, 1h, all three cohorts, `final_score` (which is **signed**:
LONG +0.05→+0.516, SHORT −0.526→−0.05, NEUTRAL inside ±0.05). Pre-flip
n=11,950 over 7.000 d; post-flip n=4,552 over 3.001 d.

| arm | pre | post | ratio |
|---|---|---|---|
| P(fs ≥ +0.36) | 0.006444 (n=77) | 0.005053 (n=23) | 0.784× |
| P(fs ≤ −0.36) | 0.009456 (n=113) | 0.012522 (n=57) | 1.324× |

Solving each arm **independently** for the shift δ:

- LONG arm → **δ = −0.0145**
- SHORT arm → **δ = −0.0144**
- disagreement: **0.0001**

Best single δ = **−0.0144**, which predicts both tails to **+0.1%**, against
binomial noise of ±20.8% (long) and ±13.2% (short). The median moved
**−0.0441 → −0.1042**.

Two independent equations, one unknown, and they agree three decimal places
in. That is not a direction check passing — it is a genuine common cause.

## Part 2 — but that shift cannot produce the dispatch ratios

Within **one** population (`dispatch_decisions`, 1h — same rows for supply
and for dispatch, so no cross-population comparison):

| win | dir | attempts/d | \|fs\|≥.36 /d | sent/d |
|---|---|---|---|---|
| pre | LONG | 450.4 | 10.57 | 17.57 |
| pre | SHORT | 757.6 | 15.00 | 0.00 |
| post | LONG | 208.9 | 6.33 | 5.33 |
| post | SHORT | 977.3 | 18.66 | 37.32 |

**LONG sent ratio 0.303× = supply 0.599× × conversion 0.507×** (product
0.3034, exact). Log shares: **supply 43%, conversion 57%.**

Conversion = sent per bar-passing candidate: **1.662 → 0.842**.

So the distribution shift delivers LONG supply at 0.599×, and no location
shift can turn that into a 0.30× *sent* ratio. **The majority term (57%) is a
conversion change downstream of scoring**, which is not a distributional
property at all.

Note both conversions exceed 1.0 (LONG pre 1.662, SHORT post 2.000): more
cards are sent than clear the raw ±0.36 bar, because `PATTERN_BOOST` adds
+0.10 to magnitude, so the effective bar is ~0.26. On a boost-inclusive
basis LONG conversion fell 0.343 → 0.235 and SHORT post sits at 0.401 — i.e.
**SHORT converts better than LONG ever did.**

## What this decides

The operator's two branches were: a distribution shift means card load
**decays** on regime reversion and the threshold must not be retuned; a
structural asymmetry means it **persists** and the SHORT bar belongs higher.

It is both, in known proportion: **43% decaying, 57% not explained by the
shift at all.** So:

- **Do not raise the SHORT bar yet.** The dominant term is a LONG conversion
  collapse, and raising the SHORT bar treats a symptom whose larger cause is
  elsewhere. Retuning now would also be fitted to a distributional component
  that is expected to revert.
- The next question is specifically: *why did LONG conversion halve?* It is
  not the SHORT-related gates — `short_disabled` passed all 604 post-flip
  LONG attempts and `regime` passed 100% in both windows. Within
  `dispatch_decisions` it decomposes as min_score pass-rate 0.833× ×
  post-min_score survival 0.764× (dedup / mtf veto / funding).

## The regime gate is inert, and the figure on file was wrong

`dispatch_decisions.gate_verdicts`, 1h, 2026-10-16 → 2026-10-26: the regime
gate saw **one single value on every one of 12,016 rows**, every day:

```
market_regime='neutral', no opposing case
```

So the degeneracy is **100.0% (12,016/12,016)**, not 94.3%, and the value is
**`neutral`**, not `sideways_grind`. `_check_regime` blocks only when regime
is `'bull'` or `'bear'`; `'neutral'` takes the no-opposing-case pass branch.
The gate has had **zero** opportunity to fire in this window.

**Forward risk worth naming:** the suppression is latent, not absent. SHORT is
now ~88% of dispatch (110 of 125 cards). The moment regime classifies
`'bull'`, every SHORT is blocked unless L2 agrees at ≥0.80 — a volume cliff,
not a taper. That cliff is invisible in every measurement above precisely
because regime has been constant.

## Method notes

- The scored-population tails and the dispatch counts come from **different
  tables**, and an earlier version of this analysis compared them directly.
  That is the pooled-rate error in another costume: `predictions` and
  `dispatch_decisions` do not cover the same symbol set. Part 2 is therefore
  computed entirely inside `dispatch_decisions`.
- **Symbol format differs by table.** `predictions` and `dispatch_decisions`
  use `WLD/USDT`; `shadow_trades` uses `WLDUSDT`. A `replace(symbol,'/','')`
  copied from a `shadow_trades` join silently matched **zero** rows here and
  reported every row as `(unmatched)` — a join that cannot succeed, which
  looks exactly like a cohort with no data. Three tables, two formats.

---

# AMENDMENT — the DAILY series, and the ratio discriminator answered early

**Added:** 2026-10-26, same day. **Status:** measured, read-only. No action.

## Why this amendment exists

The operator's order for this measurement asked for two things. Part 1
above delivered the first: one location-shift parameter, fitted, and
whether it reconciles both the 0.284x and 2.29x ratios. It does at the
scoring layer (delta = -0.0145 vs -0.0144, disagreement 0.0001) and does
not at the dispatch layer.

It did **not** deliver the second: *"Daily counts >= +0.36 and <= -0.36
with the distribution's location, same population as the projection,
spanning well before the flip."* Part 1 used two aggregate buckets
(7.000 d pre, 3.001 d post), not a daily series. **Status reported
honestly: the fit was done, the daily series was outstanding.** It is
below.

## The series

`predictions`, `timeframe='1h'`, all cohorts, signed `final_score`, 46
UTC days **2026-09-11 -> 2026-10-26**, **n = 87,503**. Spans well before
the 2026-10-23 flip, as ordered.

Pooled over the whole window: **954** rows at `fs >= +0.36`, **846** at
`fs <= -0.36`. **S/L = 0.887** -- over 46 days the LONG tail is
*slightly larger* than the SHORT tail.

## This answers the 2026-11-03 pre-registration's discriminator, early

The pre-registration
(`2026-10-26-short-overshoot-resolvability.md`) set the test as **the
ratio's behaviour, not the overshoot's sigma**, with two competing
predictions:

| hypothesis | prediction |
|---|---|
| unification / distribution shift | the ratio moves **WITH** regime |
| structural asymmetry | SHORT stays **~2x** LONG at equal thresholds **REGARDLESS** of the regime label |

Split the 46 days by the **sign of the daily location** (`mean
final_score`), at the same symmetric +-0.36 thresholds throughout:

| day group | days | n | LONG tail | SHORT tail | **S/L** |
|---|---|---|---|---|---|
| location > 0 | 25 | 50,400 | 765 | 138 | **0.180** |
| location <= 0 | 21 | 37,103 | 189 | 708 | **3.746** |

**A 20.8x difference in the ratio between the two regime groups.**

Per-day, regressing `log((S+0.5)/(L+0.5))` on the daily mean
`final_score` (Haldane correction keeps zero cells finite):

- Pearson **r = -0.9023**, **t = -13.879** on df = 44
- slope **-25.89** log-ratio per unit `final_score`
- **R^2 = 0.814** -- **81.4%** of the ratio's day-to-day variation is
  explained by the location **alone**

Restricting to the 30 days carrying **>= 20 tail events** (so a ratio is
not a 1-vs-0 artefact), the finite S/L range is **0.00 to 33.00**, with a
further two days at LONG = 0 (infinite). And the **dominant tail matches
the location's sign on 41 of 46 days**, exact one-sided binomial
**p = 2.2e-08**.

### The structural hypothesis made a falsifiable prediction and it failed

This is not a check that cannot fail. "SHORT ~2x LONG **regardless of
regime**" predicts a SHORT **floor** on positive-location days. Observed
on those 25 days: **S/L = 0.180**, i.e. LONG outnumbers SHORT **5.5:1**.
There is no floor. The prediction was testable, was tested on 50,400 rows
of positive-location data, and is **falsified as a regardless-of-regime
claim**.

What survives is the honest version: a location shift mechanically moves
the two tails in opposite directions, so **the asymmetry is a location
phenomenon**. That is a confirmation of the mechanism rather than a
surprise, and it is stated that way deliberately.

## What this does and does not replace

**DOES answer:** the operator's decision question -- *does 42 cards/day
decay on its own, or persist?* The SHORT excess tracks a location that is
currently negative but has **crossed zero repeatedly** on a 2-5 day
timescale (10-20 is +0.020 with 36 LONG / 7 SHORT; 10-22 is -0.115 with 3
LONG / 32 SHORT; 10-23 is -0.057 with 27 LONG / 20 SHORT). September ran
predominantly **positive**. There is no evidence of a persistent negative
location, so **the distributional component should revert**.

**DOES NOT replace the 2026-11-03 read.** That read is about the
overshoot's magnitude once the post-flip window has accumulated. This
measures the ratio's *dependence on location* using regime variation that
already exists in the data rather than waiting for more of it. The two are
complementary, and **the 11-03 date stands**.

**AND THE TRAP STILL APPLIES**, restated because it is easy to lose: a
non-arrival at 3 sigma on 11-03 must **not** be read as a failed
measurement. Decay is the **predicted** outcome of the leading
hypothesis, and this amendment strengthens that hypothesis rather than
weakening it. "Still not significant" will be the **confirmation**.

## The standing decision is unchanged

**Do not raise the SHORT bar.** Part 1's reason (the dominant 57% term is
a LONG conversion collapse downstream of scoring) still holds, and the
daily series adds a second: retuning the bar now would fit it to a
location that oscillates across zero on a few-day timescale, which is the
definition of fitting to a component expected to revert.
