"""Top-level package for long-running background workers.

Each module here defines one worker loop + a ``start_*`` factory used
from ``app.main.lifespan`` to spawn the task. Workers MUST register a
``WorkerSpec`` in ``app.ops.worker_registry`` so the watchdog can
observe them.
"""
