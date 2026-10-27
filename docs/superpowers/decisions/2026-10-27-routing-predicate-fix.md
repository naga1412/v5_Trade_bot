# The routing predicate — one predicate was serving two questions

**Date:** 2026-10-27
**Status:** fix shipped (routing only). Universe membership is a
**separate PR**, deliberately not bundled.
**Order:** items 0, 1, 2 of the 2026-10-27 work order.

---

## Item 0 — the urgency read

**2 of 80 current universe members would raise `UnresolvedMarket`, and
both are case (a).** As-of **2026-10-27T07:11:54Z**.

| symbol | raw spot | raw futures | class |
|---|---|---|---|
| `HFTUSDT` | **BREAK** | **SETTLING** | (a) present upstream, filtered out |
| `VICUSDT` | **BREAK** | **SETTLING** | (a) present upstream, filtered out |

Universe = latest `asset_universe` snapshot (30) ∪ `cohort_baseline_symbols`
(73) ∪ open shadow positions (9) = **80 distinct**. Zero case-(b).

**`TONUSDT` is NOT in the current set** — it was the symbol the shadow
seed reported at 2026-10-26T19:18:55Z, and the universe has turned over
since. That is the point rather than a contradiction: **three distinct
symbols in ~12 hours, all the same `BREAK`/`SETTLING` shape.** This is a
steady trickle as coins delist, not one stale entry, which is why the
routing fix ships now rather than behind #616.

## The defect: one predicate, two questions

```python
spot:    quoteAsset == "USDT" AND status == "TRADING"
futures: quoteAsset == "USDT" AND contractType == "PERPETUAL" AND status == "TRADING"
```

`fetch_market_listings` was written for cohort and fleet **SELECTION**,
where `TRADING` is exactly right — *may I trade this?* Then FU-53 made
`bar_fetch.resolve_klines_url_for_symbol` consult the same sets for
**ROUTING**, where the question is different — *does this market have
bars for it?*

**A halted (`BREAK`) or settling (`SETTLING`) symbol is not tradable but
its history still exists.** So routing on the TRADING predicate raised
`UnresolvedMarket` for symbols whose klines were sitting right there.

This is a correctness regression introduced by the no-default rule: the
raise was correct in form and wrong in fact, because the set it consults
was never built to answer routing.

**Upstream exposure, measured:** 254 USDT spot symbols in `BREAK` against
516 `TRADING`; 135 futures `SETTLING` plus 1 `PENDING_TRADING` against
755. **390 symbols** present upstream and absent from our sets.

## The fix: two sets, not a wider one

```python
@dataclass(frozen=True)
class MarketListings:
    tradable_futures:       frozenset[str]   # "may I trade this?"
    tradable_futures_only:  frozenset[str]   #   -> cohort/fleet SELECTION
    tradable_spot:          frozenset[str]
    routable_futures_only:  frozenset[str]   # "does this have bars?"
    routable_spot:          frozenset[str]   #   -> bar-fetch ROUTING
```

**Widening the existing set would not have fixed the class.** It would
have turned a false negative on *data* into a false positive on
*tradability* — cohort selection would start treating a settling contract
as tradable, which is worse. Two consumers, two questions, two sets; a
test asserts the tradable view is unmoved.

`_NO_HISTORY_STATUSES = {"PRE_TRADING", "PENDING_TRADING"}` is the only
exclusion from the routing view, and it is named rather than inferred: a
symbol that has never traded genuinely has no bars, so including it would
trade the old false negative for a new false positive — the
silent-empty-result failure the no-default rule exists to prevent. A test
asserts `BREAK` and `SETTLING` are **not** in that set.

One fetch serves both views. `fetch_market_listings` stays a thin 3-tuple
wrapper for `refresh_live_fleet_universe` and its tests (the PR #266
return-widening pitfall), and all four cached sets refresh together — two
sets from different moments could answer "neither" for a symbol one of
them knew.

### Why this is permanent, not a stopgap

It stays necessary **after** the universe-membership fix, because
backfills, historical analysis, and **the prediction validator draining a
53-day backlog** all legitimately want bars for symbols that have since
delisted. Tradability and data availability diverge permanently for any
system that looks at the past.

## Item 2 — PRE-REGISTERED INTERPRETATION OF `dead_lettered > 0`

**Written now, before it can fire.**

`prediction_validations.dead_lettered` has read **0** at every
post-deploy check so far. When it first goes non-zero:

> **CHECK THE SYMBOL AGAINST THE `BREAK`/`SETTLING` LIST BEFORE TREATING
> IT AS A NEW DEFECT.**

A dead-letter on a symbol that is non-`TRADING` upstream is the
**expected signature of the routing-predicate bug**, not a regression in
#613's dead-letter logic. The mechanism: the validator asks
`resolve_klines_url_for_symbol`, that raised for a halted symbol, the row
accumulated `validation_attempts`, and at 6 it was written off. The
dead-letter did exactly what it was built to do — on a row that should
never have failed.

**Without this written down first, the first dead-letter reads as a #613
regression and sends the next session hunting in the wrong place** — the
same shape as the 2026-09-08 false onset, where a correct query answered
a question nobody had asked.

The check is one command: take the symbol from the
`validator_dead_letter` ERROR line, look it up in public `exchangeInfo`
(`status` on both markets), and if it is `BREAK` or `SETTLING`, this
predicate bug is the cause. After this fix deploys, such rows should stop
appearing; any that remain are re-openable with
`unvalidatable_reason = NULL` once the routing sets are live.

## Item 3 — universe membership is NOT in this PR

A non-`TRADING` symbol cannot be entered or exited by anyone, which is
unexitable in the strongest sense — the condition hysteresis exists to
detect, and #601's uncovered retention path. So the fix is a **hard
universe constraint**, not hysteresis tuning.

But it is a **live coin-list change**: it removes symbols the operator
currently sees. That needs its own PR, its own report of the delta (which
symbols, how many cards/day), and its own merge decision. Bundling it
with a routing fix would hide a visible-to-the-operator change inside an
invisible one.

**Today's candidates would be `HFTUSDT` and `VICUSDT`** — but that list
is as-of 07:11Z and turns over daily, so the real PR must re-measure
rather than quote this.
