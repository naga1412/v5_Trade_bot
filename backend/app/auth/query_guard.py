"""Runtime query guard for per-user tables.

Spec §7.3 — every SELECT/UPDATE/DELETE against a per-user table must include
a `user_id` predicate. In dev (ENV=development) we raise; in prod we warn.

The check is intentionally cheap and string-based: we look at the rendered SQL
(after parameter substitution by sqlalchemy) and ensure the table name appears
alongside a `user_id` predicate. The check accepts:
  - WHERE user_id = :uid
  - WHERE ... user_id = 1
  - JOIN ... ON ... user_id ...

False positives (e.g. the literal text "user_id" appears in a SQL comment) are
acceptable since they only cause an extra raise in dev. INSERT statements and
queries against shared tables (asset_universe, users, etc.) are skipped.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)


# Tables that require a user_id predicate on SELECT/UPDATE/DELETE.
# Spec §7.1 — these are the per-user tables. asset_universe, users,
# pending_invitations, auth_violations, impersonation_events,
# impersonation_state are intentionally NOT in this set.
PER_USER_TABLES: frozenset[str] = frozenset({
    "predictions",
    "paper_trades",
    "shadow_trades",
    "shadow_open_positions",
    "shadow_cooldowns",
})

_TABLE_REGEX_CACHE: dict[str, re.Pattern[str]] = {}

# Comparison context: user_id followed by `=`, `IN`, `<`, `>`, `IS NULL`, etc.
# We keep this lenient on purpose — false positives only cost an extra raise
# in dev, which is strictly easier to debug than a silent leak in prod.
_USER_ID_PREDICATE_PATTERN: re.Pattern[str] = re.compile(
    r"\buser_id\s*(=|in|<|>|is\s+)",
    re.IGNORECASE,
)


def _references_table(sql_lower: str, table: str) -> bool:
    pat = _TABLE_REGEX_CACHE.get(table)
    if pat is None:
        # Word boundary match so `shadow_trades_user_id_idx` does not false-match
        # the `shadow_trades` table.
        pat = re.compile(rf"\b{re.escape(table)}\b")
        _TABLE_REGEX_CACHE[table] = pat
    return pat.search(sql_lower) is not None


def _has_user_id_predicate(sql_lower: str) -> bool:
    return _USER_ID_PREDICATE_PATTERN.search(sql_lower) is not None


class MissingUserIdFilterError(RuntimeError):
    """Raised in dev when a per-user table is queried without user_id."""


def attach_query_guard(engine: Engine, *, dev_mode: bool) -> None:
    """Attach a before_cursor_execute listener that enforces user_id predicates.

    Pass the `sync_engine` of an `AsyncEngine`; SQLAlchemy events fire on the
    sync side regardless of which API the application uses to talk to it.
    """

    @event.listens_for(engine, "before_cursor_execute")
    def _check(  # type: ignore[no-untyped-def]
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        # Only inspect statements that read or mutate user-scoped data.
        sql_lower = statement.lower().lstrip()
        if not (
            sql_lower.startswith("select")
            or sql_lower.startswith("update")
            or sql_lower.startswith("delete")
        ):
            return

        for table in PER_USER_TABLES:
            if not _references_table(sql_lower, table):
                continue
            if _has_user_id_predicate(sql_lower):
                return  # OK
            msg = (
                f"query touches per-user table '{table}' without user_id "
                f"predicate: {statement[:200]}"
            )
            if dev_mode:
                raise MissingUserIdFilterError(msg)
            log.warning(msg)
            return


__all__ = [
    "MissingUserIdFilterError",
    "PER_USER_TABLES",
    "attach_query_guard",
]
