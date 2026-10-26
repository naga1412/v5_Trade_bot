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
