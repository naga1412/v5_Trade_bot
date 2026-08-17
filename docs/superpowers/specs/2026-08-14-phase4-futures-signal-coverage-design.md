# Phase 4 — Futures-Only Signal Coverage

**Status**: design, pending spec review before implementation planning.
**Context**: the operator manually trades every signal on Binance
futures — they verify each one, size it, and manage risk themselves.
The bot is a **signal source**, not an execution engine, for this
workflow. The standing "expand live capacity only after shadow proves
positive net edge" rule does not apply here: that rule governs
autonomous execution risk, and there is none in this path — the
operator is the decision layer on every trade.

---

## ADDENDUM (2026-08-15) — selector inverted: liquidity floor, not top-N by volume

**Status: design, supersedes the Stage A/B sizing and the top-N-by-volume selector below. Ratified as `docs/superpowers/decisions/2026-08-15-liquidity-floor-selector-supersedes-top20.md`. Tasks 5 and 8 of the plan doc, and the plan's Rollout section, need re-drafting against this addendum before they execute — not yet done, flagged as follow-up.**

### What changed and why

Measured (2026-08-15, live data): of the full 527-symbol USDT-M perpetual market, only **42 symbols stably pass** the liquidity floor already defined below (34 spot-backed, 8 futures-only), plus 7 that flicker pass/fail within seconds on live order-book noise. Today's selector — top-20 by **spot volume rank** — is a criterion unrelated to tradeability; that's the same volume-does-not-imply-depth asymmetry this floor exists to catch (see the FU-43 finding below), just applied to the *selector* itself, not only to individual symbol inclusion.

**New selector: the live-prediction universe is every symbol passing the liquidity floor, full stop — not a rank cutoff over a volume-ordered list.** Expected size ~42, rechecked daily (it will drift as market conditions change).

This closes both coverage gaps identified so far with one mechanism:
- The 8-11 futures-only names (Goal 1 of the base design, below).
- The spot-backed names that clear the liquidity floor but rank outside today's top-20-by-volume — a larger gap (34 vs 20 spot-backed slots) than the futures-only one, surfaced during the coverage-measurement pass that motivated this addendum.

**Named, immediate consequence — not swept into "coverage roughly doubles":** cross-referencing the 07-28 fleet-cap decision's sample top-20 snapshot, **ADA** and **NEAR** — both established, currently-covered majors — stably fail the new floor on spread (ADA ≈5.59bps, NEAR ≈6.1bps, consistent across repeated sampling, not flicker). Under this selector they are candidates for exit via the hysteresis rule below, same as any other symbol. This is flagged explicitly so it is a known, named tradeoff decided with eyes open, not a silent side effect discovered later.

**Open, unresolved dependency question (verify before Task 5 is re-drafted):** the floor is evaluated on **futures** market data; `asset_universe` is ranked by **spot** volume. Not yet confirmed whether all 34 liquidity-qualified spot-backed symbols are members of today's spot-ranked 30-symbol universe. Any that aren't need new universe-membership + shadow-tracking work in addition to a live-WS-subscription change — check `asset_universe` current rows against the qualifying list before implementation.

### (a) Hysteresis — proposed rule

Order-book spread on thin-tick symbols flickers within seconds (measured directly: AKEUSDT read 7.3bps → 2.1bps → 4.2bps across three queries a few seconds apart, straddling the 5bps line each time — genuine market microstructure, not measurement error). A universe that re-evaluates and flips membership on every daily refresh using a single point-in-time sample would inherit that noise at the selection layer.

Proposed:

1. **Multi-sample per refresh, not single-sample.** At each daily universe-refresh, sample `check_liquidity` **M=5** times, spaced ~10s apart (≈50s added latency total, negligible against a 24h cycle — reuses the existing daily-refresh cadence, doesn't add a new one).
2. **Asymmetric N-of-M confirmation, biased toward retention (revised 2026-08-17, operator ruling):**
   - **Entry**: a symbol not currently in the universe must pass **≥3 of 5** samples to be added.
   - **Exit**: a symbol currently in the universe must fail **5 of 5** samples (unanimous) to be removed.
   - The original draft of this addendum proposed symmetric 4-of-5 both ways, explicitly flagged as "not load-bearing, tune after observing real flip rates." The operator's revision is better-reasoned, not just a preference: the two error types are not equal cost. A false-negative exit strands coverage outright (and, absent the override below, could orphan an open position); a false-positive retention just means a marginally-thin symbol keeps showing its real liquidity numbers on the card/app row (per the base design's Visual distinction section, below) — the operator sees the numbers and self-filters. Biasing hard toward "stay in" (unanimous 5/5 to leave) costs little given that visibility, while biasing toward "get in easily" (3/5) keeps the coverage-expansion goal from being blunted by the same noise that motivated sampling in the first place.
3. **Minimum dwell time**: once added, a symbol cannot be removed for **≥24h** (one full refresh cycle) regardless of subsequent readings, *unless* the open-position override below applies in the other direction (never dropped) or unless it fails so consistently that the next refresh's unanimous 5/5 exit check fires on schedule — dwell time bounds churn frequency, it doesn't block a confirmed exit, it just guarantees a symbol gets at least one full cycle before being re-evaluated for removal.
4. **Open-position override — hard requirement, not best-effort:** a symbol with an open `live_trades` or `shadow_open_positions` row is **never removed from the live-prediction fleet**, regardless of liquidity-check outcome, until the position closes. This is a deliberate extension of the base design's existing behavior below (which explicitly chose *not* to build retention — see "Open-position retention" in the base design) — the operator is now requiring it. On position close, the symbol re-enters normal hysteresis evaluation on the next refresh (no automatic retention beyond the position's lifetime).

**Required test**: a symbol failing 5/5 samples but holding an open position is retained; the same symbol with no open position is removed on schedule per the N-of-M/dwell rule above. Proof obligation, same standard as the base design's other required tests — not optional coverage.

### (b) Cost check — rate limits clear; compute/memory not measured, recommend staging-soak gate

**Rate-limit budget: no problem, computed exactly.**
- Futures-only cohort (8-11 symbols) uses the REST poller — unaffected by this addendum, same math as the base design below (480-660 calls/hr against a 144,000/hr shared budget). The stable-pass futures-only count (8) already roughly matches the base design's planned starting N=8.
- Spot-backed cohort (34 vs today's 20) uses WS subscriptions, not REST polling — Binance's documented per-IP WS connection limits (on the order of hundreds) are not a binding constraint at 34 concurrent connections; this was never the bottleneck.

**Compute/memory: not measured — no existing benchmark to cite, and none should be invented.** No documented per-child-task memory/CPU cost exists anywhere in this codebase, and the backend container has no configured resource ceiling in `docker-compose.yml` to test against. What can be said with the existing architecture as evidence:
- Each WS-fleet child is a lightweight asyncio task (one WS connection + a coroutine invoking the shared `run_live_prediction` body) — not a new process or thread; the same pattern already runs 20 of these today without documented issue.
- A meaningful share of the marginal compute for symbols *already inside* today's 30-symbol shadow universe is already being paid — `shadow_worker` scores the full 30-symbol universe every candle close regardless of live-fleet membership. Going from 20 to 34 spot-backed live-fleet symbols adds WS-subscription + persist + dispatch overhead on top of scoring that may already be happening for a subset of those 34, not a fully new class of load. This does **not** cover the open dependency question above — any of the 34 that fall outside the current 30-symbol shadow universe entirely would add both scoring and fleet overhead, not just fleet overhead.

**Recommendation, not a substitute for the above:** measure it — enable the liquidity-floor selector in **staging only** first (matching the base design's own staged-rollout philosophy below), and record `docker stats tr-staging-backend` CPU/memory before and after, across at least one full 24h cycle including a candle-close burst. Treat this as a go/no-go gate before promoting to prod at full N≈42, not an assumption to build on. **If the staging measurement shows N≈42 does not fit, fall back to the largest workable N ranked by liquidity** (highest `qvol_24h`/lowest `spread_bps`/highest `depth_0_5pct_usdt` among floor-passers, in that priority order — never volume alone) rather than reverting to a volume-based cutoff.

### (c) Cohort tagging — three-way, not two

**Table list correction (2026-08-17, operator caught this):** the base design's `symbol_source` column (below) is spec'd on `predictions`, `telegram_signals`, and `live_trades` only — it does **not** include `shadow_trades`. That gap matters more here than it did in the base design, because the entire safety case for this addendum rests on being able to re-run a `shadow_trades`-based split by cohort (reversal criterion #2 above, methodologically identical to the 07-28 ratification's own "30-day LONG `shadow_trades` split" — that measurement was built FROM `shadow_trades`, not `predictions`). Without a cohort tag on `shadow_trades` itself, criterion #2 cannot actually be evaluated. **`symbol_source` must be added to `shadow_trades` as a fourth tagged table**, not just the three below — this is a required correction to Task 9/10 of the plan doc's plumbing (the same class of gap already caught once there), not an optional extra.

This has a real dependency, not yet resolved: for the `futures_poll` cohort specifically, a `shadow_trades.symbol_source` column only has something to tag once shadow actually tracks futures-only symbols at all — which the base design's Goal 1e explicitly defers as "secondary... a follow-up, not a blocker." That deferral needs revisiting now that the measurement plan depends on it; flagged here, not resolved in this addendum. For the `liquidity_added_spot` cohort, this dependency is generally moot wherever the open question in "What changed and why" above resolves positively (i.e. wherever those symbols already sit inside today's 30-symbol shadow universe) — existing rows just need the new column populated, no new shadow-tracking work.

With that correction, the base design's `symbol_source` column becomes three values instead of two, on all four tables (`predictions`, `shadow_trades`, `telegram_signals`, `live_trades`):

- `'established_top20'` — symbols that were live-covered under the pre-2026-08-15 volume-rank selector, tagged at cutover (a lineage/snapshot tag, not a dynamically-recomputed rank — once assigned at cutover, a symbol's tag doesn't change just because its historical volume rank moves).
- `'liquidity_added_spot'` — spot-backed symbols newly covered because they clear the liquidity floor but were outside the old top-20-by-volume selector. **This is the larger of the two new-coverage gaps and the one carrying the real open risk**, per the operator's own framing: the 07-28 decision's "ranks 21-30 are net losers" finding was measured on a **volume-ranked** population (see the superseding decision record) and does not transfer to this **liquidity-ranked** population. It is not proven safe and not proven unsafe. It must be independently measurable from day one — same discipline as the original 07-28 ratification (30-day split at the live gate, adequate sample size, all-three-of-WR/avg-pnl/sum-pnl required before any safety claim), run fresh against this specific cohort, not inferred from the old finding.
- `'futures_poll'` — unchanged from the base design below (Goal 1's futures-only cohort).

Every downstream consumer (Telegram card, app view — both below) reads this three-way tag as the single source of truth, same "no second place this could drift" principle as the base design.

### (d) Superseding the fleet-cap ratification

`docs/superpowers/decisions/2026-07-28-fleet-cap-top-20-ratified.md` is **superseded, not silently abandoned** — see `docs/superpowers/decisions/2026-08-15-liquidity-floor-selector-supersedes-top20.md` for the formal decision record, the reversal-criterion citation, and the explicit statement of what does and does not carry over from the 07-28 measurement.

---

*(Base design below this line is the original 2026-08-14 spec. Its Goal 1/1a-1e, Step 0, and the visual-distinction/app-view sections are unaffected by this addendum except where explicitly noted above — Step 0 in particular still ships and soaks alone, unchanged, as the prerequisite for everything else. The liquidity-floor Safety section (S1) below is the same three-metric definition this addendum's measurement uses; only the *selection scope* — full market vs. futures-only-top-30-sample — and the *selector logic* — pass/fail vs. rank cutoff — change.)*

---

## Problem

The live prediction/dispatch path (`predictions` table → Telegram) is
fed by two mechanisms today, both scoped to **spot-listed** symbols:

- `live_worker` — a single hardcoded BTC/USDT WS subscription
  (`app/ws/live_prediction.py::run_live_prediction`, default
  `symbol_pair="BTC/USDT"`).
- `ws_keepalive_task` — fans the same `run_live_prediction` coroutine
  out across the top-20 `asset_universe` symbols, each on its own
  Binance **SPOT** WS subscription (`app/ws/keepalive.py`).

`asset_universe` itself ranks by **SPOT** USDT volume
(`app/shadow/universe.py::fetch_top_n_usdt_spot`). But the operator
trades **futures**. Of the 527 USDT-M perpetual contracts currently
trading, 169 have no spot equivalent on Binance at all — a real,
measured 32% of the futures universe is structurally invisible to
every part of this pipeline, and per the operator's own account, most
of today's daily top-10 futures movers fall in that invisible set.
Those symbols never enter `asset_universe`, never get a WS
subscription, never produce a `predictions` row, and never reach
Telegram — regardless of how much they're moving.

## Goals

1. Futures-only symbols reach the real live prediction → dispatch
   path (not just shadow), without touching the existing spot-WS path
   for existing symbols.
2. Signals are visible in both Telegram (unchanged) and a new app view.
3. A liquidity floor keeps thin, unexit-able coins out of the
   operator's hands entirely, with the numbers behind that decision
   shown, not hidden behind a pass/fail gate.
4. Everything ships behind a staged rollout: staging first, N=8,
   widen only after a clean week.

## Non-goals

- No change to sizing, execution, or risk logic — the operator does
  all of that manually. This ships coverage, not autonomy.
- No change to the existing spot-WS keepalive fleet's behavior for
  existing symbols — verified, not just intended (see Step 0 below).
- No rate-limiter priority reweighting between the poller and other
  Binance Futures consumers (intermarket, flow-features). Deferred
  until the new wait-visibility counter (below) shows real contention
  — unnecessary complexity at today's traffic level.

---

## Step 0 — Extract `run_live_prediction`'s candle source (its own PR, ships first)

`run_live_prediction` currently constructs its own WS stream inline:

```python
stream = BinanceKlineStream(symbol=binance_symbol, timeframe=timeframe)
async for candle in stream.stream():
    <~300 lines: pattern-stats lookup, ghost prediction, build_prediction,
     persist_prediction, publish, _maybe_dispatch, heartbeat>
```

This body — scoring, gating, dispatch, persistence, heartbeats — must
be **identical** for spot-WS and futures-REST-poll symbols; only the
candle source differs. The refactor makes that source an injectable
parameter:

```python
async def run_live_prediction(
    symbol_pair: str = "BTC/USDT",
    timeframe: str = "1h",
    *,
    candle_source: AsyncIterator[MultiStreamCandle] | None = None,
    symbol_source: str = "spot_ws",
) -> None:
    ...
    source = candle_source or BinanceKlineStream(
        symbol=binance_symbol, timeframe=timeframe,
    ).stream()
    async for candle in source:
        <SAME body, byte-for-byte unchanged, now also threading
         symbol_source into the persisted/dispatched payload>
```

**Why this ships as its own PR, ahead of the poller**: this codebase
has already paid for exactly the alternative (a second, hand-copied
implementation silently diverging) — `shadow/worker.py`'s own
`HISTORY_BARS=300` drifted independently of `live_prediction.py`'s,
PR #400 fixed one and missed the other, and `effective_score` stayed
NULL for 638 hours as a result. A third independent copy of the same
constant was later found in `tab1.py`. Two implementations of the same
~300-line body is a guaranteed repeat of that failure mode, not a
hypothetical one.

**Proof obligations for this PR** (all required, not aspirational):

- The WS caller (`ws_keepalive_task`, `live_worker`) passes exactly
  the iterator it constructs today — `stream.stream()`. No other
  change to the function body in this PR; extraction only, no
  drive-by cleanup.
- **Every existing test passes unchanged.** If any existing test needs
  editing to pass, that means behavior changed and the refactor is
  wrong — treat a required test edit as a failure signal, not as work
  to do.
- New test: assert the WS path still constructs a `BinanceKlineStream`
  and consumes it when `candle_source` is not supplied — a guard
  against a future change silently repointing the default.
- `symbol_source` defaults to `"spot_ws"` — every existing call site
  is unaffected without passing anything new.

Once this PR is soaked and merged on its own, the poller is a purely
additive second caller of an already-verified function — if anything
regresses on existing symbols, the extraction can be reverted without
unwinding the feature built on top of it.

---

## Goal 1 — Futures-only universe coverage

### 1a. Symbol universe

Futures-only = a USDT-M perpetual contract (`fapi/v1/exchangeInfo`,
`contractType=PERPETUAL`, `status=TRADING`) with **no** corresponding
symbol in the SPOT `exchangeInfo` (`status=TRADING`). Measured today:
527 futures perpetuals, 490 spot USDT pairs, **169 futures-only**.

Ranking: 24h USDT quote volume from `fapi/v1/ticker/24hr`, same shape
as the existing spot ranking in `universe.py`, but a new
`fetch_top_n_usdt_futures_only(http, n)` function — kept separate from
`fetch_top_n_usdt_spot` rather than merged, so the existing spot
ranking's behavior is untouched (same isolation principle as Step 0).

### 1b. Two separate pools, not one merged fleet

`ws_keepalive_task` keeps `KEEPALIVE_TOP_N=20` spot-backed symbols,
completely unchanged. A **new**, **separate** supervisor —
`futures_poll_task` — owns `N=8` futures-only children on top. Two
independent supervisors, two independent child-task sets, no shared
state between them beyond the `run_live_prediction` function they both
call. This is the actual isolation mechanism for requirement (d): a
crash anywhere in the new supervisor (a bug in the poller's own logic,
not just a single symbol's fetch failure) cannot reach the spot-WS
supervisor's tasks, because they are not the same asyncio task tree —
they're two `start_*_task()` calls in `main.py`'s lifespan, exactly
like every other pair of independent workers in this codebase already
is. The existing per-symbol restart-with-backoff pattern
(`_run_child_with_restart`) is reused verbatim inside the new
supervisor too, so a single futures-only symbol's fetch failures don't
take down its 7 siblings either — isolation at both the supervisor
level and the per-symbol level, matching the existing fleet's own
proven design rather than inventing a new one.

### 1c. REST poller — candle source

Confirmed: poll every ~60s per symbol, fetch the last 2 klines
(`fapi/v1/klines?symbol=X&interval=1h&limit=2`), treat the
second-to-last row as guaranteed-closed, detect a new close by
open-time advancing past the last one processed (not wall-clock).
Cost: 8 symbols × 60 polls/hr = 480 calls/hr, against a 2400
weight/min (144,000/hr) shared Binance Futures budget.

```python
async def futures_rest_poll_candles(
    symbol_pair: str, timeframe: str, *,
    rate_client: RateLimitedClient, session_factory, poll_interval_s: float = 60.0,
) -> AsyncIterator[MultiStreamCandle]:
    watermark = await _load_watermark(session_factory, symbol_pair, timeframe)
    while True:
        t0 = time.monotonic()
        rows = await _fetch_last_n_klines(rate_client, symbol_pair, timeframe, n=2)
        wait_s = time.monotonic() - t0
        if wait_s > _RATE_LIMIT_WAIT_LOG_THRESHOLD_S:
            log.warning("futures_poller: rate-limit wait %.2fs for %s", wait_s, symbol_pair)
            _RATE_LIMIT_WAIT_COUNT[symbol_pair] += 1

        closed = rows[-2]
        if watermark is None or closed.open_time > watermark:
            if watermark is not None:
                expected_next = watermark + INTERVAL_SECONDS[timeframe]
                if closed.open_time > expected_next:
                    gap = (closed.open_time - expected_next) // INTERVAL_SECONDS[timeframe]
                    log.error("futures_poller: gap %s/%s, skipped ~%d candle(s)",
                              symbol_pair, timeframe, gap)
                    _GAP_COUNT[symbol_pair] += 1
            yield _to_multistream_candle(closed)
            # Resumed only after run_live_prediction's body has fully
            # finished processing `closed` (persisted, dispatched,
            # heartbeat) — async-generator semantics guarantee the
            # watermark never advances past a candle that wasn't
            # actually processed.
            watermark = closed.open_time
            await _save_watermark(session_factory, symbol_pair, timeframe, watermark)
        await asyncio.sleep(poll_interval_s)
```

**Idempotency**: new table `live_prediction_watermarks(symbol,
timeframe, last_open_time, updated_at, PRIMARY KEY (symbol,
timeframe))`. In-memory watermark for the hot path, seeded from this
table at startup so a restart can't reprocess the last candle,
persisted only after the SAME candle's full processing (through
`persist_prediction`'s commit) has completed — not before. This keeps
the hash-chained `predictions` table's schema untouched; idempotency
lives entirely in a new, non-chained table.

**Required test**: a replay test is a proof obligation for this PR,
not optional coverage — feed `futures_rest_poll_candles` (or the
watermark-checking unit directly) the same closed candle twice in a
row (simulating a restart-then-reprocess or an overlapping-tick race)
and assert the second occurrence does **not** re-yield, does not
produce a second `predictions` row, and does not fire a second
Telegram send. This is the single most important test in the whole
feature — a duplicate signal reaching the operator's phone is a real
defect, not a cosmetic one.

**Gap handling**: skip-forward, never backfill. A stale signal acted
on late is worse than a missing one. Logged at ERROR with the gap size
(in candle-intervals), and countable via a per-symbol in-memory
counter surfaced in the supervisor's own heartbeat `details` JSONB —
no new table needed for this, matching how every other worker already
reports counters.

**Fail-loud**: every fetch failure logs at **WARNING** immediately
(never DEBUG — the pseudocode above omits the try/except for brevity;
the real implementation wraps `_fetch_last_n_klines` and logs each
failure as it happens, matching every other Binance-calling module in
this codebase). On top of that per-attempt WARNING, this reuses this
session's own consecutive-failure-streak pattern (flow_features.py /
patterns / traps) — a per-symbol counter, reset on success, escalating
to `log.error` once a threshold is crossed, so a systematic failure
(this symbol's poller is actually broken, not just one transient
network blip) is distinguishable from noise. Given the 60s cadence is
far tighter than flow_features' hourly one, the default threshold
should be tuned lower (a config detail for the implementation plan,
not load-bearing here) — three DEBUG-level swallows have already hidden
months-long outages in this codebase (the taker-ratio, OI-delta, and
regime-classifier incidents); this poller does not get a fourth.

**Rate-limit sharing + visibility**: the poller's REST calls route
through the same `RateLimitedClient` singleton
(`app.data.adapters.get_intermarket_adapter().rate_client`) that
FU-40 already wired flow-features and the intermarket adapter into —
one coordinated view of the real Binance IP-level weight limit, per
Binance's limit being IP-scoped rather than per-consumer. The poller
times its own `.request()` calls and logs at WARNING (with a countable
metric) whenever a call waits on the bucket — this distinguishes "the
limiter delayed us" from "we missed a candle" at the source, since
those two failure modes would otherwise look identical from the
outside.

### 1d. Cohort tagging (S3)

New column `symbol_source TEXT NOT NULL DEFAULT 'spot_ws'` on
`predictions`, `telegram_signals`, and `live_trades` — values
`'spot_ws'` | `'futures_poll'`. Threaded through as the one new
`symbol_source` parameter on `run_live_prediction` (default preserves
every existing row/caller unchanged), flowing into
`build_predictions_payload`, the `telegram_signals` insert, and the
`live_trades` insert on approval. This is the single source of truth
every downstream consumer (Telegram card, app view, future reporting)
reads from — no second place this tag could drift out of sync with
reality.

### 1e. Dispatch + shadow (secondary)

Dispatch inherits automatically — `_maybe_dispatch` lives inside
`run_live_prediction`'s shared body, unchanged. No separate wiring
needed. Shadow tracking of the same futures-only symbols is genuinely
secondary and out of this spec's critical path — the existing shadow
universe/WS mechanism is untouched by this design; whether/how to also
feed these symbols into shadow for measurement is a follow-up, not a
blocker for Goal 1.

---

## Safety — liquidity floor (S1)

### Thresholds (confirmed against real data)

`24h USDT quote volume ≥ $20M` **AND** `spread ≤ 5bps` **AND**
`resting depth within 0.5% of mid ≥ $50,000` (combined bid + ask).

Tested against the top 30 futures-only symbols by 24h volume today:
**10/30 pass** (HYPEUSDT, 1000PEPEUSDT, BEATUSDT, GRVTUSDT, CAPUSDT,
XMRUSDT, LITUSDT, DOSUSDT, 1000BONKUSDT, 1000SHIBUSDT) — a selective
but not overly narrow starting cohort.

**Key finding driving the three-metric design** (see FU-43 in
`backend/docs/KNOWN_ISSUES.md`): 24h volume alone would badly overstate
tradability. AKEUSDT ($1.1B 24h volume), APRUSDT ($305M), CYSUSDT
($245M), VELVETUSDT ($179M), and BTWUSDT ($110M) all carry under
$50,000 of resting depth within 0.5% of mid — thinner than several
symbols with a fraction of their volume. High volume reflects churn,
not standing book depth. A volume-only filter would have waved all
five of these through.

### Implementation shape

A pure, testable function:

```python
@dataclass(frozen=True)
class LiquidityCheck:
    passed: bool
    qvol_24h: float
    spread_bps: float
    depth_0_5pct_usdt: float

async def check_liquidity(symbol: str, rate_client: RateLimitedClient) -> LiquidityCheck:
    ...
```

Runs twice, not once:

1. **Daily**, at universe selection — coarse inclusion into the
   futures-only pool.
2. **At dispatch time**, for futures-only-cohort signals specifically
   (spot-backed signals skip this check entirely — untouched). Books
   move intraday; a symbol qualifying at 00:00 UTC can be thin by the
   time a real signal fires hours later.

**On a dispatch-time failure: suppress the card, don't send a
degraded one.** The floor's whole purpose — "never receive a signal
for a coin you can't realistically exit" — is only partially served by
sending a signal anyway with a warning attached. A missed signal costs
less than an unexit-able position. Logged at WARNING with a countable
rejection metric. (Overridable to a degraded-but-sent card if
preferred after seeing how often this actually fires in practice.)

**The same `LiquidityCheck` struct feeds both the gate and the
display** — the numbers shown on every card (Telegram and app) are
exactly what the gate decided on, not a separately-computed summary.

### Honest limitation

Resting order-book depth measured in calm conditions does not predict
depth during a fast move. On a sharp dump or pump, the book can
evaporate regardless of what it looked like moments before — no static
threshold protects against that; only position size does, and that's
the operator's own call, not something this feature can gate away.
This limitation is stated on the card itself (see Visual distinction,
below), not only in this document.

---

## Goal 2 — App view

New dedicated tab (not folded into an existing one) — this is becoming
the operator's **primary workflow surface** for manually trading these
signals day to day, not a section of a dashboard built for something
else.

- **Source**: `telegram_signals` (or a thin read-only endpoint over
  it) — never recomputed. What the operator sees is exactly what was
  dispatched.
- **Columns**: symbol, direction, score, entry, SL, TP, `rr_ratio`,
  confidence, timestamp, status, `symbol_source` cohort badge, and the
  three liquidity numbers (24h qvol, spread bps, depth-within-0.5%).
  **Correction**: `telegram_signals.response`'s actual `CHECK`
  constraint values are `'approved' | 'skipped' | 'timeout' | 'error'`
  (plus `NULL` while a card is outstanding, unresponded) — not
  `sent/approved/auto_skipped/expired` as originally phrased. The app
  view's status column should display these real values directly
  (`NULL` → "pending", `timeout` → what the operator would recognize
  as "expired"), not invent a mapping to values the schema doesn't
  have.
- **Full precision on entry/SL/TP** — the operator retypes these into
  Binance by hand; a rounded display value is a real trading error
  risk, not a cosmetic one.
- **Cohort tag unmissable at a glance** — a distinct badge/color on
  the row, not a small text label easy to skim past.
- **Auto-refresh** on a ~2 minute interval (matching the scanner's
  existing precedent) plus a manual refresh control, so a signal is
  never missed to a stale page.
- Newest-first, filterable by direction and cohort. Read-only — a
  review surface, not a trading terminal.

## Visual distinction (S2)

Both channels read from the same `symbol_source`-tagged payload — one
source of truth for "is this new-cohort," not two places that could
drift apart.

- **Telegram card**: a `🆕 NEW COHORT — thinner liquidity, unvalidated`
  banner line, plus the three liquidity numbers inline, plus the
  fast-move limitation sentence from above in short form.
- **App view row**: a distinct badge/color (not just a text tag) plus
  the same three numbers in their own column.

---

## Open-position retention when a symbol drops out of the top-N

**Verified against the actual code, not assumed**: the existing
spot-WS keepalive fleet does **not** retain symbols with open
positions today. `keepalive.py::_refresh_children` has zero
open-position awareness — it cancels any symbol's child task
unconditionally once that symbol drops out of the daily top-N
reconciliation, regardless of whether a `live_trades` or
`shadow_open_positions` row is still open on it. Separately,
`liquidation_monitor.py::_list_open_positions` queries `live_trades
WHERE status = 'open'` directly — completely independent of which
symbols the keepalive fleet currently has children for. So a
position's **exit monitoring is never orphaned** by a symbol dropping
out of the pool (SL/TP/liquidation checks run off `live_trades`
directly, not off the WS/poll fleet's state) — but the symbol **does**
stop receiving new candle-close predictions/signals from that fleet
until it re-enters the top-N, and that is true today for spot-backed
symbols, not a new gap Phase 4 introduces.

The futures-poller mirrors this **exact, verified** behavior — a
dropped-out futures-only symbol's poller child is cancelled the same
way the spot fleet already cancels dropped-out spot symbols, via the
same reused `_run_child_with_restart`/reconciliation logic. This keeps
both fleets consistent (an asymmetry between them would itself be a
new, unjustified divergence — the exact failure class Step 0 exists to
avoid) and requires no new position-awareness code.

**If retention is actually wanted** — keeping a symbol's poller alive
specifically because a position is open on it, so the operator keeps
getting fresh signals on something they're currently holding — that
would be genuinely new behavior, not a gap being closed, and the
natural scope for it is both fleets together, not just the new one.
Not building this now; flagged here so the choice is explicit rather
than assumed. Either way, this needs a test: with the current
mirrored-cancellation behavior, a test asserting an open-position
symbol's poller child *is* cancelled when it drops out of top-N,
matching the spot fleet's own (equally uncomfortable, already-shipped)
behavior — not a retention test, since that's not what's being built.

## Known limitation — manual execution is invisible to this bot's own data

The operator executes every one of these trades **manually** on
Binance, outside the bot's own `_place_live_order` path. That means
`live_trades` will contain **zero rows** corresponding to what the
operator actually did with a futures-only signal — that table only
ever records trades the bot itself placed, and it doesn't place these.

Consequently: **"did this coverage expansion actually help?" is only
ever answerable in shadow terms** — did a parallel shadow position on
the same signal turn out profitable — never in terms of the operator's
real, manual outcome. This is worth writing down plainly so it isn't
mis-read later: an absence of `live_trades` rows tagged
`symbol_source='futures_poll'` does **not** mean the futures-only
signals went unused. It means exactly what it says — the operator
didn't execute through the bot — and says nothing about whether they
executed manually on Binance instead. No measurement work is proposed
here to close this gap; it's a structural property of "signal source,
not execution engine," recorded so a future reader doesn't draw the
wrong conclusion from `live_trades` being quiet.

---

## Data model changes

- `live_prediction_watermarks(symbol, timeframe, last_open_time,
  updated_at)`, `PRIMARY KEY (symbol, timeframe)` — new table.
- `symbol_source TEXT NOT NULL DEFAULT 'spot_ws'` — new column on
  `predictions`, `telegram_signals`, `live_trades`.
- No changes to `HASH_PAYLOAD_COLUMNS` / the audit hash-chain shape —
  `symbol_source` is additive metadata, not part of any existing
  chained payload's hashed content (confirm during implementation that
  adding it to `predictions`/`live_trades` doesn't require a hash-chain
  migration; if it does, it's additive-only and existing rows keep
  their existing hashes, matching the PR1 record-only column
  precedent).

## Rollout

1. Step 0 (candle-source extraction) ships and soaks alone first —
   behavior-preserving, proven by unchanged existing tests plus one
   new guard test.
2. Futures-poller + liquidity floor + cohort tagging + app view ship
   to **staging**, verified for **24h** before main:
   - Candles arriving for all N=8 futures-only symbols.
   - `predictions` rows written with `symbol_source='futures_poll'`.
   - Telegram card renders correctly **end-to-end** — the actual
     rendered card checked before it would reach the operator's phone,
     not just that the code path ran without erroring.
   - App view renders, filters work, auto-refresh works.
   - **Existing spot-backed symbols verified unaffected** — same
     predictions cadence, same card format, no `symbol_source` drift
     on existing rows.
   - Universe membership diff reported (symbols added/dropped vs.
     today).
3. Main promotion following the standing soak-class + promotion
   checklist discipline (including the new FU-42-motivated settings
   diff step).
4. Start at `N=8`. Widen to 20-25 only after **one week** of clean
   operation (no sustained gap-count or failure-streak escalations).

## Deferred (explicitly out of scope for this spec)

- Rate-limiter priority reweighting between the poller and intermarket
  snapshots. The poller is time-critical (candle-close timing);
  snapshots are not. Revisit only if the new wait-visibility counter
  shows real contention — unnecessary complexity at today's traffic
  level.
- Shadow-side tracking of the futures-only cohort (secondary per the
  operator's own framing; not a blocker for Goal 1).
- Degraded-but-sent card as an alternative to suppression on a
  dispatch-time liquidity failure — revisit based on how often
  suppression actually fires once live.
