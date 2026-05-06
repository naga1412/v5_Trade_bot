# SP-PAUSE — Master Pause / Resume

**Date:** 2026-05-06
**Status:** brainstorm — needs user sign-off before plan
**Worktree:** `worktrees/sp-pause/`
**Branch:** `sp-pause/main`

---

## 1. Problem

The user travels and shuts down the laptop. While Oracle Ampere capacity
hasn't been claimed yet, the laptop is the production host. Today there is
no way to put the system into a clean idle state — the trading workers,
news ingest, intermarket worker, and shadow trader keep firing on every
tick, racing the lifecycle of an abrupt shutdown. After Oracle migration
the same need persists: the operator wants a single button to put the
whole platform to sleep (e.g., during a known broker outage, before a
deploy, or when reviewing trade logs without new entries arriving).

## 2. Goal

Add a master pause/resume flag with two surfaces:

- **Backend:** a single boolean (`system:paused`) checked by every
  background worker tick and by request-level middleware on non-admin
  routes. When true: workers idle, predictions return 423 Locked, but
  read-only admin/Bot Status/audit pages still work.
- **Frontend:** a prominent toggle in `/settings` with a status banner
  visible across the app while paused.

Resume = flip the flag, workers wake up on their next scheduled tick (no
restart needed).

## 3. Design

### 3.1 Where the flag lives

**Redis key `system:paused`** (`"true"` / `"false"`). Redis is already in
the lifespan stack and reachable from every worker + the FastAPI app.
Cached in process for 1s to avoid a Redis round-trip on every request.

Why not a Postgres column: workers check this on every tick (5min), routes
check it on every request — Redis is an order of magnitude faster and
already there. The pause state isn't audited; if persistence is needed
later the audit_violations table already tracks admin actions.

### 3.2 Backend module: `app/ops/pause_state.py`

```python
async def is_paused() -> bool: ...           # cached 1s
async def set_paused(paused: bool, *, by: User) -> None: ...
async def pause_event_log() -> list[PauseEvent]: ...   # last 50 toggles
```

State changes go through `audit_violations` with `kind="system_paused"` /
`kind="system_resumed"` and `attempted_email=actor.email` for the audit
chain hook to pick up.

### 3.3 Worker integration

Every long-running worker loop gets one new line at the top of its tick:

```python
if await pause_state.is_paused():
    log.debug("%s: paused, skipping tick", self.name)
    await asyncio.sleep(self.tick_seconds)
    continue
```

Affected workers (5 total):
- `app.news.ingest_worker` (5min crypto + 30min macro)
- `app.news.cleanup_worker` (nightly 04:00 UTC)
- `app.data.intermarket_worker` (5min snapshot)
- `app.data.intermarket_worker._cleanup_loop` (nightly 04:30 UTC)
- `app.shadow.worker` (per-candle event)
- `app.ops.verifier_scheduler` (nightly 03:00 UTC chain check)

The lifespan worker-spawn block stays unchanged — workers stay running, they
just yield on every tick when paused. This is by design: resume must be
instant.

### 3.4 Request middleware

`app/main.py` adds a middleware:

```python
@app.middleware("http")
async def pause_middleware(request: Request, call_next):
    if await pause_state.is_paused():
        path = request.url.path
        # Always allow:
        # - /api/v1/admin/* (admin needs to resume)
        # - /api/v1/health (Cloudflare Tunnel needs this)
        # - /api/v1/me/* (user can still see their own state)
        # - /api/v1/bot-status/* (read-only review)
        # - /api/v1/predictions/list, /shadow_trades/list (read-only)
        # - WebSocket open already gated by allow-list
        # - GET on / and frontend assets
        if not _is_allowed_when_paused(request):
            return JSONResponse(
                {"detail": "system_paused", "since": _since_ts()},
                status_code=423,  # Locked
            )
    return await call_next(request)
```

Allow-list is path-prefix based, simple, hard-coded in the middleware
module.

### 3.5 Admin REST

```
POST   /api/v1/admin/system/pause     — body: {"reason": "..."}
POST   /api/v1/admin/system/resume    — body: {}
GET    /api/v1/admin/system/state     — returns {paused, since, by_email, reason}
GET    /api/v1/admin/system/log       — last 50 pause/resume events
```

`reason` field is required for `pause` (audit trail), optional for resume.
All four routes require `require_admin`.

### 3.6 Frontend

Two components:

**`SystemPauseControl.tsx`** in `frontend/src/Settings/`:
- Big colored button (red when running → "Pause"; green when paused → "Resume")
- Reason textarea required to pause
- Shows current state + "Paused since 12:34 UTC by admin@x.com" when paused
- Polls `/admin/system/state` every 5s

**`PausedBanner.tsx`** rendered globally in `App.tsx`:
- Shows at top of every page when paused
- "System paused. Trading + ingest workers idle. Read-only mode."
- Click → links to `/settings` to resume

### 3.7 WebSocket behavior

Existing live-prediction WS pushes silently stop while paused (the worker
that emits them is the predictor + shadow worker, both already paused).
Connections stay open so resume is instant. No special WS code needed.

## 4. Test plan

| Layer | What |
|-|-|
| Unit | `pause_state.is_paused`/`set_paused` round-trip via Redis fakeredis |
| Unit | `_is_allowed_when_paused()` table-driven path matching |
| Integration | POST `/admin/system/pause` flips state, then `/predict` returns 423 and `/admin/system/state` returns 200 with paused=true |
| Integration | News + intermarket worker tick loops idle when paused (mock Redis paused, run one tick, assert no DB writes) |
| Integration | Audit chain entry created on pause + resume |
| Frontend | Vitest: SystemPauseControl renders + flips state when API mocked |
| Frontend | Vitest: PausedBanner renders only when state.paused=true |

## 5. Phases

| Phase | Scope | Tasks |
|-|-|-|
| **A** | `pause_state.py` module + 1s in-process cache + Redis round-trip | 3 |
| **B** | Audit chain integration (`auth_violations` insert on each toggle) | 2 |
| **C** | Wire into all 5 background workers (one-line tick guard each) | 3 |
| **D** | Pause middleware + allow-list + 423 response + 4 admin REST routes | 4 |
| **E** | Frontend SystemPauseControl + PausedBanner + Vitest specs + ship log | 5 |

Total: ~17 tasks, **~1 day subagent-driven** (smaller than SP-3.5).

## 6. Decisions

| # | Question | Resolution |
|-|-|-|
| 1 | Redis vs Postgres for state? | Redis. Tick rate too high for DB. |
| 2 | Pause kicks open trades? | No. Open paper trades continue exit-monitoring (SL/TP/timeout) but no new entries. |
| 3 | Frontend banner globally vs per-page? | Globally in App.tsx so it's always visible. |
| 4 | Admin can still pause when paused? | Yes, the toggle is itself admin and the allow-list permits it. |
| 5 | What about Cloudflare Tunnel health checks? | `/api/v1/health` always allowed. |
| 6 | What about `/metrics`? | Allowed (Prometheus scrape continues so we see paused-state metrics). |
| 7 | Audit `reason` storage? | Stored in `auth_violations.reason` column (already exists from SP-0.7). |

## 7. Acceptance criteria

- [ ] `POST /admin/system/pause` flips state in Redis, returns 200 with `{paused: true, since, by_email}`
- [ ] After pause, `POST /predict` returns 423 with `{detail: "system_paused"}`
- [ ] After pause, `GET /api/v1/bot-status/overview` returns 200 (read-only allowed)
- [ ] News + intermarket workers idle within one tick (verifiable via log line `paused, skipping tick`)
- [ ] Existing WS connections stay open; new pushes stop until resume
- [ ] `audit_violations` row created on pause AND resume with chained hash
- [ ] Frontend banner visible across all pages when paused
- [ ] Resume button restores normal operation; first new prediction lands within one worker tick
- [ ] No new test failures; CI green
- [ ] Operator log entry

## 8. Out of scope

- Per-component pause (e.g., "pause news but keep trading"). The whole-system flag is the v1 contract.
- Auto-pause on conditions (high error rate, broker outage). v1 is manual only.
- Pause history dashboard. The 50-event log endpoint covers the immediate need; a full UI is follow-up.
- Mobile push notification when state changes. Telegram alert via existing `app.ops.alerts` is enough.
