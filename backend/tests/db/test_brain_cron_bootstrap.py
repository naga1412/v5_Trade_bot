"""Infrastructure tests for PR-BRAIN-BOOTSTRAP-FIX.

Three discrete one-line fixes that unblock the brain training cron:

1. Cron script must be marked executable in the git index (100755).
2. tools/ml/train_brain.py must prepend /app to sys.path so that
   `from app.rl... import ...` resolves when the script is invoked
   via the /app/host-tools bind mount.
3. The rl-cache volume in docker-compose.yml must be mounted read-write
   so the trainer can persist new .pt checkpoints.

Each test is a static infrastructure check (no Postgres / container needed).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import yaml  # type: ignore[import-untyped]

# Repo root: this file lives at backend/tests/db/test_brain_cron_bootstrap.py
_REPO_ROOT = Path(__file__).resolve().parents[3]


def test_brain_cron_script_has_executable_bit_in_git_index() -> None:
    """The git index entry for hetzner_brain_cron.sh must be mode 100755.

    Without the executable bit, cron fails with `Permission denied` even
    though the file content is correct. Setting the bit in the git index
    (`git update-index --chmod=+x`) ensures every checkout/deploy restores
    the right mode.
    """
    result = subprocess.run(
        ["git", "ls-files", "--stage", "backend/scripts/hetzner_brain_cron.sh"],
        cwd=str(_REPO_ROOT),
        check=True,
        capture_output=True,
        text=True,
    )
    line = result.stdout.strip()
    assert line, "git ls-files returned no output for hetzner_brain_cron.sh"
    mode = line.split()[0]
    assert mode == "100755", (
        f"backend/scripts/hetzner_brain_cron.sh has git index mode {mode!r}; "
        f"expected '100755' (executable). Run: "
        f"git update-index --chmod=+x backend/scripts/hetzner_brain_cron.sh"
    )


def test_train_brain_has_sys_path_insert() -> None:
    """tools/ml/train_brain.py must add /app to sys.path.

    Cron invokes `python /app/host-tools/ml/train_brain.py`; Python only adds
    `/app/host-tools/ml/` to sys.path, NOT `/app/`. Without the insert, the
    trainer crashes with `ModuleNotFoundError: No module named 'app'`.
    """
    train_brain = _REPO_ROOT / "tools" / "ml" / "train_brain.py"
    text = train_brain.read_text(encoding="utf-8")
    assert 'sys.path.insert(0, "/app")' in text, (
        f"{train_brain} must contain `sys.path.insert(0, \"/app\")` so that "
        f"`from app.rl... import ...` resolves under the /app/host-tools "
        f"bind mount when invoked from the cron."
    )


def test_docker_compose_rl_cache_mount_is_writable() -> None:
    """The rl-cache volume in docker-compose.yml must NOT be mounted :ro.

    The brain trainer writes new checkpoint .pt files to /app/data/rl-cache
    after each training run. A read-only mount makes the write fail with
    `Read-only file system`. ml-cache stays :ro (operator SCPs .pt files
    in, container reads them) — only rl-cache flips to read-write.
    """
    compose_path = _REPO_ROOT / "docker-compose.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    volumes = compose["services"]["backend"]["volumes"]
    rl_cache_entries = [
        v for v in volumes if isinstance(v, str) and "rl-cache" in v
    ]
    assert rl_cache_entries, (
        "docker-compose.yml backend.volumes has no rl-cache entry"
    )
    assert len(rl_cache_entries) == 1, (
        f"expected exactly one rl-cache mount, got {rl_cache_entries!r}"
    )
    entry = rl_cache_entries[0]
    assert not entry.endswith(":ro"), (
        f"rl-cache mount {entry!r} is read-only; trainer cannot write "
        f"checkpoints. Drop the `:ro` suffix to make it read-write."
    )
