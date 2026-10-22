# Phase A — result, and the downgrade that followed

**Status: closed.** Implements
`2026-10-21-phase-a-preregistration.md` (#589, amended #590), both merged
to main before the probe existed.

**Headline: no variable in Phase A moves final P&L, and the one that
appeared to move the PATH did not survive its own measurement window.
Nothing here is actionable.** The reasoning for the downgrade is a
standing rule in its own right:
`2026-10-22-measurement-window-rule.md`.

Population: n=2,128 LONG shadow trades, `entry_score >= 0.30`, 60d, 0
fetch failures. Run 2026-10-22.

## The power note still governs, as pre-registered

**The effect we need is ~0.06-0.1pp. These scans resolve ~0.2-0.3pp at
best.** A null in Phase A does not exclude a useful effect, and every
point estimate below is reported with its SE even where it fails the
bar. **Sub-bar results are information, not proof, and are not
discarded.**

Bar for a finding: **3.68 sigma**, from 12 primary comparisons
Bonferroni-corrected off 3 sigma. Bars were computed at runtime from the
**measured per-cell SEs**, not from the pre-registration's estimated
`n` table — which matters, because that table turned out optimistic: it
projected 4,124 for 60d and 8,010 for full history against measured
**2,128** and **4,710**. The difference is most likely the
geometry-validity and timeframe filters the scan applies and that table
did not name; not investigated further. **Had the bars been taken from
the pre-registered table they would have been roughly 1.4x too narrow.**
Computing them from real SEs is the single design choice that kept the
verdicts honest, and it is one of the mutations the test suite pins.

## A1a — `mtf_agreement >= 4`: the finding, and its downgrade

As **first measured**, over each trade's realised holding window:

| target | mtf<4 (n=536) | mtf>=4 (n=1592) | diff | sigma | bar(3.68) | verdict |
|---|---|---|---|---|---|---|
| **MFE_R** | +1.2124 | +1.5482 | **+0.3357 R** | **+5.78** | 0.2137 | PASS, *since retracted* |
| final pnl_pct | -0.1044 | -0.0097 | +0.0947pp | +0.74 | 0.4716 | FAIL |
| MAE_R | +1.3333 | +1.3659 | +0.0326 R | +1.16 | 0.1032 | FAIL |

It passed three confound controls:

- **Volatility — excluded, and this was the deciding check.** The mtf>=4
  cell carries the larger sd (1.436 vs 1.055), so the MFE gain could
  have been nothing but more volatile trades, which would have raised
  MAE comparably. MFE gain **+0.3357 R** against MAE gain **+0.0326 R**,
  net **+0.3032 R**. The favourable excursion grew about 10x more than
  the adverse one.
- **Symbol concentration — excluded.** Top-5 share 21.5% vs 21.3%; 72 vs
  79 distinct symbols.
- **Duration — adjusted, and this is where it went wrong.** See below.

**Then the target itself turned out to be post-treatment.** `MFE_R` was
measured over the realised holding window, whose length is set by which
barrier was hit — an outcome — and mtf>=4 trades are held 13.04 bars
against 9.60. Recomputed over **windows fixed by design**:

| horizon | mfe mtf<4 | mfe mtf>=4 | diff | SE | sigma | advantage |
|---|---|---|---|---|---|---|
| 5 | 0.9576 | 1.0833 | +0.1256 | 0.0531 | **+2.36** | 13.1% |
| 10 | 1.3896 | 1.5660 | +0.1764 | 0.0867 | **+2.03** | 12.7% |
| 20 | 1.9323 | 2.1976 | +0.2653 | 0.1115 | **+2.38** | 13.7% |
| 40 | 2.7448 | 2.9696 | +0.2247 | 0.1529 | **+1.47** | 8.2% |

**THESE FOUR NUMBERS ARE NOT FOUR CONFIRMATIONS.** The windows are
nested — 5 inside 10 inside 20 inside 40, on the same trades — so the
maxima are highly correlated by construction: a trade's 40-bar MFE *is*
its 5-bar MFE unless price went further later. This is **one ~2 sigma
observation measured four ways.** "Consistent at every horizon" is not
corroboration here and must not be read as any; left unqualified it
inflates one piece of evidence fourfold. The independent replication
axis is temporal halves and out-of-sample windows, never nested horizons
on the same rows.

```
realised-window diff  +0.3358 R  (+5.78 sigma)
fixed-10-bar diff     +0.1764 R  (+2.03 sigma)
window length         +0.1594 R  = 47% of the headline
```

**Nothing clears the uncorrected 3 sigma bar, let alone 3.68.** And the
advantage is proportionally flat across horizons, so **the compounding
dose-response is dead** — it was the stratifier sorting on the same
contamination it was meant to remove.

### What survives

A positive ~13% proportional MFE advantage at ~2 sigma, on one
observation. Per the power note: **reported with its SE, not discarded,
not acted on.** The volatility and symbol-concentration controls stand
and are unaffected.

### The n a future re-test would need, and why we are not scheduling one

Recorded so a future session can decide **deliberately**, from numbers,
rather than inheriting a standing "revisit when data accumulates" item
that nobody owns. **No watch, no reminder, no cron. This rests here.**

Holding the current point estimates, the n multiple needed to reach
3.68 sigma:

| horizon | effect | SE now | n multiple | n required |
|---|---|---|---|---|
| 5 | +0.1256 R | 0.0531 | 2.42x | ~5,150 |
| 10 | +0.1764 R | 0.0867 | **3.27x** | **~6,960** |
| 20 | +0.2653 R | 0.1115 | 2.39x | ~5,090 |
| 40 | +0.2247 R | 0.1529 | 6.27x | ~13,340 |

**A future pre-registration must nominate ONE horizon in advance.**
Picking 20 because it currently reads best is precisely what the
correction exists to prevent, so the operative figure is whichever
horizon is nominated, not the minimum of that column.

**What exists today, measured 2026-10-22 against the scan's exact
filter:** the entire population since inception is **4,710** trades,
first close **2026-05-11**. 60d is 2,128, 120d is 3,930, and
180d / 365d / all-history are all 4,710 — there is no more history than
that.

Two paths, with their real costs:

1. **Widen the window to full history — available today, insufficient.**
   4,710 would clear the *uncorrected* 3 sigma bar at horizons 5, 10 and
   20 if the effect holds, and **would not reach 3.68 at any horizon.**
   That lands in the pre-registered SUGGESTIVE band: re-test, do not
   announce. It is also **not an independent test** — the present 2,128
   rows are 45% of it, so it is a partially overlapping re-measurement,
   not a replication; only the 2,582 rows outside the 60d window are new
   and every one of them is OLDER. That imports regime heterogeneity
   across May-October, which is the exact trap behind the LONG/SHORT
   asymmetry turning out to be an October artifact.
2. **Wait for genuinely new trades.** An out-of-sample 3.68 sigma test at
   horizon 10 needs ~6,960 **new** closures, not 6,960 total. At the
   observed recent rate of ~35.5 closures/day that is **~196 days, i.e.
   around early May 2027.** The lifetime rate is lower (~28.7/day), so
   even that is optimistic.

**And all of it is optimistic in a second way:** this point estimate is
one that attracted attention, so the true effect is more likely below it
than above. Every n in that table is a floor, not a target.

## A0 — entry_score vs the path

**In-band null on pnl_pct and MFE_R, so the pre-committed full-range
escalation is triggered.** The measured contrast between extreme deciles
is only **0.1368 score units**, against a detectable slope of **7.8
pp/unit at 3 sigma** for pnl_pct — so the pnl null carries essentially
no information, exactly as #590 pre-committed.

- pnl_pct: d0 +0.1019 -> d9 -0.2468, -0.98 sigma, FAIL
- MFE_R: +1.5100 -> +1.4459, -0.45 sigma, FAIL
- **MAE_R: +1.2867 -> +1.4824, diff +0.1957, +3.02 sigma -> SUGGESTIVE**
  (clears 3, not 3.68). Replicates in both halves. Higher score gives a
  *larger* adverse excursion, which is the **wrong sign** for a
  conviction signal.

## A2 / A3 / A4 / A1b

- **A2 `oi_delta_24h`** (93.0% matched): null on both targets (-0.10
  sigma pnl, +1.02 sigma MFE).
- **A3 `funding_rate`**: **underpowered, no conclusion permitted** — only
  223/2107 matched (10.6%; the table starts 2026-10-07). Point estimates
  banked for a December re-run: pnl -0.7177pp (-2.08 sigma), MFE
  -0.3957 R (-2.58 sigma), consistent across halves.
- **A4 depth (daily proxy)**: +1.82 sigma pnl, +1.83 sigma MFE, both
  sub-bar. A null here does **not** rule out dispatch-time depth; #555
  enables a real test around late November.
- **A1b `funding_directional_adj`**: +0.84 sigma pnl, +1.83 sigma MFE,
  sub-bar. Cells are 1788 vs 95 — the column holds only two distinct
  values (0.0 / 0.1), so this is baseline-vs-elevated, not a median
  split.

## The meta-observation, which outlasts every cell

**MFE_R is systematically more responsive than final pnl_pct across the
entire scan** — positive sub-bar MFE estimates for A1b (+1.83), A2
(+1.02) and A4 (+1.83), while *no* variable moves final P&L. That is
consistent with the stop pinning the outcome ~70% of the time, and it
vindicates treating final P&L as a contaminated target for measuring
signal quality.

**It does not vindicate `MFE_R` as a clean one.** Over a realised window
MFE is post-treatment, which is the finding this document records. A
path target is only worth more than final P&L once its window is fixed
by design.

Baseline-event agreement, stated before the path targets per
`2026-10-21-counterfactual-baseline-subset.md`: the model resolves 83.6%
stop-outs against the database's 72.4%. MFE_R and MAE_R are descriptive
properties of the realised path and carry no baseline-event requirement
— but any "what if we exited at MFE" simulation inherits that 11.2pp
gap.

## What this closes

- **The conditional take-profit lane does not open.** Its entire premise
  was a score informative about reach that the exit discards. At ~2 sigma
  sub-bar there is nothing to build a TP-distance by hold-horizon grid
  on. The design remains correct in shape if something ever passes.
- **`mtf_agreement >= 4` is not a lever.** Not reopened on aggregates,
  not reopened on the realised-window figure, and not scheduled.

## Related

- `2026-10-22-measurement-window-rule.md` — the standing rule this
  produced, including the worked example of the reassurance that
  defeated it.
- `2026-10-21-phase-a-preregistration.md` — the pre-registration, with
  A0's escalation.
- `2026-07-29-study-1-flat-geometry-ladder.md` — the flat unconditional
  ladder, whose retracted +0.481 figure is **not** carried forward here
  in any form.
