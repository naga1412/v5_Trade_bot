# BOUNDARY — the shadow seed migration changes shadow composition

**Date:** 2026-10-26
**Status:** migrated, operator-authorised. **This is a composition-change
boundary and must be honoured by every later shadow analysis.**
**Order:** work-order item 4, 2026-10-26.

---

## The boundary timestamp

**`app/shadow/worker.py`'s history seed began resolving each symbol's own
Binance market at the deploy of the commit carrying this document.**

| | |
|---|---|
| **merged to main** | *(recorded on merge — see the PR's merge commit)* |
| **effective in prod** | the first `ShadowWorker.setup()` after that deploy |
| **mechanism** | `BinanceClient.fetch_klines` (spot, cohort-blind) → `fetch_bars_by_symbol` (per-symbol market, no default) |

The seed runs once per worker start, so the boundary is the **first
restart after the deploy**, not the merge. `deploy.yml` recreates the
backend, so those coincide within a minute in practice — but a shadow
analysis that needs the exact instant should take it from the
`shadow_worker: setup done` log line, not from the merge timestamp.

## What changed, in one sentence

`futures_poll` symbols can now be seeded, therefore they can now **open
shadow trades for the first time**.

Before: the seed asked `api.binance.com/api/v3/klines` for every symbol.
A futures-only symbol has no spot pair, so it returned 400/404, the
exception was swallowed at **`log.debug`** with the reason written into
the message, and that symbol got **no bars** — and a symbol with no bars
cannot produce a signal. The whole `futures_poll` cohort was therefore
structurally absent from shadow, silently, which is precisely why that
cohort has never been measured.

## THE RULE FOR ANY ANALYSIS SPANNING THIS DATE

**SPLIT OR LABEL. Never retag history.**

- A shadow-trade population drawn from **before** the boundary contains
  **zero** `futures_poll` trades — not "few", zero, by mechanism.
- A population spanning the boundary is **two different universes**
  concatenated. Pooling them and reporting one mean is the
  [pooled-rate-applied-to-subpopulation] error with the composition
  change hidden inside the time axis.
- Pre-boundary rows keep whatever `symbol_source` they were written with.
  **Do not backfill or re-derive it.** The tag records what the system
  believed at write time, and rewriting it destroys the only evidence of
  the boundary.

### This is now the FOURTH composition/semantics boundary on file

They compose, and an analysis crossing more than one needs all of them:

| boundary | date | what changes |
|---|---|---|
| `symbol_source` tag becomes real | **2026-09-03 18:00Z** | before this, every row carries a uniform `established_top20` default — it is *wrong*, not merely absent, and it concealed the validator's poison symbols |
| futures-only spot-seed fix | **2026-09-01** | the `futures_poll` prediction cohort starts producing at all |
| validator outage | **2026-09-03 → 2026-10-27** | realized outcomes absent for the whole span |
| **shadow seed migration** | **this deploy** | `futures_poll` enters the shadow trade population |

## Why the failure mode cannot get worse

The migrated resolver answers **only by positive listing membership** and
raises `UnresolvedMarket` otherwise. `setup()` runs once at worker start
and races `futures_only_refresh_task`'s first cycle, so a cold cache
would raise for **every** symbol and seed **zero** buffers — strictly
worse than the per-symbol degradation being fixed.

So the cache is **ensured before the loop**, with one bounded HTTP
round-trip, rather than trusting a startup ordering:

```python
if get_futures_only_cache() is None:
    await refresh_futures_only_cache(http)
```

and any symbol still unresolvable is reported **once, at ERROR, with the
scale attached** — `N of M symbols ... left UNSEEDED`. The count is the
datum that separates "one freshly listed coin" from "the listing cache
never loaded and the seed is broken, not degraded". A per-symbol line
would bury exactly that.

The 400/404 arm that used to whisper at `log.debug` now logs at
**WARNING**, because a 400 on the *correct* market is a real anomaly
rather than an expected miss.

## The allowlist entry is closed, not re-justified

`app/shadow/worker.py` was the last live entry in
`KNOWN_UNMIGRATED_FETCH_KLINES` whose reason amounted to **"not done
yet"** — which is the growable-allowlist risk that guard exists to
prevent. An exemption that survives on inertia is an exemption that
arrived as an inherited default rather than a decision.

Removing it tripped **two** count assertions, both by design, and both
had to be edited deliberately:

- `len(CLOSED_UNMIGRATED_FETCH_KLINES)` **2 → 1**
- total known-open instances **6 → 5**, whose own comment reads *"A fix
  that migrates one MUST lower this."*

The guard stating its own update rule and then enforcing it is why that
count is worth trusting.

Remaining known-open: `mtf_confluence.py` (live-affecting, still awaiting
the dispatch decision), `shadow_variant_rolling_report.py`
(measurement-integrity), two retrospective probes, and
`app/scanner/batch.py` (UI card path).
