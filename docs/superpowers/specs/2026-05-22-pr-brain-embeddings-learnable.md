# PR-BRAIN-EMBEDDINGS-LEARNABLE — make asset embeddings trainable via training-loop hot-swap

**Status:** spec drafted 2026-05-22.
**Branch:** `feat/pr-brain-embeddings-learnable` (off `origin/dev` post-PR-BRAIN-SOFTER-ACTIONS).
**Class:** **brain architecture fix.** Training-only. NO live trading impact: brain remains dormant.

---

## Problem

Asset embeddings are FROZEN during PPO training. The `nn.Embedding` for `asset_table` is NOT in `policy.parameters()`, so the optimizer never updates them. The brain trains on static-random Gaussian embeddings (post-PR-WARMSTART-FIX) but can't refine them based on per-symbol outcomes. This is one driver of the id=3/id=4 28.8% win rate.

## Verified facts

1. `PolicyNetwork` ([policy.py:56-89](backend/app/rl/policy.py#L56)) defines `shared` + `policy_head` + `value_head` only. No asset embedding inside.
2. `AssetEmbeddingTable._module` is a SEPARATE `nn.Embedding`. Its parameters are not in `policy.parameters()`.
3. `ppo.train_ppo` ([ppo.py:142](backend/app/rl/ppo.py#L142)) constructs optimizer from `policy.parameters()` only.
4. `obs` in `train_ppo` is built via `torch.from_numpy(np.stack([t.obs for t in transitions]))` ([ppo.py:106](backend/app/rl/ppo.py#L106)). The numpy `t.obs` already has the embedding baked in (32 floats at the start) by `replay_buffer.build_observation`. The resulting torch tensor has **no autograd connection** to `asset_table.module.weight`.

## Critique of the directive's two options

### Option A — add `asset_table.module.parameters()` to optimizer

**Does NOT work alone.** The optimizer would call `step()` on `asset_table.module.weight`, but `.grad` is always `None` because there's no autograd path FROM the loss TO the embedding weights. Optimizer would be a silent no-op on the asset_table params.

### Option B — restructure `PolicyNetwork.forward(asset_id, ...)`

**Works, but big.** Add `asset_table` as a submodule of `PolicyNetwork`. Change `forward` signature. Cascades through `act`, `evaluate_actions`, `_stack_transitions`, `decide_action`, `predictor_glue`, `backtest_brain`. ~300-400 LoC across 5-6 files. High regression surface.

### Recommended approach — HOT-SWAP at the training-loop seam (~80-100 LoC)

Keep `Transition` shape, `build_observation`, `inference.decide_action`, and `PolicyNetwork.forward` ALL unchanged. Make surgical changes ONLY in `ppo.train_ppo` + `backtest_brain.evaluate_brain_on_holdout`:

- Stack `asset_ids` tensor alongside obs (`_stack_transitions` returns one more tensor)
- Before each forward pass, build a "live" obs by REPLACING the first 32 dims (the pre-baked embedding region) with an autograd-aware lookup from `asset_table.module(asset_ids)`
- Optimizer = `Adam(policy.parameters() + asset_table.module.parameters())`
- Gradients flow from loss → live_emb → asset_table.module.weight

This gives Option B's behavior with Option A's footprint. Inference path is unaffected because:
- `inference.decide_action` reads embeddings via `asset_table.get_embedding(symbol)`, which returns `self._module.weight[idx].detach().cpu().numpy()` — the CURRENT weight, including any post-training updates
- `build_observation` bakes the live embedding into obs at inference time — already correct

## Files changed (4)

### 1. `backend/app/rl/ppo.py` — hot-swap + optimizer

```python
def _stack_transitions(transitions):
    """Return (obs, actions, rewards, asset_ids) — PR-EMBEDDINGS-LEARNABLE
    adds asset_ids so the training loop can hot-swap pre-baked embeddings
    with a live, autograd-aware lookup."""
    ...
    asset_ids = torch.tensor(
        [t.asset_id for t in transitions], dtype=torch.long,
    )
    return obs, actions, rewards, asset_ids


def train_ppo(
    *,
    policy: PolicyNetwork,
    transitions: Sequence[Transition],
    asset_table: AssetEmbeddingTable,    # NEW
    config: TrainConfig | None = None,
    device: torch.device | None = None,
) -> TrainResult:
    ...
    obs, actions, rewards, asset_ids = _stack_transitions(transitions)
    obs = obs.to(dev); actions = actions.to(dev); rewards = rewards.to(dev)
    asset_ids = asset_ids.to(dev)
    asset_table.module.to(dev)
    
    EMB_DIM = asset_table.emb_dim  # 32
    
    def _live_obs(obs_baked, ids):
        """Replace the first EMB_DIM dims with autograd-aware embedding lookup."""
        live_embs = asset_table.module(ids)
        return torch.cat([live_embs, obs_baked[:, EMB_DIM:]], dim=1)
    
    policy = policy.to(dev)
    # PR-EMBEDDINGS-LEARNABLE: optimizer now includes asset_table params.
    # This is what makes embeddings trainable — combined with the hot-swap
    # above so gradients have a path back from loss to module.weight.
    opt = torch.optim.Adam(
        list(policy.parameters()) + list(asset_table.module.parameters()),
        lr=cfg.lr,
    )
    
    # Compute OLD log probs on the LIVE obs at entry (asset_table weights
    # are π_θ_old's view of embeddings). Detach because old log probs are
    # frozen targets for the PPO ratio.
    policy.eval()
    with torch.no_grad():
        old_obs_live = _live_obs(obs, asset_ids).detach()
        old_log_probs, _, old_values = policy.evaluate_actions(
            old_obs_live, actions,
        )
    policy.train()
    
    # ... advantages / returns (unchanged) ...
    
    for epoch in range(1, cfg.epochs + 1):
        ...
        for _inner in range(cfg.ppo_epochs_per_batch):
            for start in range(0, n, cfg.batch_size):
                idx = perm[start : start + cfg.batch_size]
                ...
                # Re-compute live obs PER BATCH — embeddings may have
                # changed since the last opt.step()
                b_obs_live = _live_obs(obs[idx], asset_ids[idx])
                b_actions = actions[idx]; ...
                new_log_probs, entropy, new_values = policy.evaluate_actions(
                    b_obs_live, b_actions,
                )
                ...
```

### 2. `backend/app/rl/backtest_brain.py` — accept asset_table + hot-swap

```python
def evaluate_brain_on_holdout(
    *,
    policy: PolicyNetwork,
    asset_table: AssetEmbeddingTable,    # NEW
    transitions: Sequence[Transition],
    holdout_fraction: float = 0.2,
    device: torch.device | None = None,
) -> BacktestResult | dict:
    """...
    
    PR-EMBEDDINGS-LEARNABLE: evaluates the trained policy with the
    CURRENT asset_table weights (which now contain trained embeddings
    after PPO updates). Without this, the backtest would evaluate the
    policy against the PRE-TRAINING embeddings still baked into obs.
    """
    ...
    EMB_DIM = asset_table.emb_dim
    with torch.no_grad():
        for tr in holdout:
            obs_t = torch.from_numpy(tr.obs).unsqueeze(0).to(dev)
            # Hot-swap with the current (trained) embedding
            asset_id_t = torch.tensor([tr.asset_id], dtype=torch.long).to(dev)
            live_emb = asset_table.module(asset_id_t)
            obs_live = torch.cat([live_emb, obs_t[:, EMB_DIM:]], dim=1)
            action_t, _, _ = policy.act(obs_live, deterministic=True)
            ...
```

### 3. `tools/ml/train_brain.py` — pass asset_table to train_ppo + backtest

1-line change to both call sites:
```python
train_result = train_ppo(
    policy=policy, transitions=transitions,
    asset_table=asset_table,                # NEW
    config=cfg, device=device,
)
backtest_outcome = evaluate_brain_on_holdout(
    policy=policy, transitions=transitions,
    asset_table=asset_table,                # NEW
    device=device,
)
```

### 4. Tests — `backend/tests/unit/test_ppo_learnable_embeddings.py` (new file)

```python
def test_optimizer_includes_asset_table_parameters():
    """asset_table.module.weight must be in opt.param_groups after train_ppo
    starts (verified by snapshotting before-vs-after to confirm step()
    operated on it)."""

def test_gradient_flows_to_embedding_after_backward():
    """One forward + backward on a single transition; assert
    asset_table.module.weight.grad is not None for the relevant asset_id row."""

def test_embedding_weights_update_after_training_step():
    """Train for 5 epochs; assert asset_table.module.weight has CHANGED
    from its init value. (Frozen-embedding regression check.)"""

def test_legacy_checkpoint_load_compat():
    """Save a state_dict with the OLD frozen-embedding training (or just
    a Gaussian init), load it via maybe_warm_start. No errors, no shape
    mismatch."""

def test_per_asset_embeddings_diverge_after_training():
    """Train on synthetic data with distinct per-asset reward signatures
    (e.g., asset 0 always rewards +1, asset 1 always rewards -1). After
    training, the cosine similarity between the two assets' embeddings
    should be < 0.99 (i.e., they specialized). Pins the learning happens."""

def test_backtest_uses_current_asset_table_embeddings():
    """Modify asset_table.module.weight in-place between train and
    backtest; assert the backtest's policy.act sees the new weights
    (i.e., backtest hot-swap is active)."""
```

## Backward compatibility

- **Old checkpoint state_dict shape**: `{"policy": <policy state>, "asset_table": <asset_table state>}` — unchanged.
- **Policy state_dict shape**: `shared.{0,2}.{weight,bias}`, `policy_head.{weight,bias}`, `value_head.{weight,bias}` — unchanged.
- **Asset_table state_dict shape**: `{"weights": <emb state>, "symbol_to_id": dict, "n_slots": int, "emb_dim": int}` — unchanged.
- **`maybe_warm_start`**: existing implementation works unchanged.
- **Inference (`decide_action`)**: unchanged — already uses `asset_table.get_embedding(...)` which reads the CURRENT module.weight (live).

No checkpoint migration. id=1..4 keep loading fine; id=5+ will have meaningfully different weights but same format.

## What this PR does NOT change

- Does NOT activate any rl_checkpoints row
- Does NOT touch trading logic / flags / signal scoring
- Does NOT touch `PolicyNetwork.forward` signature
- Does NOT touch `Transition` shape, `replay_buffer.load_from_shadow_trades`, `build_observation`
- Does NOT touch `inference.decide_action` or `predictor_glue.compute_brain_adjust_and_persist`
- Does NOT change BRAIN_ACTION_MULTIPLIER_SPREAD — held constant at 0.15 to keep id=5 vs id=4 comparable

## V-7 latency budget

**Zero.** Training-only path. Daily 03:30 UTC cron.

## Audit chain impact

**None.**

## Rollback

`git revert`. After revert, embeddings revert to frozen-Gaussian; id=5+ checkpoints' trained embeddings persist on disk but load behavior is unchanged (the saved state_dict shape is identical).

## Post-deploy verification (per Sharpe paradox lesson)

Run `brain-cron-trigger` probe. Hold `BRAIN_ACTION_MULTIPLIER_SPREAD=0.15` constant (no env override).

Compare id=5 vs id=4 on **ALL** metrics, NOT just Sharpe:

| Metric | id=4 baseline | id=5 expected if PR works | Why it matters |
|--------|--------------|---|---|
| **win_rate** ★ | 28.8% | ≥35% promising; ≥45% real learning | PRIMARY: this PR changes DECISIONS, so win_rate moves |
| Sharpe | -3.91 | less negative or positive | Apples-to-apples (SPREAD held constant) |
| max_dd | 39.36 | similar or better | Lower bound on damage |
| Embedding cosine similarity across 2 random asset slots | ~1.0 (degenerate cluster from id=4 inherit) | <0.95 | PROOF the embeddings actually changed during training |

If brain warm-starts from id=4 (clustered embeddings), updates may stay near-cluster. To get a fair test, may need to cold-start from the `_predegenerate/` archive again — surface this if early observations show cosine sim still near 1.0.

**Activation guidance** (per operator's directive):
- win_rate ≥ 40% → STRONG candidate for shadow-mode activation
- win_rate 35-40% → ACTIVATE WITH CAUTION
- win_rate < 35% → DO NOT ACTIVATE, look at other root causes (reward shaping, data volume, regime coverage)

Report all 4 metrics inline + activation recommendation.

## Commit message

```
feat(pr-brain-embeddings-learnable): training-loop hot-swap unfreezes asset embeddings
```

## Auto-merge authorization

Per directive: training-only architecture fix, no live trading impact. Auto-merge per standing rules.
