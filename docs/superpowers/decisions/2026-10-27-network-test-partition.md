# The 51 network tests, partitioned BEFORE anyone pays them down

**Date:** 2026-10-27
**Status:** screened, read-only. **Verdict: backlog, not a project.**
**Order:** follow-up items 1 and 2, 2026-10-27.

---

## The verdict in one line

**51 STRUCTURE, 0 VALUE.** No test in the set asserts on a value derived
from live market data, so **none of them can produce a false green from
market movement.** Cross-checked against CI history: **zero flakes among
the 51 across the last 80 runs.**

This is untidiness with a real operational cost, not a correctness
project.

## Why the count is 51 and not 99

Item 1 asked for an exact assertion: ten files, fifty-one tests. Building
it surfaced a defect in my own first fix.

I had marked the ten files with a **module-level**
`pytestmark = pytest.mark.allow_network`. That grants network to **every
test in each file** — and collection showed **99**, not 51:

| file | marked by blanket | actually need it |
|---|---|---|
| `test_dispatcher_e2e.py` | **24** | 5 |
| `test_trading_dispatcher.py` | **13** | 1 |
| `test_tools_backtest.py` | **7** | 2 |
| `test_pr_plumbing_1_funding_threading.py` | **7** | 2 |
| `test_predictor.py` | 15 | 15 |
| `test_predictor_brain_obs_inputs.py` | 10 | 7 |
| `test_api_predict.py` | 10 | 7 |
| `test_full_l9_pipeline.py` | 9 | 8 |
| `test_l2_pipeline_e2e.py` | 2 | 2 |
| `test_intermarket_traps_e2e.py` | 2 | 2 |
| | **99** | **51** |

**A grant 48 tests wider than the evidence is the same defect as having
no control at all** — and it would have drifted upward silently every
time an unrelated test was added to one of those files, with no review
signal. Switched to **per-test marks**, exactly 51, and a guard asserts
that no module-level blanket mark remains.

## Item 1 — the allowlist is closed

`tests/unit/test_network_allowlist_closed.py` pins the contents as
literals and asserts **both directions**, so an eleventh file *or* a
fifty-second test in an already-listed file fails and needs a deliberate
edit. The per-file counts are pinned individually as well as the totals,
so a change that preserves 51 while moving a test between files still
fails.

The failure message carries the argument rather than just the rule:

> **ADDING AN ENTRY HERE IS A DECISION, NOT A FIX.** The egress guard's
> value is entirely FORWARD-looking: it exists so the NEXT test that
> reaches the internet fails immediately rather than waiting to be
> noticed. An open allowlist reduces that value to nothing, because
> appending a line is cheaper than stubbing a transport and invisible in
> review.

The guard's own opt-in control test is counted **separately** (1), so it
cannot drift into the debt total and quietly turn 51 into 52.

## Item 2(a) — the partition, by assertion rather than by fetch

A heuristic AST pass flagged 10 candidates. **All ten were
misclassifications**, confirmed by reading each one:

| test | flagged assertion | why it is STRUCTURE |
|---|---|---|
| `test_predict_with_valid_signal_attaches_markers` | `entry == approx(100.0)` | the test **inserts** that `shadow_trades` row itself |
| `test_predict_attaches_ghost_when_active_checkpoint_loaded` | `ghost.close == 151.0` | `predict_ghost_candle` is **monkeypatched** to a stub |
| `test_l2_layer_lands_in_prediction_and_persists` | `0.0 < strength <= 1.0` | fixture bars + **seeded** `PatternStatsLookup` |
| `test_l2_pipeline_e2e_persists_and_audit_chain_includes_l2` | `strength > 0.0` | same |
| `test_build_prediction_populates_layer9_when_session_provided` | `0.0 < strength <= 1.0` | news **seeded** into the session, and the clock **frozen** so the seeded `published_at` is in the lookback window |
| `test_layer2_score_respects_disabled_patterns` | `strength == 0.0` | `enabled_patterns=set()` makes it zero **by construction** |
| `test_build_prediction_returns_required_keys` | `-1.0 <= score <= 1.0` | fixture bars; the bound is the score's **definitional** range |
| `test_brain_layer_scores_include_confidence` | `-1.0 <= score <= 1.0` | same |
| `test_run_backtest_simulates_trades_on_signaling_data` | `-1.0 <= win_rate <= 1.0` | injected `_bars_loader`; see the separate finding below |

**So why do they touch the network at all?** `build_prediction` fetches
live *side-inputs* (intermarket, funding, sentiment) that feed layers
whose outputs **none of these tests assert on**. The fetch is incidental
to every verdict — which is precisely the STRUCTURE definition.

### A separate finding, not network debt

`test_run_backtest_simulates_trades_on_signaling_data` asserts
`-1.0 <= result.win_rate <= 1.0`. A win rate is a rate in **[0, 1] by
definition**, so the lower bound is unreachable slack: **that half of the
assertion cannot fail.** Another instance of the cannot-fail family,
found incidentally. Not fixed here — it is unrelated to the egress work
and belongs with whoever next touches that file.

## Item 2(b) — CI flake history, the independent cross-check

The cross-check the order asked for: a test classified STRUCTURE that has
flaked was misclassified.

**Last 80 `ci.yml` runs: 4 failures total.**

| run | date | cause |
|---|---|---|
| `40459365051` | 2026-10-27 | **mine** — the egress guard revealed the 51 |
| `40381616297` | 2026-10-26 | **mine** — the deleted `BinanceClient` patch target |
| `40292394277` | 2026-10-26 | TimescaleDB background-worker startup, service container |
| `39621119587` | 2026-10-21 | same |

**Zero of the 51 appear.** No red-then-green-without-a-code-change among
them, so nothing contradicts the STRUCTURE classification.

**One refinement on the cross-check's logic, stated so it is not
over-read:** a STRUCTURE test *can* still flake, from a **fetch
failure** (timeout, 429) rather than from value sensitivity. Those are
different diagnoses and the error distinguishes them — a transport
exception is a fetch flake and consistent with STRUCTURE; an *assertion*
failure on a number would be the misclassification signal. Neither
occurred here.

And the window is a bound, not a proof: 0 flakes in 76 non-mine runs
bounds the observed rate, it does not establish that none can flake.

## The decision

**Backlog.** Per the order: *"If (a) returns mostly STRUCTURE, this is
untidiness and goes on the backlog."* It returned **entirely**
STRUCTURE, so there is no VALUE subset to escalate and no specific test
that needs its own review.

The debt is nonetheless real, and the reason is not value sensitivity:

- a fetch failure fails the test, so **the suite cannot run offline or
  sandboxed**;
- **CI runners reach Binance from shared GitHub IP ranges**, so the suite
  depends on an external service's tolerance of *other tenants'* traffic.
  The failure mode is not "our calls are too frequent" — it is a 418/429
  earned by somebody else.

Paying it down is low-risk precisely because the partition came back
clean: swap each fetch for a fixture and **no assertion changes**. That
is a mechanical change, reviewable file by file, and it can wait.

## A correction to my own earlier write-up

The debt block I first put in those ten files said their *"assertions are
partly about live market data rather than fixed inputs."* **That is
false**, and the partition is what showed it. I wrote it before measuring
and it has been replaced in all ten files, not softened.
