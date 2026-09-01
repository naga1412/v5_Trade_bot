# Staged promotion creates temporary file states — audit of Stage 1's shipped files against later stages

**Class:** process lesson + concrete audit. **Do not amend by squash.** Any future change to this decision requires a new decision record replayed in order.

## The lesson

Stage 1 (PR #536) shipped `backend/app/shadow/live_fleet_universe.py` at its pre-#527 state — the old `legacy_top20`-cold-start-seed + sticky-prior-cohort-inheritance classifier, not the pure `_classify_cohort` function #527 replaced it with on dev (merged there 2026-08-30, one day before Stage 1's promotion). This was correctly staged: the operator's plan deliberately placed the cohort-fix chain later, and nothing was *broken* — main briefly ran a combination (Stage 1's universe expansion + the pre-#527 classifier) dev hadn't run since 2026-08-30 either, but it's a combination that was tested and known-good before that date.

**The general lesson**: staged promotion of a file whose *later* stage also modifies it creates a temporary state that exists nowhere else — not on dev (which has moved past it), not on the final post-all-stages main (which will have every stage applied). That window is real even when nothing in it is actually broken, and it's worth knowing about deliberately rather than discovering by accident mid-window, the way this one surfaced via the T+6h `symbol_source` investigation.

## Audit: which of Stage 1's 18 shipped files does a still-pending PR also modify

Method: cross-referenced Stage 1's exact file list (from the merged commit, `git show --stat ed28427`) against the promotion manifest's 133 genuinely-missing dev PRs (file-overlap check, not stage-number lookup — the manifest's stage groupings were only ever presented in chat, never committed, so this checks the concrete, verifiable thing: does a still-pending commit touch a Stage-1-shipped path).

### Confirmed: same cohort-fix-chain family as #525/#527, still pending

| PR | Touches (of Stage 1's files) | Note |
|---|---|---|
| **#474** (Epic B, cohort-threading, Task 9 redraft) | `keepalive.py`, `live_prediction.py`, `test_ws_keepalive.py` | The dependency this document's own Stage 1 fix (#536) and #527's promotion both had to work around via forward-reference test fixes — see [[2026-08-19-live-fleet-universe-never-scheduled-incident]] and this repo's own commit history for the pattern. |
| **#476** (Task 10, dispatch-time liquidity re-check) | `live_prediction.py` | **Touches the exact function (`run_live_prediction`) that #539's futures-poll-seed fix also modifies.** Expect a real reconciliation point, not just a mechanical conflict, when this promotes — #476's liquidity-recheck logic and #539's seed-source branch both live in the same function body. |
| **#528** ("4a-i" of the cohort-tag-defect ruling — loud truncation + baseline-priority WS ordering) | `worker.py` | Same ruling family as #525/#527/#532, explicitly labeled as such in its own commit message. |
| **#532** (item 0 — synchronous cohort classification at position-open) | `main.py`, `worker_registry.py`, `live_fleet_universe.py`, `worker.py` | Four of Stage 1's files. Completed on dev per the overnight work order's PART A; not yet promoted. |
| #525 + #527 (baseline migration + pure classifier) | `live_fleet_universe.py`, its test file | **Being promoted now**, alongside #539, per this session's ruling — see the commit this file ships with. |

### Confirmed: unrelated, incidental sharing (not this pattern — different feature area, same infra file)

- **#507** (`pattern_stats` never wrote a row) — touches `main.py`, `worker_registry.py` purely because it wires a new scheduled worker. Pre-Phase-4-audit scope (explicitly deferred by the operator), not part of the cohort/universe-expansion chain.
- A long tail of pre-#465 legacy PRs (#199, #200, #266, #299, #303, #319, #352, #353, #356, #399, #400 — already resolved, see #400's own correction record, #406/#407-related work, W1/W3/W4 record-only feature computers) touch `worker.py`/`main.py`/`live_prediction.py`/`tab1.py` for entirely unrelated reasons. All fall inside the explicitly-deferred pre-Phase-4 audit scope (~63 PRs, #171-464) — not re-litigated here, and per [[2026-08-31-pr-number-crossref-false-negative]], that audit still needs to diff content, not PR numbers, when it eventually runs.
- `.github/workflows/ops-debug.yml` — 32 of the 133 missing PRs touch it. This is the known, structural "every diagnostic-probe PR appends to this file" pattern, already handled twice this session via additive conflict resolution (Stage 1's own cherry-pick, and this file's own earlier incident — see [[2026-09-01-futures-poll-seed-was-spot-only]]'s referenced fix, PR #538). Expect more of the same shape, not a new risk.

## What this means going forward

Not a call to re-sequence the approved staged plan — the plan already correctly ordered the cohort-fix chain after the universe expansion, and nothing in the audited window was ever actually broken. It's a call to expect, by name, which future stage-promotion PRs will need conflict resolution against Stage-1-and-later's already-shipped state, so it's planned work rather than a surprise each time:

- **#474, #476, #528, #532** will each need the same "forward-reference test fix" treatment this session already applied twice (#536's own cherry-pick, #527's promotion) when they promote.
- **#476 specifically** shares a function body with #539 — worth a direct look at #476's diff before it promotes, not just a generic conflict-resolution pass.

## Reversal criteria

Re-examined if a future staged promotion's file-overlap audit (repeat this method for Stages 2+ once their own contents are decided) finds a case where the temporary combination *was* actually broken, not merely untested-in-that-exact-combination — that would upgrade this from a process lesson to a gate requirement (e.g., a mandatory compatibility check before allowing a later stage to be staged independently of an earlier one that touches the same file).
