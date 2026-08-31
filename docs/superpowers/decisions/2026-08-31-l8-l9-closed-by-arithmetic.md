# L9 (news) and L8 (ConvLSTM ghost-scoring) — both CLOSED by arithmetic, no model run, no measurement build

**Class:** backlog closure record. **Do not reopen either item as "unbuilt backlog" or "never measured" — read this record first.**

## Why this exists

Both L9 and L8 were carried on the work backlog as "wire it up and measure the lift." Both closed instead on economic/statistical bounds computed from data and code already in hand, per the operator's 2026-08-30 ruling: *"apply flip-count × max-per-trade-swing against the measurement floor AND against fees... do NOT measure the 1.4% population, do NOT ship #512, record the reasoning so nobody reopens it,"* extended to L8: *"do NOT run the ConvLSTM checkpoint yet. First get the cheap upper bound; if coverage is in L9's territory, L8 closes without ever running the model."*

Governing structural fact used by both closures: per-trade `pnl_pct` has σ≈2.7496% (established and reverified this session). Two-sample 2σ detection power is `n_per_group≈8×(σ/Δ)²`; round-trip trading fees are ~0.10%.

## L9 (news sentiment layer) — CLOSED 2026-08-30

- **Real formula** (`layer9_news.py`): 60-min lookback on `news_items` filtered by base asset, `weighted_sum=Σ(sentiment×impact)`, `avg=weighted_sum/weight_total`, `strength=|tanh(avg×1.5)|`, `confidence=min(1.0, n_items/5.0)`, deadband=0.1.
- **Real coverage**: true abstain rate on BTC/USDT is 86.8% (not the misleading aggregate 99.2% figure) — `CRYPTOPANIC_API_KEY` is unset, so the layer fires only on the minority of windows where a news item happens to already be cached.
- **Real flip-screen** (re-run against #512's actual scorer output, not a synthetic ceiling, per the operator's explicit correction — the first pass used the aggregator's internal ±0.05 `NEUTRAL_BAND` instead of the real 0.36 entry gate and gave a falsely reassuring 0%): theoretical max-shift ceiling flips entry decisions for 1.4% of the qualifying population.
- **Closing arithmetic**: flip-rate(1.4%) × max-per-trade-swing(5.1pp) ≈ 0.0141 × 5.1pp ≈ **0.07pp expected effect per trade**. Two things this fails against simultaneously:
  - **Measurement floor**: detecting a 0.07pp effect at 2σ needs n≈8×(2.7496/0.07)²≈**n≈6175**, ≈4 years at current shadow cadence.
  - **Fees**: 0.07pp is smaller than the ~0.10% round-trip fee — even a perfectly-measured effect at this size doesn't clear the cost of taking the trade.
- **Verdict**: economically irrelevant against the fee floor, and unmeasurable within any useful timescale even if it weren't. **Do not measure the 1.4% population. Do not ship #512** (the built-but-unshipped L9 scorer). Config-only fix (`CRYPTOPANIC_API_KEY`) remains theoretically available but pointless to apply while the layer's maximum-possible effect is sub-fee.

## L8 (ConvLSTM ghost-scoring) — CLOSED 2026-08-31, cheaper than L9

- **Code-structural proof, no query, no model load** (`layer8_convlstm.py:41-42` + `worker.py`): `score_l8(bars, ghost)` returns `None` whenever `ghost is None`. `ghost_input` stays structurally `None` unless `L8_GHOST_SCORING_ENABLED` is `True` AND `len(buf)>=256`. The flag is globally `False` in prod (confirmed at PR #406 ship time — real checkpoint has been active since 05-09, but shipped flag-off).
- **Coverage: 0% by construction.** Not measured-and-found-small like L9's 1.4% — provably zero for every live prediction while the flag is off, by the shape of the code, before any data is touched.
- **Closing logic**: 0% coverage is strictly inside L9's already-closed territory (1.4% → economically irrelevant). A population that never fires at all cannot clear a bar that a 1.4%-firing population already failed. L8 closes on the same reasoning as L9, a fortiori, without running the checkpoint or building any measurement harness.

## Reversal criteria (either item)

1. `L8_GHOST_SCORING_ENABLED` is flipped `True` in prod — that changes L8's coverage away from the 0% this closure rests on; re-open and re-measure from scratch.
2. `CRYPTOPANIC_API_KEY` is set and L9's real coverage rate moves materially off 86.8% abstain — re-run the flip-screen against the new coverage before concluding anything.
3. Round-trip fees drop materially (e.g. a lower-fee venue/tier) — re-check L9's 0.07pp-vs-fee comparison, since the fee floor is one of the two independent reasons it's closed.
4. A future scoring change alters the real 0.36 entry gate itself — re-run both flip-screens against the new gate, not the one this closure used.

Absent one of the above, both layers stay closed. Neither is "unbuilt backlog" — both were evaluated and rejected on the numbers.
