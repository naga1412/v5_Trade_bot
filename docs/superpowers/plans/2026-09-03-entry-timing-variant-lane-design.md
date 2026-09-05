# Entry-timing shadow variant lane — design (build after the promotion completes)

**Status: design only, not built.** Answers the operator's brief: what the
faster trigger is, how pairing is enforced, what n is needed for a 2-sigma
read given sigma=2.75%, and how long that takes at current rates. Reuses
the breakeven-variant lane's machinery and discipline; does not touch it.

## 0. Why this and not a quality ranking

`entry_score` correlates with realized P&L at ~0.33σ — not separable
enough to rank trades by expected profit. No signal cap; volume stays.
The 37-63 bar entry lag is the largest measured defect that (a) is
attackable independent of a ranking model, and (b) is PAIRED, which is
what makes it measurable on a useful timescale — the same reason
breakeven-stop was buildable and gating-by-quality currently isn't.

## 1. What the faster trigger is

**Verified against the real code, not assumed.** `shadow_worker.py`'s
per-bar tick already runs the FULL scoring pipeline
(`build_prediction` → `pred.final.score`/`.confidence`) unconditionally,
on every closed candle, for every symbol shadow watches — regardless of
whether a position ends up opening. The current entry check
(`self.evaluator.evaluate(...)`) then asks: does this score cross
`MIN_ENTRY_SCORE_LONG` (0.36) with confidence ≥ 0.50? If not, the bar is
logged at INFO (`shadow_worker: %s no-signal score=...`) and discarded —
the already-computed score is never persisted anywhere.

**The faster trigger is the same already-computed score, checked against
a second, lower bar** — no new expensive computation, since
`build_prediction` already ran for the real check. Candidate thresholds
to test (pick empirically, not by fiat):

- **Score-only**: same direction, `|score| ≥ 0.20-0.25` (vs. 0.36),
  confidence gate unchanged or slightly relaxed.
- **MTF-relaxed**: if the real gate additionally requires multi-timeframe
  agreement before firing, a faster variant could fire on 1h-only
  agreement without waiting for the slower timeframe's confirming
  candle — this needs a direct read of the MTF gate's exact wait
  condition before committing to it as a second candidate; flagged as a
  verify-before-build item, not assumed here.

Start with score-only — it's the simplest, cheapest to instrument, and
isolates one variable. Add the MTF-relaxed variant later, as its own
named variant, once score-only's read is in.

## 2. Architecture — reusing breakeven's pattern, not its code

Breakeven-variant works because it replays a REAL position's OWN
already-buffered bar history under an alternate EXIT rule — pure
function, no DB, no I/O (`simulate_variant_exit`). Entry-timing needs the
mirror image: replay under an alternate ENTRY rule. Two real
differences from breakeven that shape the design:

1. **`predictions` does not have historical per-bar scores for
   shadow-only symbols to mine retroactively** — confirmed empirically
   tonight: a symbol (LTC/USDT) that fired 14 real shadow signals today
   has ZERO rows in `predictions` for the same period. `predictions` and
   shadow's own scoring are separate lanes (previously known; reconfirmed
   here for this specific design). So the faster-trigger bar cannot be
   found by querying history after the fact — it has to be **recorded
   live, going forward**, the same way breakeven's own bar buffer is
   built live and only used at close, not reconstructed after the fact.

2. **Entry-timing needs a per-(symbol, timeframe) piece of state that
   breakeven doesn't**: a small in-memory record of "the faster
   threshold has been active since bar N" — armed the first bar the
   faster condition goes true, cleared when either the real trigger
   fires (consumed into a paired row) or a bound is reached without the
   real trigger firing (recorded as a false start, see §4). This piggy-
   backs on the SAME per-bar loop that already computes the score for
   the real check — it's a few lines of bookkeeping next to code that's
   already running, not a new pipeline.

Both the earlier entry price and the forward price path needed to
simulate its outcome come from `self.bars[(symbol, tf)]` — the same
rolling OHLCV buffer `shadow_worker` already maintains for its own
scoring (confirmed in the code: `HISTORY_BARS`/`HISTORY_SEED_BARS_1H`
worth of bars, kept warm continuously). No new data source.

**Pairing enforcement**: when the real (slow) trigger fires and a
position opens, check whether a faster-trigger candidate is currently
armed for that (symbol, tf). If yes: the candidate's arm-bar is the
variant's synthetic entry. Walk the REAL bars from that arm-bar forward
— same SL/TP framework as the base signal (needs one explicit decision:
recompute SL/TP from ATR-at-the-earlier-bar, or reuse the base's SL/TP
distance scaled from the earlier entry price; recommend the latter for
the first cut, since it isolates timing alone and doesn't also vary risk
sizing) — using the same TIMEOUT_BARS_PER_TF and SL/TP-hit logic
`simulate_variant_exit` already implements, just started earlier. Both
entries reference the literal same underlying move (same symbol, same
bars, same eventual resolution) — that identity is what cancels the
~2.75%/trade cross-symbol variance, exactly as it does for breakeven.

## 3. What must be measured — both sides of the tradeoff

Per the instruction: not just the upside. Two paired metrics per base
trade with a valid faster-trigger pairing:

- **Captured-move fraction**: variant's realized R (or %) vs. the base's
  realized R, from their respective entries to their respective exits
  (which may differ — the variant's stop/target geometry can produce a
  different exit point and reason than the base). Positive delta means
  the faster entry captured more of the move.
- **False-start rate**: tracked as its OWN, unpaired count — every time
  the faster condition arms and is later CLEARED WITHOUT the real
  trigger ever firing (bounded by, say, `TIMEOUT_BARS_PER_TF` for that
  symbol's timeframe, matching how long the base strategy itself would
  wait before giving up on a setup). Reported as false-starts /
  (false-starts + paired-fires) — the fraction of "early" signals that
  never became real ones. This is the cost side; a faster trigger that
  wins on captured-move but false-starts constantly is not a free
  improvement.

Both numbers ship together in every report on this lane — never
captured-move alone.

## 4. Sample size for a 2-sigma read

Using σ = 2.75%/trade (the same paired-delta variance breakeven already
established at this scale — reused here, not re-derived, since it's the
same class of paired-delta measurement). For a true mean delta μ to
clear a 2-sigma bar (2·SE < μ, i.e. the confidence interval excludes
zero at ~2 standard errors): SE = σ/√n, so n ≥ (2σ/μ)².

| Detectable mean delta μ | Required n |
|---|---|
| 2.0% | 8 |
| 1.5% | 14 |
| 1.0% | 31 |
| 0.69% | 64 |
| 0.55% | 100 |
| 0.30% | 336 |

The project's own existing convention (breakeven-variant's DECISION
rule) uses n≥30 for an "early directional read" and n≥100 for a real
PASS/FAIL — reused here rather than inventing a new bar. At n=30 this
lane can reliably see deltas ≥ ~1.0%/trade; at n=100, deltas ≥ ~0.55%.

## 5. Timeline at current rates

Shadow's closed-trade cadence over the last 7 stable days: 50-99/day,
averaging ~65-70/day (2026-08-25 through 2026-09-03, excluding partial
first/last days). **This is NOT directly the pairing rate** — only base
trades where a faster-trigger candidate was actually armed before the
real entry are eligible. That eligibility fraction is currently
UNMEASURED — it depends on how often the lower threshold precedes the
real one by a meaningful bar count vs. how often both cross essentially
together (in which case there's no real "faster" entry available to
pair against for that trade).

**Recommended first step, before building the full lane**: a short
read-only reconnaissance pass — instrument the per-bar loop to LOG (not
persist to a new table yet) how often the faster threshold arms ahead
of the real trigger, and by how many bars, over a few days of live
traffic. This answers the eligibility rate empirically instead of
guessing, and costs almost nothing (the score is already computed every
bar; this only adds a comparison and a log line).

Illustrative timeline once eligibility is known, at ~65-70 base
closes/day:

| Eligibility rate | Paired closes/day | Days to n=30 | Days to n=100 |
|---|---|---|---|
| 100% | ~68 | <1 | ~1.5 |
| 50% | ~34 | ~1 | ~3 |
| 25% | ~17 | ~2 | ~6 |
| 10% | ~7 | ~4-5 | ~14 |

Even at a pessimistic 10% eligibility, an early directional read (n=30,
detects deltas ≥~1.0%) is available inside a week; a full n=100 read
(detects deltas ≥~0.55%) inside two.

## 6. Non-interference with the breakeven lanes

New table (`shadow_trade_entry_timing_variants` or similar — separate
from `shadow_trade_variants`, not a shared schema), new in-memory state
keyed independently, no shared code path with `breakeven_variant.py`
beyond the general "replay real bars under an alternate rule" pattern.
Does not read, write, or touch `shadow_open_positions`, the breakeven
lane's own population, or any cooldown/dispatch logic. The breakeven
measurement's 3-4 weeks to a conclusive read continues completely
undisturbed.

## 7. Open questions to resolve before implementation (not before this design)

1. Does the MTF gate's wait condition make a second, MTF-relaxed faster
   trigger candidate meaningful, or does 1h-only agreement already
   dominate the real gate's timing in practice? Needs a direct read of
   the MTF confluence gate's exact wait logic.
2. SL/TP for the earlier synthetic entry: recompute from ATR-at-the-
   earlier-bar, or scale the base's SL/TP distance from the new entry
   price? Recommend the latter (isolates timing, not sizing) for the
   first cut; revisit if results are ambiguous.
3. False-start bound: use the symbol's own `TIMEOUT_BARS_PER_TF`, or a
   separate, shorter bound tuned for "how long is a signal still
   plausibly the same setup"? Recommend reusing `TIMEOUT_BARS_PER_TF`
   for consistency with the base strategy's own patience, revisit if the
   false-start rate looks structurally different at that horizon.
4. Run the read-only reconnaissance pass from §5 first — it's cheap and
   converts the whole timeline table from "illustrative" to "real."
