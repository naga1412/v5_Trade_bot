# Fleet selector inverted: liquidity-floor pass/fail supersedes top-20-by-volume (2026-08-15)

**Class:** governance / ratified decision record.
**Supersedes:** `docs/superpowers/decisions/2026-07-28-fleet-cap-top-20-ratified.md` — that decision is **not amended**, it stands as the historical record for the volume-rank regime it governed. This is a new decision record per that doc's own rule ("Do not amend by squash. Any change to this decision requires a new decision record replayed in order.").
**Do not amend by squash.** Any future change to this decision requires a new decision record replayed in order.

## Ruling

The live-prediction universe selector changes from **top-N by SPOT volume rank** (`KEEPALIVE_TOP_N=20` at `backend/app/ws/keepalive.py`) to **every symbol that passes the three-metric liquidity floor** (`check_liquidity` — 24h qvol ≥ $20M, spread ≤ 5bps, resting depth within 0.5% of mid ≥ $50k; spec'd in `docs/superpowers/plans/2026-08-14-phase4-futures-signal-coverage.md` Task 4), evaluated across **the full USDT-M perpetual market** (spot-backed and futures-only together), not the spot-only top-30 `asset_universe` ranking.

Reversal criterion invoked from the 07-28 decision:

> **4. Live-trading migrates off the top-20 fleet altogether** — if a future architecture retires `ws_keepalive_task` in favor of a different subscription model (e.g. per-user universes, full-30 with resource optimization), this decision sunsets automatically because its scope premise no longer applies.

This is that migration. The 07-28 doc's scope premise — "there is a rank-N cutoff over a volume-ordered list" — no longer applies once the selector is pass/fail against an absolute liquidity floor rather than a relative rank.

**What is explicitly NOT being claimed:** the 07-28 doc's ratification data (top-20-by-volume outperforms ranks-21-30-by-volume, 42.9% WR vs 28.9% WR at the live gate) does not transfer to this decision. That data measured a **volume-ranked** population split. The new cohort this decision adds — liquidity-floor-qualified symbols that sit outside the old top-20-by-volume — is a **liquidity-ranked** population, structurally different from "ranks 21-30 by volume." Applying the 07-28 finding to justify (or condemn) the new cohort would be misapplying a measurement to a population it wasn't measured on. See the Cohort tagging section below — the new cohort must be measured on its own terms from day one, independent of and not assumed-equivalent to the 07-28 finding.

## Measurement backing this decision (2026-08-15, live Binance data, `check_liquidity` spec replicated standalone — not yet in-repo, see Task 4)

Full USDT-M perpetual market: **527** symbols → **358 spot-backed**, **169 futures-only** (matches the 2026-08-14 design doc's own count exactly).

Liquidity floor pass rate, sampled 3× per candidate (~4s apart) to separate genuine passes from live order-book flicker on thin-tick symbols:

| | total | spot-backed | futures-only |
|---|---|---|---|
| Stable pass (passes all 3 samples) | 42 | 34 | 8 |
| Borderline (flips within seconds) | 7 | 4 | 3 |
| Stable fail (of the 73 that clear the $20M volume pre-filter) | 24 | — | — |
| Fail on volume alone (never sampled further) | 454 | — | — |

**Known immediate impact, not a hypothetical:** cross-referencing against the 07-28 doc's sample top-20-by-volume snapshot, at least two currently-live-covered, established assets — **ADA** (spread ≈5.59bps, stable across all 3 samples, not flicker) and **NEAR** (spread ≈6.1bps, stable) — fail the new floor on spread alone and would drop out of live coverage under a pure liquidity-floor selector. This is not a corner case on an obscure coin; it is a direct, named consequence of inverting the selector, and the hysteresis exit rule below (not a special-case exemption) is what governs whether/when they actually leave.

**Open question, not yet resolved — flagging rather than assuming:** the liquidity floor is evaluated against **futures** market data (`fapi/v1/ticker/24hr`, `fapi/v1/depth`), while `asset_universe`/`shadow_worker`'s existing 30-symbol universe is ranked by **spot** volume (`fetch_top_n_usdt_spot`). It is not yet confirmed that all 34 liquidity-qualified spot-backed symbols are members of today's spot-volume-ranked 30-symbol universe — a symbol can plausibly have deep futures liquidity while ranking outside the spot top-30 (thinner spot pair, more active derivatives market — the same volume-does-not-imply-depth asymmetry this whole floor exists to catch, just across markets instead of within one). If any of the 34 fall outside today's `asset_universe`, they need new universe-membership + shadow-tracking work, not just a live-WS-subscription change. **Verify this against prod (`asset_universe` current rows) before Task 5 is re-implemented** — not verified in this decision record.

## Reversal criteria for THIS decision (required, per the same governance pattern)

Re-examined if:

1. **The liquidity floor's own thresholds change** (qvol/spread/depth constants) — any change requires re-running this decision's measurement and updating the counts above.
2. **The three-way cohort split (see design doc addendum) shows the newly-added liquidity-ranked cohort is net -EV** at the live gate, on a clean re-run with adequate sample size — mirroring the rigor of the 07-28 finding, but run on the correct (liquidity-ranked) population, not reusing the volume-ranked data.
3. **The cost check (see design doc addendum) shows N≈42 does not fit** the host's compute/memory/rate-limit budget in a staging soak — in that case this decision's *selection criterion* stands but the *implementation* falls back to the largest workable N ranked by liquidity (not volume), per the operator's explicit instruction.
4. **A future architecture change** retires the per-symbol WS/poll fleet model entirely — same sunset logic as criterion 4 of the 07-28 doc.

## Related decisions

- `docs/superpowers/decisions/2026-07-28-fleet-cap-top-20-ratified.md` — superseded by this record for the selection-criterion question; its ratification DATA remains a valid historical measurement of the volume-ranked population, just no longer the active selector logic.
- `docs/superpowers/specs/2026-08-14-phase4-futures-signal-coverage-design.md` — see the 2026-08-15 addendum for the hysteresis rule, cost-check findings, and cohort-tagging schema that implement this decision.
- `docs/superpowers/plans/2026-08-14-phase4-futures-signal-coverage.md` — Stage A/B sizing (N=8 widen-to-20-25) and Tasks 5/8 (top-N-by-volume selection, supervisor sizing) are superseded by this decision; not yet re-drafted task-by-task (flagged in-doc, follow-up work).

## Implementation trace (to be filled in as the superseding tasks land)

Not yet implemented — this decision record precedes the code. Files that will encode it once Task 4/5/8 (per the plan doc) are re-drafted and shipped:
- `backend/app/data/futures_liquidity.py` (Task 4 — not yet created)
- `backend/app/shadow/universe.py` or a new liquidity-floor universe selector (Task 5 — needs re-drafting per this decision)
- `backend/app/ws/keepalive.py` — `KEEPALIVE_TOP_N` constant retired in favor of a liquidity-floor-derived N (needs re-drafting)
