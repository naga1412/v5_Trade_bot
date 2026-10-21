# Three measurement facts worth not re-deriving

Written 2026-10-21 because each was found the hard way and each will
otherwise be rediscovered or, worse, mis-cited.

## 1. A random policy reads −2.30σ through the BOOST/SUPPRESS harness

Phase 2's offline brain evaluation ran its **untrained** neutral
baseline through the identical harness. On the primary window
(established symbols, Aug20–Sep05, n=1021) the untrained policy's
value-head split read:

```
V(s) TOP_HALF     n=511   mean=-0.209%
V(s) BOTTOM_HALF  n=510   mean=+0.299%
TOP - BOTTOM gap  = -0.508%  SE_diff=0.221%  ->  -2.30 sigma
```

**A model with random weights produced a 2.3σ "separation".** So ~2σ
through that harness requires no learning at all, and a 2σ value-head
read is not evidence of anything. This is the single most useful number
either closure produced, and it is the reason the neutral baseline was
worth specifying: without it, the trained model's −1.19σ wrong-sign
primary could plausibly have been written up as "weak but directional".

### SCOPE — this applies to that harness ONLY

**It says nothing about results obtained through other paths, and it
must not be cited to dismiss them.** Specifically it does **not** bear
on:

- **the paired breakeven-variant read** — paired Δ on the same trade
  under an alternate exit rule, where unarmed pairs are provably
  exactly 0 and the pairing removes the between-trade variance that
  this harness is exposed to;
- **the binomial win-rate tests** (e.g. the 4.16σ LONG/SHORT win-rate
  gap) — a proportion test on counts, with no model, no bucketing and
  no value head;
- **the direct-SQL correlate scans and the BTC trend-filter test** —
  cell means with per-cell SEs computed from the real data.

What makes the brain harness susceptible is specific: it **buckets
trades by a model's own output** and then compares outcome means
between buckets. A random bucketing of a heavy-tailed outcome
distribution will separate by chance far more often than a naive SE
suggests, because the bucket assignment is itself a fitted object. None
of the three paths above has that property.

The honest general lesson is narrower than "2σ means nothing here": it
is **any metric that sorts outcomes by a model's own score needs an
untrained baseline through the identical code before its sigma can be
read at all.**

## 2. Provenance of the two entry-timing constants

Neither figure existed anywhere in the repo; both came from operator
working notes and are recorded here so they stop being unexplained.

- **The 0.45% floor** is 2σ at n=150 with paired σ=2.75%:
  `SE = 2.75 / sqrt(150) = 0.2245%`, and `2 × 0.2245% = 0.449%`.
- **The 1.63/day accrual rate** is uncapped f × the live-eligible
  crossing rate: `0.534 × 2.85/day = 1.52/day`, which reconstructs it
  to within rounding. With the pre-registered ≤8-bar cap applied the
  figure is `0.354 × 2.85 = 1.01/day`.

## 3. The shadow entry bar is 0.30. The live dispatch gate is 0.36.

**These are different numbers and conflating them corrupts any
denominator built from the shadow population.** Verified directly:
`min(abs(entry_score))` over every shadow open in the 42-day recon
window is exactly **0.300**, in all four timeframe/direction segments,
and 100% of opens clear it.

- **0.30** — `SHADOW_ENTRY_THRESHOLD`-class, symmetric
  (LONG > +0.30, SHORT < −0.30). This is what decides whether a shadow
  trade opens at all.
- **0.36** — `MIN_ENTRY_SCORE_LONG`, the **live dispatch** gate. It
  filters what reaches the operator. It does **not** filter the shadow
  population.

Filtering shadow trades at `>= 0.36` therefore does not give you "the
shadow population"; it gives you the live-eligible *subset* of it, which
is roughly a third of the rows. Using it as a denominator inflated the
entry-timing f estimate by ~24% (289 vs the true 356 crossings for
1h/LONG) before it was caught. Pick the threshold that matches the
question: 0.30 for "what did shadow do", 0.36 for "what would the
operator have seen".
