# A0 full-range escalation — operational amendment

**Status: pre-registration amendment. Written and committed BEFORE the
escalation probe exists.** The git history of this file is the evidence.
Every decision below was fixed from the *population's* properties, which
were read first and are reported here, and from none of the escalation's
results, which do not exist yet.

Amends `2026-10-21-phase-a-preregistration.md`, section "A0's contrast
problem, and a PRE-COMMITTED ESCALATION" (#590). That section fixed
**whether** to escalate and **why**. This one fixes **how**, because the
population turned out to have three properties the original design did
not anticipate — one of which would have produced a false positive.

## Why an amendment at all

#590 pre-committed the escalation in one paragraph: MFE_R / MAE_R on
`predictions` rows, hypothetical entry, ATR-based stop geometry. Reading
the actual population turned up three things that paragraph could not
have known, and two of them change the statistics rather than the
plumbing. **Deciding them now, in writing, before the probe exists, is
the only way they are decisions rather than rescues.**

## The population, measured first (sql-select runs 39734427610 and 39734649104, 2026-10-22)

`predictions` schema, read from prod `information_schema`: it carries
`symbol`, `timeframe`, `ts`, `final_score`, `direction`, `confidence`,
`layer_scores` — and **no price, no ATR, no stop, no target.** The
geometry must be reconstructed, and the constants are in
`backend/app/shadow/engine.py`, not guessed:

```
LONG_THRESHOLD = 0.30     SL_ATR_MULT = 1.5
MIN_CONFIDENCE = 0.50     TP_ATR_MULT = 3.0
sl = max(last_close - 1.5*atr, last_close * 0.95)      <- the 5% cap
R  = last_close - sl = min(1.5*atr, 0.05*last_close)
atr = mean of the last 14 true ranges  (simple mean, NOT Wilder)
```

60-day distribution, `ts >= NOW() - 60 days`:

| timeframe | direction | n | symbols | n >= 0.30 | n in (0, 0.30) | min | p10 | p50 | p90 | max |
|---|---|---|---|---|---|---|---|---|---|---|
| 1h | LONG | **44,606** | **279** | 3,379 | **41,227** | 0.0500 | 0.0676 | 0.1412 | 0.2785 | **0.5642** |
| 1h | NEUTRAL | 25,212 | 272 | 0 | 13,887 | -0.0500 | -0.0422 | 0.0064 | 0.0438 | 0.0500 |
| 1h | SHORT | 33,167 | 234 | 0 | 0 | -0.5261 | -0.2720 | -0.1383 | -0.0659 | -0.0500 |

**The escalation's premise is confirmed: 92.4% of positive-score rows sit
BELOW the entry bar.** The traded band saw a 0.235-wide slice of a
variable whose realised LONG range is **0.0500 to 0.5642**.

### Finding 1, which is structural and belongs on the record now

**`final_score` has an effective ceiling near 0.57, and a floor at
|0.05|.** The in-band truncation was always described as "a 0.19-wide
span of a variable whose full range is roughly 0-1". **That framing was
wrong.** The scorer does not produce scores near 1; across 44,606 LONG
rows and 279 symbols over 60 days the maximum is 0.5642, and |score| <
0.05 is labelled NEUTRAL by construction. So the real contrast available
to *any* score-based test, ever, is ~0.05-0.56 — not 0-1. The entry bar
truncates the top-third of a range that is itself about half as wide as
assumed.

## Decision 1 — the window is fixed by design, and ONE horizon is criterion-bearing

Per `2026-10-22-measurement-window-rule.md`. Hypothetical entries have
no realised exit, so there is no realised window to be contaminated by
— **this measurement is clean on that axis for free**, and that is a
point in the escalation's favour, not merely an absence of a problem.

Horizons: **5, 10, 20, 40 bars**, the same four as #597, so the figures
are directly comparable to A1a's.

**NOMINATED CRITERION-BEARING HORIZON: 10 bars.** Chosen because the
real population's mean realised hold is 9.60 bars (mtf<4) and 13.04
(mtf>=4), so 10 sits closest to the realised centre of the trades this
is meant to inform. The other three are **descriptive**. Nominating one
in advance is mandatory and the reason is recorded above, because
selecting the best-reading horizon afterwards is exactly what the
multiple-comparison correction exists to prevent.

**And the four will be reported with the nesting caveat attached:** 5
inside 10 inside 20 inside 40 on the same rows means the maxima are
highly correlated by construction, so agreement across them is **one
observation measured four ways**, never four results.

## Decision 2 — SEs MUST be cluster-robust, and this is the one that would have produced a false positive

**The rows are not independent, and the naive SE is badly wrong.**
Hourly `predictions` rows on the same symbol overlap: a 40-bar MFE at
hour *t* and at hour *t+1* share 39 of their 40 bars. n=44,606
overlapping windows do not carry 44,606 windows' worth of information,
and treating them as if they did inflates every sigma by roughly the
square root of the design effect.

This is the **same family as the finding that triggered this
amendment** — one quantity viewed repeatedly, dressed as independent
evidence. It would have arrived here as a tidy +8σ.

Pre-committed, in order:

1. **PRIMARY: SEs clustered by `symbol`.** 279 clusters, comfortably
   enough for cluster-robust inference. Within-symbol serial correlation
   is the dominant dependence.
2. **The naive SE is printed beside it**, so the inflation factor is
   visible rather than asserted. If the ratio is near 1 the clustering
   was unnecessary and the output says so.
3. **SENSITIVITY: SEs clustered by calendar day.** 279 symbols move
   together with BTC, so timestamp is a second clustering dimension.
   Reported, not pooled into the primary.
4. **REPLICATION: a thinned, strictly non-overlapping subset** — at most
   one row per symbol per nominated-horizon window. Consistency (same
   sign, within 2 SE of primary), not re-significance.

**A result that is significant only on the naive SE is NOT a finding and
will be reported as a failure of this test.**

## Decision 3 — population: all LONG-direction rows, not "untraded only"

#590's wording was "`predictions` rows that **never became trades**".
Taken literally that anti-join **removes the highest-scoring rows** —
they are precisely the ones that cleared 0.30 and traded — which would
re-truncate the top of the range the escalation exists to restore. The
literal filter defeats the stated intent.

**PRIMARY: all 1h rows with `direction = 'LONG'`,** traded or not. This
is clean, not a liberty: MFE_R and MAE_R over a window fixed by design
are **descriptive properties of a price path**, not counterfactual
exits, so a row having also become a real trade introduces nothing. The
pre-registration says as much for A0 already.

**SECONDARY, reported for completeness: the untraded-only subset**, via
`REPLACE(p.symbol, '/', '') = t.symbol` — the slash-vs-concatenated
format difference that silently returns zero rows if forgotten.

**Deviation declared: entry at the signal bar's CLOSE, not the next
bar's open.** #590 said "next bar's open". The engine enters at
`last_close` (`engine.py` `evaluate()`), and matching the engine keeps
MFE_R numerically comparable with A1a's. On continuous 1h bars the two
differ by a tick.

**Confidence gate NOT applied in the primary.** `MIN_CONFIDENCE = 0.50`
would drop ~16% of LONG rows (37,288 of 44,606 clear it). The escalation
answers "does score predict path", which #590's own caveat already
separates from "does score predict path on trades we would take", so the
gate belongs in the secondary — where it is reported.

## Decision 4 — the bar

**12 primary comparisons becomes 14**: this escalation adds MFE_R and
MAE_R at the nominated horizon. Bonferroni off 3σ:

```
12 comparisons -> alpha' = 2.250e-4 -> 3.688 sigma   (as pre-registered)
14 comparisons -> alpha' = 1.929e-4 -> 3.729 sigma   (this run)
```

**The stricter 3.73σ governs**, and both are printed. 3σ to 3.73σ
remains SUGGESTIVE — re-test, do not announce.

## What this CAN detect, stated before it runs

sd(MFE_R) at horizon 10 is **~1.74 R**, backed out of #597's measured
SE_diff (0.0867 on cells of 536 and 1,592) rather than assumed.

| | in-band A0 | full range, NAIVE SE | full range, CLUSTERED (expected) |
|---|---|---|---|
| extreme-decile n | ~213 each | ~4,461 each | ~4,461, design effect ~9 |
| SE_diff | 0.1424 | ~0.0368 | ~0.11 |
| diff detectable at bar | 0.524 R | 0.137 R | **~0.41 R** |
| score contrast | 0.1368 | ~0.27 | ~0.27 |
| **detectable slope** | **~3.8 R/unit** | ~0.51 R/unit | **~1.5 R/unit** |

**The honest figure is ~2.5x better than in-band, not ~7x.** The naive
calculation says 7x; the clustering eats most of it, and the naive
calculation is the wrong one. Stating both is the point — a future
reader who sees only the 7x will over-read whatever comes back.

**So: this test can detect a slope of roughly 1.5 R per score unit over
a 0.27-unit contrast.** A real effect smaller than that will not clear
the bar here either, and the power note still governs: **sub-bar
estimates are reported with their SEs, not discarded.**

## Invariants to print, per the counterfactual rule

- **n per decile** — must be ~equal by construction; unequal means the
  decile cuts hit ties.
- **sd per decile** — a monotone trend in sd across score deciles means
  the score is partly a volatility proxy, which is the A1a mechanism
  that nearly passed.
- **distinct symbols per decile** — a top decile concentrated in few
  symbols is the symbol-concentration confound.
- **mean ATR/close per decile** — if high-score rows are systematically
  higher-ATR, R is systematically larger and MFE_R is deflated for them.
  **This one can produce a wrong-sign result**, so it is printed before
  any estimate.

## Outcomes, fixed in advance

- **Score predicts MFE_R at the nominated horizon, clustered, over the
  bar** → the first real score-quality finding in this project. Response
  is still not a code change: it would need a forward pre-registration,
  and #590's caveat stands — a full-range result is a finding about the
  **score**, not a licence to trade below 0.30.
- **Null, clustered** → the score carries no path information across its
  whole realised range, and the iso-expectancy wall is confirmed from a
  second direction. **A0 closes**, and the only remaining A0 question is
  whether the *scorer's* 0.05-0.56 ceiling is itself the problem.
- **Significant naive, null clustered** → reported as a FAILURE, and as
  a second instance of the dependence trap in the same week.
- **Wrong sign (higher score, worse path)** → a finding, reported as
  such. A0's in-band MAE_R already reads +3.02σ in the wrong direction.
