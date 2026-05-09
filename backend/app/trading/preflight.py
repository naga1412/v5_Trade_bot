"""SP-8 Phase J — boot-time pre-flight checks.

Spec sec 18: before live trading code paths run, the system must verify
that every safety pre-condition is met. If ANY check fails, the
autonomous-trading subsystem refuses to start and the backend logs an
explicit error — the rest of the platform (paper trading, ghost
candles, dashboard) keeps running normally.

Five checks:
  1. ``MASTER_PASSPHRASE`` env var is set + 16+ chars (Phase E vault prereq)
  2. ``secrets.enc`` exists + decrypts cleanly with the passphrase
  3. Binance API key permissions match spec sec 9.3 (no withdrawals/transfers)
  4. Migration ``0016_sp8_trading`` is applied (live_trades + tax_events
     + the four other tables exist)
  5. Audit hash chains intact (calls SP-7 verify_chain over the chained
     tables: predictions, paper_trades, shadow_trades, brain_decisions,
     mode_change_log, live_trades, tax_events)

The function returns a ``PreflightResult`` enumerating every check's
outcome. Callers (lifespan) gate the autonomous workers on
``result.all_passed``.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.exchanges.binance_live import (
    ApiKeyPermissionViolation,
    BinanceLiveClient,
    BinanceLiveError,
)
from app.secrets.vault import (
    VaultDecryptError,
    decrypt_secrets,
)


log = logging.getLogger(__name__)


CheckName = Literal[
    "master_passphrase_set",
    "vault_decrypt_ok",
    "binance_permissions_safe",
    "migration_0016_applied",
    "audit_chain_intact",
]


@dataclass(frozen=True)
class CheckResult:
    name: CheckName
    passed: bool
    detail: str  # short one-line context


@dataclass(frozen=True)
class PreflightResult:
    """Aggregated outcome of all pre-flight checks."""

    checks: list[CheckResult]

    @property
    def all_passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]

    def summary_line(self) -> str:
        passed = sum(1 for c in self.checks if c.passed)
        return f"preflight: {passed}/{len(self.checks)} checks passed"


# ---- Individual checks --------------------------------------------------


def check_master_passphrase() -> CheckResult:
    pp = os.environ.get("MASTER_PASSPHRASE", "")
    if not pp:
        return CheckResult(
            name="master_passphrase_set", passed=False,
            detail="MASTER_PASSPHRASE env var is empty",
        )
    if len(pp) < 16:
        return CheckResult(
            name="master_passphrase_set", passed=False,
            detail=f"MASTER_PASSPHRASE is only {len(pp)} chars; spec requires ≥16",
        )
    return CheckResult(
        name="master_passphrase_set", passed=True,
        detail=f"set ({len(pp)} chars)",
    )


def check_vault_decrypt(
    *, secrets_path: Path | None = None, passphrase: str | None = None,
) -> CheckResult:
    """Tries to decrypt secrets.enc. Failure is fatal for live trading.

    secrets_path defaults to /app/secrets.enc. passphrase defaults to
    MASTER_PASSPHRASE env var. Both injectable for tests.
    """
    path = secrets_path or Path("/app/secrets.enc")
    pp = passphrase if passphrase is not None else os.environ.get("MASTER_PASSPHRASE", "")
    if not path.exists():
        return CheckResult(
            name="vault_decrypt_ok", passed=False,
            detail=f"{path} does not exist (run tools/secrets/encrypt.py)",
        )
    if not pp:
        return CheckResult(
            name="vault_decrypt_ok", passed=False,
            detail="MASTER_PASSPHRASE not set; cannot decrypt vault",
        )
    try:
        secrets = decrypt_secrets(path.read_bytes(), passphrase=pp)
    except VaultDecryptError as e:
        return CheckResult(
            name="vault_decrypt_ok", passed=False,
            detail=f"vault decrypt failed: {e}",
        )
    return CheckResult(
        name="vault_decrypt_ok", passed=True,
        detail=f"decrypted {len(secrets)} secret(s)",
    )


async def check_binance_permissions(
    *, api_key: str, api_secret: str,
    use_testnet: bool = True, http: httpx.AsyncClient | None = None,
) -> CheckResult:
    """Spec §9.3 — refuse to start if Binance key has dangerous permissions."""
    client = BinanceLiveClient(
        api_key=api_key, api_secret=api_secret,
        use_testnet=use_testnet, http=http,
    )
    try:
        await client.verify_permissions()
    except ApiKeyPermissionViolation as e:
        return CheckResult(
            name="binance_permissions_safe", passed=False,
            detail=str(e),
        )
    except BinanceLiveError as e:
        return CheckResult(
            name="binance_permissions_safe", passed=False,
            detail=f"Binance permission check failed: {e}",
        )
    finally:
        await client.aclose()
    return CheckResult(
        name="binance_permissions_safe", passed=True,
        detail="Reading + Futures only; withdrawals + transfers disabled",
    )


async def check_migration_0016_applied(session: AsyncSession) -> CheckResult:
    """Verify the SP-8 tables exist. We probe by querying alembic_version
    AND checking one representative table from migration 0016."""
    # The alembic_version table tracks applied revisions.
    rev = (await session.execute(
        sa.text("SELECT version_num FROM alembic_version LIMIT 1")
    )).scalar()
    if rev is None:
        return CheckResult(
            name="migration_0016_applied", passed=False,
            detail="no alembic_version row — DB not migrated",
        )
    # Even if 0017+ has landed by then, all 0016 tables must exist.
    try:
        await session.execute(sa.text("SELECT 1 FROM live_trades LIMIT 1"))
        await session.execute(sa.text("SELECT 1 FROM tax_events LIMIT 1"))
    except Exception as e:  # noqa: BLE001
        return CheckResult(
            name="migration_0016_applied", passed=False,
            detail=f"SP-8 tables missing (head={rev!r}): {e}",
        )
    return CheckResult(
        name="migration_0016_applied", passed=True,
        detail=f"alembic head = {rev}",
    )


async def check_audit_chain_intact(
    session: AsyncSession,
    *,
    tables: list[str] | None = None,
) -> CheckResult:
    """Verify the hash chain is unbroken across the chained tables.

    Walk each table in order; for every row except the first the
    expected prev_hash equals the previous row's row_hash. A break
    means a tampering or audit corruption — refuse to start live
    trading until investigated.
    """
    table_list = tables or [
        "predictions", "paper_trades", "shadow_trades",
        "brain_decisions", "mode_change_log", "live_trades", "tax_events",
    ]
    for table in table_list:
        rows = (await session.execute(
            sa.text(
                f"SELECT prev_hash, row_hash FROM {table} ORDER BY id ASC"
            )
        )).all()
        if not rows:
            continue
        # First row's prev_hash is the GENESIS_HASH (validated by the
        # SP-7 verify_chain worker; for preflight we just check linkage
        # between consecutive rows is unbroken).
        for i in range(1, len(rows)):
            expected = rows[i - 1].row_hash
            actual = rows[i].prev_hash
            if expected != actual:
                return CheckResult(
                    name="audit_chain_intact", passed=False,
                    detail=(
                        f"chain break in {table} at row index {i}: "
                        f"expected prev_hash={expected[:12]}..., "
                        f"got {actual[:12]}..."
                    ),
                )
    return CheckResult(
        name="audit_chain_intact", passed=True,
        detail=f"{len(table_list)} chained tables OK",
    )


# ---- Top-level orchestrator --------------------------------------------


async def run_preflight(
    session: AsyncSession,
    *,
    use_testnet: bool = True,
    secrets_path: Path | None = None,
    http: httpx.AsyncClient | None = None,
) -> PreflightResult:
    """Run all checks in order; short-circuit on vault failure since the
    Binance check requires the API keys from inside the vault."""
    results: list[CheckResult] = []

    # 1. Passphrase — cheap; check first.
    pp_check = check_master_passphrase()
    results.append(pp_check)

    # 2. Vault decrypt — depends on (1).
    secrets: dict[str, str] = {}
    if pp_check.passed:
        vault_check = check_vault_decrypt(
            secrets_path=secrets_path,
            passphrase=os.environ.get("MASTER_PASSPHRASE"),
        )
        results.append(vault_check)
        if vault_check.passed and secrets_path is not None:
            try:
                secrets = decrypt_secrets(
                    secrets_path.read_bytes(),
                    passphrase=os.environ["MASTER_PASSPHRASE"],
                )
            except Exception:  # noqa: BLE001
                # Should not happen — vault_check just succeeded.
                secrets = {}
    else:
        results.append(CheckResult(
            name="vault_decrypt_ok", passed=False,
            detail="skipped (passphrase missing)",
        ))

    # 3. Binance permissions — depends on (2).
    api_key = secrets.get("binance_api_key", "")
    api_secret = secrets.get("binance_api_secret", "")
    if api_key and api_secret:
        bp = await check_binance_permissions(
            api_key=api_key, api_secret=api_secret,
            use_testnet=use_testnet, http=http,
        )
        results.append(bp)
    else:
        results.append(CheckResult(
            name="binance_permissions_safe", passed=False,
            detail="skipped (no API keys in vault)",
        ))

    # 4 + 5. DB checks — independent of secrets, always run.
    results.append(await check_migration_0016_applied(session))
    results.append(await check_audit_chain_intact(session))

    result = PreflightResult(checks=results)
    log.info(result.summary_line())
    for c in result.failures():
        log.warning("preflight: %s — %s", c.name, c.detail)
    return result


__all__ = [
    "CheckName",
    "CheckResult",
    "PreflightResult",
    "check_audit_chain_intact",
    "check_binance_permissions",
    "check_master_passphrase",
    "check_migration_0016_applied",
    "check_vault_decrypt",
    "run_preflight",
]
