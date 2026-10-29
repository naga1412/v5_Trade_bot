# PRE-COMMITTED: what the mtf status-code split decides

**Date:** 2026-10-27
**Status:** rule written **BEFORE** the counter was read. No counter data
appears in this document.
**Order:** item 2 of the 2026-10-27 work order.

---

## The rule

`mtf_kline_fetch_failed_detail` records `subject = "<SYMBOL>/<tf>/<failure>"`
where `<failure>` is `HTTPStatusError_<code>` or a transport exception
class name. On reading it:

| dominant failure | diagnosis | decision on defect (a) |
|---|---|---|
| **400 only** | not listed on spot — the FU-53 class, already understood | **(a) stays cleanup, low priority** |
| **429 or 418** | rate limiting | **MIGRATE (a) — it is a fix, not cleanup** |
| **transport exception** | connection/socket exhaustion | **MIGRATE (a) — a shared pooled client IS the fix** |

**Two of three say migrate. All three say fix (b) regardless.**

## The two defects, deliberately separated

**(a) WRONG ENDPOINT for futures-only symbols.** FU-53 class. Worth
0.367 cards/day on the veto channel and ~0.02% of the daily weight
budget. **HELD** pending the split above.

**(b) NO THROTTLING, and a raw `httpx.AsyncClient` PER CALL** with 6
concurrent requests and no semaphore. An operational hazard **on its own
terms**, independent of (a).

**Fixing (b) does not require fixing (a)**, and (b) is being fixed now:
route through the shared limiter with a pooled client, leave endpoint
selection untouched.

## The leading hypothesis, labelled as one

**CONNECTION/SOCKET EXHAUSTION, not rate limiting.**

A fresh client per call never reuses connections, so every cycle opens
and discards many sockets. TIME_WAIT or FD accumulation then produces
transport failures on **arbitrary** symbols, intermittently,
environment-specifically — and **not reproducible from a clean machine
with 30 controlled clients**, which is exactly what my 180-request repro
was.

That repro returned **400s only, zero 429/418**. That is evidence
*against* rate limiting and *consistent with* socket exhaustion, because
the mechanism cannot be reproduced on a host that is not already in the
exhausted state.

**The signature to look for is a TRANSPORT EXCEPTION, not an HTTP
status.** The counter separates them precisely because an HTTP status and
a transport failure are different diagnoses, and collapsing them was
what left ARB unexplained for two rounds.

## Why this is written before the read

The counter is one query away. Writing the mapping afterwards would let
the data choose the rule — and with three possible outcomes and a
"migrate" verdict attached to two of them, that is exactly the situation
where a post-hoc rule is indistinguishable from a conclusion.

This is the same construction as the 2026-11-03 overshoot
pre-registration and the dead-letter interpretation: the decision is
fixed, then the number is read.

## What this does NOT license

It does not license migrating (a) on a transport-exception reading *and
then* claiming the veto channel is worth more than 0.367 cards/day. The
volume question was measured and is unchanged. A transport-exception
reading says a **pooled client** is the fix; migrating (a) comes along
with it because the shared limiter and the pooled client arrive together,
not because the lever got bigger.
