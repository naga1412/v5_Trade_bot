# The (symbol, tf, failure) key, and making the CI near-miss impossible

**Date:** 2026-10-26
**Status:** shipped. Observability + test infrastructure only; no
behaviour change to any scoring, dispatch or trading path.
**Order:** follow-up items 2 and 4, 2026-10-26.

---

## Item 2 — the counter key

### What was wrong

```python
def _record_fetch_failure(symbol: str, tf: str) -> None:
    _fetch_failures[symbol] = _fetch_failures.get(symbol, 0) + 1
```

`tf` was **accepted and discarded**. So the two facts that separate a
routing failure from a timeout — *which* timeframes failed and *what*
the failure was — existed at the call site and were thrown away.

The cost was concrete: classifying ARBUSDT as permanent-vs-transient
required **two manual reads of consecutive hourly cycles**, and
"is it a timeout?" stayed unanswerable at the end of it.

### The shape the new key exposes

| diagnosis | signature |
|---|---|
| **routing failure** | every timeframe fails, one status code, persistently |
| **timeout** | concentrates on the longest fetches, or scatters |

So it automates the classification that previously needed two cycle
reads, and it answers the timeout question in a single read.

### The status code is part of the label, deliberately

```python
def _failure_kind(exc): ...  # -> "HTTPStatusError_400" | "ReadTimeout" | ...
```

Collapsing to `"HTTPStatusError"` would **throw the answer away a second
time**: 400/404 means Binance does not have this symbol on this market
(routing), while 429/418 or a transport exception means the request never
got a verdict. A test pins the exact labels rather than asserting they
are merely "different".

### Two decisions worth stating

**The symbol-level series is kept unchanged beside the new one.** The
2026-10-26 permanent/transient classification was made *from* that
series, and re-keying it would have broken that comparison. The detail
series lands under its own `check_name`
(`mtf_kline_fetch_failed_detail`), so neither disturbs the other.

**`get_fetch_failure_detail` is a separate accessor, not a widened
return.** `get_fetch_failure_counts`' `dict[str, int]` shape is consumed
by the flusher and its tests, and widening a return in place is the
pitfall that broke three integration unpack sites when PR #266 bumped a
tuple from 8 to 9.

Volume is bounded by symbols × 6 timeframes × failure kinds and
aggregated **daily**, so it stays a few hundred rows a day rather than
per-event amplification — the same granularity rule as the symbol series.

**`TF_FETCH_TIMEOUT_S` is untouched.** It stays untouched until this
establishes that a timeout is what is happening.

## Item 4 — outbound sockets blocked, enforced not reviewed

### What nearly shipped

Three tests in `tests/shadow/` patched `app.shadow.worker.BinanceClient`.
That class was deleted with its last caller, the patch target stopped
existing, and the tests failed with an `AttributeError`.

**Repairing the `AttributeError` alone would have left the double
unapplied and the call live** — green locally, green in CI, and dependent
on an external service. "A careful reader notices" is not a control.

### The design, and why it blocks connects rather than sockets

An autouse fixture in `tests/conftest.py` replaces
`socket.socket.connect` and `connect_ex` with guards that refuse any
non-loopback address.

**It does NOT patch `socket.socket` itself.** asyncio builds its
self-pipe from a socketpair on several platforms, so blocking socket
*creation* would stop the event loop from starting and take the suite
down instead of guarding it. Loopback and `AF_UNIX` stay open, so
in-process servers, aiosqlite and the event loop are unaffected. A
positive-control test asserts loopback is **not** blocked, because a
guard that blocked everything would satisfy every other test here.

`connect_ex` is covered as well as `connect`: it returns an errno instead
of raising, so a guard that patched only `connect` would leave a silent
bypass.

### The opt-in is deliberately awkward in exactly one way

`@pytest.mark.allow_network` is registered in `pyproject.toml`, and
`--strict-markers` is already on. So granting a test network access is a
**visible edit in two places**. A test pins both the registration and the
`--strict-markers` flag, because if the registration were dropped the
marker would start erroring and the tempting "fix" is to delete the
marker — silently re-blocking a test that needs egress — or to delete the
guard.

### The narrower lesson lives in the failure message

The message a developer actually hits says:

> **A TEST DOUBLE IS NOT APPLIED.** If you arrived here after fixing an
> `AttributeError` from a patch target that no longer exists: **the
> `AttributeError` was the symptom, not the bug.** Repairing the error is
> not the same as restoring the double. Check that the NEW call path is
> patched, not just that the old name resolves.

...and names the second likely cause too — code that *grew* a network
call it did not have before, which is exactly what happened here (the
listing-ensure step added to the shadow seed). A test asserts those
sentences are still present, so a future edit cannot quietly reduce the
message to "network blocked".

### Test enumeration now matches CI

CI runs plain `pytest`, which takes `testpaths = ["tests"]` from
`pyproject.toml` — the whole tree. The equivalent local command is
`python tests/run_suite.py tests/`. The glob that caused the near-miss
(`ls tests/unit/ | grep -i shadow`) could not see `tests/shadow/`, one of
**eleven** subdirectories under `backend/tests/`.

### And the guard immediately caught something of mine

`scripts/listing_completeness_audit.py`, added in the same change, names
`/api/v3/klines` and tripped the bar-fetch endpoint guard. That is the
correct outcome, and the exemption has a reason that survives review:
**the script audits the listing computation against upstream truth, so
routing it through the resolver would make the audit circular** — it
would inherit the very filters under test and could not detect an
incomplete set at all. Added to the allowlist with that argument and the
count assertion bumped 9 → 10, as a deliberate reviewable edit.
