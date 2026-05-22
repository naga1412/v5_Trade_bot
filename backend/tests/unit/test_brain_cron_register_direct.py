"""PR-BRAIN-BOOTSTRAP-FIX-2 — assert hetzner_brain_cron.sh uses register --direct.

The HTTP path POST /api/v1/admin/rl-checkpoints is gated by Cloudflare Access
JWT (require_admin → require_user → require_cf_user) in ENV=production. The
cron has no JWT/bearer, so the HTTP call would 401 silently. --direct
bypasses HTTP and writes via SQLAlchemy.

These are static string tests so a regression (e.g., accidental revert) is
caught at CI time without needing the actual prod env.
"""
from __future__ import annotations

import re
from pathlib import Path


_CRON_SCRIPT = (
    Path(__file__).resolve().parents[3] / "backend" / "scripts" / "hetzner_brain_cron.sh"
)


def _read_register_block() -> str:
    """Return the REGISTER_CMD block contents (after the assignment, before
    the next blank line). Lets tests focus on just the registration step.
    """
    text = _CRON_SCRIPT.read_text(encoding="utf-8")
    match = re.search(
        r"REGISTER_CMD=\".*?\"", text, re.DOTALL,
    )
    assert match, "REGISTER_CMD assignment block not found in cron script"
    return match.group(0)


def test_hetzner_brain_cron_uses_direct_flag() -> None:
    """Asserts the cron's register call uses --direct (SQLAlchemy bypass)."""
    block = _read_register_block()
    assert "--direct" in block, (
        "REGISTER_CMD must use --direct flag; HTTP path 401's in ENV=production "
        "because the cron has no Cloudflare Access JWT. See "
        "docs/superpowers/specs/2026-05-22-pr-brain-bootstrap-fix-2.md"
    )


def test_hetzner_brain_cron_does_not_use_localhost_http_register() -> None:
    """Catches accidental revert to the HTTP-based register path."""
    block = _read_register_block()
    assert "--base-url" not in block, (
        "REGISTER_CMD must NOT use --base-url; that selects the HTTP path "
        "which requires CF Access JWT the cron does not have."
    )
    assert "localhost:8000" not in block, (
        "REGISTER_CMD must NOT reference localhost:8000 HTTP endpoint."
    )
