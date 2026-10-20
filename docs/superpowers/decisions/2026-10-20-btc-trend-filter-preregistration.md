# BTC trend filter — pre-registration

**Status: pre-registration. Written and committed BEFORE any split was
run.** The git history of this file is the evidence for that claim; it
exists so the result cannot later be read as a bar fitted to an outcome.
Nothing below was chosen after seeing a point estimate.

## The hypothesis, and where it came from

From the 2026-10-20 LONG/SHORT analysis: **the bot is a trend-follower
with the trend filter missing.** It earns on whichever side matches the
prevailing direction and nets approximately zero overall because nothing
selects the side.

That conclusion is a specific, testable claim and it has never been
tested. BTC trend state at entry is not among the eleven null variables
already examined, and the brain's regime one-hot is not a trend filter
(3 of its 5 dims never fire; only `sideways_grind` and `bull_breakout`
ever appear).

## Honest prior, stated before the result

**The supporting evidence is six monthly data points, post-hoc, and May
is already a counterexample** (BTC −9.24% while LONG returned +0.2522%
and SHORT −0.3644%). That is the shape of the ADX finding that did not
replicate — a compelling mechanism, a small number of windows, selected
after the fact.

What justifies running it anyway: the test is cheap, uses data already
collected, and examines a variable never looked at. **If it fails, the
strategy search is genuinely complete.** The prior is recorded here so
a pass is not over-read and a fail is not treated as surprising.

## VARIABLE — must be knowable at entry

**BTCUSDT's position relative to its own 20-day simple moving average,
evaluated at each trade's entry time.**

Computed strictly without lookahead: for a trade opened at time `T`,
take the 20 most recent BTCUSDT **daily closes whose close time is
strictly before `T`**. `MA20` is their mean. The state is `uptrend` when
the latest of those closes exceeds `MA20`, otherwise `downtrend`.

**Monthly BTC drift is NOT admissible and is not used.** A month's
return is only knowable once the month has ended, so it cannot be traded
on. The monthly table motivated this test; it cannot serve as its
variable. This is the whole reason the variable is defined as a
trailing MA.

Source: Binance spot `/api/v3/klines`, `symbol=BTCUSDT`, `interval=1d`,
fetched from 2026-04-01 so the first in-scope trade already has 20 prior
closes. Read-only; no DB writes.

## TEST

For **LONG** trades: `avg_pnl_pct` in BTC-uptrend vs BTC-downtrend.
Separately and independently for **SHORT** trades. Full history from
2026-05-01, approximately 4,600 LONG and 3,400 SHORT closed trades.

`pnl_pct` is already a percent and is **not** rescaled — see the
2026-10-20 four-probe ×100 defect.

## BAR — 3σ on the difference

The bar is `3 × SE_diff`, where
`SE_diff = sqrt(sd_up²/n_up + sd_down²/n_down)` using the **real
per-cell standard deviations and cell sizes measured from the data**,
not an assumed pooled σ.

Expected magnitude, from σ≈3.19% and roughly 2,700/1,900 per cell:
`SE_diff ≈ 0.096%`, so the bar is **≈0.29%**. The monthly table implies
a gap near 0.5pp, so **this test has power** — it is capable of
returning a pass, which is the property that makes a fail meaningful.

Measured per-direction σ over the last 30 days was 3.156% (LONG) and
2.525% (SHORT), so the realised bar may land slightly below 0.29%. The
computed bar from actual cells is authoritative.

**Reporting order is fixed: cell sizes first, then the computed bar,
then the point estimates.** In that order, in the write-up.

## REPLICATION

Temporal split, first half vs second half of the window by `closed_at`.
The criterion is **consistency, not independent significance**: same
sign AND within `2 × SE_diff` of the primary estimate. A replication
half is not required to clear 3σ on its own.

## What each outcome means

- **Pass (≥3σ, replication consistent):** a tradeable trend filter
  exists and the trend-follower diagnosis is correct. It would then need
  its own forward pre-registration before any gate ships, because this
  is a retrospective test on collected data.
- **Fail:** the diagnosis is not supported by the one variable that
  could support it, and the strategy search is complete. Record it and
  stop.
- **Pass on one direction only:** report as such; do not pool. LONG and
  SHORT are separate tests here by construction.

## Not covered

This tests whether trend state separates outcomes. It does **not**
establish that gating on it would be profitable after fees, nor that the
~0.30 entry bar or any other parameter should change. Those are
different questions and would need their own pre-registrations.
