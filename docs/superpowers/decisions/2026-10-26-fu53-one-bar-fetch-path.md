# FU-53 — one bar-fetch path, and the guard that is the actual fix

**Date:** 2026-10-26
**Status:** shipped (guard + helper + the three named call sites)

## The class, not the bug

Three times, in three separate files, a bar fetch picked the Binance **spot**
klines endpoint without consulting the symbol's cohort:

| # | where | what it cost |
|---|---|---|
| 1 | `app/ws/live_prediction.py` (#539, root-caused 2026-09-01) | the entire `futures_poll` cohort produced **zero predictions for its whole existence** |
| 2 | `app/ml/validator.py` | outcome validation silently skipped every futures-only symbol |
| 3 | `backend/scripts/mfe_mae_curve.py` range fetcher | **60 of 279 symbols** dropped from the A0 escalation |

A `futures_poll` symbol is a USDT perpetual with **no spot pair at all**, so
`/api/v3/klines` for it is not a degraded answer — it is a permanent empty
payload, on every call, forever. Both the adapter and the range fetchers
swallow that into an empty list rather than raising, so all three instances
read as *"no data yet"* and ran unnoticed for weeks.

**Three incidents in three files is not three mistakes, it is a missing
constraint.** There was no single bar-fetch entry point, so every new caller
re-made the choice, and the spot endpoint is the one that looks like the
default.

## Why the guard is the fix and the helper is not

A helper callers may bypass is a convention, and this convention has already
failed three times. The fourth instance lands in a file that does not exist
yet, which no amount of correctness in the helper can prevent.

So the load-bearing artefact is
`backend/tests/unit/test_bar_fetch_endpoint_guard.py`, which asserts an
**exact** allowlist — in both directions — of files permitted to name a
klines endpoint or to call an adapter's `fetch_klines`. A new file fetching
bars from spot fails CI the moment it is written, whether or not its author
ever reads any of this.

The guard was **written first and run red against the unfixed tree**, naming
each offender, before the helper existed. A guard that has never been seen
red is not evidence.

Both directions are asserted deliberately. "No unexpected file" alone lets
the list rot: an entry whose file stopped naming the URL would sit there
forever, and the next reader could not tell a live exemption from a stale
one. That assertion immediately caught one of my own stale entries during
this change.

## Two findings the guard surfaced that were not in scope

Both are **live** and are left as open findings rather than silently fixed,
because each changes behaviour that wants an operator decision.

### `app/core/scoring/mtf_confluence.py` — LIVE, affecting 26 symbols now

Fetches spot klines per symbol for the MTF confluence vote and **fails open**
(returns `None` → zero-vote degradation). So every `futures_poll` symbol
scores as though no higher timeframe agreed with it. Measured on prod
2026-10-26: `futures_poll` had **26 symbols and 960 predictions** in a 3-day
window, so this is active, not latent. `blocked_mtf_low_agreement` and
`blocked_mtf_higher_tf_veto` are real block reasons in `dispatch_decisions`,
so this plausibly suppresses dispatch for the whole cohort.

Not fixed here: routing it to fapi would start producing real MTF votes for
26 live symbols, which changes live dispatch.

### `app/shadow/worker.py` and `scripts/shadow_variant_rolling_report.py`

The shadow history seed is cohort-blind, and the shadow universe includes
`futures_poll` symbols. The variant-lane rolling report is cohort-blind too,
so futures-only trades silently drop out of variant measurement — the same
mechanism that cost A0 60 symbols, applied to a report we make decisions
from. Both change a **measurement population**, which is exactly the kind of
change that should be made deliberately.

## The design decision worth keeping: the silent default was the defect

`resolve_klines_url` **raises** on a cohort it was not taught, rather than
falling back to spot. #539 was not really a wrong endpoint — it was a
*silent default*: spot was what you got when nobody decided. A new cohort
added to `live_fleet_universe.Cohort` without being taught here now fails
loudly at the first fetch instead of inheriting #539.

## The first version still had a default (operator ruling, 2026-10-26)

The helper originally resolved a cohort-less symbol like this:

```python
cache = get_futures_only_cache()
if cache is None:
    return None
return FUTURES_KLINES_URL if binance_symbol in cache else SPOT_KLINES_URL
```

Held, and correctly. **That `else` is a silent spot default wearing a
conditional.** It cannot distinguish "spot-listed" from "the cache has
never heard of this symbol", and it resolves both to spot. For a
futures-only pair that is #539's mechanism re-entering *through the fix
for #539*.

The operator's framing is the part worth keeping: the three known
instances all sent a **known** futures-only symbol to spot, and every
guard written for them catches that. Instance five arrives from an
**unknown** one — a pair listed since the last 24h refresh, a cold cache,
a typo — and only a raise catches it.

Two candidate fixes were rejected:

| option | why it fails |
|---|---|
| keep the cache, document the miss | a cold or stale cache silently selecting spot is precisely the defect, now behind a helper that *looks* authoritative |
| join `predictions` for `symbol_source` | **silently wrong before 2026-09-03 18:00Z.** Every pre-boundary row carries the uniform `established_top20` default, so a futures-only symbol in a historical window resolves to spot — the defect reintroduced through the fix |

The design that survives has **no default at all**: every returned
endpoint requires POSITIVE membership in a Binance listing, and anything
else raises `UnresolvedMarket`. That converts every lookup source from
SILENTLY WRONG to LOUDLY MISSING, which is what makes the cache
dependency acceptable and the join unnecessary. `fetch_market_listings`
now returns the spot set it previously computed and discarded, so a spot
answer is a positive fact rather than an inferred absence — at zero extra
network cost, since both sets come out of the same two `exchangeInfo`
calls, and they refresh together so the pair can never answer "neither"
for a symbol one of them knew about.

`fetch_bars_by_symbol` raises for the same reason it previously returned
`None`, only harder. `None` and `[]` being indistinguishable is the whole
of #539's invisibility: a wrong-market fetch returned `[]`, which every
caller read as "no bars yet". `None` fixed that conflation but still
asks every caller to remember a second return mode — and a skip path that
must be remembered is the thing this project keeps forgetting. A raise
cannot be ignored by omission.

Callers handle it explicitly, never a bare `except: continue` (cf. #447,
which deleted three of those):

- **`app.ml.validator`** — propagates out of `_fetch_close_at`; the pass
  loop catches it in a *named* arm and emits ONE aggregated ERROR
  carrying `%d of %d`. The count is the load-bearing part: the caches are
  process-global, so a cold cache makes every row unresolvable, and the
  denominator is what separates "one symbol listed yesterday" from
  "prediction validation has stopped".
- **`scripts/a0_escalation`** — re-raises *past* its per-symbol
  `except Exception`. That broad handler is right for flaky symbols, but
  an unresolvable endpoint shrinks the denominator while the run still
  prints a total, which is exactly how FU-53 cost this script 60 of its
  279 symbols invisibly. The guard asserts handler ORDER, because a
  specific arm placed after `except Exception` is dead code that reads
  like protection.
- **`app.ws.live_prediction`** — propagates. Raising beats #539's
  forever-400, which produced zero predictions silently.
- **`app.core.scoring.mtf_confluence`** — NOT migrated here; see the
  mtf-confluence decision record. Routing it changes the live mtf value
  for 26 symbols, which is a dispatch change and is gated on the
  decision-flip screen, not on this PR.

### The guard asserts the no-default property structurally

Behavioural tests cover the inputs we thought of; the defect is a
*shape*, so one test parses the resolver's AST and forbids four ways to
write a default: no ternary (the original form), no `else`, every
`return` guarded by an `in` test, and the final statement must be a
`raise`. Adding a default now means deleting an assertion that explains
why it must not exist.

That assertion's first version was over-broad — it banned ternaries
anywhere in the function and went red on two inside the exception
*message* (`"unloaded" if cache is None else len(cache)`). The right
repair was to scope it to the routing statements, not to delete it and
not to strip the diagnostic counts out of the message.

And `symbol_source` is a **required** keyword on the migrated range fetcher.
A required argument is stronger than a convention: the three callers could
not keep compiling without supplying a cohort, which is how `a0_escalation`
and `phase_a_scan` came to carry one at all.

## A test that stopped testing anything

FU-53 made #539's own regression test go quiet while still passing.

`test_futures_poll_symbol_source_seeds_from_futures_rest_not_spot` asserted
the seed path by **proxy**: that the spot adapter *class* was not
constructed, and that a named futures helper *was* called. Once the seed
routed through `app.data.bar_fetch`, neither proxy observed the seed any
more.

Its spot-direction sibling was worse — it had become a **check that cannot
fail**. It asserted `fetch_futures_seed_klines` was not called, and after the
refactor that helper is called for *no* cohort at all, so it passed
vacuously and would have kept passing had the spot cohort started seeding
from fapi.

Both are replaced by `tests/unit/test_live_prediction_seed_endpoint.py`,
which asserts **the URL the request was actually made to**, in both
directions. That is strictly stronger and survives this kind of refactor.

**The general lesson:** a test coupled to *how* the right answer is produced
goes quiet when the how changes, while still reporting green. Assert the
observable that the requirement is about — here, the endpoint — not the
collaborator that currently produces it.

## Verification

- Guard **red first** against the unfixed tree, naming every offender, with
  the argument (#539 and what it cost) in the failure message rather than a
  bare assertion.
- Then green, for the same reason it was red.
- Mutation run with **per-mutant attribution** and verdicts from **exit
  codes**, including the fourth-file scenario: a brand-new module that
  fetches bars from spot. That mutant is the one the helper cannot possibly
  catch, and the only thing that reds it is the guard.
