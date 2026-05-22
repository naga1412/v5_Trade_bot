"""PR-BRAIN-BOOTSTRAP-FIX-3 — assert register_brain.py has /app on sys.path.

When the cron invokes register_brain.py via `docker compose exec -T backend`,
the shell does NOT inherit uvicorn's PYTHONPATH — only the script's own
directory is added to sys.path. So `from app.db.session import ...` (used
in --direct mode at line 133) fails with ModuleNotFoundError unless the
script itself prepends /app to sys.path.

Same bug class as PR-BRAIN-BOOTSTRAP-FIX-1's train_brain.py fix. Static
string check guards against accidental revert.
"""
from __future__ import annotations

from pathlib import Path


_REGISTER_BRAIN = (
    Path(__file__).resolve().parents[3] / "tools" / "ml" / "register_brain.py"
)


def test_register_brain_has_sys_path_insert() -> None:
    """register_brain.py must add /app to sys.path before the `app.*` import.

    Without this, `--direct` mode (which imports app.db.session) fails with
    ModuleNotFoundError when invoked via `docker compose exec`.
    """
    text = _REGISTER_BRAIN.read_text(encoding="utf-8")
    assert 'sys.path.insert(0, "/app")' in text, (
        "register_brain.py must contain sys.path.insert(0, \"/app\") so that "
        "the --direct mode `from app.db.session import ...` resolves. See "
        "docs/superpowers/specs/2026-05-22-pr-brain-bootstrap-fix-2.md "
        "post-deploy probe analysis."
    )
