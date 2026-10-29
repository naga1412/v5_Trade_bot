# The 1000× ticker class — enumerated, bounded, and a population correction

Item 3 of the 2026-10-29 work order. A symbol's spot and futures tickers
are not always the same string, so any set-membership test or join
across the two markets has a systematic blind spot for that family. My
v1 universe-audit predicate found one instance. This enumerates the
rest.

Listing data read live from Binance `exchangeInfo` on 2026-10-29:
**522 USDT perpetuals TRADING, 519 USDT spot pairs TRADING** — the same
counts the universe audit returned yesterday, which is the cross-check
that this read is of the same world.

## The family is bounded at SIX symbols, and there is only one family

`futures_only` = `tradable_futures − tradable_spot`, a raw **string**
difference, **168 members**. Classified by *why* the string differs:

| why the string differs | count | members |
|---|---|---|
| **numeric multiplier, asset IS on spot** | **6** | `1000BONKUSDT`, `1000FLOKIUSDT`, `1000LUNCUSDT`, `1000PEPEUSDT`, `1000SHIBUSDT`, `1000XECUSDT` |
| index product, no spot market at all | 2 | `ALLUSDT`, `BTCDOMUSDT` (Binance's own `underlyingType: INDEX`) |
| *asset on spot under a different string* | **0** | — |
| genuinely futures-only (asset has no spot listing) | 160 | — |

Two things worth stating plainly:

- **The numeric-multiplier family is the ONLY naming-divergence family.**
  Comparing futures `baseAsset` against the set of *all* spot base
  assets (every quote, not just USDT) found zero further cases. So the
  blind spot is not an open-ended class — it is six symbols, listed
  above, and a new one can only appear when Binance lists another
  multiplier contract.
- **`1000RATSUSDT` is not one of them.** It carries the prefix but
  `RATS` has no spot listing, so it is genuinely futures-only. The
  prefix is not the test; the asset's spot listing is. A fix keyed on
  the string `1000` would have been wrong for this symbol.

`ALLUSDT` and `BTCDOMUSDT` were checked rather than assumed: neither
`ALL` nor `BTCDOM` appears as a spot base asset under any quote. They
have no spot market, correctly, for a different reason — they are not
assets.

## POPULATION CORRECTION, for the record

**"Futures-only" means "this CONTRACT has no same-named spot pair". It
does NOT mean "this ASSET has no spot market."** That phrase appears in
several recorded findings and should be read with this correction
attached.

The counts the work order asked for:

- **Of the 23 symbols that produced `HTTPStatusError_400` in the mtf
  counter, exactly 3 are 1000× contracts of spot-listed assets**:
  `1000BONKUSDT`, `1000PEPEUSDT`, `1000SHIBUSDT` (BONK, PEPE and SHIB
  are each spot-listed, verified against spot `baseAsset`). That is
  **3 of 23, 13.0%**.
- **Of the whole 168-member `futures_only` set, 6 are such contracts** —
  **3.6%**. The other 160 are genuinely futures-only and 2 are indices.

So the `futures_poll` cohort is **96.4% genuinely futures-only**. The
phrase overstates by 6 symbols, not by a large fraction — the
correction matters for *reasoning about specific symbols*, not for
discounting cohort-level results.

## Where the blind spot actually reaches

### (c) `fetch_market_listings_full` — AFFECTED. This is the origin.

`backend/app/shadow/live_fleet_universe.py:511,513`

```python
tradable_futures_only=frozenset(trad_fut - trad_spot),
routable_futures_only=frozenset(route_fut - route_spot),
```

**Two** differences, not one — the routing sets added on 2026-10-27
inherited the same shape. Every downstream consumer of "futures-only"
inherits it from here.

### (a) The cohort classifier — AFFECTED, but its OUTPUT is right

`_classify_cohort` (`live_fleet_universe.py:312`) tests
`symbol in futures_only` against that same set, so `1000PEPEUSDT`
classifies as `futures_poll`.

**That tag is operationally correct and should not be changed.** The
cohort answers "did this symbol arrive through the futures-only
expansion rather than the frozen baseline", and for `1000PEPEUSDT` the
answer is yes: it is not in the baseline and has no same-named spot
pair, so the futures poller is genuinely how it gets its bars.

The defect is therefore **not in the tag but in the inference**. Reading
`symbol_source = 'futures_poll'` as "this asset has no spot market"
leads to wrong conclusions about data availability, about whether a
spot-based control arm exists for the symbol, and about liquidity.
There are 6 symbols for which that inference is false.

### (b) The liquidity floor — **NOT AFFECTED**

The work order asked "if it joins spot volume by ticker". It does not,
anywhere:

| site | endpoint | keyed by |
|---|---|---|
| fleet qvol pre-filter (`live_fleet_universe.py:569`) | `fapi.binance.com/fapi/v1/ticker/24hr` | futures symbol |
| `check_liquidity` ticker (`futures_liquidity.py:42`) | `fapi.binance.com/fapi/v1/ticker/24hr` | futures symbol |
| `check_liquidity` depth (`futures_liquidity.py:60`) | `fapi.binance.com/fapi/v1/depth` | futures symbol |

Futures volume, futures spread, futures depth, futures symbol. There is
no spot join to be blind. `1000PEPEUSDT`'s floor is evaluated against
the contract we actually trade, which is correct — and note that spot
volume would have been the *wrong* number even if the strings matched,
because the floor exists to decide whether **this contract's** book can
absorb an order.

### (d) `bar_fetch.resolve_klines_url_for_symbol` — **IMMUNE BY DESIGN**

It routes `1000PEPEUSDT` → fapi and `PEPEUSDT` → spot, both correctly,
because it requires **positive membership** and has no `else` branch. A
resolver written as `futures if sym in futures_only else spot` would
also get these two right by luck, and would get a *newly listed*
multiplier contract wrong. The no-default design is what makes it
immune rather than lucky.

### (e) `shadow/universe.fetch_top_n_usdt_spot` — **NOT AFFECTED**, and instructive

Spot ticker, spot volumes, spot symbols, single market end to end. Its
docstring names `1000PEPEUSDT` explicitly as the reason it was changed
from the futures endpoint: 15 of 30 universe symbols were
400-Bad-Requesting hourly because they existed only on futures.

**This is the one place the 1000× class was already understood, and the
remedy chosen there was to stop crossing markets at all.** That is the
general remedy, and it is the same move defect (a)'s migration just
made in `mtf_confluence`: do not translate between markets, route to
the market the instrument is on.

## What is NOT being changed, and why

No code changes from this item. The enumeration found:

- one origin (the two set differences), whose output is **correct for
  routing and for cohort tagging**;
- zero sites that join spot volume by ticker;
- zero naming-divergence families beyond the six enumerated symbols.

There is no defect to fix here — there is a **phrase** that invites a
wrong inference, and the fix for that is this document plus the
population correction recorded in memory. Adding a name mapping would
be actively harmful: `1000PEPEUSDT`'s bars, price and ADX are the
contract's, and substituting `PEPEUSDT` would feed a 1000×-rescaled
instrument from a market we do not trade into real scoring votes.
`test_a_1000x_contract_routes_to_FUTURES_and_is_not_rewritten` now
forbids exactly that.

## The general rule this instance teaches

**A set difference across two namespaces measures the NAMESPACES, not
the things they name.** `trad_fut − trad_spot` is a true statement about
contract strings and a false statement about assets, and nothing in the
expression says which one the reader wanted.

Where the two readings diverge, name the one you mean in the identifier
or in the comment beside it. `tradable_futures_only` would be honest as
`contracts_with_no_same_named_spot_pair`; it is not being renamed here
because the name is load-bearing across migrations and probes, so the
correction lives in this document and in the module instead.
