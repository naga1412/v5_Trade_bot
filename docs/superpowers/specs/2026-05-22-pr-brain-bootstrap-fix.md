# PR-BRAIN-BOOTSTRAP-FIX — three one-line fixes to unblock the brain training cron

**Status:** spec drafted 2026-05-22.
**Branch:** `feat/pr-brain-bootstrap-fix` (off `origin/dev` at `5a7956f`).
**Class:** **infrastructure bug fix.** No trading-logic change, no behavior change to existing live paths, no flag flip, no migration, no auto-activation of any brain checkpoint.

---

## Problem

PR-BRAIN-CRON-AUDIT (2026-05-22T13:32Z) found that the 03:30 UTC brain training cron has been firing daily for 7+ consecutive days and crashing immediately every time. Log file `/var/log/trading-radar-brain-cron.log` contains 7 identical lines:

```
/bin/sh: 1: /opt/trading-radar/backend/scripts/hetzner_brain_cron.sh: Permission denied
```

Three discrete bugs block the cron from producing a first checkpoint:

1. **Cron script has no execute bit** (`0644`, should be `0755`)
2. **Training entrypoint can't import `app` package** (`ModuleNotFoundError: No module named 'app'` when running `python /app/host-tools/ml/train_brain.py`)
3. **`rl-cache` volume mounted read-only** (`./backend/data/rl-cache:/app/data/rl-cache:ro` in docker-compose.yml) — even if training ran, checkpoint write would fail

Replay buffer is **ready**: 285 closed `shadow_trades` with `pnl_usdt` (5.7× the trainer's `MIN_TRANSITIONS_TO_TRAIN=50` threshold), `shadow_observations` 100% coverage (291/291 rows have `components`). The trainer will succeed when the bugs are fixed.

## Fix (4 LoC across 3 files)

### Fix 1 — Execute bit on cron script

**Approach: set the bit in git itself** (cleaner than adding `chmod +x` to deploy.yml since every checkout/deploy gets the right mode automatically).

```bash
git update-index --chmod=+x backend/scripts/hetzner_brain_cron.sh
```

This changes the git index entry from `100644` to `100755`. No file content change. After this commit lands, every `git pull` on Hetzner restores the executable bit if it ever gets lost. CI's `docker-compose-smoke` job exercises the same checkout, so file-mode regressions will surface.

**No deploy.yml change required** — the existing `git pull` in [deploy.yml](.github/workflows/deploy.yml) already preserves git's file-mode bits.

### Fix 2 — `sys.path` for train_brain.py

**Approach: match PR-FIX-FLAG-BINDING's audit-bundle script precedent.** When Python runs `/app/host-tools/ml/train_brain.py`, only `/app/host-tools/ml/` is on `sys.path` — not `/app/`. So `from app.rl.adapter import ...` fails.

Add at the top of `tools/ml/train_brain.py` (which becomes `/app/host-tools/ml/train_brain.py` via the existing `./tools:/app/host-tools:ro` bind mount):

```python
import sys
sys.path.insert(0, "/app")
```

(2 lines including the import.)

**Why not PYTHONPATH env var?** Would work via `docker compose exec -e PYTHONPATH=/app -T backend ...` in the cron script, but matching the existing repo precedent ([`scripts/diag_audit_bundle.py`](.github/workflows/scripts/diag_audit_bundle.py) uses `sys.path.insert`) wins on consistency.

### Fix 3 — Drop `:ro` on rl-cache mount

**Approach: edit one character in `docker-compose.yml`.** The current line ([backend service volumes](docker-compose.yml#L76)):

```yaml
- ./backend/data/rl-cache:/app/data/rl-cache:ro
```

becomes:

```yaml
- ./backend/data/rl-cache:/app/data/rl-cache
```

(Drop `:ro` suffix. Default is read-write.)

**Why not split into two volumes?** Spec considered `rl-cache:ro` + `rl-checkpoints:rw`. Rejected as overengineering: the brain trainer both READS warm-start `.pt` files AND WRITES new checkpoint `.pt` files from/to the SAME directory. Splitting adds complexity for zero security benefit (the directory contains the operator's brain checkpoints — they're already controlled).

`ml-cache` directly above stays `:ro` (correct — operator SCPs `.pt` files in, container reads them).

## Known limitation accepted (NOT fixed in this PR)

**Cron environment is empty.** `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are NOT inherited by the cron process (cron doesn't read `.env`). The script's `notify()` function at lines 36-47 of `hetzner_brain_cron.sh`:

```bash
if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$TELEGRAM_CHAT_ID" ]; then
  echo "[$(date -u +%FT%TZ)] WARN: telegram secrets unset; skipping notify" \
    | tee -a "$LOG_FILE"
  return 0
fi
```

When tokens are blank (the cron case), `notify` logs a WARN and returns 0. **This means: training will succeed, candidate will be registered in `rl_checkpoints`, but the operator-approval telegram message will NOT be sent.** Operator must check `rl_checkpoints` directly or read the cron log.

**Why not fix in this PR:** the fix requires sourcing `/opt/trading-radar/.env` at the script start OR adding an `--env-file` to the cron line. Either is a 2-line change but spec scope is "3-bug bootstrap." Defer to a follow-up `PR-BRAIN-CRON-ENV` if operator wants the telegram alert.

## TDD test plan

Three infrastructure tests in `backend/tests/db/test_brain_cron_bootstrap.py` (new file). Each is small + uses stdlib only.

1. **`test_brain_cron_script_has_executable_bit_in_git_index`** — `git ls-files --stage backend/scripts/hetzner_brain_cron.sh` returns a line starting with `100755 ` (executable mode). Asserts the git index mode bit, which survives across checkouts. Skip-on-Windows-CI if needed since `git` mode bits are POSIX.

2. **`test_train_brain_has_sys_path_insert`** — read `tools/ml/train_brain.py`, assert the file contains `sys.path.insert(0, "/app")` (or any equivalent that adds `/app` to sys.path). String search — no actual import execution.

3. **`test_docker_compose_rl_cache_mount_is_writable`** — YAML-parse `docker-compose.yml`, find the backend service's `volumes` list, locate the entry mounting `./backend/data/rl-cache`, assert the entry does NOT end in `:ro` (or, if parsed as dict, `read_only` is not True).

All three tests run in the existing unit-test suite (no Postgres / container required).

## What this PR does NOT change

- ❌ Does NOT activate any `rl_checkpoints` row to `is_active=True` — that's a separate operator-controlled action after eval review
- ❌ Does NOT touch the brain inference path (`predictor_glue.compute_brain_adjust_and_persist` stays in no-op fast path until operator activates)
- ❌ Does NOT modify training hyperparameters, replay buffer logic, or PPO config
- ❌ Does NOT fix the telegram env-var issue (documented limitation above)
- ❌ Does NOT touch trading logic, flags, or any signal-scoring code

## Audit chain impact

**None.** `rl_checkpoints` is in `NON_HASHED_ALLOW_LIST` (per [audit.py:134-136](backend/app/db/audit.py#L134-L136)). The `symbol_performance_snapshots` chain analog applies to checkpoint registration via `id`, `prev_hash`, `row_hash`, `inputs_hash` — but training writes go through `insert_with_chain` for hash chain... let me re-check. Actually looking at `HASH_PAYLOAD_COLUMNS`, `rl_checkpoints` is NOT listed — only `symbol_performance_snapshots` from PR10. So `rl_checkpoints` is a non-chained table. Training writes don't touch the chain.

`brain_decisions` IS chained (in `HASH_PAYLOAD_COLUMNS`), but only the predictor's brain hook writes to it — not the trainer. Trainer just reads `shadow_trades` + `shadow_observations` + writes `rl_checkpoints`.

## V-7 latency budget

**Zero.** The fixes apply only to the daily 03:30 UTC cron, not to any hot path. After fix, expected training duration is 5-15 min on Hetzner CPU per spec sec 5.2 (30 epochs × 256 batch, 285 transitions).

## Rollback

`git revert <PR-BRAIN-BOOTSTRAP-FIX-squash>`. All three fixes are isolated.
- The `git update-index --chmod` is reverted by the revert commit (chmod -x).
- `sys.path.insert` removal restores ModuleNotFoundError state.
- Adding `:ro` back restores read-only state.

After rollback, brain cron resumes failing as before. No data loss (rl-cache stays empty either way until training succeeds).

## Auto-merge authorization

Per PR-BRAIN-BOOTSTRAP-FIX directive: infrastructure bug fix, no trading-logic change. Auto-merge per standing rules once CI green + both reviewers PASS + TDD tests pass.

## Post-deploy verification

1. **Wait for next 03:30 UTC cron tick** (or operator manually runs `bash /opt/trading-radar/backend/scripts/hetzner_brain_cron.sh` for faster verify).
2. **Confirm log shows:**
   - No "Permission denied" line
   - No `ModuleNotFoundError` line
   - No "Read-only file system" line
   - A `version=v1-<timestamp>` start line
   - A "✓ training completed" success line
   - A "✓ registered as rl_checkpoints.id=<id>" line
3. **Confirm new file exists:** `ls /opt/trading-radar/backend/data/rl-cache/ppo_policy_v*.pt` returns a file.
4. **Confirm new `rl_checkpoints` row:** `SELECT id, version, is_active, eval_results->>'sharpe' FROM rl_checkpoints ORDER BY id DESC LIMIT 1` returns 1 row with `is_active=False`.
5. **Confirm chain integrity:** zero new `auth_violations` from audit_chain_broken since deploy. (Trainer doesn't touch the chain, but the daily 03:00 verifier runs simultaneously — this is a defense-in-depth check.)

If telegram approval message arrives → ✅ bonus (means the cron environment has tokens somehow). If not → expected per known limitation above.

If the cron still fails after deploy, the log will tell us which bug isn't fully fixed. Operator can re-run the audit probe.

## Commit message

```
feat(pr-brain-bootstrap-fix): unblock hetzner_brain_cron with 3 one-line fixes
```
