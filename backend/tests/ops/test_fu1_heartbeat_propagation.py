"""FU-1: behavioural check that worker loops actually invoke record_heartbeat.

The static smoke test (``test_record_heartbeat_per_worker``) catches a
missing import; this drives a few representative worker loops through one
iteration and asserts ``record_heartbeat`` was called with the right
worker name and status. It complements the static gate by catching the
"call is there but unreachable" class of regression — e.g. a future
refactor that wraps the call in a branch that's never taken.

We don't exercise all 14 workers here. Three representatives cover the
shapes we care about:

  - ``universe_refresh_task`` — single-iteration loop with fetch+persist
    and an exception path (N-class worker, natural-table liveness +
    defense-in-depth heartbeat).
  - ``run_health_pinger_loop`` — for-loop over adapters with per-target
    try/except; aggregates ok/failed counts.
  - ``run_universe_sync_loop`` — same shape as health_pinger but with
    per-exchange diff; covers the ok+failed status-flip logic.

Each test patches ``asyncio.sleep`` with a cancellation-raising stub to
break out of the otherwise-infinite loop after one iteration.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.data import adapter_health, universe_sync
from app.shadow import universe_refresh


def _make_session_factory() -> MagicMock:
    """Mock session_factory; the heartbeat helper opens a session but we
    patch record_heartbeat itself, so the factory is never opened."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=session)


class _CancelAfter:
    """Async sleep stub that raises CancelledError after N invocations.

    Lets a single iteration of a ``while True`` loop complete before the
    next sleep, then breaks out cleanly so the test asserts on observed
    side effects.
    """

    def __init__(self, n: int = 1) -> None:
        self._remaining = n

    async def __call__(self, _seconds: float) -> None:
        if self._remaining <= 0:
            raise asyncio.CancelledError
        self._remaining -= 1


@pytest.mark.asyncio
async def test_universe_refresh_fires_heartbeat_on_success() -> None:
    """One successful refresh tick → record_heartbeat(status='ok')."""
    factory = _make_session_factory()
    event = asyncio.Event()

    fake_entry = MagicMock()
    fake_fetch = AsyncMock(return_value=[fake_entry])

    with patch.object(
        universe_refresh, "record_heartbeat", new_callable=AsyncMock,
    ) as hb, patch.object(
        universe_refresh, "_persist", new_callable=AsyncMock,
    ):
        with pytest.raises(asyncio.CancelledError):
            await universe_refresh.run_universe_refresh_loop(
                session_factory=factory,
                refreshed_event=event,
                _sleep=_CancelAfter(n=1),
                _fetch=fake_fetch,
                _now=lambda: datetime.now(UTC),
            )

    hb.assert_called_once()
    _, kwargs = hb.call_args
    assert hb.call_args.args[1] == "universe_refresh_task"
    assert kwargs["status"] == "ok"
    assert kwargs["details"]["entries"] == 1


@pytest.mark.asyncio
async def test_universe_refresh_fires_error_heartbeat_on_fetch_failure() -> None:
    """Fetch raises → record_heartbeat(status='error') with truncated reason."""
    factory = _make_session_factory()
    event = asyncio.Event()

    failing_fetch = AsyncMock(side_effect=RuntimeError("binance 451"))

    with patch.object(
        universe_refresh, "record_heartbeat", new_callable=AsyncMock,
    ) as hb:
        with pytest.raises(asyncio.CancelledError):
            await universe_refresh.run_universe_refresh_loop(
                session_factory=factory,
                refreshed_event=event,
                _sleep=_CancelAfter(n=1),
                _fetch=failing_fetch,
                _now=lambda: datetime.now(UTC),
            )

    hb.assert_called_once()
    kwargs = hb.call_args.kwargs
    assert hb.call_args.args[1] == "universe_refresh_task"
    assert kwargs["status"] == "error"
    assert "binance 451" in kwargs["details"]["error"]


@pytest.mark.asyncio
async def test_health_pinger_aggregates_per_adapter_status() -> None:
    """One adapter ok + one adapter raising → heartbeat status='error' with counts."""
    factory = _make_session_factory()

    healthy_adapter = MagicMock()
    healthy_adapter.health_check = AsyncMock(
        return_value=adapter_health.HealthResult(is_healthy=True, latency_ms=10),
    )
    failing_adapter_name = "broken"
    healthy_adapter_name = "ok_one"

    def fake_get_adapter(name: str):
        if name == healthy_adapter_name:
            return healthy_adapter
        raise RuntimeError("adapter init failed")

    with patch.object(
        adapter_health, "record_heartbeat", new_callable=AsyncMock,
    ) as hb, patch.object(
        adapter_health, "list_registered",
        return_value=[healthy_adapter_name, failing_adapter_name],
    ), patch.object(
        adapter_health, "get_adapter", side_effect=fake_get_adapter,
    ), patch.object(
        adapter_health, "record_health", new_callable=AsyncMock,
    ):
        with pytest.raises(asyncio.CancelledError):
            await adapter_health.run_health_pinger_loop(
                session_factory=factory,
                interval_sec=1,
                _sleep=_CancelAfter(n=1),
            )

    hb.assert_called_once()
    kwargs = hb.call_args.kwargs
    assert hb.call_args.args[1] == "health_pinger_task"
    assert kwargs["status"] == "error"
    assert kwargs["details"]["adapters_ok"] == 1
    assert kwargs["details"]["adapters_failed"] == 1


@pytest.mark.asyncio
async def test_universe_sync_aggregates_per_exchange_status() -> None:
    """All exchanges ok → heartbeat status='ok' with the success counter."""
    factory = _make_session_factory()

    async def fake_sync(adapter, session, **_kw):
        return universe_sync.SyncResult(added=0, still_active=5, newly_delisted=0)

    with patch.object(
        universe_sync, "record_heartbeat", new_callable=AsyncMock,
    ) as hb, patch.object(
        universe_sync, "get_adapter", return_value=MagicMock(name="adapter"),
    ), patch.object(
        universe_sync, "sync_universe", side_effect=fake_sync,
    ):
        with pytest.raises(asyncio.CancelledError):
            await universe_sync.run_universe_sync_loop(
                session_factory=factory,
                exchanges=["binance_futures"],
                _sleep=_CancelAfter(n=1),
                _now=lambda: datetime.now(UTC),
            )

    hb.assert_called_once()
    kwargs = hb.call_args.kwargs
    assert hb.call_args.args[1] == "universe_sync_task"
    assert kwargs["status"] == "ok"
    assert kwargs["details"]["exchanges_ok"] == 1
    assert kwargs["details"]["exchanges_failed"] == 0
