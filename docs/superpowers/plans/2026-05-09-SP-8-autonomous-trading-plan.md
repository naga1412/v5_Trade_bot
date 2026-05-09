# SP-8 Autonomous Trading — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the infrastructure required to flip the platform from research-only to autonomous money-trading. Code lands first; live trading stays GATED behind promotion criteria (§4 of the spec) until 30+ days of paper-trade data clears the bar.

**Architecture:** New `app/trading/`, `app/secrets/`, `app/telegram/` packages. Each phase is one PR, each PR is independently mergeable. Live execution never fires until Phase J wires the kill switch off — the rest of the work can ship in any order.

**Tech Stack:** Python 3.11 + FastAPI + asyncpg/SQLAlchemy. Cryptography for AES-256-GCM vault. pyotp for TOTP. python-telegram-bot is already in use (SP-4 Phase E).

**Spec:** [docs/superpowers/specs/2026-05-03-autonomous-trading-design.md](../specs/2026-05-03-autonomous-trading-design.md)

---

## Phase order + dependencies

```
A (DB schema)
├─ B (modes + gates)        — depends on A
├─ C (leverage + sizing)    — independent
├─ D (kill switches)        — depends on A
├─ E (vault + TOTP)         — depends on A
├─ F (live binance client)  — independent
├─ G (telegram approve)     — depends on B, F
├─ H (tax events)           — depends on A
├─ I (UI: tab + settings)   — depends on B, D, E
└─ J (integration + safety) — depends on ALL
```

Phases A–H + I can ship in any order in parallel sessions. Phase J is the only one that must come last.

---

## Phase A — Database schema

**Files:**
- Create: `backend/alembic/versions/2026_05_09_0016_sp8_trading_tables.py`
- Test: `backend/tests/integration/test_sp8_migrations.py`

Spec §14 enumerates every column. This phase is purely DDL: it creates the tables, adds the `users.trading_mode` / `users.totp_secret_encrypted` / `users.telegram_chat_id` columns, and seeds the `kill_switch_state` table with each user's default switches.

- [ ] **Step A1: Write the failing migration test**

```python
# tests/integration/test_sp8_migrations.py
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_trading_mode_column_exists(real_pg_session: AsyncSession):
    rows = (await real_pg_session.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='users' AND column_name='trading_mode'"
    ))).all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_live_trades_table_exists(real_pg_session: AsyncSession):
    cols = {r.column_name for r in (await real_pg_session.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='live_trades'"
    ))).all()}
    assert {"binance_order_id", "leverage", "row_hash", "prev_hash"} <= cols


@pytest.mark.asyncio
async def test_tax_events_table_exists(real_pg_session: AsyncSession):
    cols = {r.column_name for r in (await real_pg_session.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='tax_events'"
    ))).all()}
    assert {"tds_owed_inr", "fifo_match_id", "row_hash"} <= cols
```

- [ ] **Step A2: Write the migration**

Implements every CREATE TABLE / ALTER TABLE from spec §14. Hash-chained tables (`live_trades`, `tax_events`, `mode_change_log`, `hardware_confirms`) get the same `prev_hash` / `row_hash` columns as the existing `predictions` / `paper_trades` chain so SP-7's `verify_chain` worker picks them up automatically.

- [ ] **Step A3: Run migration, run test**

```bash
cd backend && alembic upgrade head
pytest tests/integration/test_sp8_migrations.py -v
```

Expected: all 3 pass.

- [ ] **Step A4: Commit**

`feat(sp-8/A): DB schema for trading_mode + live_trades + tax_events + audit tables`

---

## Phase B — Mode state machine + promotion gates

**Files:**
- Create: `backend/app/trading/__init__.py`
- Create: `backend/app/trading/modes.py`
- Create: `backend/app/trading/promotion.py`
- Test: `backend/tests/unit/test_trading_modes.py`
- Test: `backend/tests/unit/test_trading_promotion.py`

`modes.py` exposes `get_mode(user_id)`, `set_mode(user_id, new_mode, triggered_by, reason)`. The setter enforces the gate (§4) and writes a `mode_change_log` row. Downgrades are always allowed; upgrades require promotion gates passed.

`promotion.py` exposes `compute_gates(session, user_id, window_days=30 | 90) -> GateSnapshot` — reads `paper_trades` (and later `live_trades`) over the rolling window, computes Sharpe / MaxDD / WinRate / ProfitFactor, returns a snapshot.

- [ ] **Step B1: Write failing tests for compute_gates**

Cover: empty window returns zeros, all-winning trades produces Sharpe>0, one regime that should fail the threshold returns `passes=False` with the reason.

- [ ] **Step B2: Implement compute_gates**

Pure stats over a closed-trade list. No Binance dependency, no clock dependency (takes `now` as a param).

- [ ] **Step B3: Write failing tests for set_mode**

Cover: downgrade always succeeds; upgrade fails when gates not met; upgrade succeeds + writes `mode_change_log` row when gates met; concurrent upgrades on the same user are serialised.

- [ ] **Step B4: Implement set_mode**

Reads gate snapshot, gates the upgrade, writes the audit row inside the same transaction.

- [ ] **Step B5: Commit**

`feat(sp-8/B): mode state machine + promotion gate engine`

---

## Phase C — Adaptive leverage + position sizing

**Files:**
- Create: `backend/app/trading/leverage.py`
- Create: `backend/app/trading/position_sizing.py`
- Test: `backend/tests/unit/test_trading_leverage.py`
- Test: `backend/tests/unit/test_trading_position_sizing.py`

Pure functions. `leverage.recommended_leverage(margin_usdt, sl_distance_pct, hard_cap=10) -> int` — exact spec §6.1 implementation. `position_sizing.compute_position(user_settings, portfolio_value, mode='fixed' | 'percent') -> float` — spec §5.

- [ ] **Step C1: Write failing tests for the worked examples in spec §6.2**

Eight rows in the table — one test each. Include the edge cases (sl_distance=0, hard_cap=1, hard_cap=20).

- [ ] **Step C2: Implement leverage.py** (~10 lines, exactly the spec formula)

- [ ] **Step C3: Write failing tests for position_sizing.py**

Cover both modes + all four percent-mode tiers from spec §5.2.

- [ ] **Step C4: Implement position_sizing.py**

- [ ] **Step C5: Commit**

`feat(sp-8/C): adaptive leverage + position sizing`

---

## Phase D — Kill switches engine

**Files:**
- Create: `backend/app/trading/kill_switches.py`
- Test: `backend/tests/unit/test_kill_switches.py`

Each switch is a class with `should_trip(state) -> bool` and `name`. Enumerates the 6 switches from spec §11.1. The engine polls `live_trades` + portfolio state every 30s when any user is in non-Manual mode and writes to `kill_switch_state` when a trip happens.

Auto-demotion (§4.4) lives in this module too — daily-loss>5% or 10-consecutive-losses triggers a `mode.set_mode(...)` call internally.

- [ ] **Step D1: Write failing tests for each switch's trip logic**

Daily loss, consecutive losses, network outage (mock heartbeat), slippage, liquidation-near, funding rate.

- [ ] **Step D2: Implement each switch class**

- [ ] **Step D3: Write failing tests for the orchestrator (poll + trip + demote)**

- [ ] **Step D4: Implement the orchestrator**

- [ ] **Step D5: Commit**

`feat(sp-8/D): configurable kill switches + auto-demotion engine`

---

## Phase E — API key vault + TOTP

**Files:**
- Create: `backend/app/secrets/__init__.py`
- Create: `backend/app/secrets/vault.py`
- Create: `backend/app/secrets/totp.py`
- Create: `tools/secrets/encrypt.py` (operator-side helper)
- Test: `backend/tests/unit/test_secrets_vault.py`
- Test: `backend/tests/unit/test_secrets_totp.py`

`vault.py`: AES-256-GCM with PBKDF2-HMAC-SHA256 (200k iterations) for passphrase derivation. Reads `secrets.enc` at startup; passphrase comes from env var `MASTER_PASSPHRASE` (already exists per docker-compose) OR stdin prompt fallback. Decrypted secrets live in process memory only.

`totp.py`: pyotp wrapper with 30s rotation, 30s grace window. `setup()` returns `(secret, qr_code_png_bytes, backup_codes)`. `verify(secret, code) -> bool`.

`tools/secrets/encrypt.py`: CLI that prompts for plaintext keys + passphrase, writes `secrets.enc`. Run on the operator's laptop; never on the server.

- [ ] **Step E1: Failing tests for round-trip encrypt/decrypt**

- [ ] **Step E2: Implement vault.py**

- [ ] **Step E3: Failing tests for TOTP setup, verify, grace window, lockout (5 fails)**

- [ ] **Step E4: Implement totp.py**

- [ ] **Step E5: Implement tools/secrets/encrypt.py**

- [ ] **Step E6: Commit**

`feat(sp-8/E): AES-256-GCM API key vault + TOTP hardware-confirm`

---

## Phase F — Binance live client

**Files:**
- Create: `backend/app/exchanges/__init__.py`
- Create: `backend/app/exchanges/binance_live.py`
- Test: `backend/tests/integration/test_binance_live_testnet.py`

Wraps the existing `app/data/adapters/binance.py` for **placing orders**. Verifies API key permissions (§9.3) on first call, refuses to start if withdrawal is enabled. All tests run against Binance Futures testnet.

- [ ] **Step F1: Failing test — verify_permissions raises if withdrawal enabled**

(Mock the testnet response.)

- [ ] **Step F2: Failing test — place_order returns the expected schema**

- [ ] **Step F3: Failing test — close_position handles partial fills**

- [ ] **Step F4: Implement against Binance testnet (real network — gated by TESTNET env var)**

- [ ] **Step F5: Commit**

`feat(sp-8/F): Binance Futures live order client (testnet-validated)`

---

## Phase G — Telegram per-trade approve

**Files:**
- Modify: `backend/app/ops/telegram_bot.py` (extend existing Phase E telegram for brain checkpoint approval)
- Create: `backend/app/telegram/signals.py`
- Test: `backend/tests/unit/test_telegram_signals.py`

The existing Phase E code (PR #44) already has inline-button + 7-day timeout reconciler for brain checkpoint approval. Extend with the per-trade message format from spec §7.2 + `+1×` / `-1×` / `Custom` buttons + 30s auto-skip per §7.4.

- [ ] **Step G1: Failing test — message format matches spec §7.2 byte-for-byte**

- [ ] **Step G2: Failing test — +1× button re-renders with new leverage math**

- [ ] **Step G3: Failing test — auto-skip after 30s writes 'timeout' to telegram_signals**

- [ ] **Step G4: Implement signals.py + extend telegram_bot.py**

- [ ] **Step G5: Commit**

`feat(sp-8/G): Telegram per-trade approve flow with adjustable leverage`

---

## Phase H — Tax events (FIFO + INR + ITR-3 export)

**Files:**
- Create: `backend/app/trading/tax/__init__.py`
- Create: `backend/app/trading/tax/tax_events.py`
- Create: `backend/app/trading/tax/fifo_matcher.py`
- Create: `backend/app/trading/tax/inr_converter.py`
- Create: `backend/app/trading/tax/itr_export.py`
- Test: 4 corresponding test files in `backend/tests/unit/`

Spec §8 has the exact column shape, FIFO algorithm, and ITR-3 CSV format.

- [ ] **Step H1: Failing test — FIFO produces correct cost basis for hand-computed 10-trade scenario**

- [ ] **Step H2: Implement fifo_matcher.py**

- [ ] **Step H3: Failing test — inr_converter caches per-minute, falls back to last-known on API error**

- [ ] **Step H4: Implement inr_converter.py**

- [ ] **Step H5: Failing test — tax_events writes hash-chained row with all required columns**

- [ ] **Step H6: Implement tax_events.py**

- [ ] **Step H7: Failing test — itr_export produces a CSV that round-trips through pandas with no schema warning**

- [ ] **Step H8: Implement itr_export.py**

- [ ] **Step H9: Commit**

`feat(sp-8/H): tax events + FIFO + INR conversion + ITR-3 CSV export`

---

## Phase I — UI: Autonomous Trading tab + settings

**Files:**
- Create: `frontend/src/tabs/AutonomousTrading/index.tsx`
- Create: `frontend/src/tabs/AutonomousTrading/panels/{ModeSwitcher,GateStatus,LivePositions,KillSwitches,TaxExport,RecentActivity,Settings}.tsx`
- Modify: `frontend/src/App.tsx` (add tab route)
- Test: 7 component test files

Every panel is read-only over WebSocket + REST endpoints from earlier phases. The Settings panel is the only one with mutations (mode change, kill-switch toggle, leverage cap edit) — each mutation hits the hardware-confirm flow.

- [ ] **Step I1: Failing test — ModeSwitcher renders 3 buttons with correct lock icons when gates unmet**

- [ ] **Step I2: Implement ModeSwitcher**

- [ ] **Step I3: Failing test — GateStatus shows current rolling-window stats vs threshold**

- [ ] **Step I4: Implement GateStatus**

- [ ] **Step I5..I12** (LivePositions, KillSwitches, TaxExport, RecentActivity, Settings — same pattern)

- [ ] **Step I13: Commit**

`feat(sp-8/I): Autonomous Trading UI tab + settings panel`

---

## Phase J — Wire everything; safety pre-flight; integration tests

**Files:**
- Modify: `backend/app/main.py` (start kill_switches polling task; start telegram-approve worker; start liquidation monitor)
- Create: `backend/app/trading/preflight.py` — boot-time validation
- Create: `backend/tests/integration/test_sp8_e2e.py`

Pre-flight checks at startup (raise + refuse to start if any fails):
1. Vault is decryptable with `MASTER_PASSPHRASE`
2. Binance API key permissions match §9.3 exactly
3. All hash chains intact (calls SP-7's `verify_chain`)
4. Migration `2026_05_09_0016_sp8_trading_tables` is applied

E2E test suite uses Binance testnet + a fake Telegram bot to walk through:
- User flips Manual → Telegram-approve (gates met)
- Signal fires → message sent → user approves with +1× → order placed → trade closes → tax_event written
- Kill switch trips → mode auto-demotes → Telegram alert fires
- User flips back to Manual

- [ ] **Step J1..Jn** (10–15 steps to wire + write the e2e suite)

- [ ] **Step J-final: Commit**

`feat(sp-8/J): wire all phases + boot-time pre-flight + e2e suite`

---

## Operator setup after Phase J merges

This is what the user does ONCE, after all PRs are merged:

1. Generate Binance Futures **testnet** API key with: Reading + Futures, NO Withdrawals, NO Transfers, IP whitelist = Hetzner public IP
2. On laptop: `py -3.11 tools/secrets/encrypt.py` — paste keys, set strong passphrase
3. Commit `secrets.enc` to repo (encrypted; safe to commit)
4. Set `MASTER_PASSPHRASE=<your-passphrase>` in `/opt/trading-radar/.env` on Hetzner
5. `docker compose restart backend` — vault unlocks, secrets load
6. UI shows mode = Manual, all gates listed with current stats
7. Wait for paper trades to accumulate to gate thresholds (30 days + 100 trades for Telegram-approve)
8. Once gates met, hardware-confirm + flip to Telegram-approve mode
9. Repeat for Fully-auto after 90 days + 300 trades

**No live money flows until step 8.** Code can ship in any order during phases A–I; the gates make accidents impossible.

---

## Estimated calendar time

- Phases A–I: ~2–4 days of focused work each (parallel via subagents)
- Phase J: 1 day (wiring + e2e is fiddly)
- Promotion gate wait: 30 days minimum (Telegram-approve), 90 days minimum (Fully-auto). This is BIOLOGICAL — no amount of engineering compresses it.

So shippable infrastructure: **~2 weeks of dev work**. Live money: **3+ months from now**.
