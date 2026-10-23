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

## Wording and units, RULED 2026-10-23

### Units: PERCENT OF ENTRY on the card. R stays internal.

Agreed, and for a stronger reason than the convenience one.

**R above 1.0 collides with the stop and reads as nonsense.** The
measured `MAE_R` at horizon 10 runs **1.0461 (bottom decile) to 1.3309
(top)**. A trade's stop sits at exactly **1.0 R** by construction. So a
card saying *"expected adverse swing 1.33R"* tells an operator whose
stop is at 1.0R something that cannot happen to them -- they would be
out at 1.0.

The numbers are not wrong; they are **barrier-free path properties**.
A0 measured raw excursions over windows fixed by design, with no exit
simulation at all. That is the correct way to measure the path, and it
is the wrong way to *present* it to someone reading a card with a stop
printed on it.

**In percent of entry the collision disappears**, because percent makes
no reference to the stop:

```
band_percent = band_R * (R_capped / entry)       <- per trade, not a population mean
R_capped     = min(1.5 * ATR, 0.05 * entry)      <- the measurement's own definition
ATR          = R_live / 1.5                      <- exact; both paths share multiplier and _atr
```

Worked at the 1h lane's mean `R/entry` of 0.02378, horizon 10:

| | MFE_R | MAE_R | as percent of entry |
|---|---|---|---|
| bottom decile | 1.2723 | 1.0461 | ~ +3.0% / -2.5% |
| top decile | 1.6245 | 1.3309 | ~ +3.9% / -3.2% |

Those are quantities an operator can size against without arithmetic,
and they sit in the same units as the card's existing liquidity figures.

**The conversion is per trade, never from the table above.** `R/entry`
varies by trade (`1.5 x ATR / entry`, capped), so using the population
mean would reintroduce the pooled-rate substitution one layer down.

**R stays the internal unit** -- it is what the measurement is in, what
the deciles were cut on, and what any re-measurement must compare
against. It is recorded here and not printed.

### Wording: descriptive of WIDTH, two-sided, never of quality

**The mirror of the "thinner liquidity, unvalidated" lesson.** That
label was factually wrong *and* would have trained the operator to skip
good setups. This field has the inverse failure available: any phrasing
implying conviction -- "strong", "high confidence", "big move expected"
-- inverts its meaning, because the finding is that a higher score buys
larger excursions in **both** directions. The operator must read it as
**size down**, not **this one is better**.

**Band labels are comparative statements about WIDTH, not rungs on a
quality ladder:**

```
narrower than usual   /   usual   /   wider than usual
```

Chosen because **"wider than usual" cannot be misread as "better
signal"**, and because "wider" is a property of a *range*, so the
two-sidedness is carried by the label itself rather than by a caveat
beside it. Ladder words -- typical / elevated / high -- ascend, and
anything that ascends invites reading the top as the good end.

**Rejected phrasings, recorded so they are not re-proposed:** anything
with *strong*, *confidence*, *conviction*, *quality*, *expected move* on
its own (one-sided), or a bare single number without a sign pair.

**Shape of the field** (intent, not final copy):

```
Price swing, next ~10h:  about +/-3.2% of entry  (wider than usual)
Volatility estimate, not conviction.
```

The explicit `+/-` makes the two-sidedness visible in the value as well
as the label, which is the specific misinterpretation available here.
And the second line states what the number is, because the field will
outlive the memory of this document.

### "Consider sizing down" was DROPPED, and the reason generalises

An earlier draft ended the second line with *"Consider sizing down."*
**It prescribes one response among several, and we measured none of
them.**

A wider expected swing could rationally lead to a **smaller position**,
a **wider stop**, or **skipping the trade** -- and if the operator sizes
by fixed risk over stop distance, their position size is **already
ATR-driven**, so a magnitude flag changes nothing about it. We measured
volatility. We did not measure what to do about it.

**That line reintroduced exactly the blend the rest of this field is
built to avoid**: a measured fact welded to an unmeasured claim, in the
one sentence meant to keep them apart. *"Volatility estimate, not
conviction"* carries the whole message, and the operator has traded this
book by hand for months.

**Rule for the field: state the fact, not the response.** If guidance is
ever wanted, enumerate the options rather than selecting one.

### The nominated horizon happens to match the real holding period -- a PROPERTY, not a justification

**Recorded so nobody later "improves" the horizon without knowing what
they would break.** Horizon 10 was nominated from A1a's mean realised
holds (9.60 / 13.04 bars), before any of the figures below were pulled,
and it was not chosen for the reason this section describes.

Verified on the card's own lane -- 1h, LONG, closed, by exit reason:

| exit | n | mean bars | median bars |
|---|---|---|---|
| STOP_LOSS | 831 | **6.78** | 4.0 |
| TAKE_PROFIT | 301 | **11.06** | 7.0 |
| TIMEOUT | 33 | 30.55 | 24.0 |
| *all exits* | *1,165* | ***8.56*** | *not measured* |

**Horizon 10 on 1h bars is ~10 hours, and it brackets the two real
outcomes: 6.78 bars to a stop, 11.06 to a target.** So the number
describes roughly the window the operator is actually exposed for,
rather than an arbitrary lookahead. That is luck, not design.

**Two honest qualifications, because the property is weaker than it
first looks:**

1. **It matches the MEANS, not the medians -- so the band describes a
   window LONGER than most trades actually occupy.** Median hold is 4
   bars to a stop and 7 to a target (the pooled median was not
   measured), against a horizon of 10: roughly **1.4-2.5x the per-exit
   medians** and **1.17x the all-exit mean**. The distribution is
   heavily right-skewed.

   **This is not a defect** -- the measurement was taken at horizon 10
   and the band is the band. But *"describes the exposure window"* is
   true of the **centre of mass** and **not of the typical trade**, and
   someone will eventually read it the looser way. For a median trade
   the band describes a window about twice as long as the trade lasts,
   so the realised swing on such a trade will usually be **smaller**
   than the band -- not because the band is wrong, but because the trade
   ended first.
2. **The figures that motivated this check were the 15m lane's.** The
   recalled numbers were 8.1 bars to a stop and 12.0 to a target; 15m
   measures **8.28 / 13.63**, and 1h measures **6.78 / 11.06**. The
   recalled pair belongs to the lane the card **does not render on** --
   a fourth instance of a pooled-or-wrong-lane figure this week, this
   one inside the attempt to validate the horizon. The property survives
   on the correct lane; the numbers supporting it changed.

**So: if the horizon is ever changed, it stops describing the exposure
window.** That is the thing to know before changing it. It is not a
reason the horizon is correct -- the horizon is correct because it was
nominated in advance and the band was measured at it.

## What would falsify it

If the magnitude relationship does not hold **prospectively** — on
signals dispatched after this scope was written — the field should be
removed rather than re-tuned. The claim is narrow enough that a forward
check is cheap: bucket dispatched signals by the displayed band and
compare realised excursions at a fixed horizon.

**That forward check is not pre-registered here.** If the operator wants
to build this, it gets its own pre-registration first, like everything
else.
