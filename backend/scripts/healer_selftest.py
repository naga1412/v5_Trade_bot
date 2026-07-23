"""Healer Phase 0 self-test canary — sends one synthetic CRITICAL alert.

The operator's acceptance test for the whole alert path: run this probe,
confirm the ``[SELFTEST]``-tagged message physically arrived on Telegram.
It stays forever as an on-demand canary so the operator can verify the
alerting chain hasn't quietly regressed.

Path exercised:
  * this script → app.ops.alert_routing.alert_admin (level='critical')
  * → Telegram sendMessage (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)
  * → SMTP fallback if Telegram fails
  * → log line as the last-resort signal

Also records ONE row in healer_findings (detector_name='healer_selftest',
severity='info' — deliberately NOT critical, since the row is not a
real fault) so the healer-status probe can display when the last
selftest ran + whether the routing call returned True (any of the
Telegram / SMTP paths succeeded) or False (all paths exhausted).

DOES NOT modify any dispatch tables, users, live_trades, or env vars.
DOES NOT fire any real alarm — every downstream reader can filter on
the ``[SELFTEST]`` prefix or ``detector_name='healer_selftest'``.

Usage (via ops-debug ``healer-selftest`` probe):
    docker compose exec -T backend python /app/scripts/healer_selftest.py
"""
from __future__ import annotations

import asyncio
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import get_session_factory  # noqa: E402
from app.healer.findings import record_finding  # noqa: E402
from app.ops.alert_routing import alert_admin  # noqa: E402


_SELFTEST_TAG: str = "[SELFTEST]"


def _build_message() -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    host = socket.gethostname()
    env = os.environ.get("ENV", "?")
    return (
        f"{_SELFTEST_TAG} Healer Phase 0 alert-path canary.\n"
        f"time={now} host={host} env={env}\n"
        "If you can see this on Telegram, the full "
        "`alert_routing.alert_admin(level='critical')` chain works "
        "end-to-end (watchdog + healer critical alarms will land here). "
        "No real fault triggered this — it is on-demand only, via the "
        "ops-debug `healer-selftest` probe."
    )


async def _run() -> int:
    message = _build_message()
    print(f"healer_selftest: sending → alert_routing.alert_admin(level='critical')")
    print(f"healer_selftest: message length = {len(message)}")

    routed_ok: bool = False
    exc_note: str | None = None
    try:
        routed_ok = await alert_admin(message, level="critical")
    except Exception as e:  # noqa: BLE001 — the selftest must not raise up
        exc_note = f"{type(e).__name__}: {e}"
        print(
            f"healer_selftest: alert_admin raised: {exc_note}",
            file=sys.stderr,
        )
    print(f"healer_selftest: routed_ok = {routed_ok}")
    if exc_note:
        print(f"healer_selftest: exception = {exc_note}")

    # Record ONE finding row so healer-status shows the timestamp of the
    # last selftest run. Severity is 'info' — this is not a fault; the
    # row is a stamp saying "selftest was fired at T, routing returned X".
    session_factory = get_session_factory()
    try:
        await record_finding(
            session_factory,
            detector_name="healer_selftest",
            severity="info",
            summary=(
                f"selftest fired; routing returned "
                f"{'ok' if routed_ok else 'no channel succeeded'}"
            ),
            details={
                "routed_ok": routed_ok,
                "tag": _SELFTEST_TAG,
                "exception": exc_note,
            },
        )
    except Exception as e:  # noqa: BLE001
        print(f"healer_selftest: record_finding failed: {e}", file=sys.stderr)

    # Exit code carries the routing result so the ops-debug probe can
    # fail loudly if every channel is unavailable — a real bug worth
    # investigating even during the selftest itself.
    return 0 if routed_ok else 1


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
