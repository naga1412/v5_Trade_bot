# PR-BRAIN-BOOTSTRAP-FIX-2 — close the last 2 blockers on the brain training cron

**Status:** spec drafted 2026-05-22.
**Branch:** `feat/pr-brain-bootstrap-fix-2` (off `origin/dev` at post-PR-1).
**Class:** **infrastructure bug fix.** No trading-logic change, no behavior change to existing live paths, no flag flip, no migration, no auto-activation of any brain checkpoint.

---

## Background

PR-BRAIN-BOOTSTRAP-FIX-1 (#222 dev / #223 main) fixed 3 OS-layer blockers (chmod, sys.path, mount). Probe run [26294783162](https://github.com/naga1412/v5_Trade_bot/actions/runs/26294783162) confirmed they all work in prod. But the training script still crashes, and a deeper audit revealed it would also fail at registration time. This PR-2 closes both remaining blockers in one shot — verified statically against the full training path.

## Problem

Two CRITICAL blockers remain between the cron firing and a registered `rl_checkpoints` row:

### Blocker #1 — asyncpg ISO-string DataError

`backend/app/rl/replay_buffer.py:228` in `_nearest_intermarket()`:

```python
row = (await session.execute(sa.text(
    """
    SELECT funding_rate, open_interest, captured_at
    FROM intermarket_snapshots
    WHERE symbol = :s AND captured_at <= :t
    ORDER BY captured_at DESC
    LIMIT 1
    """
), {"s": symbol, "t": opened_at_iso})).first()  # ← :t bound as str
```

asyncpg rejects strings for TIMESTAMPTZ parameters:
```
asyncpg.exceptions.DataError: invalid input for query argument $2:
'2026-05-14T17:00:00+00:00' (expected datetime.datetime, got 'str')
```

SQLite (test fixtures) accepts strings, so this is invisible in `pytest`. Same bug class as PR-SAFETY-BATCH-1 commit 68f68e9 (test-fixture fix).

### Blocker #2 — register_brain.py HTTP path will 401 in prod

`backend/scripts/hetzner_brain_cron.sh:101-104` runs:
```bash
REGISTER_CMD="python /app/host-tools/ml/register_brain.py \
  --checkpoint /app/data/rl-cache/ppo_policy_${VERSION}.pt \
  --eval /app/data/rl-cache/eval_brain_${VERSION}.json \
  --base-url http://localhost:8000"
```

`backend/app/api/routes/admin_rl.py:42-46`:
```python
router = APIRouter(
    prefix="/api/v1/admin/rl-checkpoints",
    tags=["admin-rl"],
    dependencies=[Depends(require_admin)],   # require_admin → require_user → require_cf_user
)
```

`backend/app/deps.py:108-124`: in `ENV=production` (prod has this — confirmed in `.env.example:9`), `require_cf_user` enforces the CF Access JWT. Cron's `register_brain.py` invocation has no bearer, no `Cf-Access-Jwt-Assertion` header → 401 → registration fails. **`rl_checkpoints` stays empty even if training succeeds.**

`register_brain.py` already has a `--direct` flag (line 217) that bypasses HTTP entirely and writes via SQLAlchemy with `app.db.session.get_session_factory`. The `--direct` path correctly converts `trained_at` ISO→datetime (line 148-152) and casts eval_results to JSONB (line 160). Cron just isn't using it.

### Bonus — probe bugs caught alongside

- **F3 label**: `ops-debug.yml` brain-cron-trigger reports `rl-cache: ReadOnly={{.RW}}` — but docker's `.RW` field returns `true` when the mount is WRITABLE. The label is inverted.
- **V4 query**: probe queries `auth_violations.kind = 'audit_chain_broken' AND created_at > ...` — but the schema (alembic 2026_05_04_0004 line 80-87) defines columns `id`, `attempted_email`, `attempted_at`, `reason`, `jwt_sub`, `request_path`. No `kind`, no `created_at`. Right query is `WHERE reason = 'audit_chain_broken' AND attempted_at > now() - interval '30 minutes'`.

## Fix — 4 changes across 2 files

### Fix 1 — Convert ISO→datetime at the call site (replay_buffer.py)

**Architecture pushback on the spec's suggestion** of renaming `opened_at_iso` → `opened_at` upstream in `_fetch_closed_trades`:
- `_TradeRow.opened_at_iso` is also consumed by `_resolve_regime` (which parses ISO→datetime internally already, line 200-202) and by `build_eval_doc` in `train_brain.py:131-133` for JSON serialization
- Renaming would touch ~6 sites for zero correctness gain; bigger PR = bigger regression surface
- Smaller fix: convert at the 1 call site that needs it

Add inline conversion at the start of `_nearest_intermarket`:

```python
async def _nearest_intermarket(
    session: AsyncSession, *, symbol: str, opened_at_iso: str,
) -> tuple[float, float]:
    """Look up nearest at-or-before intermarket_snapshots row."""
    from datetime import datetime
    # asyncpg requires datetime.datetime for TIMESTAMPTZ params (sqlite is lenient).
    t = datetime.fromisoformat(opened_at_iso.replace("Z", "+00:00"))
    row = (await session.execute(sa.text(
        """
        SELECT funding_rate, open_interest, captured_at
        FROM intermarket_snapshots
        WHERE symbol = :s AND captured_at <= :t
        ORDER BY captured_at DESC
        LIMIT 1
        """
    ), {"s": symbol, "t": t})).first()
    if row is None:
        return 0.0, 0.0
    funding = float(row.funding_rate) if row.funding_rate is not None else 0.0
    row_24h = (await session.execute(sa.text(
        """
        SELECT open_interest
        FROM intermarket_snapshots
        WHERE symbol = :s AND captured_at <= datetime(:t, '-24 hours')
        ORDER BY captured_at DESC
        LIMIT 1
        """
    ), {"s": symbol, "t": opened_at_iso})).first() if session.bind.dialect.name == "sqlite" else None
    # ... (rest unchanged)
```

The second query (line 241) stays passing the ISO string because:
- It only runs when `dialect.name == "sqlite"` (sqlite uses `datetime(...)` SQL function which is sqlite-specific)
- sqlite accepts strings for parameter binding

(3 lines added: import, datetime parsing comment, `t = datetime.fromisoformat(...)`; 1 char changed: `opened_at_iso` → `t` in the first query binding.)

### Fix 2 — Add `--direct` to cron's register call (hetzner_brain_cron.sh)

```diff
 REGISTER_CMD="python /app/host-tools/ml/register_brain.py \
   --checkpoint /app/data/rl-cache/ppo_policy_${VERSION}.pt \
   --eval /app/data/rl-cache/eval_brain_${VERSION}.json \
-  --base-url http://localhost:8000"
+  --direct"
```

The `--base-url` flag becomes vestigial under `--direct` (HTTP isn't called). `register_brain.py`'s argparse accepts both — leaving `--base-url` would be harmless but cleaner to drop. (1 line change.)

`--direct` mode (register_brain.py:122-185):
- Imports `app.db.session.get_session_factory` (resolvable because PR-1 added `/app` to sys.path for backend imports, and this script also bind-mounts to `/app/host-tools` which is on sys.path via its own script-dir entry — `--direct` runs inside the backend container's Python env where `app.*` is already resolvable from the running uvicorn config)

Wait — verify: register_brain.py is invoked as `docker compose exec -T backend bash -c "python /app/host-tools/ml/register_brain.py ..."`. So it runs inside the backend container. The backend container's PYTHONPATH includes /app (set in Dockerfile, since uvicorn runs `app.main:app`). The script ALSO does NOT need `sys.path.insert(0, "/app")` because the container's PYTHONPATH already covers it. Confirmed — `from app.db.session import get_session_factory` will resolve.

### Fix 3 — Probe F3 label correction (ops-debug.yml)

```diff
- echo '=== F3: rl-cache mount writable (expect no :ro on the rl-cache line) ===' && \
- docker inspect tr-backend --format '{{range .Mounts}}{{if eq .Destination "/app/data/rl-cache"}}rl-cache: ReadOnly={{.RW}} Mode={{.Mode}}{{end}}{{end}}' && \
+ echo '=== F3: rl-cache mount writable (expect Writable=true Mode=rw) ===' && \
+ docker inspect tr-backend --format '{{range .Mounts}}{{if eq .Destination "/app/data/rl-cache"}}rl-cache: Writable={{.RW}} Mode={{.Mode}}{{end}}{{end}}' && \
```

### Fix 4 — Probe V4 query correction (ops-debug.yml)

```diff
- echo '=== V4: any new auth_violations from audit_chain_broken since deploy? (expect 0) ===' && \
- docker compose exec -T postgres psql -U postgres -d trading_radar -c \
-   \"SELECT count(*) FROM auth_violations WHERE kind = 'audit_chain_broken' AND created_at > now() - interval '30 minutes';\"
+ echo '=== V4: any new auth_violations from audit_chain_broken since deploy? (expect 0) ===' && \
+ docker compose exec -T postgres psql -U postgres -d trading_radar -c \
+   \"SELECT count(*) FROM auth_violations WHERE reason = 'audit_chain_broken' AND attempted_at > now() - interval '30 minutes';\"
```

## TDD test plan

Two new test files; both stdlib + existing fixtures only.

### Test 1 — `backend/tests/unit/test_replay_buffer_intermarket_dt.py` (NEW)

Three unit tests using an in-memory async sqlite engine + the real `_nearest_intermarket`:

1. `test_iso_to_datetime_conversion_succeeds_with_z_suffix` — string ending in `Z` is correctly converted to UTC datetime
2. `test_iso_to_datetime_conversion_succeeds_with_offset` — string ending in `+00:00` round-trips
3. `test_nearest_intermarket_passes_datetime_to_query` — patch `session.execute` and assert the bound `t` is `isinstance(datetime)` not `str`

Mock-based; doesn't require Postgres, but guarantees the asyncpg-incompatible string path is gone.

### Test 2 — `backend/tests/unit/test_brain_cron_register_direct.py` (NEW)

Two static tests:

1. `test_hetzner_brain_cron_uses_direct_flag` — read `backend/scripts/hetzner_brain_cron.sh`, assert `--direct` substring in the REGISTER_CMD block
2. `test_hetzner_brain_cron_does_not_use_localhost_http_register` — assert no `--base-url http://localhost:8000` in the REGISTER_CMD block (catches accidental revert)

Both stdlib `re` / `pathlib`. Pure string assertions.

## What this PR does NOT change

- ❌ Does NOT activate any `rl_checkpoints` row to `is_active=True`
- ❌ Does NOT touch the brain inference path (`predictor_glue.compute_brain_adjust_and_persist` stays in no-op fast path)
- ❌ Does NOT modify training hyperparameters, PolicyNetwork, PPO config, replay buffer logic beyond the 1 conversion
- ❌ Does NOT fix the OI-delta-24h sqlite-only gating (data quality issue; follow-up)
- ❌ Does NOT fix the telegram env-var propagation (documented limitation; follow-up `PR-BRAIN-CRON-ENV`)
- ❌ Does NOT touch trading logic, flags, signal-scoring, or any live path
- ❌ Does NOT rename the `opened_at_iso` field anywhere (deliberately preserves the existing API surface)

## Audit chain impact

**None.** `rl_checkpoints` is not in `HASH_PAYLOAD_COLUMNS` (non-chained table). The trainer reads `shadow_trades` + `shadow_observations` + `intermarket_snapshots`, writes `rl_checkpoints`. No write hits the hash chain.

## V-7 latency budget

**Zero.** All fixes apply only to the daily 03:30 UTC cron and the manual ops-debug probe. No hot-path code touched.

## Confidence in one-iteration completion

Statically verified clean past the current crash point:
- `train_ppo` signature ✓
- `PolicyNetwork`/`TrainConfig` ✓
- `AssetEmbeddingTable.register_asset` ✓
- `build_observation` signature ✓
- `compute_reward` protocol ✓
- `save_checkpoint` writes to writable mount ✓
- `register_brain.py --direct` correctly handles datetime/JSONB ✓
- `rl_checkpoints` schema vs INSERT match ✓

Runtime risks (low probability):
- PPO numerics with 285 transitions: well-defended (grad clip, adv-norm, entropy reg)
- AssetEmbeddingTable overflow: 285 trades across <100 symbols, table holds 1024
- JSON serialization: `default=str` already in train_brain.py:218

**~90% confidence** the next probe run after this PR produces a `.pt` + a `rl_checkpoints` row. If a 3rd blocker surfaces it'll be a small PR-3.

## Rollback

`git revert <PR-BRAIN-BOOTSTRAP-FIX-2-squash>`. All fixes are isolated. After rollback, cron resumes failing at the asyncpg DataError. No data loss — `rl_checkpoints` stays in its current state.

## Post-deploy verification

After merge + prod deploy:
1. Run the existing `brain-cron-trigger` probe (workflow_dispatch → ops-debug → probe=brain-cron-trigger)
2. Confirm:
   - F1/F2/F3 verifications all pass (already verified in PR-1)
   - T1: training runs to completion (expect 5-15 min CPU)
   - V1: new `rl_checkpoints` row with `is_active=False`
   - V2: new `.pt` file in `/app/data/rl-cache/`
   - V3: cron log shows `✓ training completed` + `✓ registered as rl_checkpoints.id=<id>` markers
   - V4: 0 new `audit_chain_broken` rows in last 30 min
3. **DO NOT activate the resulting checkpoint** — operator-only after eval review

## Commit message

```
feat(pr-brain-bootstrap-fix-2): close asyncpg DataError + register --direct gaps
```

## Auto-merge authorization

Per PR-BRAIN-BOOTSTRAP-FIX-2 directive: infrastructure bug fix, no trading-logic change. Auto-merge per standing rules once CI green + reviewers PASS + TDD tests pass.
