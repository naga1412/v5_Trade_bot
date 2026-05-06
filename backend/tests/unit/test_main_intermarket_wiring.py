import inspect

import app.main as main_module


def test_main_imports_intermarket_tasks() -> None:
    src = inspect.getsource(main_module)
    assert "start_intermarket_snapshot_task" in src
    assert "start_intermarket_cleanup_task" in src
    assert "intermarket_snapshot_task = None" in src
    assert "intermarket_cleanup_task = None" in src


def test_main_intermarket_tasks_gated_on_env_and_worker_enabled() -> None:
    src = inspect.getsource(main_module)
    # The intermarket calls must appear inside the same env-and-worker_enabled
    # block that gates the news worker.
    idx = src.index("start_intermarket_snapshot_task(get_session_factory())")
    prefix = src[:idx]
    # Most-recent guard line above the call.
    last_guard = prefix.rfind('settings.env not in {"test", "ci"}')
    assert last_guard != -1, "intermarket task is not behind the env/worker guard"
