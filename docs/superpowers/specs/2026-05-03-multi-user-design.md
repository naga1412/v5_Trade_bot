# Multi-User Design Spec

**Date:** 2026-05-03
**Status:** Draft, awaiting user review
**Implementation target:** Sub-project SP-0.7 (after SP-0.5 Bot Status tab; before SP-1)
**Depends on:** SP-0 (current — Cloudflare Access + Google SSO already wired)
**Companion spec:** `2026-05-03-autonomous-trading-design.md`
**Future SaaS hooks:** All design choices preserve a clean migration path to multi-tenant SaaS without rewrites.

---

## 1. Purpose

Add support for **multiple users** (the admin/owner + invited friends) to trading-radar without building a custom auth system. Use the existing Cloudflare Access + Google SSO infrastructure as the identity provider. Add per-user data isolation for trading-related tables while keeping analysis infrastructure (chart, scoring, predictions) shared.

### Non-goals

- **No custom userid/password authentication.** Cloudflare Access handles all login.
- **No subscription billing.** Deferred until first paying customer signal (separate future spec).
- **No password storage.** No password = no breach risk.
- **No multi-tenancy beyond a small friend group.** Designed for ~5–10 users; not 1000.

---

## 2. Locked decisions (from brainstorm)

| Decision | Value |
|---|---|
| Identity provider | **Cloudflare Access + Google SSO** (already configured at `ajiyuva.cloudflareaccess.com`) |
| Data isolation level | **Option B — shared analysis infrastructure, isolated trading per user** |
| Bootstrap admin | **First user to log in** (email = `nagarajan1998.yuva@gmail.com`) gets `is_admin=true` automatically |
| Auth integration | **Backend reads `Cf-Access-Jwt-Assertion` header**, extracts email, looks up or creates `users` row |
| Friend invite | **Admin-driven** — admin invites by email, then adds to Cloudflare Access policy (manual or via CF API) |
| Cloudflare API | **Manual default** (admin pastes email into CF dashboard); **API-driven opt-in** later |
| Per-user secrets storage | Each user has their **own encrypted Binance API keys, Telegram bot, TOTP secret** in their user row |
| Per-user brain | Each user has their **own RL adapter** trained on their own paper-trade outcomes (when SP-4 ships) |

---

## 3. User table schema

```sql
CREATE TABLE users (
    id BIGSERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,            -- from Cloudflare JWT
    display_name TEXT NOT NULL,             -- from Google profile, editable
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    -- Trading state (per-user, populated when autonomous trading ships)
    trading_mode TEXT NOT NULL DEFAULT 'manual'
        CHECK (trading_mode IN ('manual', 'telegram-approve', 'fully-auto')),
    position_sizing_mode TEXT NOT NULL DEFAULT 'fixed'
        CHECK (position_sizing_mode IN ('fixed', 'percentage')),
    fixed_size_min_usdt DOUBLE PRECISION DEFAULT 20.0,
    fixed_size_max_usdt DOUBLE PRECISION DEFAULT 50.0,
    max_concurrent_positions INTEGER DEFAULT 5,
    max_leverage_cap INTEGER DEFAULT 10,

    -- Per-user encrypted secrets (NULL until user configures them)
    binance_api_key_encrypted TEXT,
    binance_api_secret_encrypted TEXT,
    telegram_bot_token_encrypted TEXT,
    telegram_chat_id TEXT,
    totp_secret_encrypted TEXT,
    totp_backup_codes_encrypted TEXT,

    -- Quiet hours (per-user)
    quiet_hours_start TIME DEFAULT '23:00',
    quiet_hours_end TIME DEFAULT '07:00',
    quiet_hours_enabled BOOLEAN NOT NULL DEFAULT TRUE,

    -- Lifecycle
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login TIMESTAMPTZ,
    invited_by BIGINT REFERENCES users(id),    -- which admin invited this user
    notes TEXT                                  -- admin's private notes about user
);
CREATE INDEX users_email_idx ON users (email);
CREATE INDEX users_is_active_idx ON users (is_active) WHERE is_active = TRUE;
```

The `*_encrypted` columns hold AES-256-GCM ciphertext, decrypted with the master passphrase entered at backend startup (per autonomous trading spec §9).

---

## 4. Identity flow

### 4.1 Login sequence

```
[Browser] → GET https://trading-radar.cryptotradebotai.com/
         ↓
[Cloudflare Access] checks if user has valid JWT cookie
   - No JWT → redirect to Google SSO → user signs in → redirect back with JWT
   - Has JWT → forward to origin
         ↓
[Cloudflare Tunnel] forwards request to Oracle host (with Cf-Access-Jwt-Assertion header)
         ↓
[Backend FastAPI] dependency `require_cf_user`:
   1. Extract JWT from header
   2. Verify signature against Cloudflare's JWKS
   3. Extract email from JWT payload
   4. Lookup `users` table by email
      - Found + active → attach user object to request
      - Found + inactive → return 403
      - Not found → run "first-time login" handler (see §4.2)
         ↓
[Route handler] uses `current_user.id` to filter all queries
```

### 4.2 First-time login handler

When a JWT email doesn't match any `users` row, the backend:

1. **If users table is empty** (literally the first user ever):
   - Create user with `is_admin=true`, `is_active=true`, `display_name` from Google JWT
   - Log to `audit_log` as `bootstrap_admin_created`
   - Allow login

2. **If users table is NOT empty AND email is in `pending_invitations`**:
   - Create user with `is_admin=false`, `is_active=true`, link to `invited_by`
   - Mark invitation as `accepted_at = now()`
   - Allow login

3. **If users table is NOT empty AND email is NOT in pending_invitations**:
   - Return 403 with body `{"error": "Account not invited. Contact your administrator."}`
   - Log to `auth_violations` (someone got past CF Access but isn't in our DB — possible CF policy mismatch or misconfiguration)

This means an attacker would need to (a) compromise Cloudflare Access AND (b) be in our `pending_invitations`. Defense in depth.

### 4.3 `pending_invitations` table

```sql
CREATE TABLE pending_invitations (
    id BIGSERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT,
    invited_by BIGINT NOT NULL REFERENCES users(id),
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    invited_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    accepted_at TIMESTAMPTZ,                    -- NULL until first login
    cf_access_added BOOLEAN NOT NULL DEFAULT FALSE,
    notes TEXT
);
```

When admin invites a friend, a row is created here. When friend logs in, row gets `accepted_at` populated and a corresponding `users` row is created.

### 4.4 Dev-mode bypass

In `ENV=development`, the existing dev bypass (per SP-0 spec §2.6) is preserved. The bypass user is now mapped to a stable `dev@local` user row created on demand:

```python
async def require_cf_user_or_dev(...) -> User:
    if settings.env == "development":
        return await get_or_create_user("dev@local", display_name="Dev User", is_admin=True)
    # ... normal flow
```

---

## 5. Roles & permissions

Two roles only: **`admin`** and **`user`**. Keep it simple.

### 5.1 What every user can do

- View Tab 1 (chart, scoring, predictions, ghost candles) — shared analysis
- View Tab 2 (Paper Trading Lab) — **only their own paper trades**
- View Tab 3 (Scanner) — shared scanner output
- View Bot Status tab — **only their own simulated/real signals**
- View Autonomous Trading tab — **only their own settings, mode, positions, secrets**
- Manually place paper trades (Tab 1 "Place Trade" button)
- Configure their own trading mode, kill switches, position sizing, quiet hours
- Configure their own Binance keys, Telegram bot, TOTP
- Run their own ITR-3 tax export
- Read-only access to global system status (e.g., "scanner is operating normally")

### 5.2 What only admin can do (in addition to above)

- See **Users management page** under Settings → Admin
  - List all users + last_login + active state
  - Invite new user (creates `pending_invitations` row)
  - Toggle `is_active` on any user (revoke access)
  - Toggle `is_admin` on any user (promote/demote)
  - "View as user" mode (impersonate — see Tab 1 / Tab 2 from their perspective; admin actions log this)
  - Edit user notes
- See **Global audit log** (all hash-chained tables across all users)
- See **System health dashboard** (memory, disk, container statuses, scanner queue depth)
- **Kill switch override** — can freeze any user's autonomous trading
- **System-wide kill** — freeze ALL users' autonomous trading at once (e.g., for emergency maintenance)

### 5.3 What NO ONE can do (no UI, no API)

- Withdraw funds (API key permission disabled — bot can't even attempt)
- See another user's encrypted secrets (decryption keyed on `user_id` + master passphrase)
- See another user's positions/trades unless they're admin

---

## 6. Friend invite flow

### 6.1 Manual flow (default, no extra config)

```
1. Admin → Settings → Admin → Users → "+ Invite User"
2. Form:
   - Email: friend@gmail.com  (must be a Google account email)
   - Display name: "John Doe"
   - Make admin? [no checkbox, default false]
   - Notes: "School friend, started Apr 2026"
3. Click "Invite"
4. Backend creates pending_invitations row
5. App displays modal:
   ┌─────────────────────────────────────────────┐
   │  Step 2 of 2: Add to Cloudflare Access      │
   ├─────────────────────────────────────────────┤
   │  Open Cloudflare Zero Trust dashboard:      │
   │  → Access → Applications                    │
   │  → trading-radar → Policies                 │
   │  → "only-me" policy → Edit                  │
   │                                             │
   │  Add this email to "Include":               │
   │     friend@gmail.com   [Copy]               │
   │                                             │
   │  After adding, click below.                 │
   │                                             │
   │  [✓ I've added the email to CF Access]     │
   └─────────────────────────────────────────────┘
6. Admin clicks confirmation → cf_access_added = true
7. Admin sends friend the URL: https://trading-radar.cryptotradebotai.com
8. Friend clicks URL → Google sign-in → land on Tab 1 → users row created automatically
9. Admin's Users page shows green checkmark next to friend's name
```

### 6.2 API-driven flow (opt-in, requires CF API token)

If admin has stored a Cloudflare API token in encrypted vault, the invite flow skips the manual step:

```
1. Admin → "+ Invite User"
2. Form (same as above)
3. Click "Invite"
4. Backend:
   - Creates pending_invitations row
   - Calls Cloudflare API: PUT /accounts/{id}/access/policies/{id}
     adding the email to the existing "only-me" policy
   - On success: cf_access_added = true
5. App displays: "Invited! Send friend this URL: https://trading-radar..."
```

The CF API token requires the `Access: Apps and Policies: Edit` permission only. Stored in master passphrase vault per autonomous trading spec §9.

### 6.3 Revocation flow (mirrors invite)

To revoke a user:
1. Admin → Users → click user → "Deactivate"
2. Confirmation dialog: "Friend will lose access immediately. Their data is preserved. Continue?"
3. Backend sets `is_active=false`
4. (Manual mode) App displays: "Now remove email from CF Access policy" + copy button
5. (API-driven mode) Backend auto-removes via CF API
6. Friend's next request returns 403; existing JWT cookie still valid until expiry but useless

---

## 7. Per-user data isolation

### 7.1 Tables that get a `user_id` column (per-user)

| Table | Reason |
|---|---|
| `predictions` | Each user might be using a different brain adapter (different scores per user) |
| `paper_trades` | Each user's own simulated trades |
| `live_trades` | Each user's real money trades |
| `tax_events` | Each user's tax obligations |
| `telegram_signals` | Each user gets their own Telegram messages |
| `hardware_confirms` | Per-user TOTP usage |
| `mode_change_log` | Each user's mode changes |
| `kill_switch_state` | Per-user kill switch settings |
| `pending_invitations` | Tracks who invited who |
| `audit_violations` | Per-user tampering detection |
| `brain_adapters` | Each user's per-asset LoRA adapter |
| `ui_preferences` | Per-user UI settings |

### 7.2 Tables that stay shared (no `user_id`)

| Table | Reason |
|---|---|
| `ohlcv` | Market data is universal; one copy serves everyone |
| `data_quality_alerts` | Data quality is global (alert applies to all users) |
| `universe_history` | Asset listings are universal |
| `fx_rates` | INR↔USD rates are universal |
| `pattern_stats` (global) | Per-asset/TF pattern accuracy (across all users) |
| `feature_registry` | Feature definitions |
| `brain_checkpoints` | Global brain (per-user adapters layer on top) |
| `regime_eval_set` | Historical regime windows for backtest evaluation |

### 7.3 Query enforcement

Every backend query that touches a per-user table goes through a wrapper:

```python
async def get_user_predictions(session: AsyncSession, user_id: int, symbol: str) -> list[Prediction]:
    """All per-user queries MUST filter on user_id from the request context."""
    return await session.scalars(
        select(Prediction)
        .where(Prediction.user_id == user_id, Prediction.symbol == symbol)
        .order_by(Prediction.ts.desc())
    )
```

Code review checklist item: **"Does this query touch a per-user table without a `user_id` filter?"** Reject any PR that does.

Plus, a runtime guard: a custom SQLAlchemy event listener prints a warning (and in dev mode, raises) if any query against a per-user table has no `user_id` predicate. Catches bugs in development.

### 7.4 Migration path from current single-user state

SP-0 currently has all rows untagged (effectively user_id=1 implicitly). Migration sub-project (part of SP-0.7):

1. Alembic migration adds nullable `user_id` to all per-user tables
2. Data migration script: `UPDATE table SET user_id = 1 WHERE user_id IS NULL` (the bootstrap admin)
3. Alembic migration adds `NOT NULL` constraint
4. Code updates: every query now uses `user_id` from request context

---

## 8. Per-user trading state

### 8.1 Trading mode

Already defined in autonomous-trading-design §3. Stored in `users.trading_mode`. Per user.

### 8.2 Position sizing

Per user. Defaults: fixed $20–$50, $30 default. Per user.

### 8.3 Kill switches

Per user, in `kill_switch_state` table. Each user configures their own thresholds + can disable independently.

### 8.4 Quiet hours

Per user, columns `quiet_hours_start`, `quiet_hours_end`, `quiet_hours_enabled` on `users` table.

### 8.5 Brain adapter (when SP-4 ships)

Each user has their own LoRA adapter on top of the shared global brain:

```sql
CREATE TABLE brain_adapters (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    asset TEXT,                                  -- NULL = global adapter for user
    adapter_data BYTEA NOT NULL,                 -- LoRA weights, ~50KB each
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    trade_count_at_save INTEGER NOT NULL,         -- how many trades trained this version
    eval_sharpe DOUBLE PRECISION,                -- adapter's Sharpe at save time
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    prev_hash TEXT NOT NULL,
    row_hash TEXT NOT NULL UNIQUE
);
CREATE INDEX brain_adapters_user_active_idx ON brain_adapters (user_id, is_active);
```

Cold-start (per autonomous trading spec §15): new users use global brain only until 100 paper trades, then their adapter starts training.

### 8.6 Settings UI per user

Each user sees their own Settings page with their own:
- Trading mode (with locks per gates per autonomous trading spec)
- Position sizing config
- Asset universe whitelist
- Kill switch thresholds
- Quiet hours
- Telegram bot setup
- TOTP setup
- Binance API key entry (encrypted with their personal sub-key derived from master passphrase + user salt)

---

## 9. Admin features

### 9.1 Users management page

Path: `/settings/admin/users`. Visible only to `is_admin=true`.

```
┌─────────────────────────────────────────────────────────────────┐
│  USERS                                          [+ Invite User] │
├─────────────────────────────────────────────────────────────────┤
│  Email                       Last Login          Status   Mode  │
├─────────────────────────────────────────────────────────────────┤
│  nagarajan1998.yuva@gmail.com  Just now           ✅ Active  Manual  [Admin]   ⚙ │
│  john.doe@gmail.com            2026-05-02 14:30  ✅ Active  Manual            ⚙ │
│  jane.smith@gmail.com          2026-04-28 09:15  ✅ Active  Telegram-approve  ⚙ │
│  pending@gmail.com             —                 ⏳ Pending invite              ⚙ │
│  oldfriend@gmail.com           2026-03-12 18:42  ❌ Revoked                     ⚙ │
└─────────────────────────────────────────────────────────────────┘
```

Per-row actions (⚙ menu):
- View as user (impersonate)
- Edit display name / notes
- Toggle is_admin
- Toggle is_active (revoke / restore)
- View their audit trail
- View their open positions (admin can also force-close)

### 9.2 "View as user" (impersonation)

Admin clicks "View as user" → app sets a session flag `view_as_user_id`. UI shows a red banner at top: `🔴 Viewing as john.doe@gmail.com — Exit`.

While impersonating:
- Admin sees data through the friend's lens (Tab 1, Tab 2, Bot Status)
- Admin canNOT change settings (read-only impersonation)
- Admin canNOT trigger trades on the friend's behalf
- Every page load logs `impersonation_event` rows for audit

### 9.3 Global audit log (admin-only)

All hash-chained tables (predictions, paper_trades, live_trades, tax_events, mode_change_log, hardware_confirms, audit_violations) are queryable here in a unified view. Filterable by user, date range, table.

Admin sees everything — including audit trail of their own admin actions (add user, revoke, impersonate, override kill switch).

### 9.4 System health dashboard

- Container statuses (postgres, redis, backend, frontend, cloudflared)
- Memory + disk usage
- Scanner queue depth, latest scan time
- Backup last successful run
- Audit chain verifier last run + result
- DQ alerts in last 24h

### 9.5 System-wide kill

A button: `🛑 FREEZE ALL TRADING (all users)`. Requires hardware confirm. Freezes autonomous trading for every user immediately. Emergency use only.

---

## 10. Cloudflare Access integration

### 10.1 Manual mode (default, no extra config)

Admin manually adds/removes emails to the CF Access policy via the Cloudflare dashboard. The trading-radar UI guides them with copy-paste prompts but doesn't automate it.

Pros: zero CF API setup; works out of the box.
Cons: 30 seconds of admin overhead per invite.

### 10.2 API-driven mode (opt-in)

Admin can enter a Cloudflare API token in their settings (encrypted with master passphrase). When present:

- Invite flow: backend calls `PUT /accounts/{account_id}/access/policies/{policy_id}` to add the email
- Revocation: backend calls the same endpoint to remove
- App periodically reconciles: lists CF Access policy emails, compares to `users.is_active` table, alerts if drift

### 10.3 CF API token permissions required

When admin generates a CF API token:
- **Account → Access: Apps and Policies → Edit**
- (and only this — not Read; not Account Settings; nothing else)

### 10.4 Failure modes

| Failure | Effect |
|---|---|
| CF API down | Manual fallback shown ("Add email manually") |
| CF API token invalid/expired | Banner in admin UI: "Cloudflare API token expired, please refresh" |
| Email added to CF but user row creation failed | Reconciliation job catches drift; admin notified |
| User added to DB but not to CF | Login attempt returns 403; admin notified to add to CF |

---

## 11. Sub-project sequencing

This spec is implemented as **SP-0.7**, between SP-0.5 (Bot Status tab) and SP-1 (ML data pipeline).

Why this order:

1. SP-0 (current) ships with everything as single-user
2. SP-0.5 adds the Bot Status tab — still single-user (no admin features yet)
3. SP-0.7 adds multi-user — wraps everything to date in user-aware queries
4. SP-1 starts ML work; per-user adapters are now possible

Doing multi-user BEFORE SP-1 means the ML data pipeline starts user-aware from day one — no expensive retrofit later.

---

## 12. Implementation cost estimate

- Sub-project size: ~25 tasks (similar to SP-0.5)
- Wall-clock: ~3–5 days of subagent-driven work
- New backend modules: `auth/users.py`, `auth/invitations.py`, `auth/admin.py`
- New frontend: Settings → Admin → Users page; impersonation banner; per-user Settings sub-pages (already mostly designed)
- Database migrations: 1 large migration adding `user_id` columns + creating `users`, `pending_invitations` tables
- Test coverage: per-user query isolation tests are critical (data leakage = security bug)

---

## 13. Cross-cutting policies (compliance with meta-plan §5)

- **§5.14 audit hash chain**: all per-user hash-chained tables include user_id in canonical row hash (so cross-user tampering is detectable)
- **§5.13 backups**: existing pg_dump covers all new tables; no backup changes
- **§2.6 Cloudflare Access**: this design is **the** integration of CF Access into the app's identity model
- **§5.15 rate limits**: per-user rate-limit counters in Redis (separate from per-IP — prevents one user's bot from rate-limiting another user)

---

## 14. Open questions (resolved during implementation)

| # | Question | Resolved during |
|---|---|---|
| 1 | Should impersonation be allowed in production at all? Or admin-only with "I am viewing X" banner forced? | SP-0.7 brainstorm |
| 2 | What happens to a user's open positions when they're deactivated? Auto-close? Leave open? Notify admin? | SP-0.7 brainstorm |
| 3 | Friend's brain adapter inheritance: do they start fresh, or inherit a snapshot from admin's adapter? | SP-4 brainstorm |
| 4 | Tax export per-user OR consolidated for accountant view? | SP-8 (when tax events exist) |
| 5 | If admin invites email already in CF Access policy, do we auto-detect and skip the "add to CF" step? | SP-0.7 implementation |

---

## 15. Acceptance criteria

- [ ] First user to log in with admin email is auto-promoted to `is_admin=true`
- [ ] Subsequent users with no `pending_invitations` row get 403
- [ ] Admin can invite via UI, friend can log in once invited (manual flow)
- [ ] Per-user data isolation verified: friend cannot see admin's paper trades by URL manipulation
- [ ] Admin can impersonate friend; impersonation logged; impersonation cannot trigger trades
- [ ] Admin can revoke; revoked user gets 403 within 60s
- [ ] All per-user tables have `user_id` NOT NULL after migration
- [ ] No SQL query against a per-user table executes without `user_id` filter (runtime guard catches any miss)
- [ ] Admin's "View as user" mode is read-only (cannot change settings, cannot trigger trades)
- [ ] Audit log shows admin actions tagged with admin's user_id, not impersonated user
- [ ] Migration from single-user state completes with zero data loss (existing predictions/trades all assigned to bootstrap admin user)
- [ ] CF API integration works for admin-flow invites + reconciliation catches drift

---

## 16. Future SaaS migration hooks

When this graduates to public SaaS (months/years from now), the migration path:

1. **Add `account_id` column** above `user_id` (account = a billing entity; user = a person within an account)
2. **Add billing tables** (subscriptions, invoices, payment methods)
3. **Replace Cloudflare Access** with a public OAuth flow (e.g., Auth0, Clerk, or roll-your-own)
4. **Add quota enforcement** (per-account limits on assets, API calls, etc.)
5. **Switch to multi-tenant infrastructure** (per-account isolation in scanner, brain adapters)

None of those changes require rewriting this spec's design. The data model already supports per-user isolation; SaaS adds an additional dimension above it.

Estimated SaaS-graduation work: 2–4 weeks if/when first paying customer signal arrives.

---

## 17. Reference

- Companion: `docs/superpowers/specs/2026-05-03-autonomous-trading-design.md`
- SP-0 spec (Cloudflare Access wiring): `docs/superpowers/specs/2026-05-01-trading-radar-meta-plan-design.md` §2.6
- SP-0 Cloudflare Access JWT verifier: `worktrees/sp-0/backend/app/deps.py`

---

**END OF MULTI-USER DESIGN SPEC**
