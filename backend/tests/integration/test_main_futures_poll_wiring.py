"""Phase 4 Task 17: futures_poll_task is registered in the worker registry.

Confirms the watchdog and worker-heartbeat census can see futures_poll_task
the same way they see ws_keepalive_task -- a registry entry with no matching
lifespan wiring (or vice versa) is exactly the class of bug
test_worker_registry_consistency.py exists to catch; this test is the
Task-17-specific companion assertion tying the registry entry to the actual
exported WORKER_NAME constant the running supervisor uses for its
record_heartbeat(...) calls.
"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_futures_poll_task_is_registered_in_worker_registry() -> None:
    from app.ops.worker_registry import WORKER_REGISTRY
    from app.ws.futures_poll import WORKER_NAME

    assert WORKER_NAME in {w.name for w in WORKER_REGISTRY}


def test_futures_poll_task_is_wired_into_main_lifespan() -> None:
    """WorkerSpec alone isn't enough -- confirm main.py actually imports and
    calls start_futures_poll_task, mirroring how ws_keepalive_task is wired.
    """
    import inspect

    import app.main as main_module
    from app.ws.futures_poll import WORKER_NAME

    src = inspect.getsource(main_module)
    assert "start_futures_poll_task" in src
    assert WORKER_NAME in src


def test_futures_poll_task_shutdown_mirrors_ws_keepalive_task() -> None:
    """The shutdown block must cancel futures_poll_worker the same way it
    cancels ws_keepalive_task -- a worker started without a matching
    cancel-on-shutdown entry leaks on every hot-reload/restart in dev.
    """
    import inspect

    import app.main as main_module

    src = inspect.getsource(main_module)
    assert "if futures_poll_worker is not None:" in src
    assert "futures_poll_worker.cancel()" in src
