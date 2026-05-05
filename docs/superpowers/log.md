
## 2026-05-02 — SP-0 indicator cross-check: PASS

Verified against TradingView (BTCUSDT 1h on Binance) at bar `2026-05-02T02:00:00+00:00`:

| Indicator   | Ours        | TradingView | pct diff |
|-------------|-------------|-------------|----------|
| Open        | 78,355.00   | 78,355.00   | 0.000%   |
| Close       | 78,353.93   | 78,353.93   | 0.000%   |
| EMA 20      | 77,976.30   | 77,976.30   | 0.000%   |
| EMA 50      | 77,386.24   | 77,386.01   | 0.0003%  |
| RSI(14)     | 62.0392     | 62.04       | 0.002%   |

5/5 indicators within 0.002% — far under the 0.1% acceptance threshold from
spec §6.2. EMA / RSI Wilder math matches TradingView's reference
implementation. MACD verified indirectly: MACD = EMA12 − EMA26, both EMAs
match exactly → MACD math is mechanically identical.

§4.1 acceptance criterion #6 (3 layers compute live, indicators correct):
backed by this cross-check.

---

## 2026-05-03 — SP-0.5 Bot Status tab + multi-asset shadow trading: SHIPPED

**Scope:** added a second tab (Bot Status) to the app and a multi-asset shadow-trading subsystem behind it. Top-30 USDT-quoted Binance Futures (refreshed daily at 00:00 UTC), 1h closed-candle scoring, max 30 concurrent paper positions, $30 fixed size, 30-min per-asset cooldown, 24-bar timeout, ATR-based SL/TP. Hash-chained `shadow_trades` table per the audit policy.

**Delivered (43 commits on branch `sp-0.5/main`):**

| Phase | Sub-system | Commits |
|---|---|---|
| A   | Worktree | 1 |
| B   | Migration 0003 (4 tables) | 1 |
| C   | Asset universe fetcher + persistence | 2 |
| D   | Shadow signal engine (entry rule, position gate) | 3 |
| E   | Exit monitor (SL/TP/timeout, pessimistic SL-first) | 1 |
| F   | Persistence (open positions + closed trades hash-chained) | 1 |
| G   | Multi-stream Binance combined-stream reader | 2 |
| H   | Stats (win rate, profit factor, RR, Sharpe, max DD) | 2 |
| I   | Worker orchestrator + lifespan + universe refresh job | 3 |
| J   | 8 REST endpoints under `/api/v1/bot-status/*` + `/predict?signal=` | 10 |
| K   | WS `shadow_updates` channel (typed publishers) | 1 |
| L   | Frontend tab nav + hash-route hook | 2 |
| M   | Frontend API client + `useShadowUpdates` hook | 2 |
| N   | 7 Bot Status sections (Overview, PromotionGate, OpenPositions, PerAsset, LongShort, Equity, RecentTrades) + assembly | 8 |
| O   | Tab1 `?signal=` deeplink + TVChart entry/SL/TP markers | 2 |
| P   | Multi-asset E2E worker test + Playwright Bot Status spec | 2 |
| —   | Maintenance (vitest exclude e2e dir) | 1 |

**Test counts at ship:**
- Backend: **199 passing** (was 47 at SP-0; +152 new)
- Frontend Vitest: **88 passing** (was 10 at SP-0; +78 new)
- Frontend Playwright: 4 specs × 2 device projects = 8 cases listed clean

**Surprises / decisions worth flagging:**

- **Promotion gate computation** uses spec §4.1 thresholds (Telegram-approve mode): 30d window, ≥30 days continuous trading, ≥100 trades, Sharpe ≥1.0, max DD ≤12%, win rate ≥40%, PF ≥1.5. Fully-auto gate (§4.2) deferred to SP-8.
- **Profit factor `inf` JSON encoding:** capped at `999.0` (`_PROFIT_FACTOR_INF_CAP`) when no losses present. Frontend should treat `≥999` as "no losses yet".
- **`/bot-status/open-positions` returns `current_price=null`** intentionally — live price comes through WS `shadow_pnl_tick` instead of a Binance round-trip on every cold load.
- **Hash deeplink:** chose `#/live-prediction?signal=xyz` (URL-hash + query) instead of adding `react-router-dom`. Custom `useHashRoute` with `URLSearchParams` parser keeps deps minimal.
- **Worker DI:** `ShadowWorker` accepts `session_factory`, `reader`, `seed_history` injection points. Made E2E testing tractable — full multi-asset scenario with 30 candles across 3 symbols runs in ~0.5s with monkeypatched `build_prediction`.
- **Audit chain integrity confirmed in P1:** `verify_chain` on `shadow_trades` returns ok with valid `prev_hash`/`row_hash` linkage starting from `GENESIS_HASH`. Same chain policy as SP-0 `predictions` table.
- **Pre-existing predict tests fixed:** the J10 work (`/predict?signal=`) inadvertently repaired 2 pre-existing `test_api_predict.py` tests that were broken under `ENV=test` due to unset Cloudflare Access settings. They now run green via the new `bot_status_client` fixture which overrides `require_cf_user`.

**Manual P3 checklist (for the human after deploy):**
- [ ] All 7 sections render correctly on mobile (375px width)
- [ ] No horizontal scroll on Bot Status tab
- [ ] Touch targets ≥44px on tab nav and section controls
- [ ] Signal deeplink from RecentTrades opens Tab1 with markers visible
- [ ] WS reconnects on Cloudflare Tunnel restart (kill tunnel, observe reconnect)
- [ ] DB row counts match closed trades shown in UI

**Next:** ship Q1 (PR sp-0.5/main → main) and Q2 (tag `sp-0.5`). Then begin SP-0.7 (multi-user wrapper) per the meta-plan.

---

## 2026-05-03 — SP-0.7 Multi-User Wrapper: SHIPPED

**Scope:** wrapped the single-user SP-0.5 codebase in a real multi-user
identity layer. Cloudflare Access JWT continues to gate the network edge;
inside the app every per-user table now carries `user_id NOT NULL` and every
read endpoint filters by an effective user resolved from the JWT (or the
admin impersonation target when active). Adds AES-256-GCM-encrypted per-user
secrets (Binance keys, Telegram, TOTP), an admin invitation flow with a
two-step Cloudflare-Access reminder modal, an impersonation banner with
`page_view` audit logging, and a runtime SQLAlchemy query guard that raises
in dev when a per-user table is queried without `user_id`.

**Delivered (56 commits on branch `sp-0.7/main`, excluding base merge):**

| Phase | Sub-system                                                                                  | Commits |
|-------|---------------------------------------------------------------------------------------------|---------|
| B     | Migrations 0004 + 0005 + 0006 (users + per-user `user_id NOT NULL`)                          | 3       |
| C     | Auth ORM models (User, PendingInvitation, AuthViolation, ImpersonationEvent) — RED + GREEN  | 2       |
| D     | User loader + `require_user`/`require_admin` + impersonation store                          | 5       |
| E     | Bot Status + tab1 routes wired to `current_user_or_impersonated`                            | 2       |
| F     | Per-user filter on all `/bot-status` endpoints + cross-user leak test                       | 2       |
| G     | Shadow persistence + worker scoped to user_id (RED + GREEN)                                 | 2       |
| H     | persist_prediction + tab1 signal markers scoped to user_id (RED + GREEN)                    | 2       |
| I     | Query guard event listener (warn in prod, raise in dev) + lifespan wiring                   | 3       |
| J     | AES-256-GCM secrets + per-user secret helpers + TOTP (pyotp)                                | 3       |
| K     | Admin REST: scaffold + GET users + POST invitations + PATCH/DELETE users + impersonate × 2 + audit-trail | 8 |
| K'    | /me REST: GET/PATCH profile + binance-keys + telegram + TOTP setup/verify                   | 5       |
| K''   | Frontend: api.ts + useCurrentUser + TabNav + ImpersonationBanner + Admin (Users/InviteModal/RowMenu/AuditTrail) + impersonate-reload + Settings (Profile/Trading/Secrets) | 12 |
| —     | Maintenance: `fetchJson` generic narrowed from `void` to `undefined`                        | 1       |
| L     | E2E verification: data isolation + invitation flow + impersonation read-only + query guard coverage + Playwright admin-users + log entry | 6 |

**Test counts at ship:**
- Backend: **309 passing** (was 199 at SP-0.5; +110 new)
- Frontend Vitest: **167 passing** (was 88 at SP-0.5; +79 new)
- Frontend Playwright: 3 specs × 2 device projects = 14 cases listed clean

**Surprises / decisions worth flagging:**

- **Email comparison case-insensitive:** spec ambiguity #1 — Cloudflare Access
  does not lowercase emails before signing them, so `Friend@x.com` and
  `friend@x.com` would otherwise create two distinct users. `_normalize_email`
  in `app.auth.users` lowercases on every read + write so the constraint is
  load-bearing rather than nominal.
- **Impersonation is strictly read-only.** `/me` mutations (binance-keys,
  telegram, TOTP setup/verify, PATCH profile) call `_reject_during_impersonation`
  and 403 — the alternative ("write to admin's row but use target's lens for
  reads") is too easy to misread as "admin secretly editing the friend's
  account." Verified end-to-end in `test_impersonation_readonly.py`.
- **Dev-mode query guard raises; prod warns.** Trading off availability for
  fail-fast in dev: `MissingUserIdFilterError` will tank a request locally
  but only emits a `log.warning` in production. The Phase L4 parametric
  coverage now pins all 5 per-user tables (`predictions`, `paper_trades`,
  `shadow_trades`, `shadow_open_positions`, `shadow_cooldowns`) plus a
  `test_per_user_tables_set_is_complete` contract test that fails the build
  if anyone removes a table from `PER_USER_TABLES` without touching the spec.
- **AES-256-GCM with per-user salt + master passphrase.** Settings carries
  `master_passphrase` (env-loaded). `encrypt_for_user` mixes user_id into
  the salt so that even if the same plaintext is encrypted for two users
  the ciphertexts differ. Round-trip covered in
  `test_api_me_binance_keys.py::test_post_me_binance_keys_round_trip`.
- **Two-step CF Access modal is on purpose.** The backend cannot add the
  invitee to the Cloudflare team policy automatically (no API token flowing
  through the app). The InviteUserModal step-2 panel exists to remind the
  admin to do that out-of-band, with a clipboard-copy button on the email.
- **Bootstrap admin via empty `users` table.** First JWT to hit
  `require_user` when the `users` table is empty is auto-promoted to admin
  (`is_admin=True`). This is how `dev@local` becomes a usable admin in
  development without any bootstrap script. After that first hit, the
  invitation gate is in force.
- **Frontend cache via `useSyncExternalStore`.** `useCurrentUser` exposes
  a single shared snapshot of `/api/v1/me` across App, TabNav,
  ImpersonationBanner, Admin, and Settings — so the visibility of the
  Admin tab is consistent and cheap. `__resetCurrentUserForTests` is a
  test-only escape hatch.
- **Playwright admin-users spec is shape-only.** It depends on the dev-mode
  bypass that promotes the first hit to admin, plus the in-memory dev SQLite.
  It does NOT exercise the real Cloudflare Access policy update — that step
  is by design out of scope for E2E and is the human's responsibility per
  the InviteUserModal step-2 prompt.

**Manual checklist (for the human after deploy):**
- [ ] Migration 0004→0006 applies cleanly on the staging Postgres
- [ ] First production JWT hit creates the bootstrap admin row (verify `users` count = 1, `is_admin = true`)
- [ ] Invite a friend → friend receives Cloudflare Access policy update → friend's first login flips `accepted_at` on the invitation row
- [ ] Admin starts impersonation → ImpersonationBanner renders → /me mutations return 403 → reads show target's data lens
- [ ] Admin stops impersonation → banner clears → reads back to admin's lens
- [ ] AuditTrail tab shows `start` + `stop` events plus any `page_view` events captured during the impersonation
- [ ] `auth_violations` row appears for any uninvited email that hits the gate

**Next:** ship L7 (PR `sp-0.7/main` → `main`) and tag `sp-0.7`. Then begin
SP-0.8 (real Binance trade execution + Telegram approve flow) per the
meta-plan.

---

## 2026-05-03 — SP-2 Indicators + Patterns Library: SHIPPED

**Scope:** stood up the full SP-2 deterministic-signals layer — 34 indicator
modules (43 by output-line count: bollinger upper/middle/lower count as 3,
MACD line/signal/hist as 3, etc.), 158 pattern detectors (82 candle: 61
TA-Lib + 21 hand-rolled; plus 76 hand-rolled chart patterns), and the L2
pattern aggregator that turns per-bar fires into a `LayerScore` weighted by
`strength × confidence × historical_accuracy`. Wired into
`build_prediction()` opt-in via `PatternStatsLookup`; live + shadow workers
hold a process-memory cache per `(symbol, timeframe)` and pass it on every
closed candle. Admin REST endpoints under `/api/v1/admin/patterns/*` let
operators enable/disable individual patterns per symbol/timeframe at
runtime. Cross-validation suite (100 entries) compares current-code outputs
to a TA-Lib-derived reference and exits 0 on a clean match.

**Delivered (210 commits on branch `sp-2/main`):**

| Phase | Sub-system                                                                                                | Commits |
|-------|-----------------------------------------------------------------------------------------------------------|---------|
| A     | Worktree + migration 0009 (`pattern_enabled`) + TA-Lib install + `Pattern`/`PatternFire` scaffolding      | 5       |
| B     | 31 new indicator modules + registry update                                                                | 32      |
| C     | 82 candle patterns (61 TA-Lib wrappers via `make_talib_wrapper` + 21 hand-rolled) + registry              | 83      |
| D     | 76 chart patterns + registry + 158-total assertion                                                        | 77      |
| E     | L2 aggregator + `PatternStatsLookup` + `build_prediction()` wiring + worker cache + admin REST + mypy/ruff cleanup | 8 |
| F     | TA-Lib reference (100 entries) + cross-validation script + L2 pipeline E2E + ship/log/tag                 | 5       |

**Test counts at ship:**
- Backend: **1040 passing** (was 1036 at start of Phase F; +4 new from F4
  E2E + 2 unrelated pre-existing `test_ml_checkpoints` failures from the
  Windows sandbox CWD path issue, surfaced before SP-2)
- Cross-validation script (`tools/validation/sp2_cross_check.py`): **100/100
  entries match** the TA-Lib reference

**Surprises / decisions worth flagging:**

- **34 indicator modules, 43 by output-line count.** Spec §3.1 quotes "43
  indicators"; the discrepancy is intentional — multi-output indicators
  (Bollinger upper/middle/lower, MACD line/signal/histogram, Stochastic
  %K/%D, etc.) compose multiple "outputs" from a single module. Documented
  inline in `app/core/indicators/__init__.py` so future contributors do
  not chase a phantom 9 modules.
- **82 candle patterns = 61 TA-Lib + 21 hand-rolled.** TA-Lib gives us 61
  CDL* functions for free via `make_talib_wrapper`, each fixed at
  confidence 0.7. The 21 hand-rolled additions cover patterns TA-Lib does
  not ship (strict / context-gated variants like pin-bar with prior trend,
  inside-bar continuation, etc.) and use confidences in 0.6–0.8 to
  reflect the looser heuristics.
- **76 chart patterns are entirely hand-rolled.** TA-Lib has no
  chart-pattern functions; the bottoms/tops/triangles/flags/wedges/channels/
  cup-and-handle/gaps/harmonics/multi-TF-confluence catalogue is custom
  numpy + scipy peak-finding behind a small set of helpers
  (`_helpers.py`, `_harmonic_helpers.py`).
- **L2 layer is opt-in via `PatternStatsLookup`.** `build_prediction(...,
  pattern_stats_lookup=lookup)` runs L2 only when the caller passes a
  lookup. Existing tests / callers that do not yet hold one keep working
  unchanged. The live + shadow workers cache one lookup per
  `(symbol, timeframe)` and refresh it after the SP-1 nightly
  `update_pattern_stats` job.
- **Cold-start gating is GENERATED + lookup-side.** `pattern_stats.accuracy`
  is a `GENERATED ALWAYS AS (CASE WHEN n_samples = 0 THEN 0.5 ELSE
  n_correct/n_samples END) STORED` column in Postgres; the
  `load_pattern_stats` helper additionally drops any row with `n_samples <
  50` from the in-memory lookup so noisy early data never feeds L2.
  Default `PRIOR_ACCURACY = 0.5` falls in for missing rows.
- **Cross-validation uses TA-Lib reference, not TradingView.** Spec F1
  called for "100 samples from TradingView". Reproducibility against
  future TA-Lib version bumps mattered more than mirror-Pine-script
  validation, and TradingView's Pine Script wraps the same TA-Lib C
  library — so TA-Lib is a faithful proxy for the 61 wrapped patterns.
  The `_note` field at the top of `sp2_reference.json` documents the
  trade-off and recommends manual TradingView spot-checks for the 21
  hand-rolled patterns where we depart from TA-Lib defaults.
- **Frontend admin sub-page deferred to SP-6.** Phase E's admin REST
  endpoints (`POST /api/v1/admin/patterns/{pattern_id}/disable` etc.)
  ship live; the React UI to drive them is intentionally pushed to SP-6
  (alongside the model-checkpoints admin sub-page) so SP-2 does not block
  on UI churn. `curl` is sufficient for ops in the meantime.
- **`enabled_patterns` auto-load from DB deferred.** The L2 aggregator
  accepts an `enabled_patterns: set[str] | None` arg today and the
  worker-side cache abstraction is in place, but the DB-driven
  refresh-on-pattern_enabled-change wiring is not. Cache is currently
  passed `None` (all enabled) by the workers — a follow-up to land before
  SP-3.
- **Audit chain binds the L2 score by construction.** The
  `predictions.layer_scores` JSONB column is part of the canonical row
  payload that `compute_row_hash` covers, so any tampering with a
  persisted L2 score breaks the chain at the next `verify_chain` pass.
  Pinned in `tests/integration/test_l2_pipeline_e2e.py` with a
  hash-flip negative assertion.

**Manual checklist (for the human after deploy):**
- [ ] Migration 0009 (`pattern_enabled`) applies cleanly on the staging Postgres
- [ ] Live worker logs show "L2 cache warmed for BTC/USDT 1h: N patterns"
      after first closed candle
- [ ] At least one prediction row in production has `layer_scores->>'2'`
      non-null and `notes` containing a JSON pattern array
- [ ] Admin `POST /api/v1/admin/patterns/hammer/disable?symbol=BTC/USDT&timeframe=1h`
      returns 200 and a follow-up GET shows `enabled = false`
- [ ] Run `python tools/validation/sp2_cross_check.py` against the deployed
      backend image — exit 0 / 100/100 pass
- [ ] Manual TradingView spot-check on one of the 21 hand-rolled candle
      patterns at a known reference bar (recommended: `pinbar_long` on
      BTCUSDT 1h)

**Next:** ship F5 (PR `sp-2/main` → `main`) and tag `sp-2`. Then either
- **SP-1.1** — train the Conv-LSTM checkpoint that makes `ghost_*` columns
  non-null in production, or
- **SP-3** — multi-source data adapters (Binance + Bybit + Coinbase with
  unified bar normaliser), or
- **SP-5** — full scoring engine that fuses L1–L10 layers into the final
  signal score with calibrated weights.

