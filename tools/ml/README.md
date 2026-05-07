# SP-1.1 — First Conv-LSTM Checkpoint

This directory contains the end-to-end pipeline for training the **first** Conv-LSTM checkpoint that satisfies the SP-1 acceptance gate (MAE ≤ 1.5% on all 5 regime windows). After activation, the dashboard's `predictions.ghost_*` columns start populating on every closed BTC/USDT 1h candle and the chart's ghost-candle overlay (built in SP-1 Phase E) lights up.

**The infrastructure (model, eval harness, regime windows, checkpoint loader, admin endpoints) was shipped in tag `sp-1-infra`. This sub-project is purely the trained-checkpoint deliverable.**

---

## Files

| Path | Purpose |
|---|---|
| `fetch_ohlcv.py` | Pull BTC/USDT 1h from Binance public klines API → Parquet |
| `train.py` | Train the `ConvLSTMPredictor` (defined in `backend/app/ml/model.py`), evaluate on 5 regimes, save `.pt` + `eval.json` |
| `register.py` | Hash + POST the trained checkpoint to `/api/v1/admin/ml-checkpoints`, optionally activate |
| `colab/train_conv_lstm.ipynb` | Colab T4 GPU wrapper — does fetch + train + download in one notebook |

---

## Recommended path: Colab GPU (free)

Local CPU on the Hetzner box (4 vCPU AMD, no GPU) trains in ~12-24 hours per run. Colab T4 trains the same model in 30-60 min for free.

1. **Open the notebook in Colab:** https://colab.research.google.com/github/naga1412/v5_Trade_bot/blob/main/tools/ml/colab/train_conv_lstm.ipynb
   *(If the repo is private at the time you click this link, GitHub returns 404. Either flip the repo public temporarily, or use a fine-scoped PAT — the notebook prompts for one in cell 1.)*
2. **Runtime → Change runtime type → T4 GPU → Save**
3. **Runtime → Run all.** Cells 1–6 run unattended (~30-60 min). Cell 6 triggers download dialogs for the `.pt` file and the `eval.json`.
4. Save both files somewhere on your laptop.

If `eval.json` shows `all_regimes_pass: false`, do not activate — diagnose first (longer training, more data, different LR — see Troubleshooting below).

---

## Activate on the Hetzner server

After downloading the trained `.pt` + `eval.json` to your laptop:

```powershell
# Set these to match what the notebook produced. VERSION = the timestamp string.
$VERSION = "v1-20260507-180000"
$LOCAL_PT  = "$HOME\Downloads\conv_lstm_$VERSION.pt"
$LOCAL_EV  = "$HOME\Downloads\eval_$VERSION.json"

# 1. SCP the .pt to the path the backend container sees as /app/data/ml-cache/
ssh -i $HOME\.ssh\oracle_key root@95.216.187.204 "mkdir -p /opt/trading-radar/backend/data/ml-cache"
scp -i $HOME\.ssh\oracle_key $LOCAL_PT root@95.216.187.204:/opt/trading-radar/backend/data/ml-cache/
scp -i $HOME\.ssh\oracle_key $LOCAL_EV root@95.216.187.204:/tmp/

# 2. Inside the backend container, register + activate (force=true bypasses
#    the SP-7 champion-challenger gate — required for the first checkpoint
#    because there is no champion to beat yet).
ssh -i $HOME\.ssh\oracle_key root@95.216.187.204 "docker compose -f /opt/trading-radar/docker-compose.yml exec -T backend python /app/tools/ml/register.py --checkpoint /app/data/ml-cache/conv_lstm_$VERSION.pt --eval /tmp/eval_$VERSION.json --base-url http://localhost:8000 --activate --force"

# 3. Restart backend so the lifespan loader picks up the active row
ssh -i $HOME\.ssh\oracle_key root@95.216.187.204 "docker compose -f /opt/trading-radar/docker-compose.yml restart backend"
```

Verify the dashboard now serves ghost candles:

```powershell
ssh -i $HOME\.ssh\oracle_key root@95.216.187.204 "docker compose -f /opt/trading-radar/docker-compose.yml logs --tail=30 backend | grep -i checkpoint"
```

You should see `loaded active checkpoint conv_lstm_predictor v<VERSION>`. Hard-refresh https://aji12.nagayuaj.com — within one closed-candle cycle (≤1 hour), the ghost candle starts appearing on the chart at 50% opacity with P5/P95 uncertainty wicks.

---

## Local CPU fallback (~12-24 hours)

If you don't want to use Colab:

```bash
# From repo root
python -m tools.ml.fetch_ohlcv \
    --symbol BTCUSDT --interval 1h \
    --start 2017-08-17 --end $(date -u +%Y-%m-%d) \
    --out data/ml/btcusdt_1h.parquet

python -m tools.ml.train \
    --data data/ml/btcusdt_1h.parquet \
    --out-dir data/ml/runs/v1-$(date -u +%Y%m%d-%H%M%S) \
    --device cpu \
    --epochs 30
```

(`--device auto` picks GPU if available, falls back to CPU; explicit `--device cpu` documents the slow path.)

---

## Troubleshooting

**`all_regimes_pass: false` after first training run**

1. **Is one regime an outlier?** The 2020-03 COVID-crash window is the hardest — extreme volatility, gappy candles. If only that one fails by a small margin (say 1.6% MAE vs 1.5% threshold), try `--epochs 50 --patience 8` for longer training.
2. **All regimes near-pass with similar MAE?** Probably a learning-rate / batch-size issue. Try `--lr 1e-4 --batch-size 32`.
3. **All regimes way off (≫ 5% MAE)?** Data corruption — re-fetch with `tools.ml.fetch_ohlcv`, check the parquet has no gaps with `pd.read_parquet(...).index.diff().value_counts()`.

**Register POST returns 401**

The admin route is gated by `Depends(require_admin)` from SP-0.7. The `--internal` path (running `register.py` from inside the backend container with `--base-url http://localhost:8000`) skips Cloudflare Access since the container hits FastAPI directly. If you're running `register.py` from outside the container, you need an admin Cf-Access JWT — easiest to just `docker exec` into the container and run from there (the `ssh ... docker compose ... exec -T backend python ...` pattern in the activate-on-Hetzner section above does exactly this).

**Backend doesn't pick up the new checkpoint after activation**

The active checkpoint is loaded once in `main.py:lifespan` at startup. After activating a new row, you MUST `docker compose restart backend` for the loader to re-run. (A future SP-7.x ticket is to add a hot-reload admin endpoint; today, restart is the workflow.)

**Champion-challenger gate fires on second checkpoint and rejects it**

Expected: the gate blocks promotion unless the challenger improves the active champion's MAE by ≥5% (`challenger_mae < champion_mae * 0.95`). This is the SP-7 Phase G2 protection against accidentally regressing the live model. If you're sure you want to override, pass `--force` again. If the challenger genuinely should win (you've trained on more data, tried better hyperparams), the gate will pass automatically — no flag needed.

---

## What's next

When ghost candles render reliably for a couple of weeks:
- **SP-4** — RL Brain (L10): global PPO + per-asset LoRA adapter trains on paper-trade outcomes
- **SP-8** — Autonomous trading mode: gated on SP-1 + SP-4 + SP-7 (all done by then)
