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
