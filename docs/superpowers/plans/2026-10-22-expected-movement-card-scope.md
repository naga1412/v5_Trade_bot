# Scope: surface expected movement magnitude on the Telegram card

**Status: SCOPE ONLY, four questions RULED. Nothing built, nothing
flagged, no code touched.**
Written 2026-10-22 as the single actionable output of the A0 closure
(`2026-10-22-a0-escalation-result.md`).

**Display only. No scoring change, no gate change, no sizing
automation.**

## What this is, stated so it cannot be misread later

**The number is a VOLATILITY ESTIMATE, not a conviction score.**

It says *"this signal is likely to swing more than usual"*. It does
**not** say the swing is more likely to go the trade's way. Those are
different claims and the whole point of the A0 closure is that this
scorer supports the first and not the second.

**IT DOES NOT IMPROVE EXPECTANCY AND IS NOT CLAIMED TO.** With a fixed
stop and target, a magnitude signal lifts both barriers' hit rates and
nets approximately zero — which is exactly what final P&L has shown
throughout Phase A, for every variable, in every window. Anyone who
later finds this card field and reasons "high number, better trade" will
be wrong, and this paragraph exists so that reasoning has somewhere to
collide with the record.

## Why it is worth surfacing anyway

**The operator sizes every position by hand.** That is the one decision
layer in the system that can use a magnitude estimate, because sizing is
the lever that responds to expected movement rather than to expected
direction. Risk per trade is the operator's to set, and *"this one is
likely to move about 1.6 R in each direction rather than 1.3"* is
decision-relevant to that choice in a way it is not to the entry
decision.

So the proposal is narrow: **transfer a measured fact to the only layer
that can act on it.** Nothing more.

## The evidence it rests on

From the A0 full-range escalation, 37,682 rows over 217 symbols, 60d,
cluster-robust by symbol:

```
MAE_R @10 bars   bottom decile +1.0461  ->  top decile +1.3309
                 diff +0.2848   clustered SE 0.0330   +8.62 sigma
                 PASS at the 3.729-sigma corrected bar
```

Confirmed across two windows: the in-band test read **+3.02σ** on the
same sign with an 18× smaller population, and the full-range test reads
**+8.62σ**.

And the companion fact that makes it a magnitude claim rather than a
directional one:

```
MFE gain +0.3522 R   MAE gain +0.2848 R   asymmetry 1.24x
```

Both barriers move together. `atr/close` is flat across deciles, so this
is **realised path volatility beyond what ATR already captures** — which
is precisely why it is not already visible in the ATR-derived stop
distance the card presumably shows today.

## What the field should say

Design intent, not final copy:

- Express it in **R units**, because the trade's own R is the scale the
  operator already sizes against, and because the measurement is in R.
- Express it as a **two-sided range or a single "expected excursion"**,
  never as a one-sided upside figure — a one-sided presentation
  re-introduces exactly the directional misreading this document exists
  to prevent.
- Carry a short label that names it as volatility. Something to the
  effect of *"expected swing"* rather than *"strength"*, *"confidence"*
  or *"conviction"*.
- **Bucket rather than point-estimate.** `sd(mfe10)` is heavy-tailed and
  non-monotone across deciles (1.56 … 4.13), so a precise per-signal
  number would overstate what the data supports. Coarse bands —
  something like typical / elevated / high — match the resolution
  actually available.

## The four questions, RULED (operator, 2026-10-22)

### a. Boundaries: FIXED from the measured population, with the snapshot date

**Do not recompute on a rolling window.** Drifting boundaries would need
re-validation on every recompute, and **coarse bands make boundary
precision almost irrelevant anyway** — the whole reason for bands rather
than a point estimate.

Record the cuts with the date they were measured
(2026-10-22, 60d, n=37,682) so the provenance travels with the number.

### b + c. SHORT and `futures_poll`: ONE RULE COVERS BOTH

**Show the band only where it was measured: LONG, spot-listed. Omit the
field everywhere else rather than extrapolating.**

**A missing field is honest; a band derived from LONG data shown on a
SHORT card is not.** Same for `futures_poll`, which FU-53 excluded from
the measurement entirely — 60 of 279 symbols.

Both become available when their mirror runs happen, and **neither
blocks the LONG case.** This also means the field's absence is
informative rather than a bug, which the implementation should not
"helpfully" paper over with a fallback.

### d. It is a LOOKUP TIMES A MULTIPLIER, not a new estimator

**Corrected: the original scope was wrong about this.** It claimed
nothing computes an expected excursion at signal time and therefore a
new live estimator was needed. Checked against the code:

- `SignalProposal` carries no `atr` field — which is what the original
  scope saw — **but it carries `entry_price` and `stop_loss_price`, and
  R is their difference by definition.**
- `_build_trade_setup` (`app/core/predictor.py`) sets
  `sl = last_close - 1.5 * atr`, so **`R_live = 1.5 × ATR` exactly**, and
  ATR is recoverable as `R_live / 1.5`.

So at dispatch: the score gives the decile by **lookup**, the decile
gives the band in R, and R is already on the proposal. **No new
estimator.**

#### The one real complication, and it bites where the card matters most

**The two R definitions diverge.** The measurement used shadow's engine,
`R = min(1.5 × ATR, 0.05 × entry)` — the 5% stop cap from PR #286. The
live path has **no cap at all**: `_build_trade_setup` is uncapped
`1.5 × ATR`. That is the documented shadow-vs-live geometry gap, not a
new finding.

They differ exactly when `1.5 × ATR > 0.05 × entry`. Measured
`atr/close` averages ~0.0225, so `1.5 × ATR` is typically ~3.4% of entry
and the cap usually does not bind — **but it binds in the high-volatility
tail, which is precisely the subpopulation this card exists to flag.**

**Ruling: convert using the CAPPED definition**, reconstructing ATR as
`R_live / 1.5` and applying `min(1.5 × ATR, 0.05 × entry)`, so the
displayed band is denominated in the same R its evidence was measured
in. Using the uncapped live R would overstate the expected movement for
exactly the symbols the operator most needs it to be right about.

## What would falsify it

If the magnitude relationship does not hold **prospectively** — on
signals dispatched after this scope was written — the field should be
removed rather than re-tuned. The claim is narrow enough that a forward
check is cheap: bucket dispatched signals by the displayed band and
compare realised excursions at a fixed horizon.

**That forward check is not pre-registered here.** If the operator wants
to build this, it gets its own pre-registration first, like everything
else.
