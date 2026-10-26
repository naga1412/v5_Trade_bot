# mtf_confluence fails open for futures_poll — sized, loud, not yet routed

**Date:** 2026-10-26
**Status:** (a) fail-loud SHIPPED. (b) routing fix NOT shipped — now sized at ≤2.6 cards/day.

## What it is

`_fetch_one_tf` fetches klines from the **spot** endpoint for every symbol
and returns `None` on any HTTP error; `compute_mtf_confluence` turns `None`
into a `(0, 0.0)` vote. A symbol whose fetch always fails therefore scores as
though **no higher timeframe agreed with it** — indistinguishable from
genuine disagreement in every downstream consumer.

## Two corrections to what I previously claimed

**I said the whole `futures_poll` cohort scores as zero-agreement. That is
wrong.** Measured, `predictions` 1h, `mtf_agreement` NULL rate:

| cohort | n | NULL | NULL % |
|---|---|---|---|
| established_top20 | 89,546 | 23,809 | 26.6% |
| liquidity_added_spot | 50,189 | 12,531 | 25.0% |
| **futures_poll** | **16,791** | **14,909** | **88.8%** |

1,876 futures_poll rows carry a **positive** `mtf_agreement` (max 5, mean
2.678), so the cohort is degraded, not dead. The right statement is **88.8%
against a ~26% baseline — a 3.4× higher null rate**, not 100%.

Why 11.2% succeed is **not established**. The cohort classifier
(`live_fleet_universe:325`) is a pure function and assigns `futures_poll`
only to symbols in the `futures_only` set, i.e. with no spot pair — so on the
face of it none should succeed. Candidate explanations (a stale 24h
`futures_only` cache, or symbols spot-listed after classification) are
**untested**, and I am not asserting one.

**How long:** the whole of the cohort's existence, at a flat rate. 88.5% in
2026-09 from its first prediction (2026-09-03T18:00Z), 89.1% in 2026-10. Not
drift.

## Phase A is NOT contaminated — for a reason that is a bigger finding

The concern was that the Phase A table's "mtf_agreement: direct, full
history, CLEAN" might silently mean spot-listed-only. It does mean that, but
not through contamination:

| `shadow_trades` cohort | n | mtf NULL % |
|---|---|---|
| established_top20 | 7,170 | 3.0% |
| liquidity_added_spot | 1,108 | 0.4% |
| **futures_poll** | **0** | — |

**`futures_poll` has never opened a single shadow trade, in all history.**
So Phase A's figures are arithmetically unaffected, and its 3.0%/0.4% null
rates are genuinely clean.

The reason is the other FU-53 site: `app/shadow/worker.py`'s history seed is
cohort-blind and fetches spot, so a futures-only symbol can never obtain
shadow bars and can never open a shadow trade. That defect has cost the
cohort **100% of its shadow trades** — which is the strongest confirmation of
the FU-53 class yet, and it generalises well beyond mtf:

> **Every shadow-derived finding in this project — Phase A, the variant
> lanes, the stop-width test, the TP ladder, the breakeven work — is
> spot-listed-only by construction.** Not by a stated filter, but because the
> cohort cannot reach `shadow_trades` at all.

That belongs on every one of those results as a scope line. It does not
invalidate any of them.

## (b) The dispatch delta, sized before any routing change

`dispatch_decisions` joined to cohort, 1h, 2026-10-16T06:59:37Z → 2026-10-26
(~10.2 d):

| cohort | dir | attempts | sent | blocked_mtf | symbols |
|---|---|---|---|---|---|
| established_top20 | LONG | 1,270 | 52 | 20 | 31 |
| established_top20 | SHORT | 3,668 | 49 | 13 | 34 |
| **futures_poll** | **LONG** | **898** | **27** | **26** | **30** |
| **futures_poll** | **SHORT** | **961** | **15** | **0** | **29** |
| liquidity_added_spot | LONG | 1,649 | 60 | 14 | 59 |
| liquidity_added_spot | SHORT | 3,670 | 49 | 5 | 59 |

`futures_poll` carries **26 of the 78** mtf blocks (33%) on **16.7%** of
attempts — mtf-blocked at about twice its share, consistent with degraded
input.

**Upper bound on the volume increase from routing the fetch correctly:
26 blocks / 10.2 d = ≈2.6 additional cards/day**, i.e. **+6.2%** on the
current pooled load of 41.65/day. That assumes every mtf block flips to a
send, which is the most favourable case; a correct `mtf_agreement` can also
block *more*.

**So the ordering concern is resolved quantitatively.** The feared "blind
+26-symbol volume increase on top of 2.371×" is bounded at +2.6 cards/day.

**The bound covers only the veto path.** `mtf_agreement` is also a scoring
input, so a correct value can move `final_score` and hence the ±0.36 tail
population. That second channel is **not** bounded by 2.6/day and is the
reason to still measure rather than route blind.

## What shipped

(a) only, and it changes no behaviour: `log.error` with the greppable marker
`mtf_kline_fetch_failed`, a message naming the fail-open consequence, and a
per-symbol counter with an in-process accessor (`get_fetch_failure_counts`)
so the question has a retrieval path that a redeploy cannot truncate.

The return value and the fail-open contract are untouched, and a test asserts
that, so an observability commit cannot quietly move live scoring.

---

# CORRECTION + CLOSURE — the decision-flip screen (2026-10-26, same day)

The operator ordered the flip screen to close 2(b): recompute `final_score`
offline for the `futures_poll` symbols with the CORRECT mtf value and count
how many cross 0.36, reporting flips and reversals separately. Running it
produced two corrections to what is written above, one of them a **sign**
error of mine.

## 1. There is NO SCORING CHANNEL. The screen returns zero by arithmetic

| | |
|---|---|
| **flips** (rows crossing ±0.36) | **0** |
| **reversals** (direction changes) | **0** |
| **mean \|final_score\| shift** | **0.0000, exactly** |
| population | `futures_poll`, 1h, prod `predictions`, 30d window 2026-09-26T15:00Z→2026-10-26T13:00Z, 56 distinct symbols, 31 UTC days, **n=9,870** (all-history: n=16,851, 72 symbols) |

Not zero as a measurement — zero **by construction**, and the distinction
matters because it is not subject to sampling error.

`mtf_agreement` is not an input to `final_score`. `app/core/predictor.py`
computes `final` first, then calls
`_compute_aggregator_hook_fields(..., final, ...)`, whose own docstring is
*"Compute the 7 PR1 record-only analytics fields"* and whose call site is
commented **"Record-only attach of 7 analytics fields. NEVER modifies
final_score"**. `compute_mtf_confluence` is invoked as
`compute_mtf_confluence(symbol, final.direction)` — it **consumes** the
finished decision rather than feeding it, and `final.score` is passed *in*.

So recomputing `final_score` with a corrected mtf value returns the
identical number for every one of the 9,870 rows. The claim above that
`mtf_agreement` "is also a scoring input" is **wrong**, and with it the
claim that the second channel is unbounded. The operator's inference that a
fabricated "no agreement" presumably LOWERS `final_score` does not hold in
this codebase, so the `≤` does not reverse for that reason.

## 2. The veto FAILS OPEN on None, so the delta cannot be POSITIVE

This is the sign error. `dispatcher.py`'s mtf gate reads:

```python
if (proposal.mtf_agreement is not None
        and proposal.mtf_agreement < settings.MTF_MIN_AGREEMENT_1H):
    return DispatchResult(outcome="blocked_mtf_low_agreement", ...)
```

with an explicit docstring contract: *"`mtf_agreement is None` → PASS. PR1
MTF compute returns None on cold cache / fetch failure; we must never poison
dispatch on missing signal data."*

**So today's broken fetch is the MAXIMALLY PERMISSIVE state.** A failed
futures_poll fetch yields `None`, and `None` passes. Correcting the routing
replaces `None` with a real 0–5 agreement, and every value below
`MTF_MIN_AGREEMENT_1H` (default 3) becomes a **block**.

Measured over the stated 30d population, of the 212 rows clearing
|final_score| ≥ 0.36:

| today's mtf state | rows | today's veto outcome |
|---|---|---|
| NULL | 187 | **PASS** (fail-open) |
| < 3 | 4 | blocked |
| ≥ 3 | 21 | pass |

After the fix the 187 acquire real values, and those landing below 3 flip to
blocked. The share cannot be extrapolated from the 25 that currently
compute: **why that 11.2% succeeds is not established** (see above), so they
are not a random sample of the 187. Bounding instead:

| case | delta |
|---|---|
| every one of the 187 computes < 3 | **−6.03 cards/day** |
| every one computes ≥ 3 | **0** |
| **so the delta lies in** | **[−6.03, 0] cards/day — it cannot be positive** |

**My "+2.6 cards/day, +6.2%" above is wrong in sign.** It assumed
NULL→vetoed, i.e. that fixing the fetch would UNBLOCK a suppressed cohort.
The code's documented contract is the opposite, and I asserted the mechanism
instead of reading it — the same error this project has on file repeatedly,
here committed inside the write-up whose purpose was to avoid acting blind.

## 3. A third channel, enumerated and found to have no surface

`app/trading/cooldown_compute.py:78` reads `last_mtf_agreement`: after a
stop-loss cooldown's calendar expires, re-entry stays blocked unless
`new_mtf > last_mtf` (under `LIVE_COOLDOWN_SL_REQUIRES_FRESH_MTF`). That is
a genuine decision channel and it is not the veto.

It has **zero surface**: `live_cooldowns` holds **0 rows** table-wide, so no
symbol in any cohort can reach the branch today.

## The answer to 2(b)

| channel | delta | basis |
|---|---|---|
| scoring (`final_score`) | **0 flips, 0 reversals, 0.0000 shift** | arithmetic — mtf is record-only |
| dispatcher mtf veto | **[−6.03, 0] cards/day** | 187/212 bar-clearing rows currently pass via fail-open, 30d/56 symbols/31 days |
| cooldown fresh-MTF | **0** | `live_cooldowns` is empty |

**Routing `mtf_confluence` correctly cannot increase card load.** The feared
"+26-symbol volume increase on top of 2.371×" does not exist in either
direction of the arithmetic: the scoring channel is absent and the veto
channel is a net *reducer*. The ordering concern that held this change is
therefore dissolved — but the change is now a different proposal than the
one that was queued. It no longer adds cards; it removes up to ~6/day, and
whether that is desirable depends on whether the mtf veto has demonstrated
edge, which **nothing here measures**.

That is the open question, and it should not be answered by shipping the
routing fix. Note also the operator's "26 symbols" against the measured
**56** distinct `futures_poll` symbols in 30d and **72** all-history — the
26 is presumably the current futures-only universe count, a third
population, and the three are not interchangeable.
