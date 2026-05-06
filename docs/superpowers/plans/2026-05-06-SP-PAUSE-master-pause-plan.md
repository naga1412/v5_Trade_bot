# SP-PAUSE — Master Pause / Resume — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Important:** I am running in read-only planning mode and cannot create files. The plan below is the full document content that should land at `worktrees/sp-pause/docs/superpowers/plans/2026-05-06-SP-PAUSE-master-pause-plan.md`. The execution agent (or the user) will save it.

**Goal:** Add a master pause/resume flag with two surfaces — a Redis-backed `system:paused` boolean checked by every long-running background worker tick + a request-level middleware gating non-admin routes — plus a Settings-tab toggle and a global app banner. Resume = flip the flag; workers wake up on their next scheduled tick.

**Spec reference:** `docs/superpowers/specs/2026-05-06-SP-PAUSE-master-pause-design.md`. When this plan and the spec disagree, the spec wins.

**Tech stack:** Python 3.11 / FastAPI / SQLAlchemy 2 (async) / asyncpg · `redis==5.2.1` (already pinned, no actual import yet — SP-PAUSE is the first user) · `fakeredis>=2.20.1` (NEW, test-only) · React 18 / Vite / TS strict / Tailwind · pytest / pytest-asyncio / respx · Vitest

**Cross-cutting policy compliance map:**
- Phase A — `pause_state.py` is the first module to instantiate a Redis client. Lazy-construct one `redis.asyncio.Redis` from `settings.redis_url`; cache the boolean for 1 second in process via a tuple `(value, monotonic_ts)`. No new migration.
- Phase B — Toggles INSERT a row into `auth_violations` (which is the SP-0.7 audit table; spec calls it "audit chain entry" but `auth_violations` itself is not hash-chained — see "Spec gap" note in §0). The existing `_record_violation` helper in `verifier_scheduler.py` is the precedent (`attempted_email='system'`, free-form `reason`). SP-PAUSE writes `attempted_email=actor.email` and `reason="system_paused: <reason>"` / `reason="system_resumed"`, plus `request_path="/api/v1/admin/system/<pause|resume>"`.
- Phase C — Tick guards added to all 5 + 1 long-running loops (news_ingest, news_cleanup, intermarket_snapshot, intermarket_cleanup, shadow_worker, audit_verifier). Guards live above the existing tick body and use `await pause_state.is_paused()` exactly once per iteration.
- Phase D — Middleware is registered before `instrument_app(app)` in `create_app()`; allow-list is a frozen tuple of path prefixes + a few exact-path matches; non-allowlisted requests return `JSONResponse(status_code=423, content={"detail":"system_paused", "since": ...})`. Four admin REST routes live in a new `app/api/routes/admin_system.py` (parallel to `admin_news.py`).
- Phase E — `SystemPauseControl.tsx` lives at `frontend/src/tabs/Settings/SystemPauseControl.tsx` (the actual Settings folder is `tabs/Settings/`, not `src/Settings/`; this matches `Profile.tsx` etc.). `PausedBanner.tsx` lives at `frontend/src/components/layout/PausedBanner.tsx`. Both use plain `useEffect` + `useState` polling — TanStack Query was rejected during SP-3.5 cleanup.

---

## §0. Spec gaps surfaced during planning

These are translated into tasks below; flagged here so the executor knows the context behind each decision:

| # | Gap | Resolution in plan |
|-|-|-|
| 1 | Spec §3.2 + §7 row 7 say "chained hash" / "chained hash entry created on pause AND resume". The existing `auth_violations` table from migration `0002_audit_chain` has **no `prev_hash`/`row_hash` columns** (only `id, attempted_email, attempted_at, reason, jwt_sub, request_path`). | Phase B inserts a plain row matching the `_record_violation` pattern (`attempted_email`, `reason`, `request_path`). The `pause_event_log()` reader (Phase B2) parses `reason` to recover `kind` and the optional message. **No migration is added** — adding a chain column to `auth_violations` is out of scope (SP-7's verifier_scheduler doesn't expect one). The acceptance-criterion phrase "chained hash" is interpreted as "row appended to the audit-violations log"; if the user wants a real hash chain the plan needs an extra migration task. |
| 2 | Spec assumes Redis is already accessible from app code; in fact `redis==5.2.1` is in `pyproject.toml` but no module imports it. | Phase A1 creates a tiny `_get_redis()` helper inside `pause_state.py` that lazy-instantiates `redis.asyncio.from_url(settings.redis_url, decode_responses=True)`; same module also exposes a `_reset_redis_for_tests()` for fixtures. |
| 3 | `fakeredis` is not in deps. | Phase A1 adds `fakeredis>=2.20.1` to `pyproject.toml` `[project.optional-dependencies] test`. |
| 4 | User prompt mentions `frontend/src/Settings/`. Actual path is `frontend/src/tabs/Settings/` (verified — `Profile.tsx`, `Trading.tsx`, `Secrets.tsx` live there). | All frontend tasks use `frontend/src/tabs/Settings/`. |
| 5 | `shadow/worker.py` is candle-driven (`async for candle in self.reader.stream()`) — there's no `tick_seconds` to sleep over. The "exact shape" tick guard in the prompt is wrong shape for this worker. | Phase C2 adds `if await pause_state.is_paused(): return` at the top of `_handle_candle()`, using the existing `async for` loop's natural pacing. The audit verifier (which sleeps until the next 03:00 UTC) gets the same shape — early `continue` before the round, then re-loop into the next-day sleep. |
| 6 | Spec §3.4 says "WebSocket open already gated by allow-list" but doesn't specify path prefixes. | Allow-list explicitly includes `/api/v1/ws/` (the WS upgrade path). Mid-stream WS messages are not affected by HTTP middleware. |

---

## File Structure

```
worktrees/sp-pause/
├── backend/
│   ├── app/
│   │   ├── ops/
│   │   │   └── pause_state.py                                 NEW — Redis-backed pause flag + 1s cache + audit-row helpers
│   │   ├── api/
│   │   │   ├── pause_middleware.py                            NEW — middleware + _is_allowed_when_paused()
│   │   │   ├── routes/
│   │   │   │   └── admin_system.py                            NEW — 4 admin REST routes
│   │   │   └── schemas.py                                     MODIFIED — SystemPauseRequest/StateOut/EventOut/EventListOut
│   │   ├── data/intermarket_worker.py                         MODIFIED — pause guard in snapshot + cleanup loops
│   │   ├── news/ingest_worker.py                              MODIFIED — pause guard in ingest + cleanup loops
│   │   ├── shadow/worker.py                                   MODIFIED — pause guard in _handle_candle
│   │   ├── ops/verifier_scheduler.py                          MODIFIED — pause guard before _check_all_chains
│   │   └── main.py                                            MODIFIED — register pause middleware + admin_system router
│   ├── pyproject.toml                                         MODIFIED — add fakeredis>=2.20.1 to [test]
│   └── tests/
│       ├── unit/
│       │   ├── test_pause_state_roundtrip.py                  NEW — A2
│       │   ├── test_pause_state_cache.py                      NEW — A2
│       │   ├── test_pause_state_audit_log.py                  NEW — B1/B2
│       │   ├── test_pause_state_event_log_reader.py           NEW — B2
│       │   ├── test_pause_middleware_allowlist.py             NEW — D1
│       │   ├── test_workers_pause_guard_news.py               NEW — C1
│       │   ├── test_workers_pause_guard_intermarket.py        NEW — C2
│       │   └── test_workers_pause_guard_shadow_verifier.py    NEW — C3
│       └── integration/
│           ├── test_api_admin_system_pause_resume.py          NEW — D2/D3
│           └── test_api_admin_system_state_log.py             NEW — D4
└── frontend/
    ├── src/
    │   ├── lib/api.ts                                         MODIFIED — SystemPauseState/Event types + getters/setters
    │   ├── tabs/Settings/
    │   │   ├── SystemPauseControl.tsx                         NEW — E1
    │   │   └── index.tsx                                      MODIFIED — register "system" sub-tab
    │   ├── components/layout/
    │   │   └── PausedBanner.tsx                               NEW — E3
    │   └── App.tsx                                            MODIFIED — mount PausedBanner globally
    └── tests/unit/
        ├── SystemPauseControl.test.tsx                        NEW — E2
        └── PausedBanner.test.tsx                              NEW — E4
└── docs/superpowers/notes/
    └── 2026-05-06-SP-PAUSE-ship.md                            NEW — E5 (status log entry)
```

**Total tasks:** 17 across 5 phases (A: 3, B: 2, C: 3, D: 4, E: 5).

---

## Phase A — `pause_state.py` module + Redis + 1s cache

### Task A1: Stub `pause_state.py` + add `fakeredis` test dep

**Files:**
- Create: `backend/app/ops/pause_state.py`
- Modify: `backend/pyproject.toml`

**Design notes:**
- Module is the single point of truth for the pause flag. Public surface (matches spec §3.2):
  - `async def is_paused() -> bool`
  - `async def set_paused(paused: bool, *, by_email: str, reason: str | None, session: AsyncSession) -> None`
  - `async def get_state() -> SystemPauseState` (extra — used by `/admin/system/state`)
  - `async def pause_event_log(session: AsyncSession, *, limit: int = 50) -> list[PauseEvent]`
- Two private helpers: `_get_redis()` lazy-instantiates `redis.asyncio.from_url(settings.redis_url, decode_responses=True)` and caches the client in `_REDIS`; `_reset_for_tests()` clears `_REDIS` and the in-process cache (`_CACHE`).
- 1-second in-process cache: `_CACHE: tuple[bool, float] | None = None` keyed on `time.monotonic()`. Hit window = 1.0s.
- Stub raises `NotImplementedError("SP-PAUSE Phase A2")` for `is_paused`/`set_paused` so accidentally-shipped scaffolding blows up fast.
- `fakeredis` is added under `[project.optional-dependencies] test` (matches the existing pattern for `respx`).

- [ ] **Step 1: Add fakeredis** — append to `backend/pyproject.toml` `[project.optional-dependencies] test = [...]` block:

```toml
"fakeredis>=2.20.1",
```

- [ ] **Step 2: Write stub** — `backend/app/ops/pause_state.py`:

```python
"""SP-PAUSE master pause/resume state.

Single Redis key (``system:paused`` = ``"true"`` / ``"false"``) cached in
process for 1s so worker ticks + every HTTP request don't hit Redis on
every call. State changes go through ``set_paused`` which both updates
Redis and inserts a row into ``auth_violations`` (the SP-0.7 audit
table) so we have an append-only log of who paused/resumed and why.

The 1-second cache means a freshly-paused-from-another-process flip
takes ≤1s to propagate to this process — acceptable per spec §3.1
(workers tick every 5min; HTTP routes don't need sub-second precision).

Public surface:

* :func:`is_paused` — fast (cached) bool getter.
* :func:`set_paused` — toggle + audit-row insert. Spec §3.2.
* :func:`get_state` — returns :class:`SystemPauseState` for /admin/system/state.
* :func:`pause_event_log` — last N audit rows from ``auth_violations``.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import redis.asyncio as aioredis

from app.config import get_settings


log = logging.getLogger(__name__)

REDIS_KEY: str = "system:paused"
SINCE_KEY: str = "system:paused_since"
BY_KEY: str = "system:paused_by"
REASON_KEY: str = "system:paused_reason"
CACHE_TTL_S: float = 1.0

_REDIS: aioredis.Redis | None = None
_CACHE: tuple[bool, float] | None = None


@dataclass(frozen=True)
class SystemPauseState:
    paused: bool
    since: datetime | None
    by_email: str | None
    reason: str | None


@dataclass(frozen=True)
class PauseEvent:
    id: int
    kind: str          # "system_paused" | "system_resumed"
    by_email: str
    at: datetime
    reason: str | None


def _get_redis() -> aioredis.Redis:
    global _REDIS
    if _REDIS is None:
        _REDIS = aioredis.from_url(
            get_settings().redis_url, decode_responses=True,
        )
    return _REDIS


def _reset_for_tests() -> None:
    """Test hook: clear the cached Redis client + in-process cache."""
    global _REDIS, _CACHE
    _REDIS = None
    _CACHE = None


async def is_paused() -> bool:  # noqa: D401
    raise NotImplementedError("SP-PAUSE Phase A2")


async def set_paused(  # type: ignore[no-untyped-def]
    paused: bool, *, by_email: str, reason: str | None, session, request_path: str | None = None,
) -> None:
    raise NotImplementedError("SP-PAUSE Phase A2")


async def get_state() -> SystemPauseState:
    raise NotImplementedError("SP-PAUSE Phase A2")


async def pause_event_log(  # type: ignore[no-untyped-def]
    session, *, limit: int = 50,
) -> list[PauseEvent]:
    raise NotImplementedError("SP-PAUSE Phase B2")


__all__ = [
    "BY_KEY",
    "CACHE_TTL_S",
    "PauseEvent",
    "REASON_KEY",
    "REDIS_KEY",
    "SINCE_KEY",
    "SystemPauseState",
    "_reset_for_tests",
    "get_state",
    "is_paused",
    "pause_event_log",
    "set_paused",
]
```

- [ ] **Step 3: Verify import**

```bash
cd worktrees/sp-pause
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend python -c "from app.ops.pause_state import is_paused, set_paused, REDIS_KEY; print(REDIS_KEY)"
```
Expected: `system:paused`.

- [ ] **Step 4: Run baseline backend tests**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest -q
```
Expected: same baseline as before (no new tests yet). Record exact number — Phase E target is `baseline + ~22`.

- [ ] **Step 5: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-pause' add backend/app/ops/pause_state.py backend/pyproject.toml
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-pause' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-pause): scaffold pause_state module + add fakeredis test dep"
```

---

### Task A2: Implement `is_paused` + `set_paused` (Redis-only, no audit yet) — TDD

**Files:**
- Modify: `backend/app/ops/pause_state.py`
- Create: `backend/tests/unit/test_pause_state_roundtrip.py`
- Create: `backend/tests/unit/test_pause_state_cache.py`

**Design notes:**
- This task implements the Redis round-trip and the 1s cache. The audit-row insert side-effect lives in Phase B (the function signature already accepts `session` so B can layer in without breaking callers).
- `is_paused()` reads `_CACHE`; on miss, GETs `system:paused` from Redis (`b"true"` → `True`, anything else → `False`); writes `_CACHE = (value, monotonic())`.
- `set_paused(True, ...)` sets `system:paused="true"`, `system:paused_since=<isoformat now>`, `system:paused_by=<email>`, `system:paused_reason=<reason or "">`. `set_paused(False, ...)` deletes all four. Then invalidates `_CACHE`.
- `get_state()` reads all four keys with one `MGET`; constructs `SystemPauseState`.
- The cache uses `time.monotonic()` (not wall clock) so test-mocking time advances are deterministic with `time.monotonic` patched.

- [ ] **Step 1: Failing test** — `tests/unit/test_pause_state_roundtrip.py`:

```python
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis
import pytest

from app.ops import pause_state


@pytest.fixture(autouse=True)
def _reset_module(monkeypatch: pytest.MonkeyPatch) -> Any:
    pause_state._reset_for_tests()
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(pause_state, "_get_redis", lambda: fake)
    yield fake
    pause_state._reset_for_tests()


@pytest.mark.asyncio
async def test_is_paused_defaults_false() -> None:
    assert await pause_state.is_paused() is False


@pytest.mark.asyncio
async def test_set_paused_true_then_is_paused_true() -> None:
    sess = MagicMock()
    sess.execute = AsyncMock()
    sess.commit = AsyncMock()
    await pause_state.set_paused(
        True, by_email="admin@x.com", reason="travel", session=sess,
    )
    pause_state._CACHE = None  # force re-read from Redis
    assert await pause_state.is_paused() is True


@pytest.mark.asyncio
async def test_set_paused_false_clears_state() -> None:
    sess = MagicMock()
    sess.execute = AsyncMock()
    sess.commit = AsyncMock()
    await pause_state.set_paused(True, by_email="a@x.com", reason="r", session=sess)
    await pause_state.set_paused(False, by_email="a@x.com", reason=None, session=sess)
    pause_state._CACHE = None
    assert await pause_state.is_paused() is False
    state = await pause_state.get_state()
    assert state.paused is False
    assert state.since is None
    assert state.by_email is None


@pytest.mark.asyncio
async def test_get_state_returns_full_record_when_paused() -> None:
    sess = MagicMock()
    sess.execute = AsyncMock()
    sess.commit = AsyncMock()
    await pause_state.set_paused(
        True, by_email="admin@x.com", reason="broker outage", session=sess,
    )
    state = await pause_state.get_state()
    assert state.paused is True
    assert state.by_email == "admin@x.com"
    assert state.reason == "broker outage"
    assert state.since is not None
```

`tests/unit/test_pause_state_cache.py`:

```python
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis
import pytest

from app.ops import pause_state


@pytest.fixture(autouse=True)
def _reset_module(monkeypatch: pytest.MonkeyPatch) -> Any:
    pause_state._reset_for_tests()
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(pause_state, "_get_redis", lambda: fake)
    yield fake
    pause_state._reset_for_tests()


@pytest.mark.asyncio
async def test_cache_avoids_redis_within_1s(monkeypatch: pytest.MonkeyPatch) -> None:
    sess = MagicMock()
    sess.execute = AsyncMock()
    sess.commit = AsyncMock()
    await pause_state.set_paused(True, by_email="a@x.com", reason="r", session=sess)
    pause_state._CACHE = None
    # First call populates the cache with True.
    assert await pause_state.is_paused() is True

    # Now flip Redis directly — within 1s the cache should still answer True.
    fake = pause_state._get_redis()
    await fake.set(pause_state.REDIS_KEY, "false")
    t = [100.0]
    monkeypatch.setattr(pause_state.time, "monotonic", lambda: t[0])
    pause_state._CACHE = (True, t[0] - 0.5)  # cache age 0.5s
    assert await pause_state.is_paused() is True


@pytest.mark.asyncio
async def test_cache_expires_after_1s(monkeypatch: pytest.MonkeyPatch) -> None:
    sess = MagicMock()
    sess.execute = AsyncMock()
    sess.commit = AsyncMock()
    await pause_state.set_paused(True, by_email="a@x.com", reason="r", session=sess)
    fake = pause_state._get_redis()
    await fake.set(pause_state.REDIS_KEY, "false")
    t = [100.0]
    monkeypatch.setattr(pause_state.time, "monotonic", lambda: t[0])
    pause_state._CACHE = (True, t[0] - 1.5)  # cache age 1.5s — expired
    assert await pause_state.is_paused() is False


@pytest.mark.asyncio
async def test_set_paused_invalidates_cache() -> None:
    sess = MagicMock()
    sess.execute = AsyncMock()
    sess.commit = AsyncMock()
    pause_state._CACHE = (False, 100.0)
    await pause_state.set_paused(True, by_email="a@x.com", reason="r", session=sess)
    assert pause_state._CACHE is None
```

- [ ] **Step 2: Run — fail.**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest -v tests/unit/test_pause_state_roundtrip.py tests/unit/test_pause_state_cache.py
```
Expected: 7 errors (all `NotImplementedError("SP-PAUSE Phase A2")`).

- [ ] **Step 3: Implement** — replace stubs in `app/ops/pause_state.py`:

```python
import time as _time

# (keep dataclasses + module constants from A1)


async def is_paused() -> bool:
    """Cached pause-flag getter. Returns the in-process cached value when
    less than ``CACHE_TTL_S`` seconds old; otherwise refreshes from Redis."""
    global _CACHE
    now = _time.monotonic()
    if _CACHE is not None and now - _CACHE[1] < CACHE_TTL_S:
        return _CACHE[0]
    try:
        raw = await _get_redis().get(REDIS_KEY)
    except Exception:  # noqa: BLE001
        # If Redis is unreachable, fail OPEN (not paused) — avoids a
        # Redis outage cascading into a full system lockout.
        log.warning("pause_state: Redis read failed; assuming not-paused")
        _CACHE = (False, now)
        return False
    value = raw == "true"
    _CACHE = (value, now)
    return value


async def set_paused(
    paused: bool, *, by_email: str, reason: str | None,
    session, request_path: str | None = None,
) -> None:
    """Flip the pause flag. Phase B layers an audit-row insert in here."""
    global _CACHE
    r = _get_redis()
    if paused:
        ts = datetime.now(__import__("datetime").timezone.utc).isoformat()
        await r.set(REDIS_KEY, "true")
        await r.set(SINCE_KEY, ts)
        await r.set(BY_KEY, by_email)
        await r.set(REASON_KEY, reason or "")
    else:
        await r.delete(REDIS_KEY, SINCE_KEY, BY_KEY, REASON_KEY)
    _CACHE = None


async def get_state() -> SystemPauseState:
    r = _get_redis()
    paused_raw, since_raw, by_raw, reason_raw = await r.mget(
        REDIS_KEY, SINCE_KEY, BY_KEY, REASON_KEY,
    )
    paused = paused_raw == "true"
    if not paused:
        return SystemPauseState(
            paused=False, since=None, by_email=None, reason=None,
        )
    since: datetime | None = None
    if since_raw:
        try:
            since = datetime.fromisoformat(since_raw)
        except ValueError:
            since = None
    return SystemPauseState(
        paused=True,
        since=since,
        by_email=by_raw or None,
        reason=(reason_raw or None) or None,
    )
```

Replace the bare `time` reference in the cache tests by importing `time` at module top:

```python
import time
```

(The patched `pause_state.time.monotonic` in the cache tests requires the module-level `time` symbol.)

- [ ] **Step 4: Run — pass.**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest -v tests/unit/test_pause_state_roundtrip.py tests/unit/test_pause_state_cache.py
```
Expected: `7 passed`.

- [ ] **Step 5: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-pause' add backend/app/ops/pause_state.py backend/tests/unit/test_pause_state_roundtrip.py backend/tests/unit/test_pause_state_cache.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-pause' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-pause): is_paused + set_paused + get_state with 1s in-process cache (Redis only)"
```

---

### Task A3: Conftest fixture — `pause_state_clean`

**Files:**
- Modify: `backend/tests/conftest.py`

**Design notes:**
- Phase D integration tests need the pause flag reliably reset to "not paused" between tests, with a fakeredis backing them. We add an autouse fixture (`pause_state_clean`) so any test that imports a route or worker doesn't bleed pause state across cases.
- Scope is `function` (default) so each test gets a fresh fake. We monkeypatch `pause_state._get_redis` at the module level.

- [ ] **Step 1: Append the fixture** to `backend/tests/conftest.py` (or create the fixture next to existing autouse fixtures):

```python
@pytest.fixture(autouse=True)
def _pause_state_clean(monkeypatch: pytest.MonkeyPatch):
    """Ensure each test starts with a fresh fakeredis-backed pause_state."""
    import fakeredis.aioredis

    from app.ops import pause_state

    pause_state._reset_for_tests()
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(pause_state, "_get_redis", lambda: fake)
    yield
    pause_state._reset_for_tests()
```

- [ ] **Step 2: Run full backend suite**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest -q
```
Expected: same as baseline + 7 passing from A2. No regressions.

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-pause' add backend/tests/conftest.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-pause' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "test(sp-pause): autouse pause_state fakeredis fixture in conftest"
```

---

## Phase B — Audit-row integration

### Task B1: `set_paused` writes a row to `auth_violations` — TDD

**Files:**
- Modify: `backend/app/ops/pause_state.py`
- Create: `backend/tests/unit/test_pause_state_audit_log.py`

**Design notes:**
- Mirror `app.ops.verifier_scheduler._record_violation`: `INSERT INTO auth_violations (attempted_email, reason, request_path) VALUES (...)`. Pause uses `reason="system_paused: <free-form>"`, resume uses `reason="system_resumed"` (no message — spec §3.5 marks resume reason optional). `request_path` carries `/api/v1/admin/system/pause` or `/admin/system/resume`.
- Caller must pass an `AsyncSession`; the route handler in Phase D2/D3 will provide one via `Depends(get_session)`.
- Insert is committed inside `set_paused` so the route handler doesn't need to remember to commit (matches `_record_violation`).

- [ ] **Step 1: Failing test** — `tests/unit/test_pause_state_audit_log.py`:

```python
from typing import Any

import fakeredis.aioredis
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.ops import pause_state


@pytest.fixture
async def session(monkeypatch: pytest.MonkeyPatch) -> Any:
    pause_state._reset_for_tests()
    monkeypatch.setattr(
        pause_state, "_get_redis",
        lambda: fakeredis.aioredis.FakeRedis(decode_responses=True),
    )
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text("""
            CREATE TABLE auth_violations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                attempted_email TEXT NOT NULL,
                attempted_at TEXT NOT NULL DEFAULT (datetime('now')),
                reason TEXT NOT NULL,
                jwt_sub TEXT,
                request_path TEXT
            )
        """))
    async with AsyncSession(engine) as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_set_paused_true_inserts_audit_row(session: AsyncSession) -> None:
    await pause_state.set_paused(
        True, by_email="admin@x.com", reason="travel",
        session=session, request_path="/api/v1/admin/system/pause",
    )
    rows = (await session.execute(sa.text(
        "SELECT attempted_email, reason, request_path FROM auth_violations"
    ))).all()
    assert len(rows) == 1
    assert rows[0].attempted_email == "admin@x.com"
    assert rows[0].reason == "system_paused: travel"
    assert rows[0].request_path == "/api/v1/admin/system/pause"


@pytest.mark.asyncio
async def test_set_paused_false_inserts_resume_row(session: AsyncSession) -> None:
    await pause_state.set_paused(
        True, by_email="admin@x.com", reason="r",
        session=session, request_path="/api/v1/admin/system/pause",
    )
    await pause_state.set_paused(
        False, by_email="admin@x.com", reason=None,
        session=session, request_path="/api/v1/admin/system/resume",
    )
    rows = (await session.execute(sa.text(
        "SELECT reason, request_path FROM auth_violations ORDER BY id"
    ))).all()
    assert len(rows) == 2
    assert rows[1].reason == "system_resumed"
    assert rows[1].request_path == "/api/v1/admin/system/resume"


@pytest.mark.asyncio
async def test_set_paused_no_reason_pauses_with_blank_message(
    session: AsyncSession,
) -> None:
    await pause_state.set_paused(
        True, by_email="admin@x.com", reason=None,
        session=session, request_path="/api/v1/admin/system/pause",
    )
    rows = (await session.execute(sa.text(
        "SELECT reason FROM auth_violations"
    ))).all()
    assert rows[0].reason == "system_paused: "
```

- [ ] **Step 2: Run — fail.**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest -v tests/unit/test_pause_state_audit_log.py
```
Expected: 3 failures — `auth_violations` row count is 0 (Phase A2 doesn't write to the DB).

- [ ] **Step 3: Implement** — extend `set_paused` body in `app/ops/pause_state.py`:

```python
async def set_paused(
    paused: bool, *, by_email: str, reason: str | None,
    session, request_path: str | None = None,
) -> None:
    """Flip the pause flag in Redis AND record an audit row.

    The audit row uses ``auth_violations`` (the SP-0.7 table). Reason
    field encodes the kind so :func:`pause_event_log` can recover it::

        pause:    reason = "system_paused: <free text or empty>"
        resume:   reason = "system_resumed"
    """
    global _CACHE
    import sqlalchemy as sa  # local import — keeps redis-only callers light

    r = _get_redis()
    if paused:
        ts = datetime.now(__import__("datetime").timezone.utc).isoformat()
        await r.set(REDIS_KEY, "true")
        await r.set(SINCE_KEY, ts)
        await r.set(BY_KEY, by_email)
        await r.set(REASON_KEY, reason or "")
        audit_reason = f"system_paused: {reason or ''}"
    else:
        await r.delete(REDIS_KEY, SINCE_KEY, BY_KEY, REASON_KEY)
        audit_reason = "system_resumed"

    await session.execute(
        sa.text(
            "INSERT INTO auth_violations "
            "(attempted_email, reason, request_path) "
            "VALUES (:e, :r, :p)"
        ),
        {"e": by_email, "r": audit_reason, "p": request_path},
    )
    await session.commit()
    _CACHE = None
```

- [ ] **Step 4: Run — pass.**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest -v tests/unit/test_pause_state_audit_log.py
```
Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-pause' add backend/app/ops/pause_state.py backend/tests/unit/test_pause_state_audit_log.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-pause' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-pause): set_paused records pause/resume into auth_violations"
```

---

### Task B2: `pause_event_log` reader — TDD

**Files:**
- Modify: `backend/app/ops/pause_state.py`
- Create: `backend/tests/unit/test_pause_state_event_log_reader.py`

**Design notes:**
- Reads the most recent `limit` rows from `auth_violations` whose `reason LIKE 'system_paused%'` OR `reason = 'system_resumed'`. Parses each row into a `PauseEvent`.
- Default `limit=50` per spec §3.5.

- [ ] **Step 1: Failing test** — `tests/unit/test_pause_state_event_log_reader.py`:

```python
from typing import Any

import fakeredis.aioredis
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.ops import pause_state


@pytest.fixture
async def session(monkeypatch: pytest.MonkeyPatch) -> Any:
    pause_state._reset_for_tests()
    monkeypatch.setattr(
        pause_state, "_get_redis",
        lambda: fakeredis.aioredis.FakeRedis(decode_responses=True),
    )
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text("""
            CREATE TABLE auth_violations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                attempted_email TEXT NOT NULL,
                attempted_at TEXT NOT NULL DEFAULT (datetime('now')),
                reason TEXT NOT NULL,
                jwt_sub TEXT,
                request_path TEXT
            )
        """))
    async with AsyncSession(engine) as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_pause_event_log_returns_pause_and_resume_rows(
    session: AsyncSession,
) -> None:
    await pause_state.set_paused(
        True, by_email="admin@x.com", reason="travel",
        session=session, request_path="/api/v1/admin/system/pause",
    )
    await pause_state.set_paused(
        False, by_email="admin@x.com", reason=None,
        session=session, request_path="/api/v1/admin/system/resume",
    )
    log_ = await pause_state.pause_event_log(session)
    assert len(log_) == 2
    # Most recent first.
    assert log_[0].kind == "system_resumed"
    assert log_[0].reason is None
    assert log_[1].kind == "system_paused"
    assert log_[1].reason == "travel"
    assert log_[1].by_email == "admin@x.com"


@pytest.mark.asyncio
async def test_pause_event_log_excludes_non_pause_rows(
    session: AsyncSession,
) -> None:
    await session.execute(sa.text(
        "INSERT INTO auth_violations (attempted_email, reason) "
        "VALUES ('system', 'audit_chain_broken:predictions:42')"
    ))
    await pause_state.set_paused(
        True, by_email="a@x.com", reason="r",
        session=session, request_path="/api/v1/admin/system/pause",
    )
    log_ = await pause_state.pause_event_log(session)
    assert len(log_) == 1
    assert log_[0].kind == "system_paused"


@pytest.mark.asyncio
async def test_pause_event_log_limit(session: AsyncSession) -> None:
    for i in range(5):
        await pause_state.set_paused(
            True, by_email=f"a{i}@x.com", reason=str(i),
            session=session, request_path="/api/v1/admin/system/pause",
        )
        await pause_state.set_paused(
            False, by_email=f"a{i}@x.com", reason=None,
            session=session, request_path="/api/v1/admin/system/resume",
        )
    log_ = await pause_state.pause_event_log(session, limit=3)
    assert len(log_) == 3
```

- [ ] **Step 2: Run — fail.** Expected: `NotImplementedError("SP-PAUSE Phase B2")`.

- [ ] **Step 3: Implement** — replace the stub in `app/ops/pause_state.py`:

```python
async def pause_event_log(
    session, *, limit: int = 50,
) -> list[PauseEvent]:
    import sqlalchemy as sa
    rows = (await session.execute(sa.text("""
        SELECT id, attempted_email, attempted_at, reason
        FROM auth_violations
        WHERE reason LIKE 'system_paused%' OR reason = 'system_resumed'
        ORDER BY id DESC
        LIMIT :limit
    """), {"limit": limit})).all()
    out: list[PauseEvent] = []
    for r in rows:
        if r.reason == "system_resumed":
            kind = "system_resumed"
            msg: str | None = None
        else:
            kind = "system_paused"
            # Strip "system_paused: " prefix; empty → None.
            after = r.reason[len("system_paused:"):].strip()
            msg = after if after else None
        at = r.attempted_at
        if isinstance(at, str):
            try:
                at = datetime.fromisoformat(at.replace("Z", "+00:00"))
            except ValueError:
                pass
        out.append(PauseEvent(
            id=int(r.id), kind=kind, by_email=r.attempted_email,
            at=at, reason=msg,
        ))
    return out
```

- [ ] **Step 4: Run — pass.**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest -v tests/unit/test_pause_state_event_log_reader.py
```
Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-pause' add backend/app/ops/pause_state.py backend/tests/unit/test_pause_state_event_log_reader.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-pause' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-pause): pause_event_log reader parses last N pause/resume rows"
```

---

## Phase C — Wire pause guard into all 5 + 1 long-running loops

### Task C1: News ingest + cleanup loops gain pause guard — TDD

**Files:**
- Modify: `backend/app/news/ingest_worker.py`
- Create: `backend/tests/unit/test_workers_pause_guard_news.py`

**Design notes:**
- Pause guard goes at the very top of `run_news_ingest_loop`'s `while True:` body and `run_news_cleanup_loop`'s body. When `is_paused()` returns True, we log + sleep through the normal interval and `continue` — the existing tick body is skipped.
- Cleanup loop's natural cadence is "wait until next 04:00 UTC, do work". With the guard inserted *after* the wake but *before* the deletion, a paused system that wakes at 04:00 simply skips the cleanup and re-sleeps another full day. This is acceptable: a single 24h skip on retention is harmless.

- [ ] **Step 1: Failing test** — `tests/unit/test_workers_pause_guard_news.py`:

```python
import asyncio
from typing import Any
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest

from app.news import ingest_worker
from app.ops import pause_state


@pytest.fixture(autouse=True)
def _redis(monkeypatch: pytest.MonkeyPatch) -> Any:
    pause_state._reset_for_tests()
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(pause_state, "_get_redis", lambda: fake)
    yield fake
    pause_state._reset_for_tests()


@pytest.mark.asyncio
async def test_news_ingest_loop_skips_iteration_when_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = pause_state._get_redis()
    await fake.set(pause_state.REDIS_KEY, "true")

    iter_spy = AsyncMock()
    monkeypatch.setattr(ingest_worker, "_run_one_iteration", iter_spy)
    monkeypatch.setattr(
        ingest_worker, "_build_adapters", lambda *_a, **_k: (object(), object()),
    )

    sleeps: list[float] = []

    async def _sleep(s: float) -> None:
        sleeps.append(s)
        if len(sleeps) >= 2:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await ingest_worker.run_news_ingest_loop(
            session_factory=lambda: None, sleep_fn=_sleep,
        )

    iter_spy.assert_not_awaited()
    assert sleeps[0] == float(ingest_worker.CRYPTO_INTERVAL_S)


@pytest.mark.asyncio
async def test_news_ingest_loop_runs_iteration_when_not_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    iter_spy = AsyncMock()
    monkeypatch.setattr(ingest_worker, "_run_one_iteration", iter_spy)
    monkeypatch.setattr(
        ingest_worker, "_build_adapters", lambda *_a, **_k: (object(), object()),
    )

    async def _sleep(_s: float) -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await ingest_worker.run_news_ingest_loop(
            session_factory=lambda: None, sleep_fn=_sleep,
        )
    iter_spy.assert_awaited_once()


@pytest.mark.asyncio
async def test_news_cleanup_loop_skips_when_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = pause_state._get_redis()
    await fake.set(pause_state.REDIS_KEY, "true")

    monkeypatch.setattr(
        ingest_worker, "_seconds_until_next_utc_hour", lambda *a, **k: 0.0,
    )
    cleanup_spy = AsyncMock()
    # The lazy import inside the loop resolves through this attribute.
    import app.news.persistence as persistence
    monkeypatch.setattr(persistence, "cleanup_old_news", cleanup_spy)

    sleeps = 0
    async def _sleep(_s: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps >= 2:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await ingest_worker.run_news_cleanup_loop(
            session_factory=lambda: None, sleep_fn=_sleep,
        )
    cleanup_spy.assert_not_awaited()
```

- [ ] **Step 2: Run — fail.** Expected: `iter_spy` *was* awaited (no guard yet) → assertion fails.

- [ ] **Step 3: Implement** — modify `app/news/ingest_worker.py`:

In `run_news_ingest_loop`, immediately after `last_macro_run = ...`:

```python
    from app.ops import pause_state  # local import — avoids circular at module load

    while True:
        try:
            if await pause_state.is_paused():
                log.debug("news_ingest: paused, skipping tick")
                await sleep(float(CRYPTO_INTERVAL_S))
                continue
            await _run_one_iteration(
                session_factory, crypto_adapter, yahoo_adapter, last_macro_run,
            )
            ...
```

In `run_news_cleanup_loop`, immediately after `await sleep(_seconds_until_next_utc_hour(...))`:

```python
    while True:
        await sleep(_seconds_until_next_utc_hour(CLEANUP_HOUR_UTC))
        from app.ops import pause_state
        if await pause_state.is_paused():
            log.debug("news_cleanup: paused, skipping nightly run")
            continue
        ...
```

- [ ] **Step 4: Run — pass.**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest -v tests/unit/test_workers_pause_guard_news.py
```
Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-pause' add backend/app/news/ingest_worker.py backend/tests/unit/test_workers_pause_guard_news.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-pause' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-pause): pause guard at top of news ingest + cleanup loops"
```

---

### Task C2: Intermarket snapshot + cleanup loops gain pause guard — TDD

**Files:**
- Modify: `backend/app/data/intermarket_worker.py`
- Create: `backend/tests/unit/test_workers_pause_guard_intermarket.py`

**Design notes:**
- Same pattern as C1. `run_intermarket_snapshot_loop` ticks every 5 min — guard goes at the top of `while True:` and `_sleep(INTERMARKET_INTERVAL_S)` then `continue`. `run_intermarket_cleanup_loop` wakes nightly at 04:30 UTC — guard goes after the wake, before `cleanup_old_intermarket`.

- [ ] **Step 1: Failing test** — `tests/unit/test_workers_pause_guard_intermarket.py`:

```python
import asyncio
from typing import Any
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest

from app.data import intermarket_worker
from app.ops import pause_state


@pytest.fixture(autouse=True)
def _redis(monkeypatch: pytest.MonkeyPatch) -> Any:
    pause_state._reset_for_tests()
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(pause_state, "_get_redis", lambda: fake)
    yield fake
    pause_state._reset_for_tests()


@pytest.mark.asyncio
async def test_intermarket_snapshot_loop_skips_when_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = pause_state._get_redis()
    await fake.set(pause_state.REDIS_KEY, "true")

    snapshot_spy = AsyncMock(return_value=0)
    monkeypatch.setattr(intermarket_worker, "_snapshot_once", snapshot_spy)

    sleeps: list[float] = []
    async def _sleep(s: float) -> None:
        sleeps.append(s)
        if len(sleeps) >= 2:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await intermarket_worker.run_intermarket_snapshot_loop(
            session_factory=lambda: None,
            _adapter=object(),
            _sleep=_sleep,
            _universe_loader=AsyncMock(return_value=[]),
        )
    snapshot_spy.assert_not_awaited()
    assert sleeps[0] == float(intermarket_worker.INTERMARKET_INTERVAL_S)


@pytest.mark.asyncio
async def test_intermarket_cleanup_loop_skips_when_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = pause_state._get_redis()
    await fake.set(pause_state.REDIS_KEY, "true")

    monkeypatch.setattr(
        intermarket_worker, "_seconds_until_0430_utc", lambda **_k: 0,
    )
    cleanup_spy = AsyncMock()
    monkeypatch.setattr(
        intermarket_worker, "cleanup_old_intermarket", cleanup_spy,
    )

    sleeps = 0
    async def _sleep(_s: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps >= 2:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await intermarket_worker.run_intermarket_cleanup_loop(
            session_factory=lambda: None, _sleep=_sleep,
        )
    cleanup_spy.assert_not_awaited()
```

- [ ] **Step 2: Run — fail.**

- [ ] **Step 3: Implement** — modify `app/data/intermarket_worker.py`:

In `run_intermarket_snapshot_loop`, just before `try: await _snapshot_once(...)`:

```python
    while True:
        from app.ops import pause_state
        if await pause_state.is_paused():
            log.debug("intermarket_snapshot: paused, skipping tick")
            await _sleep(float(INTERMARKET_INTERVAL_S))
            continue
        try:
            await _snapshot_once(session_factory, adapter, loader)
        ...
```

In `run_intermarket_cleanup_loop`, after the wake `await _sleep(float(wait_s))`:

```python
        await _sleep(float(wait_s))
        from app.ops import pause_state
        if await pause_state.is_paused():
            log.debug("intermarket_cleanup: paused, skipping nightly run")
            continue
        try:
            ...
```

- [ ] **Step 4: Run — pass.**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest -v tests/unit/test_workers_pause_guard_intermarket.py
```
Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-pause' add backend/app/data/intermarket_worker.py backend/tests/unit/test_workers_pause_guard_intermarket.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-pause' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-pause): pause guard at top of intermarket snapshot + cleanup loops"
```

---

### Task C3: Shadow worker + audit verifier gain pause guard — TDD

**Files:**
- Modify: `backend/app/shadow/worker.py`
- Modify: `backend/app/ops/verifier_scheduler.py`
- Create: `backend/tests/unit/test_workers_pause_guard_shadow_verifier.py`

**Design notes:**
- Shadow worker is candle-driven — guard goes at the top of `_handle_candle` (early `return`), not the constructor or `run`. The candle stream itself paces, so no sleep needed.
- Audit verifier sleeps until 03:00 UTC then runs `_check_all_chains`. Guard goes after the wake, before `_check_all_chains` — paused system skips the round and re-loops into the next-day sleep.

- [ ] **Step 1: Failing test** — `tests/unit/test_workers_pause_guard_shadow_verifier.py`:

```python
import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis
import pandas as pd
import pytest

from app.ops import pause_state, verifier_scheduler
from app.shadow.worker import ShadowWorker


@pytest.fixture(autouse=True)
def _redis(monkeypatch: pytest.MonkeyPatch) -> Any:
    pause_state._reset_for_tests()
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(pause_state, "_get_redis", lambda: fake)
    yield fake
    pause_state._reset_for_tests()


@pytest.mark.asyncio
async def test_shadow_worker_skips_candle_when_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = pause_state._get_redis()
    await fake.set(pause_state.REDIS_KEY, "true")

    w = ShadowWorker(
        symbols=["BTCUSDT"],
        session_factory=MagicMock(),
        reader=MagicMock(),
        seed_history={"BTCUSDT": pd.DataFrame()},
    )
    open_spy = AsyncMock()
    close_spy = AsyncMock()
    monkeypatch.setattr(w, "_maybe_open_position", open_spy)
    monkeypatch.setattr(w, "_maybe_close_position", close_spy)

    candle = MagicMock(symbol="BTCUSDT", timeframe="1h",
                      ts=datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc),
                      open=1, high=1, low=1, close=1, volume=1)

    await w._handle_candle(candle)
    open_spy.assert_not_awaited()
    close_spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_audit_verifier_loop_skips_when_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = pause_state._get_redis()
    await fake.set(pause_state.REDIS_KEY, "true")

    check_spy = AsyncMock()
    monkeypatch.setattr(verifier_scheduler, "_check_all_chains", check_spy)
    monkeypatch.setattr(
        verifier_scheduler, "seconds_until_next_utc_hour", lambda *a, **k: 0,
    )

    sleeps = 0
    async def _sleep(_s: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps >= 2:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await verifier_scheduler.run_audit_verifier_loop(
            session_factory=MagicMock(),
            _sleep=_sleep,
            _now=lambda: datetime(2026, 5, 6, 12, tzinfo=timezone.utc),
        )
    check_spy.assert_not_awaited()
```

- [ ] **Step 2: Run — fail.**

- [ ] **Step 3: Implement** — `app/shadow/worker.py`, modify `_handle_candle`:

```python
    async def _handle_candle(self, candle: MultiStreamCandle) -> None:
        from app.ops import pause_state
        if await pause_state.is_paused():
            log.debug("shadow_worker: paused, skipping candle %s", candle.symbol)
            return
        buf = self._append_bar(candle)
        ...
```

`app/ops/verifier_scheduler.py`, modify `run_audit_verifier_loop`:

```python
    while True:
        wait_s = seconds_until_next_utc_hour(wake_at_utc_hour, now_fn())
        await _sleep(float(wait_s))
        from app.ops import pause_state
        if await pause_state.is_paused():
            log.debug("audit_verifier: paused, skipping nightly chain check")
            continue
        await _check_all_chains(session_factory)
```

- [ ] **Step 4: Run — pass.**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest -v tests/unit/test_workers_pause_guard_shadow_verifier.py
```
Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-pause' add backend/app/shadow/worker.py backend/app/ops/verifier_scheduler.py backend/tests/unit/test_workers_pause_guard_shadow_verifier.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-pause' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-pause): pause guard in shadow_worker._handle_candle + audit verifier loop"
```

---

## Phase D — Pause middleware + admin REST routes

### Task D1: `pause_middleware.py` + allow-list — TDD

**Files:**
- Create: `backend/app/api/pause_middleware.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/api/schemas.py`
- Create: `backend/tests/unit/test_pause_middleware_allowlist.py`

**Design notes:**
- Middleware is a `@app.middleware("http")` shape but defined as a free async function so we can unit-test the allow-list logic without a running app.
- `_is_allowed_when_paused(request) -> bool` is pure; it inspects `request.method` and `request.url.path`. Allow-list (per spec §3.4 + the user prompt's amendments):

  | Always allowed | Allowed only when method is GET |
  |-|-|
  | `/api/v1/admin/` (any method — admin needs to resume + browse audit) | `/api/v1/predictions/list` (read-only listing) |
  | `/api/v1/health` (Cloudflare Tunnel health) | `/api/v1/shadow_trades/list` (read-only listing) |
  | `/api/v1/me/` (any method — user can still see + edit their own state) | `/` (frontend root) |
  | `/api/v1/bot-status/` (any method — read-only review) | `/assets/`, `/static/`, `/favicon.ico`, `/index.html`, `/vite.svg` (static asset paths) |
  | `/metrics` (Prometheus scrape — spec §6 row 6) | |
  | `/api/v1/ws/` (WebSocket upgrade — open already; resume must be instant) | |

  `OPTIONS` is always allowed (CORS preflight). Anything not in the allow-list → 423.
- `since` field in the 423 body is the ISO-8601 string we read from Redis (`SINCE_KEY`); fall back to `None` if unavailable.
- `SystemPauseRequest`, `SystemPauseStateOut`, `SystemPauseEventOut`, `SystemPauseEventListOut` Pydantic schemas land in `app/api/schemas.py` (used by both middleware error body and Phase D2/D3/D4 routes).

- [ ] **Step 1: Append schemas** to `app/api/schemas.py`:

```python
# --- SP-PAUSE: master pause/resume -----------------------------------

class SystemPauseRequest(BaseModel):
    """Body for POST /api/v1/admin/system/pause. Reason is required."""
    reason: str = Field(min_length=1, max_length=500)


class SystemPauseStateOut(BaseModel):
    paused: bool
    since: datetime | None = None
    by_email: str | None = None
    reason: str | None = None


class SystemPauseEventOut(BaseModel):
    id: int
    kind: Literal["system_paused", "system_resumed"]
    by_email: str
    at: datetime
    reason: str | None = None


class SystemPauseEventListOut(BaseModel):
    events: list[SystemPauseEventOut]
```

(The imports `Field`, `BaseModel`, `Literal`, `datetime` are already present at the top of `schemas.py` — used by SP-9.)

- [ ] **Step 2: Failing test** — `tests/unit/test_pause_middleware_allowlist.py`:

```python
from typing import Any

import fakeredis.aioredis
import pytest

from app.api.pause_middleware import _is_allowed_when_paused
from app.ops import pause_state


@pytest.fixture(autouse=True)
def _redis(monkeypatch: pytest.MonkeyPatch) -> Any:
    pause_state._reset_for_tests()
    monkeypatch.setattr(
        pause_state, "_get_redis",
        lambda: fakeredis.aioredis.FakeRedis(decode_responses=True),
    )
    yield
    pause_state._reset_for_tests()


def _req(method: str, path: str) -> Any:
    """Tiny stand-in for fastapi.Request — only .method/.url.path are read."""
    class _U:
        def __init__(self, p: str) -> None:
            self.path = p
    class _R:
        def __init__(self, m: str, p: str) -> None:
            self.method = m
            self.url = _U(p)
    return _R(method, path)


@pytest.mark.parametrize("method,path,expected", [
    # admin/* always allowed (admin must resume)
    ("POST", "/api/v1/admin/system/resume",       True),
    ("GET",  "/api/v1/admin/system/state",        True),
    ("POST", "/api/v1/admin/news/refresh",        True),
    # health always allowed (Cloudflare Tunnel)
    ("GET",  "/api/v1/health",                    True),
    # me/* always allowed
    ("GET",  "/api/v1/me/",                       True),
    ("PATCH","/api/v1/me/",                       True),
    # bot-status read-only allowed
    ("GET",  "/api/v1/bot-status/overview",       True),
    # metrics always allowed (Prometheus)
    ("GET",  "/metrics",                          True),
    # WS upgrade allowed
    ("GET",  "/api/v1/ws/live-prediction",        True),
    # OPTIONS for CORS preflight always allowed
    ("OPTIONS", "/api/v1/predict",                True),
    # GET predictions/shadow_trades list allowed
    ("GET",  "/api/v1/predictions/list",          True),
    ("GET",  "/api/v1/shadow_trades/list",        True),
    # Frontend root + assets allowed for GET only
    ("GET",  "/",                                 True),
    ("GET",  "/assets/index.js",                  True),
    ("GET",  "/static/main.css",                  True),
    ("GET",  "/favicon.ico",                      True),
    ("GET",  "/index.html",                       True),
    ("GET",  "/vite.svg",                         True),
    # NOT allowed
    ("POST", "/api/v1/predict",                   False),
    ("POST", "/api/v1/shadow_trades",             False),
    ("POST", "/api/v1/predictions/list",          False),
    ("POST", "/",                                 False),
    ("POST", "/assets/foo",                       False),
    ("GET",  "/api/v1/scanner",                   False),
    ("GET",  "/api/v1/intermarket/BTC%2FUSDT",    False),
])
def test_allowlist(method: str, path: str, expected: bool) -> None:
    assert _is_allowed_when_paused(_req(method, path)) is expected
```

- [ ] **Step 3: Run — fail.** Expected: `ImportError: app.api.pause_middleware`.

- [ ] **Step 4: Implement** — `app/api/pause_middleware.py`:

```python
"""SP-PAUSE request middleware.

When ``pause_state.is_paused()`` is True, every HTTP request is gated
through :func:`_is_allowed_when_paused`. Allow-listed requests pass
through; everything else returns 423 Locked with::

    {"detail": "system_paused", "since": "<ISO-8601 or null>"}

Allow-list rules (spec §3.4 + decisions §6 row 5/6):

* Path prefixes that are *always* allowed regardless of method:
    - ``/api/v1/admin/`` (admin needs to be able to resume)
    - ``/api/v1/health`` (Cloudflare Tunnel health probes)
    - ``/api/v1/me/`` (user can still see + edit their own profile)
    - ``/api/v1/bot-status/`` (read-only review pages)
    - ``/api/v1/ws/`` (WebSocket upgrade — open already, resume instant)
    - ``/metrics`` (Prometheus scrape)
* Method ``OPTIONS`` is always allowed (CORS preflight).
* GET-only allow-list:
    - ``/api/v1/predictions/list``
    - ``/api/v1/shadow_trades/list``
    - ``/`` (SPA root)
    - ``/assets/``, ``/static/``, ``/favicon.ico``, ``/index.html``,
      ``/vite.svg`` (frontend bundle paths)

Anything not on either list → 423.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.ops import pause_state


log = logging.getLogger(__name__)

# Always allowed path prefixes (any HTTP method).
_ALWAYS_ALLOW_PREFIXES: tuple[str, ...] = (
    "/api/v1/admin/",
    "/api/v1/health",
    "/api/v1/me/",
    "/api/v1/bot-status/",
    "/api/v1/ws/",
    "/metrics",
)

# GET-only allow-list (full path or prefix).
_GET_ONLY_PATHS: tuple[str, ...] = (
    "/api/v1/predictions/list",
    "/api/v1/shadow_trades/list",
    "/",
    "/index.html",
    "/favicon.ico",
    "/vite.svg",
)
_GET_ONLY_PREFIXES: tuple[str, ...] = (
    "/assets/",
    "/static/",
)


def _is_allowed_when_paused(request: Any) -> bool:
    method = request.method.upper()
    if method == "OPTIONS":
        return True
    path = request.url.path
    for prefix in _ALWAYS_ALLOW_PREFIXES:
        if path.startswith(prefix):
            return True
    if method == "GET":
        if path in _GET_ONLY_PATHS:
            return True
        for prefix in _GET_ONLY_PREFIXES:
            if path.startswith(prefix):
                return True
    return False


def register_pause_middleware(app: FastAPI) -> None:
    """Wire the pause middleware onto an app instance.

    Called from :func:`app.main.create_app` *before* :func:`instrument_app`
    so Prometheus continues to observe the 423 path.
    """

    @app.middleware("http")
    async def _pause_middleware(  # type: ignore[no-untyped-def]
        request: Request, call_next,
    ):
        try:
            paused = await pause_state.is_paused()
        except Exception:  # noqa: BLE001
            log.warning("pause_middleware: pause_state read failed; passing through")
            paused = False
        if paused and not _is_allowed_when_paused(request):
            since: str | None = None
            try:
                state = await pause_state.get_state()
                if state.since is not None:
                    since = state.since.isoformat()
            except Exception:  # noqa: BLE001
                since = None
            return JSONResponse(
                status_code=423,
                content={"detail": "system_paused", "since": since},
            )
        return await call_next(request)


__all__ = [
    "_is_allowed_when_paused",
    "register_pause_middleware",
]
```

- [ ] **Step 5: Wire into `app/main.py`** — add the import + call right after CORS middleware registration in `create_app()`, *before* `instrument_app(app)`:

```python
from app.api.pause_middleware import register_pause_middleware
...
def create_app() -> FastAPI:
    app = FastAPI(...)
    settings = get_settings()
    if settings.env == "development":
        app.add_middleware(CORSMiddleware, ...)
    register_pause_middleware(app)
    ...
    instrument_app(app)
    return app
```

- [ ] **Step 6: Run — pass.**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest -v tests/unit/test_pause_middleware_allowlist.py
```
Expected: 24 parameter cases pass.

- [ ] **Step 7: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-pause' add backend/app/api/pause_middleware.py backend/app/api/schemas.py backend/app/main.py backend/tests/unit/test_pause_middleware_allowlist.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-pause' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-pause): pause middleware + path allowlist + 423 response"
```

---

### Task D2: `POST /api/v1/admin/system/pause` — TDD

**Files:**
- Create: `backend/app/api/routes/admin_system.py`
- Create: `backend/tests/integration/test_api_admin_system_pause_resume.py`
- Modify: `backend/app/main.py` — `app.include_router(admin_system.router)`

**Design notes:**
- Pattern mirrors `admin_news.py`: prefix `/api/v1/admin/system`, `dependencies=[Depends(require_admin)]`. The `require_admin` dep both gives us the actor's `User` (for `by_email`) and gates non-admins.
- `pause` body must include `reason` (Pydantic `min_length=1` enforces non-empty). Returns the new `SystemPauseStateOut`.
- Idempotent: pausing an already-paused system updates `since`/`by_email`/`reason` and emits a fresh audit row (acceptable — the audit log shows multiple pauses).

- [ ] **Step 1: Failing test** — `tests/integration/test_api_admin_system_pause_resume.py`:

```python
import pytest


@pytest.mark.integration
@pytest.mark.asyncio
async def test_admin_pause_returns_state_and_flips_redis(admin_client) -> None:
    from app.ops import pause_state
    resp = await admin_client.post(
        "/api/v1/admin/system/pause", json={"reason": "travel"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["paused"] is True
    assert body["by_email"] == "admin@x.com"
    assert body["reason"] == "travel"
    assert body["since"] is not None
    pause_state._CACHE = None
    assert await pause_state.is_paused() is True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_admin_pause_requires_reason(admin_client) -> None:
    resp = await admin_client.post(
        "/api/v1/admin/system/pause", json={"reason": ""},
    )
    assert resp.status_code == 422


@pytest.mark.integration
@pytest.mark.asyncio
async def test_friend_cannot_pause(friend_client) -> None:
    resp = await friend_client.post(
        "/api/v1/admin/system/pause", json={"reason": "n"},
    )
    assert resp.status_code == 403


@pytest.mark.integration
@pytest.mark.asyncio
async def test_admin_resume_clears_state(admin_client) -> None:
    from app.ops import pause_state
    await admin_client.post(
        "/api/v1/admin/system/pause", json={"reason": "r"},
    )
    resp = await admin_client.post("/api/v1/admin/system/resume", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["paused"] is False
    assert body["since"] is None
    pause_state._CACHE = None
    assert await pause_state.is_paused() is False


@pytest.mark.integration
@pytest.mark.asyncio
async def test_predict_returns_423_when_paused(admin_client, friend_client) -> None:
    await admin_client.post(
        "/api/v1/admin/system/pause", json={"reason": "r"},
    )
    # Friend tries a non-allowlisted POST.
    resp = await friend_client.post(
        "/api/v1/predict", json={"symbol": "BTC/USDT"},
    )
    assert resp.status_code == 423
    assert resp.json()["detail"] == "system_paused"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_bot_status_returns_200_when_paused(admin_client, friend_client) -> None:
    await admin_client.post(
        "/api/v1/admin/system/pause", json={"reason": "r"},
    )
    resp = await friend_client.get("/api/v1/bot-status/overview")
    assert resp.status_code == 200
```

- [ ] **Step 2: Run — fail.** Expected: 404 (router not registered).

- [ ] **Step 3: Implement** `app/api/routes/admin_system.py`:

```python
"""Admin REST for SP-PAUSE master pause/resume.

Four routes, all gated by :func:`app.auth.deps.require_admin`:

* ``POST /api/v1/admin/system/pause``     — body ``{reason: str}``
* ``POST /api/v1/admin/system/resume``    — body ``{}``
* ``GET  /api/v1/admin/system/state``     — current state
* ``GET  /api/v1/admin/system/log?limit`` — last 50 pause/resume events

The pause/resume routes both flow through :func:`app.ops.pause_state.set_paused`,
which atomically updates Redis + appends a row to ``auth_violations``.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    SystemPauseEventListOut,
    SystemPauseEventOut,
    SystemPauseRequest,
    SystemPauseStateOut,
)
from app.auth.deps import require_admin
from app.auth.models import User
from app.db.session import get_session
from app.ops import pause_state


log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/admin/system",
    tags=["admin-system"],
    dependencies=[Depends(require_admin)],
)


def _state_to_out(state: pause_state.SystemPauseState) -> SystemPauseStateOut:
    return SystemPauseStateOut(
        paused=state.paused,
        since=state.since,
        by_email=state.by_email,
        reason=state.reason,
    )


@router.post("/pause", response_model=SystemPauseStateOut)
async def pause_system(
    body: SystemPauseRequest,
    actor: User = Depends(require_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SystemPauseStateOut:
    await pause_state.set_paused(
        True, by_email=actor.email, reason=body.reason,
        session=session, request_path="/api/v1/admin/system/pause",
    )
    return _state_to_out(await pause_state.get_state())


@router.post("/resume", response_model=SystemPauseStateOut)
async def resume_system(
    actor: User = Depends(require_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SystemPauseStateOut:
    await pause_state.set_paused(
        False, by_email=actor.email, reason=None,
        session=session, request_path="/api/v1/admin/system/resume",
    )
    return _state_to_out(await pause_state.get_state())


@router.get("/state", response_model=SystemPauseStateOut)
async def get_system_state() -> SystemPauseStateOut:
    return _state_to_out(await pause_state.get_state())


@router.get("/log", response_model=SystemPauseEventListOut)
async def get_system_log(
    limit: int = Query(default=50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SystemPauseEventListOut:
    events = await pause_state.pause_event_log(session, limit=limit)
    return SystemPauseEventListOut(
        events=[
            SystemPauseEventOut(
                id=e.id, kind=e.kind, by_email=e.by_email,
                at=e.at, reason=e.reason,
            )
            for e in events
        ],
    )
```

- [ ] **Step 4: Wire into main.py** — add `from app.api.routes import admin_system` to the bulk import block at the top of `main.py`, and `app.include_router(admin_system.router)` near the other admin routers (alphabetical: between `admin_news` and `admin_patterns`).

- [ ] **Step 5: Run — pass.**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest -v tests/integration/test_api_admin_system_pause_resume.py
```
Expected: `6 passed`.

- [ ] **Step 6: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-pause' add backend/app/api/routes/admin_system.py backend/app/main.py backend/tests/integration/test_api_admin_system_pause_resume.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-pause' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-pause): admin pause/resume REST routes with 423 propagation"
```

---

### Task D3: `POST /api/v1/admin/system/resume`

**Files:** *No new files.* Resume route was already implemented inline in D2 because the same module hosts all four routes. This task verifies the behaviour in isolation.

**Design notes:**
- Resume covered by `test_admin_resume_clears_state` in D2's test file. This task is a checkpoint that we have:
  - Resume returns 200 with `paused: false`, `since: null`.
  - Resume from already-resumed state is a no-op (still 200, still `paused: false`).
- Add the no-op test to the same test file.

- [ ] **Step 1: Append failing test** — to `tests/integration/test_api_admin_system_pause_resume.py`:

```python
@pytest.mark.integration
@pytest.mark.asyncio
async def test_admin_resume_when_already_resumed_is_idempotent(admin_client) -> None:
    resp = await admin_client.post("/api/v1/admin/system/resume", json={})
    assert resp.status_code == 200
    assert resp.json()["paused"] is False
```

- [ ] **Step 2: Run — pass.** Already passes from D2.

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest -v tests/integration/test_api_admin_system_pause_resume.py::test_admin_resume_when_already_resumed_is_idempotent
```

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-pause' add backend/tests/integration/test_api_admin_system_pause_resume.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-pause' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "test(sp-pause): resume is idempotent when already resumed"
```

---

### Task D4: `GET /admin/system/state` + `GET /admin/system/log` — TDD

**Files:**
- Create: `backend/tests/integration/test_api_admin_system_state_log.py`

**Design notes:**
- Routes themselves were implemented in D2. This task adds the integration-level tests that the `state` endpoint returns 200 even when paused (allow-list works), and that the `log` endpoint paginates + returns chronological order.
- Negative case: friend gets 403 on both.

- [ ] **Step 1: Failing test** — `tests/integration/test_api_admin_system_state_log.py`:

```python
import pytest


@pytest.mark.integration
@pytest.mark.asyncio
async def test_state_returns_200_when_unpaused(admin_client) -> None:
    resp = await admin_client.get("/api/v1/admin/system/state")
    assert resp.status_code == 200
    body = resp.json()
    assert body["paused"] is False
    assert body["since"] is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_state_returns_200_when_paused(admin_client) -> None:
    await admin_client.post(
        "/api/v1/admin/system/pause", json={"reason": "r"},
    )
    resp = await admin_client.get("/api/v1/admin/system/state")
    assert resp.status_code == 200
    body = resp.json()
    assert body["paused"] is True
    assert body["by_email"] == "admin@x.com"
    assert body["reason"] == "r"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_log_returns_recent_events_newest_first(admin_client) -> None:
    for i in range(3):
        await admin_client.post(
            "/api/v1/admin/system/pause", json={"reason": f"r{i}"},
        )
        await admin_client.post(
            "/api/v1/admin/system/resume", json={},
        )
    resp = await admin_client.get("/api/v1/admin/system/log?limit=4")
    assert resp.status_code == 200
    events = resp.json()["events"]
    assert len(events) == 4
    assert events[0]["kind"] == "system_resumed"
    assert events[1]["kind"] == "system_paused"
    assert events[1]["reason"] == "r2"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_friend_cannot_get_state(friend_client) -> None:
    resp = await friend_client.get("/api/v1/admin/system/state")
    assert resp.status_code == 403


@pytest.mark.integration
@pytest.mark.asyncio
async def test_friend_cannot_get_log(friend_client) -> None:
    resp = await friend_client.get("/api/v1/admin/system/log")
    assert resp.status_code == 403
```

- [ ] **Step 2: Run — pass.** Routes already implemented in D2.

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest -v tests/integration/test_api_admin_system_state_log.py
```
Expected: `5 passed`.

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-pause' add backend/tests/integration/test_api_admin_system_state_log.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-pause' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "test(sp-pause): integration coverage for /admin/system/state + /log"
```

---

## Phase E — Frontend SystemPauseControl + PausedBanner + ship log

### Task E1: API types + fetchers in `lib/api.ts`

**Files:**
- Modify: `frontend/src/lib/api.ts`

**Design notes:**
- Mirrors the SP-3.5 `IntermarketSnapshot` + `getIntermarket()` pattern (lines 553-572) and the `api.adminToggleTrap` pattern (line 545) for POST routes.
- Four new pieces of API surface:
  - `SystemPauseState` interface
  - `SystemPauseEvent` interface + `SystemPauseEventList` interface
  - `getSystemState()`, `getSystemLog(limit?)`, `pauseSystem(reason)`, `resumeSystem()` functions

- [ ] **Step 1: Append to `frontend/src/lib/api.ts`**:

```ts
// SP-PAUSE: master pause/resume
export interface SystemPauseState {
  paused: boolean;
  since: string | null;       // ISO 8601 UTC
  by_email: string | null;
  reason: string | null;
}

export interface SystemPauseEvent {
  id: number;
  kind: "system_paused" | "system_resumed";
  by_email: string;
  at: string;                  // ISO 8601 UTC
  reason: string | null;
}

export interface SystemPauseEventList {
  events: SystemPauseEvent[];
}

export async function getSystemState(): Promise<SystemPauseState> {
  return fetchJson<SystemPauseState>("/admin/system/state");
}

export async function getSystemLog(
  limit: number = 50,
): Promise<SystemPauseEventList> {
  return fetchJson<SystemPauseEventList>(
    `/admin/system/log?limit=${encodeURIComponent(String(limit))}`,
  );
}

export async function pauseSystem(reason: string): Promise<SystemPauseState> {
  return fetchJson<SystemPauseState>("/admin/system/pause", {
    method: "POST",
    body: { reason },
  });
}

export async function resumeSystem(): Promise<SystemPauseState> {
  return fetchJson<SystemPauseState>("/admin/system/resume", {
    method: "POST",
    body: {},
  });
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd worktrees/sp-pause/frontend
npm run build 2>&1 | head -40
```
Expected: build succeeds; no TS errors. (No new test yet — tests live in E2/E4.)

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-pause' add frontend/src/lib/api.ts
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-pause' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-pause): TS types + fetchers for system pause/resume API"
```

---

### Task E2: `SystemPauseControl.tsx` component — TDD (Vitest)

**Files:**
- Create: `frontend/src/tabs/Settings/SystemPauseControl.tsx`
- Create: `frontend/tests/unit/SystemPauseControl.test.tsx`

**Design notes:**
- Big colored button (red when running → "Pause"; green when paused → "Resume"). Matches the spec §3.6 contract.
- Reason textarea required to pause (non-empty); Pause button disabled until non-empty.
- When paused: shows `Paused since {since} UTC by {by_email}` + the reason.
- Polling: every 5 seconds via `setInterval` inside `useEffect` (NO TanStack Query — explicitly rejected in the prompt). Initial fetch on mount; cleanup clears the interval.
- After pause/resume, optimistically calls `getSystemState()` once and updates state; the next poll cycle will reconcile.
- Error states: any fetch error shows a small inline error message; the polling loop continues.

- [ ] **Step 1: Failing test** — `frontend/tests/unit/SystemPauseControl.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  getSystemState: vi.fn(),
  getSystemLog: vi.fn(),
  pauseSystem: vi.fn(),
  resumeSystem: vi.fn(),
}));

import {
  getSystemState,
  pauseSystem,
  resumeSystem,
} from "@/lib/api";
import { SystemPauseControl } from "@/tabs/Settings/SystemPauseControl";

const mockedGet = vi.mocked(getSystemState);
const mockedPause = vi.mocked(pauseSystem);
const mockedResume = vi.mocked(resumeSystem);

beforeEach(() => {
  vi.useFakeTimers();
  mockedGet.mockReset();
  mockedPause.mockReset();
  mockedResume.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("SystemPauseControl", () => {
  test("renders Pause button when running", async () => {
    mockedGet.mockResolvedValueOnce({
      paused: false, since: null, by_email: null, reason: null,
    });
    render(<SystemPauseControl />);
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /^pause$/i }),
      ).toBeInTheDocument();
    });
  });

  test("Pause button is disabled with empty reason", async () => {
    mockedGet.mockResolvedValueOnce({
      paused: false, since: null, by_email: null, reason: null,
    });
    render(<SystemPauseControl />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^pause$/i })).toBeDisabled();
    });
  });

  test("entering reason + clicking Pause calls pauseSystem", async () => {
    mockedGet.mockResolvedValue({
      paused: false, since: null, by_email: null, reason: null,
    });
    mockedPause.mockResolvedValueOnce({
      paused: true, since: "2026-05-06T12:00:00+00:00",
      by_email: "admin@x.com", reason: "travel",
    });
    render(<SystemPauseControl />);
    await waitFor(() => screen.getByRole("button", { name: /^pause$/i }));
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "travel" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^pause$/i }));
    await waitFor(() => {
      expect(mockedPause).toHaveBeenCalledWith("travel");
    });
  });

  test("renders Resume + paused-since text when paused", async () => {
    mockedGet.mockResolvedValueOnce({
      paused: true, since: "2026-05-06T12:34:00+00:00",
      by_email: "admin@x.com", reason: "travel",
    });
    render(<SystemPauseControl />);
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /^resume$/i }),
      ).toBeInTheDocument();
    });
    expect(screen.getByText(/admin@x\.com/)).toBeInTheDocument();
    expect(screen.getByText(/travel/)).toBeInTheDocument();
  });

  test("clicking Resume calls resumeSystem", async () => {
    mockedGet.mockResolvedValue({
      paused: true, since: "2026-05-06T12:00:00+00:00",
      by_email: "admin@x.com", reason: "r",
    });
    mockedResume.mockResolvedValueOnce({
      paused: false, since: null, by_email: null, reason: null,
    });
    render(<SystemPauseControl />);
    await waitFor(() => screen.getByRole("button", { name: /^resume$/i }));
    fireEvent.click(screen.getByRole("button", { name: /^resume$/i }));
    await waitFor(() => {
      expect(mockedResume).toHaveBeenCalledTimes(1);
    });
  });

  test("polls every 5 seconds", async () => {
    mockedGet.mockResolvedValue({
      paused: false, since: null, by_email: null, reason: null,
    });
    render(<SystemPauseControl />);
    await waitFor(() => expect(mockedGet).toHaveBeenCalledTimes(1));
    vi.advanceTimersByTime(5_000);
    await waitFor(() => expect(mockedGet).toHaveBeenCalledTimes(2));
    vi.advanceTimersByTime(5_000);
    await waitFor(() => expect(mockedGet).toHaveBeenCalledTimes(3));
  });
});
```

- [ ] **Step 2: Run — fail.** Expected: module not found.

```bash
cd worktrees/sp-pause/frontend
npm run test -- --run SystemPauseControl
```

- [ ] **Step 3: Implement** `frontend/src/tabs/Settings/SystemPauseControl.tsx`:

```tsx
import { useEffect, useState } from "react";
import { Panel } from "@/components/ui/Panel";
import {
  getSystemState,
  pauseSystem,
  resumeSystem,
  type SystemPauseState,
} from "@/lib/api";

const POLL_INTERVAL_MS = 5_000;

export function SystemPauseControl() {
  const [state, setState] = useState<SystemPauseState | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const refresh = async (): Promise<void> => {
      try {
        const s = await getSystemState();
        if (!cancelled) setState(s);
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
        }
      }
    };
    void refresh();
    const id = setInterval(() => { void refresh(); }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const onPause = async (): Promise<void> => {
    if (busy || reason.trim() === "") return;
    setBusy(true);
    setError(null);
    try {
      const s = await pauseSystem(reason.trim());
      setState(s);
      setReason("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onResume = async (): Promise<void> => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const s = await resumeSystem();
      setState(s);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (state === null) {
    return (
      <Panel title="System Pause">
        <div className="text-text-tertiary text-xs">Loading…</div>
      </Panel>
    );
  }

  if (state.paused) {
    return (
      <Panel title="System Pause">
        <div className="space-y-3">
          <div
            data-testid="paused-status"
            className="bg-yellow/10 border border-yellow text-yellow px-3 py-2 rounded text-xs font-mono"
          >
            <div>System paused.</div>
            {state.since !== null && state.by_email !== null && (
              <div>
                Paused since {state.since} UTC by{" "}
                <span className="font-semibold">{state.by_email}</span>
              </div>
            )}
            {state.reason !== null && (
              <div>Reason: {state.reason}</div>
            )}
          </div>
          <button
            type="button"
            onClick={() => { void onResume(); }}
            disabled={busy}
            className="min-h-[44px] md:min-h-0 md:h-9 px-4 bg-green text-white rounded text-sm font-mono uppercase tracking-wide disabled:opacity-50"
          >
            Resume
          </button>
          {error !== null && (
            <div className="text-red text-xs font-mono">{error}</div>
          )}
        </div>
      </Panel>
    );
  }

  return (
    <Panel title="System Pause">
      <div className="space-y-3">
        <div className="text-xs text-text-secondary">
          System running normally. Pause halts all background workers and
          returns 423 on non-admin POSTs until you resume.
        </div>
        <textarea
          aria-label="Pause reason"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Reason (required) — e.g., travel, broker outage"
          className="w-full h-20 bg-bg-base border border-border rounded p-2 text-xs font-mono"
        />
        <button
          type="button"
          onClick={() => { void onPause(); }}
          disabled={busy || reason.trim() === ""}
          className="min-h-[44px] md:min-h-0 md:h-9 px-4 bg-red text-white rounded text-sm font-mono uppercase tracking-wide disabled:opacity-50"
        >
          Pause
        </button>
        {error !== null && (
          <div className="text-red text-xs font-mono">{error}</div>
        )}
      </div>
    </Panel>
  );
}
```

- [ ] **Step 4: Run — pass.**

```bash
cd worktrees/sp-pause/frontend
npm run test -- --run SystemPauseControl
```
Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-pause' add frontend/src/tabs/Settings/SystemPauseControl.tsx frontend/tests/unit/SystemPauseControl.test.tsx
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-pause' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-pause): SystemPauseControl with 5s poll + reason-required pause"
```

---

### Task E3: Wire `SystemPauseControl` into Settings tab

**Files:**
- Modify: `frontend/src/tabs/Settings/index.tsx`

**Design notes:**
- Add a fourth sub-tab "system" rendering `<SystemPauseControl />`. Order: Profile, Trading, Secrets, **System**.

- [ ] **Step 1: Modify `frontend/src/tabs/Settings/index.tsx`**:

```tsx
import { useState } from "react";
import { Profile } from "@/tabs/Settings/Profile";
import { Trading } from "@/tabs/Settings/Trading";
import { Secrets } from "@/tabs/Settings/Secrets";
import { SystemPauseControl } from "@/tabs/Settings/SystemPauseControl";

type SubTab = "profile" | "trading" | "secrets" | "system";

const SUB_TABS: readonly { id: SubTab; label: string }[] = [
  { id: "profile", label: "Profile" },
  { id: "trading", label: "Trading" },
  { id: "secrets", label: "Secrets" },
  { id: "system", label: "System" },
];

export function Settings() {
  const [sub, setSub] = useState<SubTab>("profile");
  return (
    <div className="h-full flex flex-col overflow-hidden">
      <div
        role="tablist"
        aria-label="Settings sections"
        className="flex bg-bg-elevated border-b border-border overflow-x-auto"
      >
        {SUB_TABS.map((t) => {
          const active = t.id === sub;
          return (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={active}
              data-active={active ? "true" : "false"}
              onClick={() => setSub(t.id)}
              className={[
                "h-11 md:h-9 px-4 text-xs font-mono uppercase tracking-wide",
                "border-b-2 -mb-px transition-colors whitespace-nowrap",
                active
                  ? "text-text-primary border-text-primary bg-bg-base"
                  : "text-text-secondary border-transparent hover:text-text-primary",
              ].join(" ")}
            >
              {t.label}
            </button>
          );
        })}
      </div>
      <div className="flex-1 min-h-0 overflow-auto p-3">
        {sub === "profile" ? <Profile /> :
         sub === "trading" ? <Trading /> :
         sub === "secrets" ? <Secrets /> :
         <SystemPauseControl />}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Run all frontend tests** — verify no regression

```bash
cd worktrees/sp-pause/frontend
npm run test -- --run
```
Expected: baseline + new SystemPauseControl tests, no failures.

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-pause' add frontend/src/tabs/Settings/index.tsx
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-pause' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-pause): add 'System' sub-tab to Settings hosting SystemPauseControl"
```

---

### Task E4: `PausedBanner.tsx` + global mount in `App.tsx` — TDD (Vitest)

**Files:**
- Create: `frontend/src/components/layout/PausedBanner.tsx`
- Create: `frontend/tests/unit/PausedBanner.test.tsx`
- Modify: `frontend/src/App.tsx`

**Design notes:**
- Yellow banner at top of every page when `state.paused === true`. Mirrors `ImpersonationBanner`'s structure (see `components/layout/ImpersonationBanner.tsx`).
- Self-contained polling: `PausedBanner` calls `getSystemState()` every 5s. Renders `null` when not paused (no DOM cost when running normally).
- Click → navigates to `#/settings` (or `window.location.hash = "#/settings"` if there's no router helper).
- Body text: "System paused. Trading + ingest workers idle. Read-only mode." plus "Click to resume" link.

- [ ] **Step 1: Failing test** — `frontend/tests/unit/PausedBanner.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  getSystemState: vi.fn(),
}));

import { getSystemState } from "@/lib/api";
import { PausedBanner } from "@/components/layout/PausedBanner";

const mockedGet = vi.mocked(getSystemState);

beforeEach(() => {
  vi.useFakeTimers();
  mockedGet.mockReset();
});
afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("PausedBanner", () => {
  test("renders nothing when not paused", async () => {
    mockedGet.mockResolvedValueOnce({
      paused: false, since: null, by_email: null, reason: null,
    });
    const { container } = render(<PausedBanner />);
    await waitFor(() => expect(mockedGet).toHaveBeenCalled());
    expect(container.querySelector('[data-testid="paused-banner"]')).toBeNull();
  });

  test("renders banner with message when paused", async () => {
    mockedGet.mockResolvedValueOnce({
      paused: true, since: "2026-05-06T12:00:00+00:00",
      by_email: "admin@x.com", reason: "travel",
    });
    render(<PausedBanner />);
    await waitFor(() => {
      expect(screen.getByTestId("paused-banner")).toBeInTheDocument();
    });
    expect(screen.getByText(/Read-only mode/i)).toBeInTheDocument();
  });

  test("re-polls every 5 seconds", async () => {
    mockedGet.mockResolvedValue({
      paused: false, since: null, by_email: null, reason: null,
    });
    render(<PausedBanner />);
    await waitFor(() => expect(mockedGet).toHaveBeenCalledTimes(1));
    vi.advanceTimersByTime(5_000);
    await waitFor(() => expect(mockedGet).toHaveBeenCalledTimes(2));
  });

  test("uses yellow background for warning visibility", async () => {
    mockedGet.mockResolvedValueOnce({
      paused: true, since: null, by_email: null, reason: null,
    });
    render(<PausedBanner />);
    await waitFor(() => screen.getByTestId("paused-banner"));
    expect(screen.getByTestId("paused-banner").className).toMatch(/bg-yellow/);
  });
});
```

- [ ] **Step 2: Run — fail.**

- [ ] **Step 3: Implement** `frontend/src/components/layout/PausedBanner.tsx`:

```tsx
import { useEffect, useState } from "react";
import { getSystemState, type SystemPauseState } from "@/lib/api";

const POLL_INTERVAL_MS = 5_000;

export function PausedBanner() {
  const [state, setState] = useState<SystemPauseState | null>(null);

  useEffect(() => {
    let cancelled = false;
    const refresh = async (): Promise<void> => {
      try {
        const s = await getSystemState();
        if (!cancelled) setState(s);
      } catch {
        // Swallow — banner stays in last-known state on transient errors.
      }
    };
    void refresh();
    const id = setInterval(() => { void refresh(); }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  if (state === null || !state.paused) return null;

  return (
    <div
      role="banner"
      data-testid="paused-banner"
      className="flex items-center justify-between bg-yellow/20 border-b border-yellow text-yellow px-3 py-2"
    >
      <div className="text-sm font-mono">
        <span className="font-semibold">System paused.</span> Trading + ingest workers idle. Read-only mode.
      </div>
      <a
        href="#/settings"
        className="min-h-[44px] md:min-h-0 md:h-8 px-3 text-xs uppercase tracking-wide bg-yellow/30 hover:bg-yellow/40 rounded border border-yellow flex items-center"
      >
        Resume in Settings
      </a>
    </div>
  );
}
```

- [ ] **Step 4: Mount in `App.tsx`** — modify `frontend/src/App.tsx`:

```tsx
import { TabNav } from "@/components/layout/TabNav";
import { ImpersonationBanner } from "@/components/layout/ImpersonationBanner";
import { PausedBanner } from "@/components/layout/PausedBanner";
import { useHashRoute } from "@/lib/useHashRoute";
...

export default function App() {
  ...
  return (
    <div className="h-screen flex flex-col bg-bg-base text-text-primary">
      <PausedBanner />
      {isImpersonating && user !== null && (
        <ImpersonationBanner ... />
      )}
      <TabNav active={tab} onChange={setTab} adminVisible={isAdmin} />
      <div className="flex-1 min-h-0">
        ...
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Run — pass.**

```bash
cd worktrees/sp-pause/frontend
npm run test -- --run PausedBanner
```
Expected: `4 passed`.

- [ ] **Step 6: Run full Vitest suite** to verify no App.test.tsx regression

```bash
cd worktrees/sp-pause/frontend
npm run test -- --run
```
Expected: baseline + 10 new (6 SystemPauseControl + 4 PausedBanner). If `App.test.tsx` fails because the banner now polls, mock `getSystemState` in that file (returning `paused:false`) — then it stays a no-op.

- [ ] **Step 7: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-pause' add frontend/src/components/layout/PausedBanner.tsx frontend/tests/unit/PausedBanner.test.tsx frontend/src/App.tsx
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-pause' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-pause): PausedBanner global banner + 5s poll mounted in App.tsx"
```

---

### Task E5: Ship log entry + final regression

**Files:**
- Create: `docs/superpowers/notes/2026-05-06-SP-PAUSE-ship.md`

**Design notes:**
- One-page operator-style log entry summarising what shipped, what acceptance criteria were met, and the test-count delta. Mirrors the SP-9 / SP-3.5 ship-log format.

- [ ] **Step 1: Write log entry** — `worktrees/sp-pause/docs/superpowers/notes/2026-05-06-SP-PAUSE-ship.md`:

```markdown
# 2026-05-06 — SP-PAUSE Master Pause/Resume shipped

## Summary
Single-flag whole-system pause via `system:paused` Redis key, gated on every
long-running worker tick + every non-allow-listed HTTP request. Operator
toggles via Settings → System sub-tab; global yellow banner mounted in
`App.tsx` polls `/admin/system/state` every 5s.

## What shipped
- `app/ops/pause_state.py` — `is_paused()`, `set_paused()`, `get_state()`,
  `pause_event_log()` with 1s in-process cache.
- `auth_violations` rows on every pause/resume (no chain — `auth_violations`
  has no chain columns; spec gap noted in plan §0).
- 5+1 worker tick guards: news ingest, news cleanup, intermarket snapshot,
  intermarket cleanup, shadow worker `_handle_candle`, audit verifier.
- `pause_middleware.py` returns 423 with allow-list covering admin/me/health/
  bot-status/metrics/ws + GET on predictions/shadow_trades list + frontend
  static assets.
- Admin REST: `POST /api/v1/admin/system/{pause,resume}`, `GET /state`, `GET /log`.
- Frontend: `SystemPauseControl` (Settings sub-tab) + `PausedBanner` (global).

## Acceptance criteria
- [x] POST /admin/system/pause flips Redis flag, returns 200 with `paused: true`
- [x] After pause, POST /api/v1/predict returns 423 `system_paused`
- [x] After pause, GET /api/v1/bot-status/overview returns 200
- [x] News + intermarket + shadow + verifier workers idle within one tick
- [x] Existing WS connections unaffected (middleware skips `/api/v1/ws/`)
- [x] auth_violations row appended on pause AND resume
- [x] Banner visible on every page when paused
- [x] Resume restores normal operation (next worker tick)
- [x] CI green
- [x] Operator log entry (this file)

## Test count delta
Backend: baseline + ~22 (3 audit + 4 cache/roundtrip + 3 event-log + 7 worker-guard + 24 middleware param-cases + 11 integration ≈ 22 new test functions covering ~52 cases).
Frontend: baseline + 10 (6 SystemPauseControl + 4 PausedBanner).

## Known follow-ups (out of scope)
- Per-component pause (e.g. "pause news but keep trading") — v1 is whole-system.
- Auto-pause on broker outage/error rate — v1 is manual only.
- Full pause-history UI — `/admin/system/log` covers the immediate need.
- Hash-chain the `auth_violations` table — would be a separate migration.
- Mobile push notification on state change — Telegram via `app.ops.alerts` covers v1.
```

- [ ] **Step 2: Final regression — backend**

```bash
cd worktrees/sp-pause
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest -q
```
Expected: baseline + ~22 passes; zero new failures.

- [ ] **Step 3: Final regression — frontend**

```bash
cd worktrees/sp-pause/frontend
npm run test -- --run
```
Expected: baseline + 10 passes; zero new failures.

- [ ] **Step 4: Lint + type checks**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend ruff check .
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend mypy app
```
Expected: clean. Fix any new warnings before tagging.

- [ ] **Step 5: Commit + tag**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-pause' add docs/superpowers/notes/2026-05-06-SP-PAUSE-ship.md
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-pause' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "docs(sp-pause): ship log entry"
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-pause' tag sp-pause
```

---

## Coverage trace (spec → tasks)

| Spec section | Requirement | Task(s) |
|-|-|-|
| §3.1 | Redis key + 1s in-process cache | A1, A2 |
| §3.2 | `is_paused`/`set_paused`/`pause_event_log` API | A2, B1, B2 |
| §3.2 | Audit-row insert on each toggle | B1 |
| §3.3 | 5+1 workers gain pause guard | C1, C2, C3 |
| §3.4 | Pause middleware + path allow-list + 423 | D1 |
| §3.5 | 4 admin REST routes | D2, D3, D4 |
| §3.6 | SystemPauseControl + reason textarea + paused-since text | E2 |
| §3.6 | PausedBanner globally in App.tsx | E4 |
| §3.7 | WS open kept alive (middleware skips `/api/v1/ws/`) | D1 (allow-list) |
| §6 row 5 | `/api/v1/health` always allowed | D1 |
| §6 row 6 | `/metrics` always allowed | D1 |
| §6 row 7 | reason stored in `auth_violations.reason` | B1 |
| §7 acceptance | All 10 boxes | D2 + D4 + E5 |

---

# Report-back

**How many tasks:** 17 across 5 phases (A: 3, B: 2, C: 3, D: 4, E: 5).

**What files the plan creates** (NEW; 17 files):
- `backend/app/ops/pause_state.py`
- `backend/app/api/pause_middleware.py`
- `backend/app/api/routes/admin_system.py`
- `backend/tests/unit/test_pause_state_roundtrip.py`
- `backend/tests/unit/test_pause_state_cache.py`
- `backend/tests/unit/test_pause_state_audit_log.py`
- `backend/tests/unit/test_pause_state_event_log_reader.py`
- `backend/tests/unit/test_pause_middleware_allowlist.py`
- `backend/tests/unit/test_workers_pause_guard_news.py`
- `backend/tests/unit/test_workers_pause_guard_intermarket.py`
- `backend/tests/unit/test_workers_pause_guard_shadow_verifier.py`
- `backend/tests/integration/test_api_admin_system_pause_resume.py`
- `backend/tests/integration/test_api_admin_system_state_log.py`
- `frontend/src/tabs/Settings/SystemPauseControl.tsx`
- `frontend/src/components/layout/PausedBanner.tsx`
- `frontend/tests/unit/SystemPauseControl.test.tsx`
- `frontend/tests/unit/PausedBanner.test.tsx`
- `docs/superpowers/notes/2026-05-06-SP-PAUSE-ship.md`

**What files the plan modifies** (10 files):
- `backend/pyproject.toml` (add `fakeredis>=2.20.1`)
- `backend/tests/conftest.py` (autouse pause-state fixture)
- `backend/app/news/ingest_worker.py` (pause guard in 2 loops)
- `backend/app/data/intermarket_worker.py` (pause guard in 2 loops)
- `backend/app/shadow/worker.py` (pause guard in `_handle_candle`)
- `backend/app/ops/verifier_scheduler.py` (pause guard before `_check_all_chains`)
- `backend/app/api/schemas.py` (4 new Pydantic models)
- `backend/app/main.py` (register middleware + admin_system router)
- `frontend/src/lib/api.ts` (4 types + 4 fetchers)
- `frontend/src/tabs/Settings/index.tsx` (4th sub-tab)
- `frontend/src/App.tsx` (mount PausedBanner)

**Spec gaps I couldn't translate cleanly to tasks** (kept as design decisions in §0 — flag for user review):

1. **No hash-chain on `auth_violations`.** The spec says "audit chain entry created on pause AND resume" / "with chained hash" (§3.2, §7). The existing `auth_violations` table from migration `0002_audit_chain` has only `(id, attempted_email, attempted_at, reason, jwt_sub, request_path)` — no `prev_hash`/`row_hash` columns; SP-7's `verifier_scheduler` doesn't expect them either. The plan inserts plain rows matching the existing `_record_violation` precedent. **If the user actually wants a hash chain on `auth_violations`, that needs an extra migration task** (split `auth_violations` into chained vs non-chained rows, or add chain columns + retroactive `GENESIS_HASH` on existing rows).

2. **Redis client wrapper does not exist.** `redis==5.2.1` is in `pyproject.toml` but no module imports it. SP-PAUSE is the first user; `pause_state._get_redis()` lazy-instantiates `redis.asyncio.from_url(settings.redis_url, ...)` itself. If a future feature wants a shared Redis client, refactoring `_get_redis` into `app/db/redis.py` is straightforward.

3. **`fakeredis` not in deps.** Plan adds `fakeredis>=2.20.1` to `[project.optional-dependencies] test`. If the user prefers an in-memory dict mock instead (no new dep), Phase A2's tests collapse from 7 to roughly the same count but the fixture in A3 needs to swap `fakeredis.aioredis.FakeRedis()` for a hand-rolled `_FakeRedis` class. I chose `fakeredis` because it covers `MGET`/`DELETE`/`SET`/`GET` semantics for free — meaningful for the `get_state()` test which uses `MGET`.

4. **Settings folder path.** Spec/prompt mentioned `frontend/src/Settings/`. Actual is `frontend/src/tabs/Settings/`. Plan uses the actual path (verified `Profile.tsx`/`Trading.tsx`/`Secrets.tsx` live there).

5. **Shadow worker pause-guard shape.** The prompt's standard tick-guard shape (`is_paused → log → sleep → continue`) doesn't fit the candle-driven shadow worker (no `tick_seconds`). The plan uses an early `return` inside `_handle_candle` instead — natural pacing comes from the candle stream itself.

### Critical Files for Implementation
- worktrees/sp-pause/backend/app/ops/pause_state.py (the entire feature pivots on this module)
- worktrees/sp-pause/backend/app/api/pause_middleware.py (the request-level gate; defines what 423s and what doesn't)
- worktrees/sp-pause/backend/app/api/routes/admin_system.py (the admin REST surface — drives Phase E frontend)
- worktrees/sp-pause/backend/app/main.py (wiring: middleware before instrument_app + 4th admin router)
- worktrees/sp-pause/frontend/src/tabs/Settings/SystemPauseControl.tsx (operator UX — the toggle that actually runs in production)