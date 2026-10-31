# RESULT: the universe-membership re-measure came back EMPTY

**Date:** 2026-10-29
**As-of:** 2026-10-29T16:47:41Z
**Pre-registration / scoping:** `2026-10-27-routing-predicate-fix.md`,
item 3 — *"universe membership is NOT in this PR ... the real PR must
re-measure rather than quote this."*
**Status:** re-measured. **Zero removal candidates.** The behaviour
change is **NOT shipped**; the detector that would find it is.

---

## What was asked, and what the number says

The 10-27 doc named **`HFTUSDT`** and **`VICUSDT`** as that morning's
removal candidates, explicitly flagged the list as as-of 07:11Z, and
instructed the real PR to re-derive it. Re-derived:

```
universe snapshot      : 71 symbols
open positions         : 12 symbols
binance futures TRADING: 522 USDT perps
binance spot TRADING   : 519 USDT pairs

NOT TRADABLE but in the universe               : 0
RETAINED ONLY BY THE OPEN-POSITION OVERRIDE    : 0
OPEN POSITION, NOT TRADING, NOT YET A MEMBER   : 0
```

**Which symbols leave: none. Cards/day effect: zero**, against the
measured dispatch steady state of ~33 cards/day (2026-10-26T17:50Z) —
removing zero symbols moves it by zero. There is no live coin-list
change to put in front of the operator, which was the entire reason this
was carved out as its own PR.

## Why the 10-27 list evaporated, which is the informative part

Entry was never the exposure. `refresh_live_fleet_universe` builds
`candidates` from `tradable_futures` (status == `TRADING`), so a
non-`TRADING` symbol **cannot be admitted by the main loop**. And a
symbol already in `prior` that stops being `TRADING` drops out of
`candidates` entirely, is therefore never iterated, never enters
`results`, and **falls out of the snapshot implicitly**.

So `TONUSDT` / `HFTUSDT` / `VICUSDT` — all spot=`BREAK` /
futures=`SETTLING` on 10-27 — were removed by the ordinary refresh
within a cycle or two. The universe self-heals through its main loop.
What the 10-27 read caught was a transient, not a retention.

The one genuine retention path is the **open-position override**, which
re-adds every symbol in `get_open_position_symbols()` with no
tradability test at all. A position on a non-`TRADING` symbol can never
close, so the override would re-add it on every refresh, forever —
unexitable in the strongest sense, which the liquidity hysteresis cannot
detect because it measures depth and spread on an order book that is not
there. **That path currently has nothing non-tradable to re-add.**

## MY OWN MEASUREMENT HAD A POPULATION BUG, and the probe found it

The first version of the audit tested `s not in tradable_futures` alone
and reported two findings: `PEPEUSDT` and `SOXLBUSDT`.

Both were **false positives**. That predicate conflates two unrelated
things:

1. listed as a USDT perp but `BREAK`/`SETTLING` — genuinely untradable,
   the case this audit is for;
2. **not a USDT perp at all** — a spot-only symbol, perfectly tradable,
   just not on the market the fleet universe is built from.

`PEPEUSDT`'s perp is `1000PEPEUSDT`, so the bare spot ticker can never
appear in `tradable_futures` no matter how healthy it is. Re-read with a
two-market predicate: both are `spot=TRADING has-history`.

This is the **standing pooled-rate rule** in a new costume — *name the
population a predicate was defined over AND the one it is applied to;
if they differ, that is the defect.* I defined membership over USDT
perps and applied it to shadow open positions, which include spot-only
symbols. Caught by running the probe and **not believing its third
section**, not by reading the code.

Corrected: `untradable()` now requires non-`TRADING` on **both** markets,
every listed symbol carries an explicit per-market verdict rather than an
inference from absence, and open-but-spot-only is reported in its own
section labelled **NOT A FINDING**.

## Merge decision: ship the detector, DO NOT ship the constraint

**Shipped here** (read-only, no behaviour change — merges on green):

- `backend/scripts/universe_tradable_audit.py`
- the `universe-tradable-audit` ops-debug probe

**Deliberately not shipped: the hard universe constraint.**

The 10-27 doc ruled that the fix is *"a hard universe constraint, not
hysteresis tuning"* — and that ruling was made when the premise held.
It does not hold now, and three things argue against shipping a
behaviour change to the fleet-selection path anyway:

1. **Zero symbols are affected.** A behaviour change on a path that
   feeds dispatch eligibility, to fix a condition that does not exist,
   is risk without benefit.
2. **The residual path is already covered.** The override's
   no-prior-entry rescue branch calls `check_liquidity`, which fails for
   a symbol with no futures order book, and the existing code already
   logs loudly on that failure rather than silently admitting it. An
   explicit tradability guard there would make the decision clearer; it
   would not change an outcome today.
3. **It collides with a live operator ruling I should not overrule
   silently.** The 2026-08-20 ruling is that the open-position override
   *"still wins — never strand a live position."* That was decided about
   **severe liquidity failure**, not about non-`TRADING` status, and the
   two are genuinely different (a non-`TRADING` symbol is already
   stranded by the exchange, not by us). Whether "never strand" was
   meant to reach this case is the operator's call, not mine.

### The condition that re-opens this

**The audit reporting non-zero in either of its first two sections.**
At that point the removal list is real, the cards/day delta is
measurable, and the constraint ships with the operator's ruling on
point 3 attached.

### Known gap, stated rather than left implicit

The probe is **on-demand**. Nothing schedules it, so nothing will notice
the condition arriving. That is the same gap `external-endpoint-healthcheck`
carries and acknowledges. It is a smaller gap than the one it replaces —
before this, the question could not be answered at all without
hand-writing a query against two exchangeInfo endpoints — but it is not
zero, and a scheduled run plus a `check_events` registry entry is the
honest follow-up.

---

## RESOLVED 2026-10-31 — the ruling extends, and the detector is wired

Two things were left open above. Both are now closed.

**1. The collision with the 2026-08-20 "never strand a live position"
ruling.** Settled by the operator: **the ruling extends to non-TRADING
status.** No hard constraint is built, no tradability test is added to
the override loop, and the detector is the only trigger.

The reasoning worth keeping, because it is why this is not a compromise:
removing the symbol from the universe **does not close the position.**
It only stops the fleet streaming prices for an exposure that still
exists — trading a visible stuck position for an invisible one. The
override is doing the right thing with a bad situation, so detection is
the correct lever and removal is not a lever at all.

Recorded against the original ruling in
`app/shadow/live_fleet_universe.py`'s module docstring, beside the
override text it modifies, so the extension is not re-litigated. While
doing that I found the citation that docstring carries for Task 5d —
`2026-08-19-live-fleet-universe-never-scheduled-incident.md` — **is
dangling: that file is not in the tree.** The docstring is therefore the
only durable record of the ruling, which is noted there too.

**2. "Re-opens the moment the audit reports non-zero" had no mechanism
to ever report.** Fixed. The audit is now produced on the 5-minute
`check_events_flush` cycle and registered in `EXPECTED_CHECKS`, so:

- a finding writes a `violation` row with the symbol lists;
- a clean cycle still writes an `ok` row, because silence is not
  success;
- an unloaded listing cache writes an `error` row rather than an `ok`
  one, because a check that reports success when it could not run is
  the defect itself;
- and the detector's **own** silence is a finding, within 20 minutes.

The classification is one shared pure function
(`app.shadow.universe_tradability.audit_tradability`), called by both
the scheduled producer and the on-demand script, so the two cannot
drift. They differ only in where listings come from, and that
difference is deliberate and documented: the script fetches fresh (an
on-demand forensic read should not inherit a cache's staleness), the
producer reads the 24h cohort cache (two exchangeInfo calls every five
minutes would be 576 requests a day for a property that changes on the
order of weeks). Detection latency is therefore ~24h + 5 min, which is
the same staleness bound `app.data.bar_fetch` already routes every bar
fetch on.

**This was the sixth instance** of instrumentation with no retrieval
path in this repo (FU-47, the entry-timing recon, #447's streaks, #601
log-only, #610's homeless counter, and this one). The pattern is not
forgetting to build the reader — it is that "re-run it when needed"
reads like a plan and is not one.
