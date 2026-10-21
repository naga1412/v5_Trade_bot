# Phase A — pre-registration

**Status: pre-registration. Written and committed BEFORE any Phase A
split is run.** The git history of this file is the evidence. Nothing
below was chosen after seeing a point estimate.

Supersedes nothing; this is the first pre-registration for these
variables. Coverage was audited first, deliberately — a
pre-registration that misstates its own `n` is worse than none.

## Power note, stated before anything else

**The effect we need is ~0.06–0.1pp. These scans resolve ~0.2–0.3pp at
best.** So a null in Phase A does **not** exclude a useful effect. Every
point estimate is reported with its SE **even when it fails the bar**,
because a consistent ~0.1pp with the right sign across variables and
windows is information, even though it is not proof. **Sub-bar results
are not to be discarded.**

Bars, computed from σ(pnl_pct) ≈ 3.156 (the measured LONG per-trade sd)
for a two-cell split:

| population | n | SE_diff | 3σ bar |
|---|---|---|---|
| full history | 8,010 | 0.0705pp | **0.212pp** |
| 60d | 4,124 | 0.0983pp | **0.295pp** |
| funding_rate overlap | ~800 | 0.2232pp | **0.670pp** |

For MFE_R and MAE_R the bar is computed from the **realised per-cell
sd**, since those distributions have not been characterised. Reported
before the estimates, as always.

### Comparison count and correction

**12 primary comparisons**: A0 (2) + A1 (4) + A2 (2) + A3 (2) + A4 (2).

At 3σ two-sided (α = 0.0027), Bonferroni gives α′ = 0.000225, i.e.
**a corrected bar of ≈3.68σ**. Both figures are reported: the
uncorrected σ for every estimate, and 3.68σ as the threshold for calling
anything a finding. A variable that clears 3σ but not 3.68σ is recorded
as suggestive and re-tested, not announced.

## A0 — THE HEADLINE TEST: does entry_score predict the PATH?

**entry_score vs MFE_R, and separately vs MAE_R.** Never done — confirmed
by search across `docs/`, `backend/` and the workflow; the MFE probes
(#371/#376/#379) compute MFE_R/MAE_R and report cohort means, ladders and
breakeven simulations, never a score-vs-path relationship.

**Why it outranks the rest, and the logic comes from this week:**

- The TP ladder proved **unconditional** geometry is iso-expectancy, to
  0.002R.
- That holds only if the score carries no information about the **path** a
  trade takes.
- **Conditional** geometry — a different take-profit per score band — is
  **not** ruled out by a flat unconditional ladder.

**Outcomes, fixed in advance:**

- **Score predicts MFE but not final P&L** → the first actionable
  finding in months. The signal is informative about how far a trade
  travels and the exit is discarding it. Response would be
  score-conditional take-profits, which the flat ladder never tested.
  This would require its own forward pre-registration before shipping.
- **Score predicts neither** → the iso-expectancy wall is confirmed from
  a second direction and the search is finished.
- **Score predicts final P&L but not MFE** → treat as suspect, not as a
  finding: final P&L is pinned by the stop ~70% of the time, so this
  combination more likely reflects exit mechanics than signal.

**Reported by entry_score decile**, with cells and bar before estimates,
plus the top-vs-bottom decile contrast as the single criterion-bearing
statistic.

### A0's binding limitation, stated up front

**The score range available in `shadow_trades` is truncated by the entry
bar itself.** Shadow only opens above |entry_score| ≥ 0.30, and the
observed maxima are 0.495 (1h) and 0.507 (15m). So the full traded band
is **0.30–0.51**, and deciles inside it are ~0.02 wide.

A0 therefore tests whether score predicts path **within the traded
band** — not across the score distribution. A null means "no gradient
inside 0.30–0.51", which does not speak to scores below 0.30 that never
became trades.

**The existing `mfe-mae-curve` population is additionally filtered to
`entry_score >= 0.36`**, which would cut the band to its top third. A0
must therefore load at **0.30**, and must not reuse that script's
population — the ladder, stop-width and agreement figures already
recorded came from the 0.36 population and must stay comparable. A0 gets
its own loader and reuses only the bar-fetch and MFE/MAE computation.

## A1 — CONCLUSIVE TIER

Full-history, per-trade, no join. **These carry the interpretable
weight.**

- **`mtf_agreement >= 4` as a THRESHOLD.** Not ruled out by the
  continuous scan (0.80σ) or the median split, since 4 may sit far from
  the median — **the median is reported** so that is checkable rather
  than asserted. Its original +0.481 figure is **UNUSABLE**: the baseline
  it was measured against was retracted in its own document. That number
  is not carried forward in any form.
- **`funding_directional_adj`.** Exists as a column, was never among the
  eleven.

Both against **final pnl_pct AND MFE_R**.

## A2 — CLEAN BUT JOINED

**`oi_delta_24h`**, from `flow_feature_snapshots.oi_24h_delta`
(2026-08-06 → present, 95 symbols). The collector started 5 August and
the table starts 08-06, so **the post-5-Aug restriction is automatic**:
pre-August rows do not exist, so there is nothing to zero-fill and no
temporal leak to create. **No zero-filling under any circumstance** — it
would make "is this an old trade?" readable off one dimension.

**The join's row loss is reported**, not assumed.

Both targets.

## A3 — DESCRIPTIVE ONLY. NO CONCLUSION PERMITTED.

**`funding_rate`**, from `intermarket_snapshots.funding_rate`. That table
begins **2026-10-07** — ~14 days of overlap and 60 of 89 traded symbols,
on the order of **~800 trades**.

**Pre-registered in advance: at ~800 trades the 3σ bar is ~0.670pp,
so this arm CANNOT resolve the ~0.06–0.1pp target.** It is reported with
its point estimate and SE and **labelled underpowered**. No conclusion —
positive or null — may be drawn from it.

Run now rather than deferred because: a point estimate above 0.670pp
would be detectable even at this n, and if it is small we have banked
the reading for a re-run around December when coverage reaches ~4,100
trades.

## A4 — PROXY ONLY

**`depth_0_5pct_usdt`**, from `live_fleet_universe` (2026-08-31 →
present, 277 symbols, **14,426 rows ≈ one snapshot per symbol per day**).

#476 computes depth at dispatch, but what is **persisted** is a periodic
universe snapshot. A join therefore yields the **nearest daily value, not
the value at entry**. Labelled a daily proxy. **A null here does NOT rule
out dispatch-time depth.**

**FORWARD PATH, filed so it is not rediscovered:** #555 now persists
dispatch-time depth in the card payload, so a real test of entry-time
depth becomes available from roughly **late November 2026**. Until then
A4 is descriptive.

## Replication, for every tier

**Temporal held-out split.** The criterion is **consistency, not
independent significance**: same sign AND within `2 × SE_diff` of the
primary estimate. A replication half is not required to clear the bar on
its own.

## The confound rule applies to the MFE arm specifically

Per `2026-10-21-counterfactual-baseline-subset.md`: any "what if we had
exited at the MFE" counterfactual must be restricted to trades whose
baseline exit occurred **as recorded**, not as the model resolves it. The
model resolves ~87.0% of trades as stop-outs against the database's
~70.5%, and that gap will bite identically here — a counterfactual exit
measured over trades that did not exit the way the model thinks measures
the model's error rate, not the intervention.

**A0 itself is not a counterfactual** — MFE_R and MAE_R are descriptive
properties of the realised path, so they carry no baseline-event
requirement. The rule binds only if A0's result motivates a
"score-conditional exit" simulation, which would be a separate,
separately-pre-registered piece of work.

Also per that doc: **name the quantities that should be invariant under
each split and print them.** For these scans the trade count per cell and
the cell sd are the invariants worth watching — a split that changes
either in a way the variable cannot explain is selecting on something
else.

## Reporting order, fixed

For every variable and every target: **cell sizes → computed bar →
point estimates → replication.** In that order.
