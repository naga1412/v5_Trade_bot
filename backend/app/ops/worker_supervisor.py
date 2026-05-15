"""Process-local registry of (worker → factory + current task).

Built so the in-process watchdog can do more than alert when a non-
stateful worker goes silent — it can actually replace the dead task
with a fresh one and write the heal action to the log.

Why a separate module instead of stuffing this into ``main.py``'s
lifespan? Three reasons:

1. The watchdog runs *inside* the same event loop as the workers, but
   it lives in ``app/ops/`` and shouldn't depend on ``app/main.py``
   (circular import). A neutral third module both can import is the
   cleanest split.
2. Tests need to drive ``restart()`` in isolation. A module-level dict
   is trivially monkey-patchable; a lifespan-scoped closure isn't.
3. Future ops endpoints (``POST /api/v1/admin/workers/{name}/restart``)
   need the same registry. Putting it in main means duplication.

Safety contract — read this before adding stateful workers:

The supervisor will happily restart anything you register. ONLY register
workers whose state is safe to lose: stateless polls (universe refresh,
health pinger, scanner, validator), nightly cleanups, snapshot loops.

NEVER register:
  - ``live_worker`` (holds in-memory bar history; restart drops it)
  - ``shadow_worker`` (holds open paper-trade positions in memory)
  - ``liquidation_monitor_task`` (touches the live exchange)
  - ``telegram_poller_task`` (re-init resets the offset cursor)
  - ``ws_keepalive_task`` (owns N live Binance WS connections; reopen
    too-fast hits per-IP rate limits)

The unregistered workers stay alert-only via the existing watchdog
path — the watchdog SMTPs the admin, ops handles them manually.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

log = logging.getLogger(__name__)

# How long to wait for an old (likely-stuck) task to honor cancel()
# before we give up and spawn the replacement anyway. Generous enough
# for a Binance HTTP timeout to unblock; short enough that a restart
# doesn't take the whole watchdog tick.
_CANCEL_TIMEOUT_SECONDS: float = 10.0


# name → zero-arg factory that returns a freshly-spawned asyncio.Task.
_FACTORIES: dict[str, Callable[[], asyncio.Task[None]]] = {}
# name → currently-active task. None entries are workers that were
# registered but never successfully spawned (factory raised) — rare.
_TASKS: dict[str, asyncio.Task[None] | None] = {}


def register(
    name: str, factory: Callable[[], asyncio.Task[None]],
) -> asyncio.Task[None]:
    """Spawn the worker now and remember its factory for future restarts.

    Caller pattern in ``main.py`` lifespan:

        ws_keepalive_task = supervisor.register(
            "scanner_batch_task",
            lambda: start_scanner_batch_task(get_session_factory()),
        )

    The returned ``Task`` is the same one stored internally — caller can
    still cancel it directly during lifespan shutdown, OR call
    ``cancel_all()`` to cancel everything supervisor-tracked at once.
    """
    if name in _FACTORIES:
        log.warning(
            "worker_supervisor.register: %s already registered; overwriting",
            name,
        )
    task = factory()
    _FACTORIES[name] = factory
    _TASKS[name] = task
    log.info("worker_supervisor.register: %s spawned (task=%s)", name, id(task))
    return task


def is_registered(name: str) -> bool:
    return name in _FACTORIES


def get_task(name: str) -> asyncio.Task[None] | None:
    """Return the current task for a registered worker, or None.

    Used by ops admin endpoints + by tests to assert restart replaced
    the task identity.
    """
    return _TASKS.get(name)


async def restart(name: str) -> bool:
    """Cancel the current task and spawn a replacement via the factory.

    Returns True iff the worker is registered AND a fresh task was
    successfully spawned. False otherwise (unregistered, or factory
    raised — both are logged).

    The cancel + wait is bounded by ``_CANCEL_TIMEOUT_SECONDS`` so a
    permanently-hung old task doesn't block the new one indefinitely.
    """
    factory = _FACTORIES.get(name)
    if factory is None:
        log.warning("worker_supervisor.restart: %s not registered", name)
        return False

    old = _TASKS.get(name)
    if old is not None and not old.done():
        old.cancel()
        try:
            await asyncio.wait_for(old, timeout=_CANCEL_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            # Expected — the task honored cancel().
            pass
        except asyncio.TimeoutError:
            log.warning(
                "worker_supervisor.restart: %s did not honor cancel within "
                "%.1fs; spawning replacement anyway",
                name, _CANCEL_TIMEOUT_SECONDS,
            )
        except Exception as e:  # noqa: BLE001
            log.warning(
                "worker_supervisor.restart: %s old-task await raised: %s",
                name, e,
            )

    try:
        new = factory()
    except Exception as e:  # noqa: BLE001
        log.error("worker_supervisor.restart: %s factory raised: %s", name, e)
        _TASKS[name] = None
        return False

    _TASKS[name] = new
    log.warning(
        "worker_supervisor.restart: %s respawned (new_task=%s)", name, id(new),
    )
    return True


def cancel_all() -> list[asyncio.Task[None]]:
    """Cancel every registered worker. Called from the lifespan finally.

    Returns the list of cancelled tasks so the caller can ``await`` them
    if it wants to confirm clean shutdown.
    """
    cancelled: list[asyncio.Task[None]] = []
    for name, task in _TASKS.items():
        if task is None or task.done():
            continue
        task.cancel()
        cancelled.append(task)
        log.info("worker_supervisor.cancel_all: cancelled %s", name)
    return cancelled


def _reset_for_tests() -> None:
    """Clear the registry. Test fixture only — never call from prod code."""
    _FACTORIES.clear()
    _TASKS.clear()


__all__ = [
    "cancel_all",
    "get_task",
    "is_registered",
    "register",
    "restart",
]
