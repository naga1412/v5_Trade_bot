# Fleet cap ratified at top-20 by rank (2026-07-28)

> **SUPERSEDED (2026-08-15).** See `docs/superpowers/decisions/2026-08-15-liquidity-floor-selector-supersedes-top20.md`. The selector this record ratified (top-N by SPOT VOLUME rank) is no longer the active live-prediction universe selector — it was replaced by a liquidity-floor pass/fail criterion evaluated across the full market. This record stands as the historical measurement for the volume-rank regime it governed (its ratification data is not invalidated, just no longer the active logic) — added as a pointer, not amended, per this doc's own rule below.

**Class:** governance / ratified decision record.
**Do not amend by squash.** Any change to this decision requires a new decision record replayed in order.

## Ruling

The `ws_keepalive_task` fleet's top-20 universe cap (`KEEPALIVE_TOP_N=20` at `backend/app/ws/keepalive.py`) is **ratified as the permanent live-prediction coverage boundary** on 2026-07-28.

Consequences:
- Ranks 21-30 of `asset_universe` are **structurally outside live coverage BY DESIGN, not by bug.** They do not receive per-symbol live prediction writes from either the fleet or the `live_worker` singleton. They still receive shadow-only trades from `shadow_worker` (which subscribes to all 30), but those shadow trades cannot produce live signals because no `predictions` row is written for them.
- The healer C3 detector (`detect_per_symbol_prediction_freshness`) is narrowed to the fleet's expected subscription set = top-20 by rank ∪ `DEFAULT_EXCLUDE` (i.e. the singleton's coverage). See `backend/app/healer/detectors.py:_load_expected_prediction_pairs`.
- Aug 3-10 dual-axis validation and any future strategy-evaluation query MUST filter to top-20 by rank at time-of-trade. Reporting on all 30 dilutes the numbers with untradeable signals. If aggregate all-30 numbers are shown for comparison to prior analyses, they must be labeled as such and top-20 numbers presented as primary.

## Data (30-day LONG shadow_trades split as of 2026-07-28)

Split by whether each symbol was in today's top-20 vs ranks 21-30 of `asset_universe`:

| bucket | n | WR% | avg pnl% | sum pnl% | n @ ≥0.36 | WR @ gate | avg pnl @ gate | sum pnl @ gate |
|---|---|---|---|---|---|---|---|---|
| **top-20 (in fleet)** | 443 | **32.3%** | **+0.326** | **+144.45** | 133 | **42.9%** | **+0.955** | **+127.08** |
| **ranks_21_30 (out)** | 121 | 24.8% | -0.374 | **-45.28** | 38 | 28.9% | -0.308 | **-11.70** |

At the live entry gate (`MIN_ENTRY_SCORE_LONG=0.36`):
- **Top-20 concentrates the alpha**: 42.9% WR, +0.96% avg PnL, +127 sum PnL from 133 trades — a strong performer.
- **Ranks 21-30 are net losers**: 28.9% WR, -0.31% avg PnL, -11.70 sum PnL from 38 trades — a 14-percentage-point WR gap AND net-negative expectation.
- Sample size for ranks 21-30 is not thin (121 total / 38 at gate) — this is a decision-adequate difference, not noise.

Today's rank list (2026-07-28 00:00 UTC snapshot):
```
Top-20 (IN):  BTC(1) ETH(2) BANK(3) SOL(4) AERO(5) XRP(6) DEXE(7) ZEC(8) BNB(9)
              DOGE(10) TRX(11) PEPE(12) PUMP(13) ZAMA(14) LA(15) NEAR(16) ESP(17)
              U(18) SUI(19) ADA(20)
Ranks 21-30 (OUT): AAVE(21) ENA(22) LINK(23) SHIB(24) ONDO(25) SKHYB(26)
                   VANA(27) XLM(28) WLD(29) NIL(30)
```

## Historical context

- **Cap set on 2026-05-15** by PR #131 (`feat(workers): server-side WS keepalive fan-out for top-N universe`), commit `9f35ce1`, author naga1412. Set as `KEEPALIVE_TOP_N: int = 20` at what is now line 54 of `backend/app/ws/keepalive.py`. Never changed since.
- **Universe was already 30** at PR #131 landing time (`UNIVERSE_TOP_N: int = 30` at `backend/app/shadow/universe_refresh.py:36`, pre-existing). The cap was intentionally set BELOW universe size on day one — **not stale config drift.**
- PR #131 body describes "default top-20" without citing a specific resource constraint or benchmark. Best interpretation: an intuitive conservative choice made when the universe was newly-expanded to 30 and the fleet architecture was new. Today's empirical data validates that intuition — the ranks 21-30 subset is materially -EV at the live gate.

## Reversal criteria (REQUIRED — this decision is not dogma)

This decision is re-examined and potentially reversed if ANY of the following becomes true on a documented re-analysis:

1. **Universe composition change** — the SPOT top-30 population shifts such that ranks 21-30 stop being small-cap / low-liquidity residuals (e.g. a market regime where mid-caps outperform majors). Trigger: monthly recheck by operator, or ad-hoc after any major universe-membership change.

2. **Ranks 21-30 turn +EV at the gate on a clean re-run** — same query as the ratification data above (30-day LONG at ≥0.36), showing WR ≥ 40% AND positive avg PnL AND positive sum PnL for the ranks_21_30 bucket. All three must hold; win rate alone or positive-sum alone is not sufficient.

3. **Strategy-parameter change makes ranks 21-30 competitive** — e.g. a lower `MIN_ENTRY_SCORE_LONG` threshold, a new gate that filters differently by market cap, or a signal-rotation regime where the top-20's inherent bias is a liability. Trigger: any operator-authorized change to entry gates or signal thresholds.

4. **Live-trading migrates off the top-20 fleet altogether** — if a future architecture retires `ws_keepalive_task` in favor of a different subscription model (e.g. per-user universes, full-30 with resource optimization), this decision sunsets automatically because its scope premise no longer applies.

**Any reversal MUST re-run the ratification data query (using the same predicate as above) and include the results in the new decision record.** Do not reverse based on qualitative reasoning alone.

## Related decisions

- Row 7 of `docs/healer-phase-1-action-matrix.md` — **BLOCKED PENDING ROOT CAUSE** downgrade on 2026-07-28 is a consequence of this ruling. Row 7 proposed auto-restarting the WS keepalive child for stale (symbol, timeframe); with the ratified top-20 cap, ranks 21-30 have no WS child to restart — the auto-action would be a no-op. Row 7 stays BLOCKED as long as this decision stands.

- `MIN_ENTRY_SCORE_LONG=0.36` — the live gate used in the ratification data. If the gate value changes materially, re-run the split and re-ratify.

## Implementation trace

Files that encode this decision:
- `backend/app/ws/keepalive.py:54` — `KEEPALIVE_TOP_N: int = 20` (source of truth for the cap).
- `backend/app/ws/keepalive.py:74` — `to_pair()` (public helper for symbol normalization, shared with the C3 detector).
- `backend/app/healer/detectors.py:_load_expected_prediction_pairs` — C3's scope, imports the same constants; single source of truth.
- `backend/tests/healer/test_detectors.py:test_c3_ranks_21_plus_are_out_of_scope` — regression against the "9/27 permanent WARNING" state that fired every 5-min tick pre-fix.
- `backend/tests/healer/test_detectors.py:test_c3_btc_singleton_stays_expected_when_dropped_from_top_20` — regression against the mis-implementation where BTC could go dark for C3 on rare days it drops out of top-20 by rank.
