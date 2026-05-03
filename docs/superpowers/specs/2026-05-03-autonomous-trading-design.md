# Autonomous Trading — Design Spec

**Date:** 2026-05-03
**Status:** Draft, awaiting user review
**Implementation target:** Future sub-project (SP-8 or later, after SP-4 RL brain trains)
**Depends on:** SP-0 (current — paper engine + audit chain), SP-1 (ML data pipeline), SP-4 (RL brain), SP-7 (champion-challenger), Bot Status tab (SP-0.5)
**Authors:** trading-radar / Claude Code

---

## 1. Purpose

Define the contract for **flipping the platform from research-only to autonomous money-trading**. The implementation is deferred until the RL brain (SP-4) is trained AND the platform has accumulated 90+ days of paper-trade history that meets statistical gates. This document specifies what "real trading" means, what gates it must pass, and how it stays safe.

This spec is written *now* to lock in design decisions while the project is small. It's not implemented now — implementing real-money trading on a 3-layer heuristic scoring engine (the current SP-0 state) would lose money. Implementation sub-project starts only after gates are mechanically satisfied.

**Out of scope (deferred to other specs):**
- Multi-user / friends access (separate `multi-user-design.md`)
- SaaS subscription billing (deferred indefinitely until first paying customer signal)
- Forex / commodities trading (would require their own brains; defer to SP-9+)

---

## 2. Trading scope (locked decisions)

### 2.1 Markets

**Crypto only, on Binance Futures (USDT-margined).**

Rationale: The existing data adapter, scoring engine, and Conv-LSTM brain are all crypto-native. Forex/commodities have different price dynamics, different liquidity profiles, and different regulatory burden — they need their own brains and their own specs.

### 2.2 Asset universe

Default: **Top 30 USDT pairs by 30-day rolling volume** on Binance Futures.

- Excludes thinly-traded coins (slippage death zone)
- Excludes scammy low-volume tokens (pump-and-dump)
- Captures ~95% of total crypto volume → tight bid-ask spreads even on $30 trades
- Configurable in settings: top 10 / top 30 / top 50 / custom whitelist

The bot recomputes the top-30 list every 24 hours. New entries are added to the watchlist; falling-out entries are NOT immediately removed if a position is open (closes positions first).

### 2.3 Trade type

**Futures contracts, USDT-margined, 1×–10× leverage** (adaptive per trade — see §6.4).

Rationale: futures = long + short capability (2× the opportunity surface vs spot). USDT margin = simpler accounting than coin-margined.

### 2.4 Direction

**Long + short, asymmetric thresholds.** Per CLAUDE.md hard rule #9: shorts require +2 layer threshold higher than longs (e.g., LONG fires at score > +0.30; SHORT fires at score < -0.50).

Rationale: shorts have asymmetric tail risk (price can rally without limit; falls are bounded). Asymmetric threshold reduces overshorting in choppy markets.

### 2.5 Concurrent positions

**Max 5 open positions at any time.** Configurable 1–10.

At $30/trade × 5 positions = $150 max capital deployed simultaneously. Even in a worst-case "all 5 stop out at the same instant", loss is bounded at ~$7.50.

### 2.6 Per-asset cooldown

**60 minutes after closing a position on an asset, no re-entry on that asset.** Configurable: 0 / 30 / 60 / 240 minutes.

Rationale: prevents whipsaw re-entries after stop-outs.

---

## 3. The three modes

The autonomous trading tab has a **mode selector** with three options:

| Mode | Bot does | Human does | Real money? |
|---|---|---|---|
| **Manual (default)** | Generates signals + displays them | Clicks "Place Trade" button per trade in Tab 1 | If user clicks: yes |
| **Telegram-approve** | Generates signals + DMs you with details + adjustable-leverage buttons | Taps Approve / Skip on each Telegram message within 30s | Yes, only on tap-approve |
| **Fully-auto** | Generates signals AND executes trades automatically | Monitors, reads daily summaries, can `/freeze` anytime | Yes, no per-trade approval |

Manual mode is the default after first deploy. Telegram-approve and Fully-auto unlock progressively, gated by the criteria in §4.

### 3.1 Mode switching rules

- **Always allowed:** downgrade (Fully-auto → Telegram-approve → Manual). Instant, no confirmation required.
- **Upgrade Manual → Telegram-approve:** requires §4.1 gates met + hardware confirm.
- **Upgrade Telegram-approve → Fully-auto:** requires §4.2 gates met + hardware confirm + (configurable) cooling-off period.

### 3.2 What each mode persists

The current mode is per-user (multi-user spec) and stored in `users.trading_mode`. Mode changes are append-only logged in `mode_change_log` with timestamp, old mode, new mode, who performed it, and gate-check snapshot at the time.

---

## 4. Promotion criteria — gates per mode

### 4.1 Telegram-approve mode unlocks when

**All required (rolling 30-day window):**

| Metric | Threshold |
|---|---|
| Continuous paper-trading days | ≥ **30** |
| Closed paper trades in window | ≥ **100** |
| Sharpe ratio (annualized) | ≥ **1.0** |
| Max drawdown | ≤ **12%** |
| Win rate | ≥ **40%** |
| Profit factor (gross profit / gross loss) | ≥ **1.5** |

If any metric drops below threshold during operation, the gate auto-locks. User has to either re-pass gates or manually override (with hardware confirm + flag in audit log).

### 4.2 Fully-auto mode unlocks when

**All required (rolling 90-day window):**

| Metric | Threshold |
|---|---|
| Continuous paper-trading days | ≥ **90** |
| Closed paper trades in window | ≥ **300** |
| Sharpe ratio (annualized) | ≥ **1.5** |
| Max drawdown | ≤ **10%** |
| Win rate | ≥ **45%** |
| Profit factor | ≥ **2.0** |
| All 5 historical regime backtests pass | bull / bear / sideways / high-vol / low-vol |
| Hardware confirm at toggle time | TOTP or Telegram code (either) |

The 5 regime backtests use the eval set defined in SP-1 (5 fixed historical windows representing different market conditions). Each must show positive expectancy.

### 4.3 Cooling-off period

**Default: 0 days (no cooling-off).** Configurable per-mode in settings: `0 / 1 / 3 / 7 / custom`.

If user sets a cooling-off period, after toggling Fully-auto ON the bot still **doesn't trade** for that many days — gives the user a window to change their mind. Trades resume automatically after the period elapses unless user has toggled off.

User-configurable via settings UI; default is OFF as the user explicitly requested.

### 4.4 Auto-demotion triggers

The bot **automatically downgrades** modes if any of these happen:

| Trigger | Demotion |
|---|---|
| Daily loss > 5% of portfolio (NOT just the kill-switch threshold of 2%) | Fully-auto → Telegram-approve |
| 10 consecutive losing trades | Fully-auto → Telegram-approve |
| Promotion gates fall below thresholds for 7 consecutive days | Telegram-approve → Manual |
| Audit chain integrity violation detected | Any mode → Manual + freeze + Telegram alert |

Auto-demotion fires a Telegram alert + email immediately. Re-promotion requires gates met + hardware confirm again.

---

## 5. Position sizing

### 5.1 Default — fixed dollar amount

Default: **$20–$50 per trade margin (configurable; default $30 fixed)**.

Settings UI offers:
- **Fixed amount:** single value, e.g., `$30`
- **Random range:** lower/upper bounds, e.g., `$20–$50` (uniformly random per trade)

Why fixed: at this scale ($30 × 5 max positions = $150 deployed) blowup risk is bounded regardless of portfolio size. Same dollar amount whether portfolio is $1K or $100K.

### 5.2 Alternate — percentage of portfolio (opt-in)

User can switch to **percentage mode** in settings. When percentage mode is enabled, a strict ramp applies:

| Successful real trades | Position size as % of portfolio |
|---|---|
| 0–30 | **0.1%** |
| 30–100 | **0.25%** |
| 100–500 | **0.5%** |
| 500+ | **1.0%** (Half-Kelly cap from original meta-plan) |

A "successful" trade is any closed trade where loss ≤ stop-loss (i.e., didn't gap through stop). Trades that hit TP, stop loss as expected, or close-positive all count.

### 5.3 Default for new users

Fixed mode at **$30 per trade, $20–$50 random range**. Percentage mode is opt-in only.

---

## 6. Adaptive leverage 1×–10×

### 6.1 The math

The bot computes a recommended leverage per trade given:
- Margin: e.g., $30
- Stop-loss distance from entry: e.g., 2% (computed from ATR or pattern)
- Safety buffer: stop-loss must be ≤ 80% of liquidation distance (20% buffer for wicks)

```python
def recommended_leverage(margin_usdt: float, sl_distance_pct: float, hard_cap: int = 10) -> int:
    """
    Compute leverage so:
      - Stop loss is at most 80% of liquidation distance (wick safety).
      - Liquidation never closer than 1.25× the SL distance.
    Returns int leverage in [1, hard_cap].
    """
    if sl_distance_pct <= 0:
        return 1
    # Approximation: liquidation distance (price %) ≈ 1 / leverage (excluding fees/funding)
    # Want: sl_distance_pct ≤ 0.80 / leverage
    # Therefore: leverage ≤ 0.80 / sl_distance_pct
    max_safe = int(0.80 / sl_distance_pct)
    return max(1, min(max_safe, hard_cap))
```

### 6.2 Worked examples (margin $30)

| SL distance | Math max safe lev | Recommended | Liquidation point | Loss at SL |
|---|---|---|---|---|
| 1% | 80× → cap 10× | **10×** | -10% adverse | $3 |
| 2% | 40× → cap 10× | **10×** | -10% adverse | $6 |
| 3% | 26× → cap 10× | **10×** | -20% (at 5×) | $4.50 |
| 5% | 16× → cap 10× | **10×** | -33% (at 3×) | $4.50 |
| 8% | 10× | **10×** | -50% | $4.80 |
| 10% | 8× | **8×** | -100% | $3 |
| 15% | 5× | **5×** | n/a | $4.50 |

### 6.3 Hard limits

- Min leverage: **1×** (always)
- Max leverage: **10×** absolute hard cap (configurable up to 20× with warning shown; never exceeds Binance's allowed max for the symbol)
- Default if math fails: **1×** (always safe)

### 6.4 User override in Telegram approval

Each Telegram approval message shows the recommended leverage with `+1×` / `-1×` adjustment buttons and a `Custom leverage` input. User can override before approving.

In fully-auto mode, the bot uses the recommended leverage with no human input.

---

## 7. Telegram bot integration

### 7.1 Setup

Single Telegram bot, single recipient: the user.

User creates a bot via @BotFather, gets a token, pastes into `.env.enc`. User gets the chat ID by messaging @userinfobot. Both stored as encrypted secrets.

### 7.2 Per-trade message format

For Telegram-approve mode, each candidate trade fires a message like:

```
🔔 LONG  •  BTC/USDT  •  04 May 2026 14:23 UTC
─────────────────────────────────────
Entry:        $78,250
Stop loss:    $76,685  (-2.0%)
Take profit:  $81,450  (+4.1%)
RR ratio:     2.05 : 1
Confidence:   72%

Layer scores:
  L1 macro:   +0.85 (LONG, EMAs aligned)
  L3 momentum: +0.72 (RSI 64, MACD hist+)
  L5 volume:  +0.40 (1.4× avg volume)
  Combined:   +0.66
─────────────────────────────────────
Margin:       $30 USDT
Leverage:     5× (math: max safe 10×, capped at 5× for risk profile)
Position:     $150
Loss at SL:   $3.00 (10% of margin)
Liquidation:  $62,600  (-20% adverse)
Buffer:       10× safety vs SL
Funding rate: -0.012% (you receive funding for shorts)
─────────────────────────────────────
🔗 View on chart with ghost candle:
https://trading-radar.cryptotradebotai.com/tab1/BTC-USDT/1h?signal=abc123
─────────────────────────────────────
[ ✅ Approve 5× ]  [ +1× ]  [ -1× ]
[ ⚙ Custom leverage ]  [ ❌ Skip ]
─────────────────────────────────────
Auto-skip in 30s if no response
```

### 7.3 Button actions

| Button | Action |
|---|---|
| `Approve N×` | Bot places the order at leverage N immediately. Confirmation message sent: "Order placed. Position ID: X" |
| `+1×` / `-1×` | Bot recomputes the message with new leverage (math + position value updated) and re-renders inline. Min 1×, Max 10×. |
| `Custom leverage` | Bot prompts: "Reply with leverage (1-10)". User types e.g. `7`. Re-renders. |
| `Skip` | Trade discarded. Logged as "skipped by user" in `signals` table. |
| Auto-skip on timeout | Trade discarded. Logged as "skipped by timeout (no response in 30s)". |

### 7.4 No-response behavior

**Default: auto-skip after 30s.** Configurable: `15s / 30s / 60s / 5min / never`.

If timeout = `never`, signals stay open until user responds. NOT recommended (signals get stale).

### 7.5 Quiet hours

**Default: 23:00 IST – 07:00 IST.** Fully editable in settings: any time range, or disable entirely.

During quiet hours:
- **Telegram-approve mode:** signals auto-skip (no notification, no trade)
- **Fully-auto mode:** trades still execute (silently) — bot doesn't sleep just because you do
- **Critical alerts** (kill switch trips, liquidation-near, audit violations) override quiet hours

### 7.6 Daily / weekly summary

**Daily 08:00 IST** message:
```
📊 Daily Summary  •  04 May 2026

Yesterday:
  Trades:     7 (4 win, 3 loss)
  P&L:        +$8.40 (+0.84%)
  Win rate:   57%
  Avg RR:     1.85
  
Open positions: 2
  BTC LONG  $78,400 → $79,150 (+0.96%)  [P&L: +$1.20]
  ETH SHORT $3,890 → $3,855 (-0.90%)   [P&L: +$2.70]

Kill switches: all green
Promotion gate: 32 days, 142 trades — Telegram-approve unlocked
Fully-auto:     58/90 days remaining

Mode: Telegram-approve
```

**Weekly Monday 08:00 IST** message: aggregated 7-day stats, equity curve sparkline, top winning + losing assets.

### 7.7 Emergency commands

Always available, any time:

| Command | Effect |
|---|---|
| `/freeze` | Freeze all autonomous trading immediately. Open positions left as-is. |
| `/unfreeze` | Resume autonomous trading. |
| `/closeall` | Cancel all open positions at market price NOW. |
| `/status` | Reply with current open positions + 24h P&L + bot mode + kill switch states. |
| `/kill` | Hard stop: freeze + closeall + auto-downgrade to Manual mode. |

All commands require message author = configured user chat ID. Other users get `Not authorized`.

---

## 8. Tax compliance (India / ITR-3 Schedule VDA)

### 8.1 Per-trade tax event log

Every closed trade generates a row in `tax_events`:

```sql
CREATE TABLE tax_events (
    id              BIGSERIAL PRIMARY KEY,
    trade_id        BIGINT NOT NULL REFERENCES paper_trades(id),  -- or live_trades
    user_id         BIGINT NOT NULL,
    symbol          TEXT NOT NULL,
    direction       TEXT NOT NULL,          -- LONG | SHORT
    quantity        DOUBLE PRECISION NOT NULL,
    entry_price     DOUBLE PRECISION NOT NULL,
    exit_price      DOUBLE PRECISION NOT NULL,
    entry_value_inr DOUBLE PRECISION NOT NULL,   -- cost basis in INR
    exit_value_inr  DOUBLE PRECISION NOT NULL,
    realized_pnl_inr DOUBLE PRECISION NOT NULL,
    tds_owed_inr    DOUBLE PRECISION NOT NULL,   -- 1% of exit_value_inr
    fee_paid_inr    DOUBLE PRECISION NOT NULL,
    leverage        INTEGER NOT NULL,
    exchange        TEXT NOT NULL DEFAULT 'binance',
    fy_year         TEXT NOT NULL,                -- e.g., "FY2026-27"
    closed_at       TIMESTAMPTZ NOT NULL,
    fifo_match_id   BIGINT,                       -- references prior buy that this sell offsets
    prev_hash       TEXT NOT NULL,
    row_hash        TEXT NOT NULL UNIQUE
);
CREATE INDEX tax_events_user_fy_idx ON tax_events (user_id, fy_year);
```

Hash-chained per §5.14 of the meta-plan (audit integrity).

### 8.2 FIFO cost-basis matching

Required by Indian tax law: when you sell, the cost basis is the *first* unit you bought at the matching exchange.

Implementation: maintain a per-asset, per-exchange queue of (timestamp, quantity, cost) tuples. On sell, dequeue from the front to match the sold quantity, computing weighted-average cost. Persist the match in `fifo_match_id`.

### 8.3 INR conversion

All prices/quantities are USDT-denominated on Binance. For tax purposes, convert to INR at the trade timestamp using:

- **Spot rate:** `https://api.exchangerate.host/latest?base=USD&symbols=INR` (free, no key)
- **Cached** in `fx_rates` table per minute (sufficient granularity for tax)
- **Fallback:** if rate API down, use last cached rate + log a `data_quality_alert`

### 8.4 Monthly TDS computation

Monthly cron job (`05:00 IST on day 1 of month`) computes:
- Total TDS owed for previous month
- Per-asset breakdown
- Telegram alert: "TDS for April 2026: ₹485. Deposit before 7th May. Link to Income Tax e-filing."

The bot does NOT pay TDS automatically (that requires depositing to govt portal — outside scope). It tracks the obligation.

### 8.5 Annual ITR-3 export

User triggers in settings: "Download FY tax report". Bot exports:

- **Schedule VDA CSV**: every closed trade, formatted for ClearTax / Quicko / manual ITR upload
- **Capital gains summary**: total realized profit, total TDS deposited (manual entry by user)
- **Chronological ledger** for audit defense

Format compatible with major Indian tax filing tools.

---

## 9. API key vault — encrypted at rest

### 9.1 Storage

Real exchange API keys live in `secrets.enc`, encrypted at rest with AES-256-GCM. Never plaintext on disk.

### 9.2 Encryption flow

**One-time setup** (user runs locally):

```bash
# On laptop, never on Oracle:
py -3.11 tools/secrets/encrypt.py
# Prompts for:
#   - Binance API key (testnet first; live after promotion)
#   - Binance API secret
#   - Telegram bot token
#   - Telegram chat ID
# Asks for a passphrase (min 16 chars, mixed case, special chars)
# Outputs: secrets.enc (committed to git safely — encrypted)
# Passphrase NEVER stored anywhere — only in user's password manager / head
```

**Backend startup** (on Oracle host):

```
1. systemctl start trading-radar-backend
2. Backend reads secrets.enc from disk
3. Backend prompts via stdin: "Enter passphrase to decrypt secrets:"
4. User SSHes in and types passphrase
5. Backend decrypts to in-memory dict (dict.get('binance_api_key') etc.)
6. Plaintext keys NEVER written back to disk
7. Backend marks itself "secrets-ready" → trading mode unlocks
```

If passphrase not entered:
- Backend runs in **paper-only mode** (no real keys = no live trades possible)
- Health endpoint reports `secrets_unlocked: false`
- All other features (UI, Tab 1, scanner) work normally

### 9.3 Required Binance key permissions

Validated programmatically at first use:

- ✅ **Enable Reading** — required (account info)
- ✅ **Enable Futures** — required (placing orders)
- ❌ **Enable Withdrawals** — must be DISABLED (script refuses to start if enabled)
- ❌ **Enable Internal Transfer** — must be DISABLED
- ❌ **Permits Universal Transfer** — must be DISABLED
- ✅ **IP Restriction** — must be ENABLED + whitelist Oracle host's public IP only

The first API call after passphrase decrypt verifies all permissions match. If withdrawal is enabled by accident, backend logs an audit violation, sends Telegram alert, and refuses to start trading.

### 9.4 Key rotation

User regenerates Binance keys every 90 days (Binance recommendation). Process:

1. User generates new keys on Binance (with same restricted permissions)
2. User runs `py -3.11 tools/secrets/encrypt.py` locally with new keys + same passphrase
3. New `secrets.enc` committed + deployed
4. Backend restart with new passphrase prompt
5. Old keys deleted on Binance

Alert at day 80 of cycle: "Binance keys are 80 days old. Rotate within 10 days."

---

## 10. Hardware confirm — TOTP-or-Telegram

### 10.1 Triggers requiring confirm

Any of these actions:

- Mode upgrade (Manual → Telegram-approve OR Telegram-approve → Fully-auto)
- Disabling any kill switch
- Raising daily loss limit above $200
- Increasing max leverage cap above 5×
- Adding a new exchange API key
- Adding a new asset to allowed universe
- Decrypting API key vault (paired with passphrase)

**Explicitly NOT requiring confirm:** `/freeze`, `/closeall`, `/kill` — these are safety actions and must be one-tap for emergency use.

### 10.2 Confirmation methods (either passes)

**Method A — TOTP (primary):**
- Setup: scan QR code with Authy / Google Authenticator → save 10 backup codes
- Use: type current 6-digit code in app prompt
- 30-second rotation; 30s grace window for clock skew

**Method B — Telegram code (fallback):**
- Bot sends 6-character alphanumeric code to user's Telegram DM
- User replies with that code in app prompt within 60 seconds
- One-time use

**Either method valid.** App shows TOTP prompt first (faster). User can switch to Telegram tab if TOTP unavailable.

### 10.3 Notification on every confirm

Every successful or failed hardware-confirm fires a Telegram message:
```
🔐 Hardware confirm just used:
Action: Toggle fully-auto ON
Method: TOTP
Result: SUCCESS
Time: 04 May 2026 14:25 IST
IP: 132.226.X.X (your Oracle host)

If this wasn't you, /kill immediately and rotate credentials.
```

### 10.4 Lockout policy

- 5 failed confirms in 10 minutes → lock the action for 1 hour, send urgent Telegram alert
- 10 failed confirms in 24h → lock all privileged actions for 24h + email alert

---

## 11. Kill switches — configurable safety layer

### 11.1 Defaults

| Kill switch | Default trigger | Effect |
|---|---|---|
| Daily loss | 2% of portfolio OR $200 absolute (whichever first) | Freeze all autonomous trading for 24h |
| Consecutive losses | 5 in a row | Freeze; require manual unfreeze |
| Network outage | 60s without exchange reachability | Cancel all open orders + freeze |
| Slippage | 0.5% greater than expected | Halt + flag |
| Liquidation-near | Open position < 50% buffer to liq | Telegram alert (NOT freeze; lets you decide manually) |
| Funding-rate guard | Funding > 1% per day on open position | Don't open new positions in same direction |

### 11.2 User control

All thresholds are editable in settings. Each can be **disabled entirely** with these constraints:

- Disabling any kill switch requires hardware confirm
- Confirmation dialog warns: *"You are disabling [name]. Risk profile increases. Type DISABLE to confirm."*
- Disabled kill switches show on Bot Status tab as red badges (visible reminder)
- Audit log records every disable / re-enable with timestamp + user

### 11.3 What kill switches do NOT block

- **Manual trades** (Tab 1 "Place Trade" button) — always work, regardless of kill switch state
- **Closing existing positions** — always work
- **Withdrawing funds** — N/A (API key cannot withdraw)
- **Viewing data** — never blocked

Kill switches only constrain the **bot's autonomous decisions**. The user always retains manual control.

---

## 12. UI components

### 12.1 New tab — Bot Status (in scope for SP-0.5, separate spec)

A read-only dashboard showing what the bot is doing / would do. Built first as SP-0.5 to provide the data layer the autonomous tab depends on.

(See `docs/superpowers/specs/2026-05-03-SP-0.5-bot-status-tab-design.md` — to be created next.)

### 12.2 New tab — Autonomous Trading (in scope for SP-8 or later)

Composed of:

- **Mode switcher** at top (3 buttons: Manual / Telegram-approve / Fully-auto, with locks shown for unmet gates)
- **Promotion gate panel** — current state of each gate, days remaining to next mode
- **Live position panel** — open positions, real-time P&L, time-to-liquidation, manual close button
- **Settings panel** — position sizing mode, asset universe, kill switch thresholds, quiet hours, Telegram bot setup, hardware confirm setup, leverage cap
- **Recent activity log** — last 50 signals (approved / skipped / executed), with chart links
- **Tax export panel** — current FY summary, monthly TDS breakdown, "Download ITR-3 export" button

### 12.3 Settings page additions

Under existing Settings → new sub-section "Autonomous Trading":

- Trading mode + reason if locked
- API key vault status (locked / unlocked / not configured)
- TOTP setup (QR code, backup codes regenerate button)
- Telegram bot token / chat ID (enter encrypted)
- Position sizing config
- Kill switch thresholds + enable/disable toggles
- Quiet hours editor

---

## 13. Backend architecture

### 13.1 New modules

```
backend/app/
├── trading/
│   ├── modes.py              # mode state machine + gate checks
│   ├── promotion.py          # gate evaluation logic
│   ├── leverage.py           # adaptive leverage math
│   ├── position_sizing.py    # fixed vs % mode
│   ├── kill_switches.py      # configurable trip logic
│   ├── execution/
│   │   ├── live_engine.py    # real-money execution (mirror of paper_engine)
│   │   ├── order_manager.py  # Binance order lifecycle
│   │   └── liquidation_monitor.py
│   └── tax/
│       ├── tax_events.py     # per-trade tax row generator
│       ├── fifo_matcher.py   # cost-basis FIFO queue
│       ├── inr_converter.py  # FX rate fetch + cache
│       └── itr_export.py     # CSV generation for ITR-3
├── secrets/
│   ├── vault.py              # passphrase-decrypt at startup
│   └── totp.py               # TOTP generation + verify
├── telegram/
│   ├── bot.py                # bot lifecycle (start/stop)
│   ├── signals.py            # send approval messages, handle button callbacks
│   ├── commands.py           # /freeze /unfreeze /closeall /status /kill
│   └── summaries.py          # daily/weekly summary generators
└── exchanges/
    └── binance_live.py       # subset of binance.py with order placement
```

### 13.2 New services

- **Live execution worker** — replaces paper_engine when mode is live; reads decisions from scoring engine, places real orders, tracks position lifecycle
- **Telegram bot service** — long-polling worker that handles incoming user messages and sends outbound notifications
- **Liquidation monitor** — every 30s, polls all open positions; alerts if any approaches liquidation
- **Tax event generator** — runs after every trade close; writes `tax_events` row + updates FIFO queue

### 13.3 Reuse from SP-0

- `core.scoring.aggregator` — already produces the FinalScore the bot trades on (just adds L10 brain output once SP-4 ships)
- `core.execution.paper_engine` — same interface, swap to `live_engine` when mode is live
- `db.audit` — extend hash chain to `live_trades`, `tax_events`, `mode_change_log`
- `core.dataquality.validator` — same data quality gate
- WebSocket infrastructure — same channel; new `bot_activity` topic

---

## 14. Data model — new tables

```sql
-- Mode state (per user)
ALTER TABLE users ADD COLUMN trading_mode TEXT NOT NULL DEFAULT 'manual'
    CHECK (trading_mode IN ('manual', 'telegram-approve', 'fully-auto'));
ALTER TABLE users ADD COLUMN totp_secret_encrypted TEXT;
ALTER TABLE users ADD COLUMN telegram_chat_id TEXT;

-- Mode change audit (hash-chained)
CREATE TABLE mode_change_log (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    old_mode TEXT NOT NULL,
    new_mode TEXT NOT NULL,
    triggered_by TEXT NOT NULL,    -- 'user' | 'auto-demote' | 'admin'
    reason TEXT,
    gate_snapshot JSONB,            -- gate values at change time
    changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    prev_hash TEXT NOT NULL,
    row_hash TEXT NOT NULL UNIQUE
);

-- Live (real-money) trades — mirror of paper_trades schema with extras
CREATE TABLE live_trades (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('LONG','SHORT')),
    margin_usdt DOUBLE PRECISION NOT NULL,
    leverage INTEGER NOT NULL,
    position_value_usdt DOUBLE PRECISION NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    exit_price DOUBLE PRECISION,
    stop_loss DOUBLE PRECISION NOT NULL,
    take_profit DOUBLE PRECISION NOT NULL,
    liquidation_price DOUBLE PRECISION,
    binance_order_id TEXT NOT NULL UNIQUE,
    binance_position_id TEXT,
    opened_at TIMESTAMPTZ NOT NULL,
    closed_at TIMESTAMPTZ,
    pnl_usdt DOUBLE PRECISION,
    pnl_pct DOUBLE PRECISION,
    fees_paid_usdt DOUBLE PRECISION,
    funding_paid_usdt DOUBLE PRECISION,
    exit_reason TEXT,
    mode_at_open TEXT NOT NULL,    -- mode when the trade was placed
    approved_via TEXT,              -- 'telegram-button' | 'auto' | 'manual-button'
    reasoning JSONB,
    prev_hash TEXT NOT NULL,
    row_hash TEXT NOT NULL UNIQUE
);
CREATE INDEX live_trades_user_opened_idx ON live_trades (user_id, opened_at DESC);

-- Telegram signals (audit of every signal sent for approval)
CREATE TABLE telegram_signals (
    id TEXT PRIMARY KEY,            -- e.g., 'sig_abc123' for chart link
    user_id BIGINT NOT NULL,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload JSONB NOT NULL,         -- full message body
    response TEXT,                  -- 'approved-N' | 'skipped' | 'timeout' | NULL
    response_at TIMESTAMPTZ,
    response_leverage INTEGER,
    resulted_in_trade_id BIGINT      -- references live_trades(id)
);

-- Hardware confirm log
CREATE TABLE hardware_confirms (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    action TEXT NOT NULL,
    method TEXT NOT NULL,           -- 'totp' | 'telegram'
    success BOOLEAN NOT NULL,
    ip_address TEXT,
    user_agent TEXT,
    attempted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Kill switch state
CREATE TABLE kill_switch_state (
    user_id BIGINT NOT NULL,
    switch_name TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    threshold_value DOUBLE PRECISION,
    is_tripped BOOLEAN NOT NULL DEFAULT FALSE,
    tripped_at TIMESTAMPTZ,
    tripped_reason TEXT,
    PRIMARY KEY (user_id, switch_name)
);

-- Tax events (see §8.1)
-- (already shown above)

-- FX rates cache
CREATE TABLE fx_rates (
    pair TEXT NOT NULL,             -- 'USDINR'
    ts TIMESTAMPTZ NOT NULL,
    rate DOUBLE PRECISION NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (pair, ts)
);
```

---

## 15. Cross-cutting policies (compliance with meta-plan §5)

- **§5.1 Look-ahead bias:** all trade decisions use only data ≤ current bar close (no peeking)
- **§5.2 Survivorship:** asset universe filter uses point-in-time top-30 list (frozen at decision time, not recomputed retroactively)
- **§5.3 Feature store:** signal generation uses the same `core.features` module as paper trading — no train/serve skew
- **§5.4 Inputs hash:** every `live_trades` row includes the SP-0 inputs_hash that produced its signal
- **§5.5 Reward shaping:** reward function (when live) matches the SP-4 spec — no override
- **§5.7 Cold-start:** new assets in the universe use global brain only (no per-asset adapter) for first 100 trades
- **§5.8 WebSocket reliability:** unchanged — bot uses same WS reconnect+gap-fill as Tab 1
- **§5.9 Data quality gate:** if `data_quality_alert` fires for an asset, bot freezes auto-trades on that asset for 1 hour
- **§5.10 Champion-challenger:** any new model deployed in production runs in shadow mode for 14 days minimum before becoming the default decision-maker
- **§5.13 Backups:** `live_trades` + `tax_events` are included in the hourly pg_dump (already covered)
- **§5.14 Audit hash chain:** all new tables (live_trades, tax_events, mode_change_log, hardware_confirms, kill_switch_state) are hash-chained
- **§5.15 Rate limits:** Binance Futures has separate rate limit pool from spot; bot respects (1200 weight/min)

---

## 16. Failure modes + their handling

| Failure | Detection | Action |
|---|---|---|
| Binance API down | Order placement fails / heartbeat missing >60s | Cancel any partial orders; freeze; Telegram alert |
| Network outage on Oracle | WebSocket disconnect + REST fail | Cancel all open orders preemptively; freeze; alert |
| Disk full on Oracle | pg_dump fails / write fails | Halt trading; alert; cannot resume until cleared |
| API key revoked / wrong permissions | First call after restart fails | Halt; require user to fix + re-decrypt |
| Liquidation imminent (< 30s) | Liquidation monitor flag | Auto-close at market; Telegram alert; log as "liquidation-prevented" |
| FX rate API down | exchangerate.host returns 5xx | Use last cached rate; log dq_alert; continue (rate matters only at tax-time) |
| Audit chain broken | Nightly verifier finds violation | Freeze ALL trading; downgrade to Manual; email + Telegram urgent |
| Bot model produces NaN signal | Signal validation fails | Skip the trade; alert if >3 in 1 hour |

---

## 17. Open questions (resolved during implementation, not now)

| # | Question | Resolved during |
|---|---|---|
| 1 | TOTP backup-codes regeneration UX (which storage location?) | SP-8 brainstorm |
| 2 | Margin transfer between Spot wallet ↔ Futures wallet (auto?) | SP-8 brainstorm |
| 3 | Funding-rate prediction for "should I close before funding?" decisions | SP-9 (advanced ops) |
| 4 | Multi-exchange execution (Bybit fallback if Binance is rate-limited) | SP-9 |
| 5 | Per-asset position size override (e.g., $50 on BTC, $20 on alts) | SP-8 (settings UI) |
| 6 | Stop-loss order TYPE (Market vs Limit, when to use which) | SP-8 |
| 7 | Should bot avoid trading 5 minutes around major economic events (FOMC, CPI)? | SP-9 |
| 8 | Long-term capital gains qualifying period (Section 115BBH treats all crypto as 30%) | n/a — clarified |

---

## 18. Implementation gating (when SP-8 actually starts)

**Pre-conditions (ALL must be true):**

- [ ] SP-0 deployed to production for ≥ 30 days with zero Sev-1 incidents
- [ ] SP-1 (ML data + ghost candles) implemented and passing all tests
- [ ] SP-4 (RL brain) trained, paper-tested, beating equal-weight baseline by ≥ 10% Sharpe
- [ ] SP-7 (champion-challenger) implemented; current brain is "champion"
- [ ] Bot Status tab (SP-0.5) shipped, producing visible "would trade" simulations for ≥ 30 days
- [ ] User has reviewed and explicitly approved this design spec
- [ ] User has Binance Futures account with 2FA + IP restriction set up

If ANY are false, autonomous trading implementation is blocked. The infrastructure can be designed/specced now (this doc), but no code that calls `binance.create_order()` ships until all gates met.

---

## 19. Acceptance criteria (when SP-8 is "done")

- [ ] User can toggle Mode in UI; gates enforce per §4
- [ ] Telegram approval message format matches §7.2 exactly
- [ ] +1×/-1× buttons re-render leverage with updated math (§6.1)
- [ ] Auto-skip on 30s timeout (configurable per §7.4)
- [ ] Quiet hours edit-able and respected (§7.5)
- [ ] Daily summary fires at 08:00 IST (§7.6)
- [ ] All 5 emergency commands work (§7.7)
- [ ] FIFO cost-basis matching produces correct ITR-3 export (§8.5) — verified vs hand-computed for 100 trades
- [ ] API key vault validates withdrawal-disabled at startup (§9.3) — refuses to start if violated
- [ ] TOTP setup + use + Telegram fallback all work (§10)
- [ ] All kill switches trip correctly + can be disabled with confirm (§11)
- [ ] Audit hash chain unbroken across all new tables for 30-day soak
- [ ] Mode-change auto-demote triggers fire correctly (§4.4)
- [ ] Liquidation-near monitor sends alert + auto-close before liquidation (§16)

---

## 20. Reference

- Original meta-plan: `docs/superpowers/specs/2026-05-01-trading-radar-meta-plan-design.md`
- SP-0 implementation plan: `docs/superpowers/plans/2026-05-01-SP-0-tracer-bullet-plan.md`
- Future companion specs (to be written): `multi-user-design.md`, `SP-0.5-bot-status-tab-design.md`

---

**END OF AUTONOMOUS TRADING DESIGN SPEC**
