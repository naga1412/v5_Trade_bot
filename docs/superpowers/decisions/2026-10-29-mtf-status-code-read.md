# RESULT: the mtf status-code read, against the rule filed 2026-10-27

**Date:** 2026-10-29
**Pre-registration:** `2026-10-27-mtf-status-code-rule.md`, committed
2026-10-27 **before** the counter held any data. Applied here **as
filed**, not revised.
**Counter window:** 2026-10-27T17:04:55Z -> 2026-10-29T16:05:39Z
(the `(symbol,tf,failure)` key shipped in #616, deployed
2026-10-27T16:20Z). 47 hourly scoring cycles.
**As-of:** 2026-10-29T16:35Z.

---

## The number

`check_events`, `check_name = 'mtf_kline_fetch_failed_detail'`:

| failure_kind | occurrences | share | symbols | subject rows |
|---|---:|---:|---:|---:|
| `HTTPStatusError_400` | 3,387 | 96.50% | 23 | 282 |
| `ConnectTimeout` | 109 | 3.11% | 26 | 103 |
| `WriteTimeout` | 11 | 0.31% | 2 | 11 |
| `ReadTimeout` | 3 | 0.09% | 3 | 3 |
| **429 / 418** | **0** | **0.00%** | 0 | 0 |
| **total** | **3,510** | | | 399 |

## The verdict: MIGRATE (a)

The rule's three branches, checked against the above:

- **"400 only"** -> **condition NOT met.** Transport exceptions are
  present: 123 occurrences across three distinct exception classes.
- **"429 or 418"** -> **condition NOT met.** Exactly zero, over 47
  cycles. Rate limiting is ruled out, not merely unobserved.
- **"transport exception"** -> **condition MET.** -> **MIGRATE (a). It
  is a fix, not cleanup.**

### The one place the rule is ambiguous, and how I resolved it

The table header reads *"dominant failure"*; the first row's condition
reads *"400 only"*. Those are not the same test, and **they disagree on
this data**: 400 is dominant at 96.50%, so a literal "dominant" reading
would return "(a) stays cleanup" — the opposite verdict.

I applied **"only"**, the row's own wording, for a reason internal to the
rule rather than convenient to me: its hypothesis section states the test
explicitly in advance —

> *"The signature to look for is a TRANSPORT EXCEPTION, not an HTTP
> status. The counter separates them precisely because an HTTP status and
> a transport failure are different diagnoses, and collapsing them was
> what left ARB unexplained for two rounds."*

A test for *presence of a signature* is not a test for *share of
volume*. Recording the ambiguity here because the next person to read
this rule should see that it had a soft edge, and that the edge was
load-bearing.

## What the 400s actually are — defect (a), confirmed by construction

All 23 symbols fail on **6 of 6 timeframes**, every one of them. That is
a per-symbol property, not a per-request flake. The roster settles it:

`1000PEPEUSDT`, `1000SHIBUSDT`, `1000BONKUSDT`, `VVVUSDT`, `PONSUSDT`,
`BTWUSDT`, `GRASSUSDT`, `CAPUSDT`, `BIRBUSDT`, `CTUSDT`, `AKEUSDT`,
`AINUSDT`, `DOODUSDT`, `APRUSDT`, `EVAAUSDT`, `USELESSUSDT`,
`BEATUSDT`, `JCTUSDT`, `IRYSUSDT`, `QUSDT`, `RAYSOLUSDT`, `SOONUSDT`,
`BRUSDT`

The `1000*` prefix is the proof rather than the hint: **those symbol
strings do not exist on spot at all.** Spot lists `PEPEUSDT`,
`SHIBUSDT`, `BONKUSDT`; the `1000`-multiplied contracts are a futures-only
construction. A spot-klines request for `1000PEPEUSDT` cannot do anything
but 400.

So the 96.50% is **defect (a) in full** — `mtf_confluence` building its
own spot URL instead of asking `resolve_klines_url_for_symbol`. That is
an independent argument for migrating (a) that does not rely on the
transport branch: migrating it removes 96.5% of all recorded mtf fetch
failures, and removes them deterministically, because the cause is a
constant property of the symbol.

## What the transport exceptions are — bursty, not steady

**The failures concentrate at the hour boundary**, which is when the
scoring cycle fires every symbol at once.

11 distinct flushes carried a transport row. Bucketing each by its
five-minute slot within the hour: slot 0 (`:00`-`:05`) carried **8 of
11**; slots 1, 3 and 9 carried one each. Under a uniform null across the
12 flush slots the expectation for slot 0 is **0.92**, and
`P(X >= 8 | n = 11, p = 1/12) = 3.0e-07`.

**σ-provenance:** none imported. The null is **structural** — 12
equal flush slots per hour — and the test is a binomial on counts of
distinct carrying flushes. No dispersion figure from any other
population enters this calculation. (Per the mandatory-provenance rule
filed the same day; a structural null is the one case that needs no
measured σ.)

That is the socket-churn signature the rule predicted in advance: a
fresh `httpx.AsyncClient` per call, six concurrent requests per symbol,
no reuse — failing hardest exactly when concurrency peaks, and on
**arbitrary** symbols (`BTCUSDT` and `ETHUSDT` are in the list) rather
than on a fixed set.

### GRANULARITY CAVEAT, stated because it bounds the claim

`last_seen_at` is written as `now()` **at flush time**, and every row in
one flush shares that stamp. So *simultaneity is resolvable to five
minutes and no finer.* I cannot claim that all six timeframes of
`ARBUSDT` failed in the same instant — only that they failed inside the
same five-minute window. The hour-boundary concentration survives this,
because a flush with no new delta writes no row, so which flushes carry
rows does track when failures happen at 5-minute resolution.

## ARB: now has a MECHANISM, and is still NOT EXPLAINED

The rule pre-committed me to this distinction, so I am holding to it.

`ARBUSDT` appears with `ConnectTimeout` on all six timeframes, in one
flush window at 2026-10-28T04:00-04:05Z. Two prior rounds had left it
mechanism-less: not a 400 (it is spot-listed, so the endpoint is right),
and not a timeout (the 180-request repro returned 0 requests near the
2.0s budget). **There is now a transport failure on ARB in the record.**

**But two occurrences across 47 cycles is 4.3% of cycles, and that does
not account for a persistent NULL.** A mechanism that fires on 4% of
cycles cannot be the cause of an mtf value that is missing far more often
than that.

**So: ARB's NULL remains unexplained.** 1(b) does not absorb it, and I am
not going to let the arrival of a plausible-looking exception class close
a question that the arithmetic says is still open.

## What this does NOT license

Restating the rule's own limit, which still binds: a transport-exception
reading says a **pooled client** is the fix, and (a) comes along with it
because the shared limiter and the pooled client arrive together — **not
because the lever got bigger.** The veto channel was measured at
**0.367 cards/day** and that number is unchanged by anything here.

## NO POST-DEPLOY DATA EXISTS YET

Every `last_seen_at` in this read is `<= 16:05:39Z`. The 1(b) pooled,
loop-scoped, throttled client deployed at **16:28:28Z**. **The entire
window above is PRE-FIX.** Whether the pooled client reduces
`ConnectTimeout` is therefore **untested**, and this document asserts
nothing about it. The read-back at 16:29Z confirmed the deployed object
is a `RateLimitedClient` with `_max_connections == 20` and
`klines` weight 2 — that is the fix being *present*, which is not the
fix being *effective*.

The next read of this counter should be taken no earlier than
**2026-10-31T16:30Z** (47+ post-fix cycles, matching this window's
denominator) and compared on `ConnectTimeout` occurrences per cycle.
