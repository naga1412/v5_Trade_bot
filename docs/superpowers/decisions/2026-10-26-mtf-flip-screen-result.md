# RESULT — the mtf backfill flip screen

**Date:** 2026-10-26
**As-of:** 2026-10-26T17:49Z. The 30-day window **rolls**; every count
below is stale by construction without this timestamp.
**Status:** measured, read-only. No DB write. No live change.
**Pre-registration:** `2026-10-26-mtf-backfill-preregistration.md`,
committed before the run and **deliberately not revised now that data
exists.**

---

## The deliverable

| | |
|---|---|
| **FLIPS (pass → block)** | **11 / 188** |
| **REVERSALS (block → pass)** | **0** |
| **cards/day removed if routed** | **0.367/day** (11 over 30 days) |

Against the ≈33 cards/day steady-state dispatch load
(`2026-10-26-dispatch-layer-card-load.md`), the lever is **≈1.1%**.

**Zero reversals, exactly as the pre-registration predicted by
construction.** NULL already passes the dispatcher veto (fail-open:
*"we must never poison dispatch on missing signal data"*), so nothing can
become more permissive and a non-zero reversal count would have been a
measurement bug rather than a two-sided lever.

## The lever is far smaller than my own bound, and here is why

I had bounded it at **[−6.03, 0] cards/day**. That bound assumed the
worst case — that every NULL row might fall below the threshold. It
holds, but it is loose by **16×**.

The recomputed agreement values are mostly **high**:

| recomputed `mtf_agreement` | rows | |
|---|---|---|
| 2 | 11 | ← blocks |
| 3 | 19 | |
| 4 | 70 | |
| 5 | 76 | |
| 6 | 12 | |

**158 of 188 (84%) sit at ≥4.** The futures-only symbols that clear the
±0.36 score bar have *strong* multi-timeframe agreement, so the veto
would rarely fire on them. That is a fact about the cohort which no
amount of reasoning about the veto's direction could have supplied — it
needed the measurement.

## Method, and what was reused

`MTF_MIN_AGREEMENT_1H = 3`, read from the **running settings object**
inside the container (config.py's default is also 3; the standing rule
is to read the running value regardless).

`compute_mtf_confluence` has **no historical mode** — it votes on the
latest 200 bars from a module-level cache and takes no timestamp — so the
probe imports the **real** decision logic (`_vote_for_tf`,
`_compute_agreement_and_dominant`) and re-paths **only the fetch**
through #609's resolver.

**188/188 rows resolved fully (6/6 timeframes), 0 partially degraded.**
Market resolution: **22/22 symbols → fapi, 0 spot, 0 unresolved.** That
last line independently re-confirms item 1's attribution from a third
direction: the live Binance listing says every symbol in this cohort is
futures-only, which is exactly why the validator's spot fetch returned
nothing for 53 days.

## The outcome arm: ABANDONED, data-absent

Step 0 returned **213 validation rows present, 0 carrying
`validated_at`.**

Not underpowered — **empty**. Item 1's validator outage (stopped
2026-09-03) covers the entire 30-day window, so no row in this cohort has
a realized outcome. This is the pre-registered step-0 abort firing
exactly as written, and it is the same fact as item 1 rather than a
second problem.

Per the pre-registration, **no substitute outcome was back-constructed
from klines.** A hand-rolled outcome column would be a new estimator with
no verifiable degenerate case.

The drain is expected to clear the cohort window within ~23 h of
2026-10-26T17:47Z, at which point the outcome arm has data for the first
time. The pre-registration's power analysis still governs it: at the
expected ~30/~158 split the SE is **≈1.0 pp** against decision bars of
0.2–0.3 pp, so it is **expected to be unresolvable even with data**, and
a sub-bar difference must not be reported as directional.

## Counts, each with its own denominator

**Five** non-interchangeable `futures_poll` counts are now on the record:

| count | population |
|---|---|
| **26** | the operator's figure — current futures-only universe |
| **24** | distinct symbols in the 213-row bar-clearing cohort |
| **22** | distinct symbols in the **188-row NULL subset** (measured this run) |
| **56** | distinct symbols across all `futures_poll` 1h rows in the 30d window (n=9,870) |
| **72** | all-history |

The 22 is new and is *not* the 24: the NULL subset is narrower than the
cohort. And the rolling window moved the cohort from 212/187 → 213/188
within one hour, which is why the as-of timestamp is in the header.

## Still not shipped, and still not recommended on this evidence

Routing `mtf_confluence` through the bar-fetch helper remains **a live
dispatch change requiring explicit authorisation**. This measurement
sizes the lever at 0.367 cards/day and establishes the sign (reduction
only). It says **nothing** about whether the mtf veto has demonstrated
edge — that question is measured nowhere, and shipping the fix is not a
way of answering it.
