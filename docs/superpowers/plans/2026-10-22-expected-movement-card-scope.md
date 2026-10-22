# Scope: surface expected movement magnitude on the Telegram card

**Status: SCOPE ONLY. Nothing built, nothing flagged, no code touched.**
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

## Open questions to settle before building

1. **Which decile boundaries, and computed when?** The measurement used
   60d deciles of `final_score`. A live card needs a rule that does not
   drift with the window, which is a decision, not an implementation
   detail.
2. **Does it apply to SHORT?** Everything above is LONG-only. The
   SHORT-side score range mirrors it (−0.5261 to −0.0500) but the
   excursion measurement has not been run on SHORT, so the field should
   either be LONG-only at first or wait for the mirror run.
3. **Is `futures_poll` covered?** No — **FU-53**. 60 of 279 symbols were
   dropped by a spot-klines fetch, systematically the futures-only
   cohort. A card field that silently gives a less-founded number for
   those symbols is worse than one that omits it, so this needs the
   FU-53 fix or an explicit per-cohort abstain.
4. **Where does the number come from at dispatch time?** The measurement
   is retrospective, over fixed bar horizons. Nothing currently computes
   an expected excursion at signal time, so this is a new live
   computation, not a lookup.

## What would falsify it

If the magnitude relationship does not hold **prospectively** — on
signals dispatched after this scope was written — the field should be
removed rather than re-tuned. The claim is narrow enough that a forward
check is cheap: bucket dispatched signals by the displayed band and
compare realised excursions at a fixed horizon.

**That forward check is not pre-registered here.** If the operator wants
to build this, it gets its own pre-registration first, like everything
else.
