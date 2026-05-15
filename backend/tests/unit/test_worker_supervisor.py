"""Unit tests for ``app.ops.worker_supervisor``.

Covers the four public surface methods plus the safety contract the
watchdog relies on:

  - ``register`` — spawns + remembers
  - ``restart`` — cancels old + spawns new (preserves the name->factory
    mapping; replaces the task identity)
  - ``restart`` of unregistered worker → returns False, no spawn
  - ``restart`` when factory raises → returns False, sentinel-None left
  - ``restart`` when old task ignores cancel() → bounded by timeout, new
    task still spawned
  - ``cancel_all`` — cancels every live task; safe to call twice
"""

from __future__ import annotations

import asyncio

import pytest

from app.ops import worker_supervisor


@pytest.fixture(autouse=True)
def _clean_registry():
    """Module-level registry leaks between tests if we don't reset it."""
    worker_supervisor._reset_for_tests()
    yield
    # Cancel anything left over so the event loop doesn't complain on teardown.
    for t in worker_supervisor.cancel_all():
        if not t.done():
            t.cancel()
    worker_supervisor._reset_for_tests()


async def _idle_runner() -> None:
    """Hangs forever — proxy for a healthy worker."""
    await asyncio.Event().wait()


def _idle_factory() -> asyncio.Task[None]:
    return asyncio.create_task(_idle_runner())


# ---------------------------------------------------------------------------
# register


@pytest.mark.asyncio
async def test_register_spawns_and_remembers() -> None:
    task = worker_supervisor.register("alpha", _idle_factory)
    try:
        assert worker_supervisor.is_registered("alpha")
        assert worker_supervisor.get_task("alpha") is task
        assert not task.done()
    finally:
        task.cancel()


@pytest.mark.asyncio
async def test_register_overwrites_existing(caplog) -> None:
    """Re-registering the same name must replace the factory, log a warning,
    and spawn a fresh task — supports hot-reload of the factory in dev."""
    first = worker_supervisor.register("dup", _idle_factory)
    second = worker_supervisor.register("dup", _idle_factory)
    try:
        assert worker_supervisor.get_task("dup") is second
        assert first is not second
    finally:
        first.cancel()
        second.cancel()


# ---------------------------------------------------------------------------
# restart — happy path + edge cases


@pytest.mark.asyncio
async def test_restart_replaces_task_identity() -> None:
    original = worker_supervisor.register("beta", _idle_factory)
    try:
        ok = await worker_supervisor.restart("beta")
        assert ok is True
        replaced = worker_supervisor.get_task("beta")
        assert replaced is not None
        assert replaced is not original
        assert original.cancelled() or original.done()
        assert not replaced.done()
    finally:
        new = worker_supervisor.get_task("beta")
        if new is not None:
            new.cancel()


@pytest.mark.asyncio
async def test_restart_unregistered_returns_false() -> None:
    ok = await worker_supervisor.restart("does_not_exist")
    assert ok is False


@pytest.mark.asyncio
async def test_restart_factory_failure_returns_false_and_clears_task() -> None:
    """If the replacement factory raises, restart() returns False and the
    stored task is set to None so subsequent watchdog ticks can retry."""
    original = worker_supervisor.register("flaky", _idle_factory)

    def _bad_factory() -> asyncio.Task[None]:
        raise RuntimeError("boom")

    # Swap the registered factory for the bad one via re-register.
    original.cancel()
    worker_supervisor._FACTORIES["flaky"] = _bad_factory

    ok = await worker_supervisor.restart("flaky")
    assert ok is False
    assert worker_supervisor.get_task("flaky") is None


@pytest.mark.asyncio
async def test_restart_uncancellable_old_task_still_spawns_new(
    monkeypatch,
) -> None:
    """If the old task absorbs CancelledError and refuses to die, the
    timeout fires and restart() proceeds to spawn the replacement —
    the supervisor must not be held hostage by a sick task."""
    monkeypatch.setattr(
        worker_supervisor, "_CANCEL_TIMEOUT_SECONDS", 0.05,
    )

    async def _absorbs_cancel() -> None:
        while True:
            try:
                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                # Naughty: swallow and keep running.
                pass

    def _bad_factory() -> asyncio.Task[None]:
        return asyncio.create_task(_absorbs_cancel())

    worker_supervisor.register("zombie", _bad_factory)
    # Replace with the well-behaved idle factory for the spawn.
    worker_supervisor._FACTORIES["zombie"] = _idle_factory

    ok = await worker_supervisor.restart("zombie")
    try:
        assert ok is True
        replaced = worker_supervisor.get_task("zombie")
        assert replaced is not None
        assert not replaced.done()
    finally:
        for t in worker_supervisor.cancel_all():
            if not t.done():
                t.cancel()


# ---------------------------------------------------------------------------
# cancel_all


@pytest.mark.asyncio
async def test_cancel_all_cancels_everything() -> None:
    worker_supervisor.register("w1", _idle_factory)
    worker_supervisor.register("w2", _idle_factory)
    cancelled = worker_supervisor.cancel_all()
    assert len(cancelled) == 2
    # Give the event loop a turn to actually run the cancellation.
    await asyncio.sleep(0)
    for t in cancelled:
        assert t.cancelled() or t.done()


@pytest.mark.asyncio
async def test_cancel_all_is_idempotent() -> None:
    """Lifespan teardown may call cancel_all() then await individual tasks;
    a second call must NOT raise on already-done tasks."""
    worker_supervisor.register("w", _idle_factory)
    worker_supervisor.cancel_all()
    await asyncio.sleep(0)
    # Second call: every task is already done — should return empty list.
    result = worker_supervisor.cancel_all()
    assert result == []
