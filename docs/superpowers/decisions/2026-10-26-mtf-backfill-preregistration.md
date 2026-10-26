# PRE-REGISTRATION — the mtf backfill and its flip screen

**Date:** 2026-10-26
**Status:** pre-registered. Written and committed BEFORE the measurement runs.
**Order:** work-order item 2, 2026-10-26. "PRE-REGISTER BEFORE RUNNING,
because the answer is probably 'underpowered' and I want that said in
advance rather than discovered."

This measures the lever. **It does not pull it.** `mtf_confluence` is not
being routed through the bar-fetch helper in this work, and that routing
remains a live dispatch change requiring explicit authorisation.

---

## 1. The population, with its denominator attached

`predictions`, `timeframe='1h'`, `symbol_source='futures_poll'`, 30 days
to 2026-10-26, `ABS(final_score) >= 0.36`.

Measured 2026-10-26: **212 rows** clear the bar, of which **187 carry
`mtf_agreement IS NULL`**, 4 are below 3, and 21 are at or above 3.
Over **24** distinct `futures_poll` symbols and 31 UTC days.

**FOUR `futures_poll` counts are now in play and none are
interchangeable** — corrected 2026-10-26 when the step-0 join printed the
cohort's own symbol count beside the row count:

| count | population |
|---|---|
| **26** | the operator's figure: the current futures-only universe |
| **24** | distinct symbols among the 213 **bar-clearing** rows (this cohort) |
| **56** | distinct symbols across ALL `futures_poll` 1h rows in the 30d window (n=9,870) |
| **72** | all-history |

I had been carrying **56** as this cohort's symbol count. It is not: 56
belongs to the whole 1h window, and applying it to the bar-clearing
subset is the [[pooled-rate-applied-to-subpopulation]] error on the
denominator rather than the rate. The cohort is **24 symbols**.

A re-run one hour later also returned **213 rows / 188 NULL** rather than
212 / 187: the 30-day window is rolling, so these counts drift by a row
or two per hour. Quote them with their read time.

## 2. Why the 187 are NULL, and what "correct" means here

`mtf_confluence._BASE_URL` is hardcoded to `https://api.binance.com` and
the fetch path is `/api/v3/klines` — **spot**. A `futures_poll` symbol has
no spot pair, so all six timeframe fetches fail, every vote degrades to
zero, and `mtf_agreement` comes back NULL. Same class as #539 and FU-53.

**`compute_mtf_confluence` HAS NO HISTORICAL MODE.** It votes on the most
recent `KLINE_LIMIT=200` bars, served from a module-level cache with
per-TF TTLs up to 3600s. It takes no timestamp. So the backfill:

- **imports the real decision logic** — `_vote_for_tf` and
  `_compute_agreement_and_dominant` — and does not reimplement it;
- **re-paths only the fetch**, supplying 200 bars ending at each row's
  `ts` per timeframe, resolved through #609's `fetch_bars_by_symbol` so a
  futures-only symbol reaches `fapi` instead of 404ing on spot.

**Stated limitation, not discovered later:** an exact reproduction of
what live *would* have computed is impossible, because the live path's
4h/1d/1w entries can be up to an hour stale from cache. The backfill
computes the value *intended* at the row's `ts`. A disagreement of one
vote on a slow timeframe is within that slack and must not be read as a
discrepancy in the routing fix.

## 3. The deliverable: the flip count

How many of the 187 come back **below** `MTF_MIN_AGREEMENT_1H`.

That count is the lever's size, because the dispatcher's veto reads
`mtf_agreement is not None AND mtf_agreement < MTF_MIN_AGREEMENT_1H`
with the contract *"we must never poison dispatch on missing signal
data."* NULL therefore **passes**. Replacing NULL with a real 0–5 value
can only ever **add** blocks, so the routing fix is a card-load
**reducer** in `[-6.03, 0]` cards/day and cannot be positive.

Reported as the `pattern_stats` screen was, **separately**:

- **flips** — rows whose veto outcome changes (pass → block);
- **reversals** — rows whose outcome changes the *other* way
  (block → pass). Expected to be **exactly zero by construction**, since
  NULL already passes and nothing can become more permissive. A non-zero
  reversal count would mean the measurement is wrong, not that the lever
  is two-sided, and it must be investigated rather than reported.
- **mean shift** in `mtf_agreement` is **not** a meaningful statistic
  here and will not be reported as one: `mtf_agreement` is NOT an input
  to `final_score` (`_compute_aggregator_hook_fields` is documented
  "record-only ... NEVER modifies final_score"), so there is no score to
  shift. This is the correction that made the earlier flip screen return
  zero by arithmetic.

The flip count is **deterministic recomputation, not estimation**, so it
carries no power problem. It is the deliverable.

## 4. THE OUTCOME TEST IS PRE-DECLARED LIKELY UNRESOLVABLE

Two independent reasons, in order of severity.

### (a) Step 0 — do realized outcomes exist at all?

The outcome arm needs realized P&L per row, which for `predictions` lives
in `prediction_validations`. **Item 1 of this same work order establishes
that validation stopped on 2026-09-03** and that every `target_ts` from
2026-09-09 onward sat at 0% validated until a drain began on 2026-10-26.
This cohort is the last 30 days — i.e. **entirely inside the outage**.

So step 0 of the procedure is a join from the cohort to
`prediction_validations` counting rows that actually carry a
`validated_at`. **If that count is zero or near-zero, the outcome test
does not run at all** — not for want of power, but for want of data, and
that is the finding to report. No substitute outcome will be
back-constructed from klines to fill the gap: a hand-rolled outcome
column is a new estimator with no verifiable degenerate case, which is
the trap the standing rule names.

### (b) Even with outcomes, the power is below the bar

Pre-computed so it cannot be reverse-fitted later. Writing `SE` for the
standard error of the difference in mean P&L between the two arms, with
`sigma` the per-row P&L standard deviation:

- **Even split, 93 / 94:** `SE = sigma * sqrt(1/93 + 1/94) = sigma * 0.1464`.
  At `sigma = 5.12 pp` this is **0.750 pp**, reproducing the operator's
  ≈0.75pp figure and making its implied `sigma` explicit.
- **Proportional split** — the 25 non-NULL rows run 4 below / 21 at-or-
  above (16.0% below), so the expected split is ~30 / ~157:
  `SE = sigma * sqrt(1/30 + 1/157) = sigma * 0.1967` = **1.007 pp**.
  This is the case to expect, and it is **34% worse** than the even-split
  figure.

Against project decision bars of **0.2–0.3 pp**, a 3-sigma detectable
effect is **2.25 pp** (even split) to **3.02 pp** (proportional) —
**7.5x to 15x the bar**.

**THEREFORE, PRE-DECLARED NOW:** the outcome comparison is expected to be
unresolvable. If it is, the report says so and **stops**. A sub-bar P&L
difference will **not** be reported as directional, will not be described
as "suggestive" or "trending", and will not be carried forward as a
reason to route `mtf_confluence`. Nothing measured here bears on whether
the mtf veto has demonstrated edge; that question is measured nowhere and
must not be answered by shipping the fix.

## 5. What would make this actionable later

A cohort with realized outcomes and enough rows for a ~0.25pp SE needs
roughly `(5.12 / 0.25)^2 * 2 ≈ 840` rows per arm at the observed sigma —
about **1,700 rows** against today's 187. At ~7 bar-clearing
`futures_poll` rows/day that is years, so the honest path is not waiting:
it is measuring the veto's edge on the population where it already has
volume, which is a different study and not this one.

## 6. Procedure, fixed before running

1. Step 0: count cohort rows with a non-NULL `validated_at`. If ~zero,
   the outcome arm is abandoned and reported as data-absent.
2. Read `MTF_MIN_AGREEMENT_1H` **from the running backend object**, never
   from `config.py`'s default — the standing rule after the env-flip
   incident. The default is 3; the running value governs.
3. Recompute `mtf_agreement` for the 187 at their own `ts` via the real
   vote functions over `fapi`-resolved bars.
4. Report flips and reversals separately, with the denominator attached.
5. Compute only. **No DB write.** No instrumentation, no shadow-diff, no
   live change.
