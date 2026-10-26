# Steady-state card load, AT THE DISPATCH LAYER

**Date:** 2026-10-26
**As-of:** 2026-10-26T17:50Z (the window rolls; quote these with the timestamp)
**Status:** measured, read-only. No action, no retune.
**Order:** work-order item 4, 2026-10-26.

---

## The one sentence

**Steady-state delivered card load is ≈33 cards/day (68% interval ≈30–37),
split almost evenly LONG ≈16.6 and SHORT ≈16.8, once the location regime
averages out** — against the ≈40/day the last three days have shown,
because those three days all sat in SHORT's best regime.

## The population, stated

Two different populations are in play and **mixing them is the error**:

| layer | source | filter |
|---|---|---|
| **crossing** | `predictions` | `timeframe='1h'`, all cohorts, signed `final_score`, ±0.36 |
| **dispatch** | `dispatch_decisions` | `timeframe='1h'`, `outcome='sent_telegram'` |

Dispatch is **post-dedup, post-gate** — the card the operator actually
receives. Dedup alone suppressed **149 of 257** would-be cards (58%) in
the post-flip window, so it is not a rounding term.

Window: **44 complete UTC days, 2026-09-12 → 2026-10-25.** Both ends of
the raw 46-day pull are partial days (09-11 carries n=532, 10-26 n=957
against a 1,700–2,300 full day) and are dropped rather than diluting a
per-day rate.

## Why a raw average would be wrong

**SHORT dispatch has existed for 3.4 days.** `sent_telegram` SHORT
begins **2026-10-23 07:00Z**; before that, SHORT attempts never reached
a card. LONG spans 70 days but its *conversion* halved at the same flip,
so pre-flip LONG dispatch does not describe the current config either.

And the decisive problem: the post-flip window's three days carry daily
locations **−0.0574, −0.1213, −0.0488 — all negative.** From the 44-day
series, negative-location days supply **36.47 SHORT crossings/day**
against **5.52** on positive days. So the observed post-flip SHORT rate
was measured in the regime that maximises SHORT. Quoting it as the
steady state is a pooled rate applied to the wrong subpopulation
(cf. the standing rule).

## The decomposition

**supply × conversion, conditioned on the location regime.** Supply is a
property of the scorer and the regime and is config-independent, so the
44 days measure it. Conversion is a property of the current gate config,
so only the post-flip window measures it.

### Supply (crossing layer, per day)

| regime | days | LONG | SHORT |
|---|---|---|---|
| location > 0 | 25 | 30.60/d | 5.52/d |
| location ≤ 0 | 19 | 9.58/d | 36.47/d |
| **all** | **44** | **21.52/d** | **18.89/d** |

Long-run mix: **25/44 positive, 19/44 negative.**

### Conversion (current config, post-flip only)

2.708 post-flip day-equivalents (10-23 contributes 17/24, the flip
being at 07:00Z; 10-26 excluded as partial).

| | sent | expected crossing at negative-day supply | conversion |
|---|---|---|---|
| LONG | 20 | 25.94 | **0.771** |
| SHORT | 88 | 98.78 | **0.891** |

### Steady state

| | long-run supply | × conversion | = cards/day |
|---|---|---|---|
| LONG | 21.52/d | 0.771 | **16.59** |
| SHORT | 18.89/d | 0.891 | **16.82** |
| | | | **33.42** |

## The uncertainty is the conversion, not the supply

Poisson on the 3-day numerators:

| | n sent | rel SE | cards/day |
|---|---|---|---|
| LONG | 20 | 22.4% | 16.6 ± 3.7 |
| SHORT | 88 | 10.7% | 16.8 ± 1.8 |
| **total** | **108** | **9.6%** | **33.4 ± 3.2** |

So ≈**30–37 cards/day** at 68%. The interval is driven entirely by three
days of post-flip data; the 44-day supply distribution is the
well-measured half. This improves on its own as post-flip days
accumulate and **needs no new instrumentation** — it is the same two
tables re-read.

## The number I did not reuse, and why

The observed **dispatch S/L in the post-flip window is 4.40**
(88 SHORT / 20 LONG) against **1.74 at crossing** in the same window and
**3.746 at crossing** across all negative-location days. These are three
different ratios over three different populations.

The operator cited ~7.3 for the flip-window dispatch ratio. I measure
**4.40** over 10-23 → 10-25 with 10-23 weighted 17/24. I have not
reconciled the difference and am **not** adopting either figure silently:
it is most likely a different window boundary (a 3.001-day window with
different endpoints gives a different split, and 10-26 is partial), but
that is a hypothesis, not a finding. The steady-state numbers above do
not depend on it — they are built from supply and conversion, not from a
ratio.

## What this does and does not decide

- **Does:** gives the operator a planning number with its interval and
  its as-of timestamp.
- **Does not** justify a retune. The SHORT bar stays where it is, for the
  reasons in `2026-10-26-long-collapse-short-overshoot-two-causes.md`:
  43% of the shift is expected to revert, and the dominant 57% term is a
  LONG conversion collapse downstream of scoring.
- **Consistency check worth noting:** the 2026-11-03 pre-registration
  predicted unification would take LONG/day toward ~17.6, SHORT/day
  toward ~16, total toward ~33. This computation lands at **16.6 / 16.8 /
  33.4** from a different direction — supply × conversion rather than the
  overshoot — which is mild independent support for that branch. It is
  **not** the 11-03 test, and it does not discharge it.
