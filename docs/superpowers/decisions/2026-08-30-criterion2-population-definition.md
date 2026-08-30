# Criterion #2 population definition — two populations, never mixed (2026-08-30)

Operator ruling, delivered alongside item 0's build authorization, in response to
the "rank-filter catch": criterion #2 (Phase 4 promotion gate measuring
whether the liquidity-floor selector's new-cohort symbols are worth
trading) cannot reuse the breakeven-variant lane's existing population
filter, but the fix is a SECOND, separate population definition — not a
change to the first one.

## The two populations

**BREAKEVEN VARIANT population — unchanged, frozen until 30 September.**
`direction=LONG, entry_score>=0.36, blacklist excluded, top-20 rank at
trade-open (BTCUSDT exempt)`. This carries the live falsification read
(n=38 at the 2026-08-15 check, heading to n=150, due mid-September).
Do not touch this filter for any reason before that read completes —
changing its population definition mid-flight destroys comparability
with every prior read.

**CRITERION #2 population — new, separate.** Live-eligibility is
`symbol was present in live_fleet_universe at open`, applied
IDENTICALLY to both the established arm and the treatment arm. This is
not a workaround for the rank filter being unmeasurable on
non-baseline symbols — it is what the rank filter was always a proxy
for. Under the old (pre-Phase-4) selector, the live fleet WAS the top
20 by volume, so `rank<=20` and `fleet-membership at open` were the
same condition by construction. Under the new liquidity-floor
selector they diverge (a symbol can be fleet-admitted without being
top-20 by volume, and vice versa on a given refresh), and
fleet-membership is the one that actually answers the question
criterion #2 needs answered: "would this signal have reached the
operator."

An asymmetric filter — e.g. rank-based for the established arm,
fleet-membership-based for the treatment arm — would invalidate the
comparison outright. Both arms use fleet-membership-at-open, full
stop.

## Status

This is a DEFINITION, not yet an OPERATIONALIZATION. No report or
probe currently computes criterion #2 against this population — that
is future work, gated on real trade volume accumulating under item 0's
correct cohort tags (see `app/shadow/cohort_cache.py` and
`app/shadow/worker.py`'s position-open path, PR #532). When that report
is built, it must apply this exact rule identically to both arms, and
must NOT fall back to the breakeven-variant population's rank filter
for either one.

## Related

- Item 0 (PR #532): the code that makes fleet-membership-at-open an
  exact, non-stale signal — `symbol_source` classified synchronously
  at position-open time rather than joined against a snapshot batch
  that can be hours stale.
- The treatment-arm clock (2026-08-30 report): ~84 days to n=217 is a
  FLOOR resting on an unverified per-symbol-rate assumption — quote it
  as a floor every time, and supersede it with the real measured rate
  the moment treatment-arm data exists.
