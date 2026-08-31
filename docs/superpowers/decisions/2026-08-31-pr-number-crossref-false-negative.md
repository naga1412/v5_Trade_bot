# PR-number cross-reference has a false-negative mode — diff content, not numbers, for the pre-Phase-4 audit

**Class:** methodology correction, binding on the deferred ~63-PR pre-Phase-4 audit (#171-464). **Read before starting that audit.**

## What happened

The promotion manifest (2026-08-30) claimed PR #400 (`HISTORY_BARS`/`HISTORY_SEED_BARS` 300→504 fix) was "genuinely missing from main," built on a cross-reference of every `(#NNN)` reference in main's full commit-title history against each dev-only commit's own trailing PR number. On that basis I also claimed #400's absence "likely explains the persistent `realized_vol_20d` coverage gap" (5-25% on recent prod trades).

Both claims were wrong. Direct code read of main's actual `worker.py::HISTORY_BARS`, `live_prediction.py::HISTORY_SEED_BARS`, and `tab1.py`'s candle-fetch limit found **all three already at 504 on main** before PR #535 touched them. The content had already reached main — via a cherry-pick or bundled promotion whose commit title never cited `"(#400)"`, so the number-matching cross-reference produced a false negative.

**Consequence for the causal claim**: `realized_vol_20d`'s coverage gap is NOT explained by this. Its real cause is genuinely unknown and stays open, uninvestigated. Do not carry the #400 explanation forward as if it were diagnosed.

## The actual defect in the audit method

Cherry-picks preserve *content*, not commit identity. A dev commit titled `"...(#400)"` can land on main inside a differently-titled bundled promotion commit that cites a different PR number (or none). Scanning main's commit-title history for the literal string `"(#400)"` therefore proves nothing about whether #400's *content* is on main — it only proves whether that specific string appears in a title. Absence of the string is consistent with either "never promoted" or "promoted under a different title," and the manifest treated both as the former without checking.

## Binding rule for the deferred pre-Phase-4 audit (#171-464, ~63 PRs)

**Diff content, not PR numbers.** For each candidate PR, the only valid check is: does the file/function this PR touches, on `origin/main`, already contain this PR's actual change? (`git show <dev_sha>:<path>` vs `git show origin/main:<path>`, or a direct read of the current file, per [[audit_branch_discipline]].) A PR-number string match — present or absent — is a hint to investigate, never a conclusion.

**Consequence for scope**: the manifest's headline "~63 genuinely missing pre-Phase-4 PRs" figure is an **upper bound**, built the same title-matching way #400 was, and may be substantially overstated for the same reason. Do not treat 63 as a confirmed count until it's been re-derived by content diff.

**Status**: audit explicitly NOT started yet, per the operator's 2026-08-30 ordering ("WHEN YOU RUN THE PRE-PHASE-4 AUDIT... DIFF CONTENT, NOT PR NUMBERS... Do not start that audit yet"). This record exists so the audit starts with the corrected method rather than repeating #400's mistake at 63x scale.

## Reversal criteria

None — this is a standing methodology rule, not a conclusion with a falsification condition. It applies to any future dev/main content audit, not just the pending one.
