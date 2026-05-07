"""SP-4 Phase E3 — 7-day timeout fallback for pending brain approvals.

Spec sec 5.4 — if no callback arrives within ``APPROVAL_TIMEOUT_DAYS``
of the original Telegram approval message, the candidate auto-rejects
and the operator gets a daily reminder until they trigger a fresh
training run.

Tracking surface: a row in ``rl_checkpoints`` is "pending approval"
iff:

  is_active=false AND deactivated_at IS NULL AND notes LIKE 'pending_approval_until=%'

The cron writes ``notes='pending_approval_until=<ISO timestamp>'`` when
it registers the candidate; this module's :func:`expire_pending_approvals`
walks all such rows and:

  * If now > deadline → DELETE (soft-rejects, sets deactivated_at)
    + sends a final Telegram message: "auto-rejected after 7 days"
  * If now > deadline - REMINDER_INTERVAL_DAYS and a fresh reminder
    is due (notes don't carry a "last_reminder_at" past the deadline
    today) → send a reminder ping

Idempotent — running every hour is fine; the deadline-vs-now check
gates everything.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.ops.telegram_bot import (
    APPROVAL_TIMEOUT_DAYS,
    REMINDER_INTERVAL_DAYS,
    TelegramConfig,
    _api_url,
)


log = logging.getLogger(__name__)


# Regex parses 'pending_approval_until=2026-05-21T03:30:00+00:00' (and
# any optional 'last_reminder_at=...' that the reminder writer appends).
_DEADLINE_RE = re.compile(
    r"pending_approval_until=(?P<deadline>[0-9T:+\-.Z]+)",
)
_REMINDER_RE = re.compile(
    r"last_reminder_at=(?P<reminder>[0-9T:+\-.Z]+)",
)


@dataclass(frozen=True)
class PendingApproval:
    id: int
    version: str
    notes: str
    deadline: datetime
    last_reminder_at: datetime | None


@dataclass(frozen=True)
class TimeoutTickResult:
    """Summary of one :func:`expire_pending_approvals` run."""

    auto_rejected: int
    reminders_sent: int
    still_pending: int


def _parse_dt(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_pending_notes(notes: str | None) -> tuple[datetime | None, datetime | None]:
    """Return (deadline, last_reminder_at) parsed from the notes string."""
    if not notes:
        return None, None
    deadline = None
    reminder = None
    m = _DEADLINE_RE.search(notes)
    if m:
        deadline = _parse_dt(m.group("deadline"))
    m = _REMINDER_RE.search(notes)
    if m:
        reminder = _parse_dt(m.group("reminder"))
    return deadline, reminder


def make_pending_notes(*, deadline: datetime,
                       last_reminder_at: datetime | None = None) -> str:
    """Build the canonical notes string the cron writes on register."""
    parts = [f"pending_approval_until={deadline.isoformat()}"]
    if last_reminder_at is not None:
        parts.append(f"last_reminder_at={last_reminder_at.isoformat()}")
    return " ".join(parts)


async def list_pending_approvals(session: AsyncSession) -> list[PendingApproval]:
    """Walk rl_checkpoints for inactive non-deleted rows with a pending notes marker.

    Returns a list of (id, version, notes, deadline, last_reminder_at).
    Rows whose notes don't parse as a pending marker are skipped.
    """
    rows = (await session.execute(sa.text(
        """
        SELECT id, version, notes
        FROM rl_checkpoints
        WHERE is_active = 0
          AND deactivated_at IS NULL
          AND notes IS NOT NULL
          AND notes LIKE 'pending_approval_until=%'
        ORDER BY id
        """
    ))).all()
    out: list[PendingApproval] = []
    for r in rows:
        deadline, reminder = parse_pending_notes(r.notes)
        if deadline is None:
            continue
        out.append(PendingApproval(
            id=int(r.id), version=str(r.version),
            notes=str(r.notes), deadline=deadline,
            last_reminder_at=reminder,
        ))
    return out


async def _send_telegram_text(
    *,
    config: TelegramConfig,
    text: str,
    client: httpx.AsyncClient,
) -> bool:
    """Plain sendMessage helper for reminders + final auto-reject pings."""
    try:
        resp = await client.post(
            _api_url(bot_token=config.bot_token, method="sendMessage"),
            json={"chat_id": config.chat_id, "text": text},
        )
    except httpx.HTTPError as e:
        log.warning("telegram_timeout: HTTP error on sendMessage: %s", e)
        return False
    if resp.status_code != 200:
        log.warning(
            "telegram_timeout: sendMessage returned %d: %s",
            resp.status_code, resp.text[:200],
        )
        return False
    return True


async def expire_pending_approvals(
    *,
    session: AsyncSession,
    config: TelegramConfig | None,
    now: datetime | None = None,
    client: httpx.AsyncClient | None = None,
) -> TimeoutTickResult:
    """Reconcile pending approvals against the wall clock.

    For each pending row in ``rl_checkpoints``:

      * If ``now > deadline`` → mark deactivated (auto-reject) + ping Telegram.
      * Else if ``now > deadline - REMINDER_INTERVAL_DAYS`` and we
        haven't reminded today → send reminder + update notes.
      * Else → leave alone.

    Returns counts. Caller is responsible for ``session.commit()``.

    Skips Telegram I/O entirely when ``config`` is None — useful for
    pure-DB tests and when bot creds are unset.
    """
    n = now or datetime.now(timezone.utc)
    pending = await list_pending_approvals(session)
    auto_rejected = 0
    reminders_sent = 0
    still_pending = 0

    owns_client = client is None and config is not None
    if config is not None and client is None:
        client = httpx.AsyncClient(timeout=15.0)

    try:
        for p in pending:
            if n > p.deadline:
                # 7-day window elapsed — auto-reject.
                await session.execute(sa.text(
                    "UPDATE rl_checkpoints SET deactivated_at = :n "
                    "WHERE id = :i"
                ), {"n": n, "i": p.id})
                auto_rejected += 1
                log.warning(
                    "telegram_timeout: auto-rejected rl_checkpoints id=%d "
                    "(version=%s, deadline=%s)",
                    p.id, p.version, p.deadline,
                )
                if config is not None and client is not None:
                    await _send_telegram_text(
                        config=config, client=client,
                        text=(
                            f"⏰ Brain candidate {p.version} (id={p.id}) "
                            f"auto-rejected — no approval received in "
                            f"{APPROVAL_TIMEOUT_DAYS} days. The next "
                            f"nightly retrain will produce a fresh "
                            f"candidate."
                        ),
                    )
                continue

            # Still inside the window. Check if a reminder is due.
            from datetime import timedelta
            reminder_threshold = p.deadline - timedelta(days=REMINDER_INTERVAL_DAYS)
            if n >= reminder_threshold:
                last = p.last_reminder_at
                # Send if never reminded, or last reminder >24h ago.
                due = (last is None) or (
                    (n - last) >= timedelta(days=REMINDER_INTERVAL_DAYS)
                )
                if due:
                    new_notes = make_pending_notes(
                        deadline=p.deadline, last_reminder_at=n,
                    )
                    await session.execute(sa.text(
                        "UPDATE rl_checkpoints SET notes = :nt "
                        "WHERE id = :i"
                    ), {"nt": new_notes, "i": p.id})
                    reminders_sent += 1
                    log.info(
                        "telegram_timeout: reminder for id=%d "
                        "(deadline %s in %s)",
                        p.id, p.deadline, p.deadline - n,
                    )
                    if config is not None and client is not None:
                        await _send_telegram_text(
                            config=config, client=client,
                            text=(
                                f"⏰ Reminder: brain candidate {p.version} "
                                f"(id={p.id}) needs approval. Auto-rejects "
                                f"on {p.deadline.isoformat()}."
                            ),
                        )
                    continue
            still_pending += 1
    finally:
        if owns_client and client is not None:
            await client.aclose()

    return TimeoutTickResult(
        auto_rejected=auto_rejected,
        reminders_sent=reminders_sent,
        still_pending=still_pending,
    )


__all__ = [
    "PendingApproval",
    "TimeoutTickResult",
    "expire_pending_approvals",
    "list_pending_approvals",
    "make_pending_notes",
    "parse_pending_notes",
]
