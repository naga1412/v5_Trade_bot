# PR-BRAIN-WARMSTART-FIX — restore asset-id + asset-embedding signal in the trainer

**Status:** spec drafted 2026-05-22.
**Branch:** `feat/pr-brain-warmstart-fix` (off `origin/dev` post-PR-3).
**Class:** **infrastructure bug fix.** Training-only path. NO impact on live trading: `rl_checkpoints.id=1` remains `is_active=False` throughout; the brain inference hook stays in the no-op fast path.

---

## Background

`PR-BRAIN-EVAL-REVIEW` of `rl_checkpoints.id=1` surfaced that the first-ever checkpoint was trained on **degenerate observations**:
- Every transition had `asset_id=0` (because `sym_to_id` was empty at buffer-build time)
- Every transition had `embedding=zeros(32)` (because `asset_embeddings` was never passed to `load_from_shadow_trades`)

Result: across 45 distinct symbols, the brain saw observations that were identical in the asset-identity dimensions. Final-epoch entropy was 1.605 / ln(5)=1.609 → policy is essentially uniform across actions.

## Critique of the simple "reorder 2 calls" fix

A literal swap of `_build_buffer()` and `register_asset()` calls is **insufficient** for three reasons:

### Issue A — `asset_embeddings` is still `None`

`tools/ml/train_brain.py:78-82` calls `load_from_shadow_trades(asset_id_for_symbol=asset_table.symbol_to_id)` but does NOT pass `asset_embeddings=...`. Per [replay_buffer.py:290-294](backend/app/rl/replay_buffer.py#L290), `embeds = asset_embeddings or {}` defaults to empty → `embedding = embeds.get(asset_id, embed_zero)` → always zeros. Even with `sym_to_id` populated, the obs would still have zero embeddings.

### Issue B — `register_asset()` median-seeds, destroying Gaussian-init diversity

[adapter.py:74-80](backend/app/rl/adapter.py#L74) overwrites a newly-allocated slot with the **median of all prior known embeddings**. The intent (per docstring) is inference-time cold-start for unseen symbols. At training-time bulk pre-registration, this clusters all 45 slot embeddings near the first asset's Gaussian sample, defeating the diversity the `__post_init__` Gaussian init provides.

### Issue C — Warm-start ordering paradox

Currently `_build_buffer()` runs at line 166, `maybe_warm_start()` at line 188. So even when warm-starting from `id=1.pt`, the buffer is built BEFORE warm-start restores `asset_table.symbol_to_id`. The bug repeats on every run. The fix must run `maybe_warm_start` BEFORE `_build_buffer`.

## The real fix — 3 coordinated changes

### Fix A — `adapter.py`: add `bulk_register()` that preserves Gaussian init

```python
def bulk_register(self, symbols: Iterable[str]) -> None:
    """Pre-register many symbols WITHOUT median-seeding their embeddings.

    Differs from register_asset(): we want each slot to keep its
    independent Gaussian-init embedding (provided by __post_init__),
    so the brain sees diverse per-asset signal during training.

    register_asset() is meant for INFERENCE-time unseen-symbol entry
    (where median-seed gives an "average asset" cold-start). Using it
    in a tight training-time loop collapses all embeddings to a single
    cluster — which is what produced the degenerate id=1 checkpoint.
    """
    for symbol in symbols:
        if symbol in self.symbol_to_id:
            continue
        next_id = len(self.symbol_to_id)
        if next_id >= self.n_slots:
            raise RuntimeError(f"AssetEmbeddingTable full: {self.n_slots} slots used")
        self.symbol_to_id[symbol] = next_id
        # No weight overwrite — preserves __post_init__'s Gaussian init
```

### Fix B — `train_brain.py`: reorder + pre-register + embeddings

New ordering in `_async_main`:

```python
asset_table = AssetEmbeddingTable()
policy = PolicyNetwork().to(device)

# Step 1: warm-start FIRST (restores asset_table.symbol_to_id + weights if prior .pt exists)
warm_start_path = Path(args.warm_start) if args.warm_start else None
warm_started = maybe_warm_start(policy, asset_table, warm_start_path)

# Step 2 (cold-start only): pre-register all training symbols to give them
# diverse Gaussian-init embeddings. Skipped on warm-start because the
# restored asset_table already has its symbol_to_id + weights.
if not warm_started:
    training_symbols = await _discover_training_symbols(args.db_url)
    asset_table.bulk_register(training_symbols)
    log.info("cold-start: pre-registered %d symbols with diverse embeddings",
             len(asset_table.symbol_to_id))

# Step 3: build buffer with populated asset_id map + per-asset embeddings
transitions = await _build_buffer(
    db_url=args.db_url,
    window_days=args.window_days,
    asset_table=asset_table,
)

# Step 4: defensive register_asset for any symbols that appeared in the
# buffer but aren't yet in asset_table (covers warm-start case where
# new symbols entered the universe since prior training). Uses the
# median-seed register_asset since these are truly "new" assets that
# should cold-start from the average — matches inference behavior.
for t in transitions:
    asset_table.register_asset(t.symbol)
```

Helpers:

```python
async def _discover_training_symbols(db_url: str) -> list[str]:
    """Return distinct symbols appearing in closed shadow_trades.

    Cheap query — separate engine to avoid sharing with _build_buffer's.
    """
    engine = create_async_engine(db_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        rows = (await session.execute(sa.text(
            "SELECT DISTINCT symbol FROM shadow_trades "
            "WHERE closed_at IS NOT NULL AND pnl_usdt IS NOT NULL "
            "ORDER BY symbol"
        ))).all()
    await engine.dispose()
    return [r.symbol for r in rows]


def _extract_asset_embeddings(asset_table: AssetEmbeddingTable) -> dict[int, np.ndarray]:
    """Snapshot the table's current embeddings as a {asset_id: np.ndarray} dict.

    Passed to load_from_shadow_trades so obs vectors carry the real
    per-asset embedding instead of np.zeros(32).
    """
    out: dict[int, np.ndarray] = {}
    weight = asset_table.module.weight.detach().cpu().numpy().astype(np.float32)
    for asset_id in asset_table.symbol_to_id.values():
        out[asset_id] = weight[asset_id].copy()
    return out
```

And `_build_buffer` is updated to extract + pass embeddings:

```python
async def _build_buffer(
    *, db_url: str, window_days: int, asset_table: AssetEmbeddingTable,
):
    asset_embeddings = _extract_asset_embeddings(asset_table)
    engine = create_async_engine(db_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        transitions = await load_from_shadow_trades(
            session,
            window_days=window_days,
            asset_id_for_symbol=asset_table.symbol_to_id,
            asset_embeddings=asset_embeddings,
        )
    await engine.dispose()
    return transitions
```

### Decision: cold-start vs warm-start the next run

**Recommendation: COLD-START the next training run.**

Rationale:
- `rl_checkpoints.id=1` saved its degenerate state: 45 entries in `symbol_to_id` but all 45 embedding weights are derived from a single Gaussian sample via cluster-median seeding → they're near-identical.
- Warm-starting from id=1 inherits this clustered state. Even with the new code's "defensive register_asset for new symbols" step, the pre-existing 45 entries stay clustered.
- Cold-starting goes through `bulk_register()` → 45 independent Gaussian embeddings → maximum per-asset diversity.

**Operator action required before next cron tick:**
Move existing .pt files out of `/opt/trading-radar/backend/data/rl-cache/` so the cron's auto-warm-start glob (`ls -t ppo_policy_v*.pt`) misses them. Two options:

```bash
# Option 1: rename in-place (preserves files, blocks warm-start)
mv /opt/trading-radar/backend/data/rl-cache/ppo_policy_v1-20260522-164636.pt \
   /opt/trading-radar/backend/data/rl-cache/ppo_policy_v1-20260522-164636.pt.predegenerate
mv /opt/trading-radar/backend/data/rl-cache/ppo_policy_v1-20260522-171030.pt \
   /opt/trading-radar/backend/data/rl-cache/ppo_policy_v1-20260522-171030.pt.predegenerate

# Option 2: move to a subdirectory
mkdir -p /opt/trading-radar/backend/data/rl-cache/_predegenerate/
mv /opt/trading-radar/backend/data/rl-cache/*.pt \
   /opt/trading-radar/backend/data/rl-cache/_predegenerate/
mv /opt/trading-radar/backend/data/rl-cache/*.json \
   /opt/trading-radar/backend/data/rl-cache/_predegenerate/
```

Documented for operator (or me to run via ad-hoc command after merge).

## Limitation NOT fixed in this PR (documented for follow-up)

**Asset embeddings are frozen during PPO training.** [ppo.py:142](backend/app/rl/ppo.py#L142) creates the optimizer from `policy.parameters()` only. The `AssetEmbeddingTable._module` (nn.Embedding) is NOT in `policy.parameters()`. So embeddings keep their init Gaussian noise values throughout training — gradients don't flow back to them.

With this PR, the brain trains on observations that **differ per asset** (different 32-dim Gaussian noise pre-baked into obs) → policy can learn per-asset patterns based on those noise vectors. But the embeddings themselves don't get updated by training. A future PR could either:
- Refactor `PolicyNetwork.forward` to take `asset_id` and do the embedding lookup inside the policy (so gradients flow), OR
- Add `asset_table.module.parameters()` to the optimizer's param list

Out of scope here.

## TDD test plan

### `backend/tests/unit/test_rl_adapter.py` (add tests)

1. `test_bulk_register_preserves_gaussian_init` — register 5 symbols via `bulk_register`, assert the 5 corresponding weight vectors are NOT all identical (would be if median-seeded).
2. `test_bulk_register_is_idempotent` — calling twice with overlapping symbols doesn't double-allocate or overwrite.
3. `test_bulk_register_respects_n_slots_cap` — exceeds cap → RuntimeError.

### `backend/tests/unit/test_train_brain_warmstart.py` (new file)

1. `test_discover_training_symbols_returns_distinct_sorted` — sqlite fixture with duplicated symbols; helper returns deduped sorted list.
2. `test_extract_asset_embeddings_round_trip` — populate asset_table with 3 symbols, extract, assert dict keys = asset_ids and values are 32-dim float32 arrays matching the table's weights.

### `backend/tests/unit/test_rl_replay_buffer.py` (add 1 test)

1. `test_load_from_shadow_trades_uses_provided_embeddings` — insert 3 closed trades on 3 different symbols + provide `asset_id_for_symbol={"A":0, "B":1, "C":2}` and `asset_embeddings={0: [1.0]*32, 1: [2.0]*32, 2: [3.0]*32}`; assert each transition's first-32 obs floats match the expected embedding.

All tests use stdlib + existing sqlite fixtures. No Postgres dependency.

## What this PR does NOT change

- Does NOT activate any `rl_checkpoints` row
- Does NOT touch trading logic, flags, signal scoring
- Does NOT touch brain inference path (`predictor_glue.compute_brain_adjust_and_persist`)
- Does NOT modify the cron script (warm-start logic stays; operator action handles the one-time cold-start)
- Does NOT fix the "embeddings frozen during PPO" architectural issue (follow-up)

## V-7 latency budget

**Zero.** Training-only path. Daily 03:30 UTC cron only.

## Audit chain impact

**None.** `rl_checkpoints` is not in `HASH_PAYLOAD_COLUMNS`. The new `_discover_training_symbols` query is read-only against `shadow_trades`.

## Rollback

`git revert <PR-BRAIN-WARMSTART-FIX-squash>`. Code-only revert.

If the cron has already produced a new checkpoint after the fix, that checkpoint stays in the DB but inactive. Operator can deactivate by leaving it as-is or DELETE'ing the row. No live impact regardless.

## Post-deploy verification

1. **Operator action**: move existing .pt files out of rl-cache (commands above).
2. **Re-run probe**: `gh workflow run ops-debug.yml --ref main -f probe=brain-cron-trigger`
3. **Confirm in V1 (rl_checkpoints row)**:
   - `train_data_window.asset_count` is now > 1 (expect ~45)
   - `train_data_window.n_transitions` is ~291+ (replay buffer keeps growing)
4. **Confirm in V3 (cron log tail)**:
   - "cold-start: pre-registered N symbols with diverse embeddings" log line appears
   - PPO training runs to completion
   - Final-epoch entropy logged at < 1.605 (any reduction from 99.7% max would indicate the brain is finding signal)
5. **If asset_count is still 1**: the fix didn't take effect — investigate via deeper SSH probe.

## Commit message

```
feat(pr-brain-warmstart-fix): warm-start before build-buffer + diverse asset embeddings
```

## Auto-merge authorization

Per PR-BRAIN-WARMSTART-FIX directive: training-only bug fix, no live trading impact. Auto-merge per standing rules once CI green + reviewers PASS + TDD tests pass.
