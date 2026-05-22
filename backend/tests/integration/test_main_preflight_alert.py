"""PR-PREFLIGHT-ALERT + PR-DECOUPLE-WORKERS — surface preflight failures
via telegram + heartbeat, and split preflight into chain_writer /
chain_reader profiles for safety-net worker spawn decoupling.

Tests that the preflight gate in ``app/main.py:282-394``:
  - On PASS: writes a heartbeat row with status='passed' and does NOT alert.
  - On FAIL: log.critical + alert via app.ops.alert_routing.alert_admin
    (level='critical') + heartbeat row with status='failed' and the
    failed_checks names in details.
  - On EXCEPTION (run_preflight raises): log.critical + alert + heartbeat
    row with status='raised'.
  - Alerter / heartbeat failure must NEVER break the lifespan.

PR-DECOUPLE-WORKERS additionally tests:
  - chain_reader profile skips audit_chain_intact (4 checks).
  - chain_writer profile (default) includes audit_chain_intact (5 checks).
  - When reader passes but chain_intact fails: spawn the 3 safety-net
    workers + new heartbeat status="reader_only_passed" + new telegram
    alert "Audit chain WRITER blocked".
  - When reader fails: existing PR-PREFLIGHT-ALERT path unchanged.

All cases drive the FastAPI lifespan directly (the existing
``test_app_startup`` pattern) and monkeypatch the alerter + heartbeat
helpers so we assert on calls + lifespan completion.
"""
from __future__ import annotations

import logging
from typing import Any

import pytest

from app import main as app_main
from app.trading import preflight as preflight_module
from app.trading.preflight import CheckResult, PreflightResult


# ---------------------------------------------------------------------------
# Helpers — settings + worker stubs + lifespan driver
# ---------------------------------------------------------------------------


class _NoopTask:
    def cancel(self) -> None:
        pass


async def _async_noop(*_a: Any, **_k: Any) -> None:
    return None


class _NoopFactory:
    """Async-context-manager-shaped session factory.

    `run_preflight` is monkeypatched away in every test so the session
    yielded here is never queried — but lifespan still opens a session
    so the factory must look real.
    """

    def __call__(self) -> "_NoopFactory":
        return self

    async def __aenter__(self) -> Any:
        class _S:
            async def commit(self) -> None:
                return None

            async def execute(self, *_a: Any, **_k: Any) -> Any:
                return None
        return _S()

    async def __aexit__(self, *_: Any) -> None:
        return None


def _stub_lifespan_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace every worker factory + DB helper so lifespan completes
    without opening real DB connections.

    The PR-PREFLIGHT-ALERT branch sits inside the autonomous_trading
    code path, which downstream of run_preflight() calls
    initialize_vault_cache + start_liquidation_monitor + others. We
    monkeypatch all of them — including run_preflight itself, which
    each test then re-patches with a controlled result.
    """
    # Top-level worker starters (lifespan section before autonomous block)
    monkeypatch.setattr(app_main, "load_active_checkpoint", _async_noop)
    monkeypatch.setattr(app_main, "start_background_worker", lambda: _NoopTask())
    monkeypatch.setattr(app_main, "start_shadow_worker", lambda: _NoopTask())
    monkeypatch.setattr(
        app_main, "start_universe_refresh_task",
        lambda *_a, **_k: _NoopTask(),
    )
    monkeypatch.setattr(
        app_main, "start_universe_sync_task",
        lambda *_a, **_k: _NoopTask(),
    )
    monkeypatch.setattr(
        app_main, "start_health_pinger_task",
        lambda *_a, **_k: _NoopTask(),
    )
    monkeypatch.setattr(
        app_main, "start_audit_verifier_task",
        lambda *_a, **_k: _NoopTask(),
    )
    monkeypatch.setattr(
        app_main, "start_news_ingest_task",
        lambda *_a, **_k: _NoopTask(),
    )
    monkeypatch.setattr(
        app_main, "start_news_cleanup_task",
        lambda *_a, **_k: _NoopTask(),
    )
    monkeypatch.setattr(
        app_main, "start_intermarket_snapshot_task",
        lambda *_a, **_k: _NoopTask(),
    )
    monkeypatch.setattr(
        app_main, "start_intermarket_cleanup_task",
        lambda *_a, **_k: _NoopTask(),
    )
    monkeypatch.setattr(
        app_main, "start_worker_watchdog",
        lambda *_a, **_k: _NoopTask(),
    )
    monkeypatch.setattr(
        app_main, "start_scanner_batch_task",
        lambda *_a, **_k: _NoopTask(),
    )
    monkeypatch.setattr(
        app_main, "start_prediction_validator_task",
        lambda *_a, **_k: _NoopTask(),
    )
    monkeypatch.setattr(
        app_main, "start_keepalive_task",
        lambda *_a, **_k: _NoopTask(),
    )
    monkeypatch.setattr(
        app_main, "start_mtf_cache_prewarm_task",
        lambda *_a, **_k: _NoopTask(),
    )
    monkeypatch.setattr(
        app_main, "start_mtf_cache_ttl_refresh_task",
        lambda *_a, **_k: _NoopTask(),
    )
    # PR10 / PR10.5 — these imports happen inside the function body via
    # `from app.workers.symbol_allowlist_refresh import ...`. We patch the
    # source modules so the lazy import returns a stub.
    import app.workers.symbol_allowlist_refresh as _sar
    import app.workers.ui_freshness_monitor as _ufm
    monkeypatch.setattr(_sar, "start_symbol_allowlist_refresh",
                        lambda *_a, **_k: _NoopTask())
    monkeypatch.setattr(_ufm, "start_ui_freshness_monitor",
                        lambda *_a, **_k: _NoopTask())
    # Autonomous-trading interior: vault + liquidation/exit/telegram workers
    monkeypatch.setattr(app_main, "initialize_vault_cache",
                        lambda *_a, **_k: False)
    monkeypatch.setattr(app_main, "vault_keys", lambda: None)
    monkeypatch.setattr(
        app_main, "start_liquidation_monitor",
        lambda *_a, **_k: _NoopTask(),
    )
    monkeypatch.setattr(
        app_main, "start_live_exit_monitor",
        lambda *_a, **_k: _NoopTask(),
    )
    monkeypatch.setattr(
        app_main, "start_telegram_poller",
        lambda *_a, **_k: _NoopTask(),
    )
    monkeypatch.setattr(
        app_main, "start_auto_promote_task",
        lambda *_a, **_k: _NoopTask(),
    )
    # PR-DECOUPLE-WORKERS — main.py now calls check_audit_chain_intact
    # directly from the lifespan against the _NoopFactory session. The
    # noop session can't satisfy real SQL, so install a passing default;
    # tests that need a failing chain override this explicitly.
    async def _default_chain_pass(_session: Any, **_k: Any) -> CheckResult:
        return CheckResult(
            name="audit_chain_intact", passed=True,
            detail="7 chained tables OK",
        )
    monkeypatch.setattr(
        app_main, "check_audit_chain_intact", _default_chain_pass,
    )
    # DB plumbing — bypass real engines.
    monkeypatch.setattr(app_main, "get_session_factory", lambda: _NoopFactory())
    monkeypatch.setattr(
        app_main, "get_engine",
        lambda: type("E", (), {"sync_engine": None})(),
    )
    monkeypatch.setattr(app_main, "attach_query_guard", lambda *_a, **_k: None)


def _patch_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production-like settings with autonomous_trading_enabled=True."""
    monkeypatch.setattr(
        app_main, "get_settings",
        lambda: type("S", (), {
            "env": "production",
            "worker_enabled": True,
            "autonomous_trading_enabled": True,
            "binance_use_testnet": True,
            "master_passphrase": "",
            "auto_promote_to_telegram_enabled": False,
            "auto_promote_to_fullyauto_enabled": False,
            "auto_promote_consecutive_days": 7,
        })(),
    )


async def _drive_lifespan(app: Any) -> None:
    async with app_main.lifespan(app):
        pass


def _make_pass_result() -> PreflightResult:
    # PR-DECOUPLE-WORKERS — main.py now calls run_preflight with
    # profile='chain_reader' (4 checks; audit_chain_intact is checked
    # separately by main.py). The mocked run_preflight in these tests
    # mirrors that contract; chain_check is stubbed via _stub_lifespan_workers.
    return PreflightResult(checks=[
        CheckResult(name="master_passphrase_set", passed=True, detail="ok"),
        CheckResult(name="vault_decrypt_ok", passed=True, detail="ok"),
        CheckResult(name="binance_permissions_safe", passed=True, detail="ok"),
        CheckResult(name="migration_0016_applied", passed=True, detail="ok"),
    ])


def _make_fail_result() -> PreflightResult:
    return PreflightResult(checks=[
        CheckResult(name="master_passphrase_set", passed=True, detail="ok"),
        CheckResult(name="vault_decrypt_ok", passed=True, detail="ok"),
        CheckResult(name="binance_permissions_safe", passed=True, detail="ok"),
        CheckResult(name="migration_0016_applied", passed=True, detail="ok"),
        CheckResult(
            name="audit_chain_intact", passed=False,
            detail="chain break in predictions at row index 796: "
                   "expected prev_hash=abc..., got def...",
        ),
    ])


# ---------------------------------------------------------------------------
# Test 1 — preflight pass writes heartbeat with status='passed'
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_pass_writes_heartbeat_with_passed_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_lifespan_workers(monkeypatch)
    _patch_settings(monkeypatch)

    async def fake_preflight(*_a: Any, **_k: Any) -> PreflightResult:
        return _make_pass_result()

    monkeypatch.setattr(app_main, "run_preflight", fake_preflight)

    heartbeat_calls: list[dict[str, Any]] = []

    async def fake_record_heartbeat(
        _factory: Any, name: str, *, status: str = "ok",
        details: dict[str, Any] | None = None,
    ) -> None:
        heartbeat_calls.append({
            "name": name, "status": status, "details": details,
        })

    monkeypatch.setattr(app_main, "_record_heartbeat", fake_record_heartbeat)

    alert_calls: list[tuple[str, str]] = []

    async def fake_alert(message: str, *, level: str = "warning") -> bool:
        alert_calls.append((message, level))
        return True

    monkeypatch.setattr(app_main, "_route_alert", fake_alert)

    app = app_main.create_app()
    await _drive_lifespan(app)

    # Heartbeat written under the preflight_gate worker name with passed.
    preflight_beats = [b for b in heartbeat_calls
                       if b["name"] == "preflight_gate"]
    assert len(preflight_beats) == 1
    beat = preflight_beats[0]
    assert beat["status"] == "passed"
    assert beat["details"] is not None
    assert beat["details"]["passed_count"] == 5
    assert beat["details"]["total_count"] == 5


# ---------------------------------------------------------------------------
# Test 2 — preflight fail writes heartbeat with status='failed' + failed_checks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_fail_writes_heartbeat_with_failed_status_and_failed_check_names(  # noqa: E501
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_lifespan_workers(monkeypatch)
    _patch_settings(monkeypatch)

    async def fake_preflight(*_a: Any, **_k: Any) -> PreflightResult:
        return _make_fail_result()

    monkeypatch.setattr(app_main, "run_preflight", fake_preflight)

    heartbeat_calls: list[dict[str, Any]] = []

    async def fake_record_heartbeat(
        _factory: Any, name: str, *, status: str = "ok",
        details: dict[str, Any] | None = None,
    ) -> None:
        heartbeat_calls.append({
            "name": name, "status": status, "details": details,
        })

    monkeypatch.setattr(app_main, "_record_heartbeat", fake_record_heartbeat)

    async def fake_alert(_message: str, **_k: Any) -> bool:
        return True

    monkeypatch.setattr(app_main, "_route_alert", fake_alert)

    app = app_main.create_app()
    await _drive_lifespan(app)

    preflight_beats = [b for b in heartbeat_calls
                       if b["name"] == "preflight_gate"]
    assert len(preflight_beats) == 1
    beat = preflight_beats[0]
    assert beat["status"] == "failed"
    assert beat["details"] is not None
    assert beat["details"]["failed_checks"] == ["audit_chain_intact"]
    assert beat["details"]["passed_count"] == 4
    assert beat["details"]["total_count"] == 5
    # failed_check_details carries the per-check detail string.
    assert "audit_chain_intact" in beat["details"]["failed_check_details"]
    assert "chain break in predictions" in (
        beat["details"]["failed_check_details"]["audit_chain_intact"]
    )


# ---------------------------------------------------------------------------
# Test 3 — preflight fail sends telegram alert via alert_admin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_fail_sends_telegram_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_lifespan_workers(monkeypatch)
    _patch_settings(monkeypatch)

    async def fake_preflight(*_a: Any, **_k: Any) -> PreflightResult:
        return _make_fail_result()

    monkeypatch.setattr(app_main, "run_preflight", fake_preflight)
    monkeypatch.setattr(app_main, "_record_heartbeat", _async_noop)

    alert_calls: list[dict[str, Any]] = []

    async def fake_alert(message: str, *, level: str = "warning") -> bool:
        alert_calls.append({"message": message, "level": level})
        return True

    monkeypatch.setattr(app_main, "_route_alert", fake_alert)

    app = app_main.create_app()
    await _drive_lifespan(app)

    assert len(alert_calls) == 1
    call = alert_calls[0]
    assert call["level"] == "critical"
    assert "Preflight FAILED" in call["message"]
    assert "audit_chain_intact" in call["message"]


# ---------------------------------------------------------------------------
# Test 4 — preflight fail logs CRITICAL even with no telegram env
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_fail_logs_critical_if_telegram_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _stub_lifespan_workers(monkeypatch)
    _patch_settings(monkeypatch)

    # Clean telegram env — the real alert_admin will short-circuit.
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    async def fake_preflight(*_a: Any, **_k: Any) -> PreflightResult:
        return _make_fail_result()

    monkeypatch.setattr(app_main, "run_preflight", fake_preflight)
    monkeypatch.setattr(app_main, "_record_heartbeat", _async_noop)

    # Use the real _route_alert (it falls through to log.warning).
    caplog.set_level(logging.CRITICAL, logger="app.main")
    app = app_main.create_app()
    await _drive_lifespan(app)

    # At least one CRITICAL record from app.main about preflight failure.
    critical_records = [
        r for r in caplog.records
        if r.levelno == logging.CRITICAL and r.name == "app.main"
    ]
    assert len(critical_records) >= 1
    assert any(
        "pre-flight failed" in r.getMessage()
        or "preflight failed" in r.getMessage().lower()
        for r in critical_records
    )


# ---------------------------------------------------------------------------
# Test 5 — preflight pass does NOT send alert (regression-safety)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_pass_does_not_send_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_lifespan_workers(monkeypatch)
    _patch_settings(monkeypatch)

    async def fake_preflight(*_a: Any, **_k: Any) -> PreflightResult:
        return _make_pass_result()

    monkeypatch.setattr(app_main, "run_preflight", fake_preflight)
    monkeypatch.setattr(app_main, "_record_heartbeat", _async_noop)

    alert_calls: list[tuple[str, str]] = []

    async def fake_alert(message: str, *, level: str = "warning") -> bool:
        alert_calls.append((message, level))
        return True

    monkeypatch.setattr(app_main, "_route_alert", fake_alert)

    app = app_main.create_app()
    await _drive_lifespan(app)

    assert alert_calls == [], (
        f"alert_admin must not be called on preflight PASS, got: {alert_calls}"
    )


# ---------------------------------------------------------------------------
# Test 6 — preflight raises: heartbeat status='raised' + alert sent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_raises_writes_heartbeat_with_raised_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_lifespan_workers(monkeypatch)
    _patch_settings(monkeypatch)

    async def boom(*_a: Any, **_k: Any) -> PreflightResult:
        raise RuntimeError("simulated")

    monkeypatch.setattr(app_main, "run_preflight", boom)

    heartbeat_calls: list[dict[str, Any]] = []

    async def fake_record_heartbeat(
        _factory: Any, name: str, *, status: str = "ok",
        details: dict[str, Any] | None = None,
    ) -> None:
        heartbeat_calls.append({
            "name": name, "status": status, "details": details,
        })

    monkeypatch.setattr(app_main, "_record_heartbeat", fake_record_heartbeat)

    alert_calls: list[dict[str, Any]] = []

    async def fake_alert(message: str, *, level: str = "warning") -> bool:
        alert_calls.append({"message": message, "level": level})
        return True

    monkeypatch.setattr(app_main, "_route_alert", fake_alert)

    app = app_main.create_app()
    await _drive_lifespan(app)

    # Heartbeat row with status='raised'.
    preflight_beats = [b for b in heartbeat_calls
                       if b["name"] == "preflight_gate"]
    assert len(preflight_beats) == 1
    beat = preflight_beats[0]
    assert beat["status"] == "raised"
    assert beat["details"] is not None
    assert beat["details"]["error_type"] == "RuntimeError"
    assert "simulated" in beat["details"]["error_msg"]

    # Alert also dispatched on exception path.
    assert len(alert_calls) == 1
    assert alert_calls[0]["level"] == "critical"
    assert "Preflight RAISED" in alert_calls[0]["message"]
    assert "RuntimeError" in alert_calls[0]["message"]


# ---------------------------------------------------------------------------
# Test 7 — alerter raises: lifespan completes + heartbeat still written
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_alert_dispatch_failure_does_not_kill_lifespan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_lifespan_workers(monkeypatch)
    _patch_settings(monkeypatch)

    async def fake_preflight(*_a: Any, **_k: Any) -> PreflightResult:
        return _make_fail_result()

    monkeypatch.setattr(app_main, "run_preflight", fake_preflight)

    heartbeat_calls: list[dict[str, Any]] = []

    async def fake_record_heartbeat(
        _factory: Any, name: str, *, status: str = "ok",
        details: dict[str, Any] | None = None,
    ) -> None:
        heartbeat_calls.append({
            "name": name, "status": status, "details": details,
        })

    monkeypatch.setattr(app_main, "_record_heartbeat", fake_record_heartbeat)

    async def boom_alert(_message: str, **_k: Any) -> bool:
        raise RuntimeError("simulated alerter outage")

    monkeypatch.setattr(app_main, "_route_alert", boom_alert)

    # Lifespan must complete without uncaught exception.
    app = app_main.create_app()
    await _drive_lifespan(app)  # would raise here if not best-effort

    preflight_beats = [b for b in heartbeat_calls
                       if b["name"] == "preflight_gate"]
    assert len(preflight_beats) == 1
    assert preflight_beats[0]["status"] == "failed"


# ---------------------------------------------------------------------------
# Test 8 — heartbeat raises: lifespan completes + alert still sent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_heartbeat_failure_does_not_kill_lifespan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_lifespan_workers(monkeypatch)
    _patch_settings(monkeypatch)

    async def fake_preflight(*_a: Any, **_k: Any) -> PreflightResult:
        return _make_fail_result()

    monkeypatch.setattr(app_main, "run_preflight", fake_preflight)

    async def boom_heartbeat(
        _factory: Any, _name: str, *, status: str = "ok",
        details: dict[str, Any] | None = None,
    ) -> None:
        raise RuntimeError("simulated heartbeat DB outage")

    monkeypatch.setattr(app_main, "_record_heartbeat", boom_heartbeat)

    alert_calls: list[dict[str, Any]] = []

    async def fake_alert(message: str, *, level: str = "warning") -> bool:
        alert_calls.append({"message": message, "level": level})
        return True

    monkeypatch.setattr(app_main, "_route_alert", fake_alert)

    # Lifespan must complete without uncaught exception.
    app = app_main.create_app()
    await _drive_lifespan(app)

    # Alert was still dispatched before the heartbeat failure.
    assert len(alert_calls) == 1
    assert alert_calls[0]["level"] == "critical"
    assert "Preflight FAILED" in alert_calls[0]["message"]


# ===========================================================================
# PR-DECOUPLE-WORKERS — chain_reader / chain_writer profile tests (8 new)
# ===========================================================================


def _make_reader_pass_chain_fail_result() -> PreflightResult:
    """4-check chain_reader pass (audit_chain_intact is NOT in this result)."""
    return PreflightResult(checks=[
        CheckResult(name="master_passphrase_set", passed=True, detail="ok"),
        CheckResult(name="vault_decrypt_ok", passed=True, detail="ok"),
        CheckResult(name="binance_permissions_safe", passed=True, detail="ok"),
        CheckResult(name="migration_0016_applied", passed=True, detail="ok"),
    ])


def _make_chain_intact_pass() -> CheckResult:
    return CheckResult(
        name="audit_chain_intact", passed=True,
        detail="7 chained tables OK",
    )


def _make_chain_intact_fail() -> CheckResult:
    return CheckResult(
        name="audit_chain_intact", passed=False,
        detail="chain break in predictions at row index 796: "
               "expected prev_hash=abc..., got def...",
    )


def _make_reader_fail_result() -> PreflightResult:
    """A reader-side failure (vault decrypt) — only 4 checks (no chain)."""
    return PreflightResult(checks=[
        CheckResult(name="master_passphrase_set", passed=True, detail="ok"),
        CheckResult(
            name="vault_decrypt_ok", passed=False,
            detail="vault decrypt failed: bad passphrase",
        ),
        CheckResult(
            name="binance_permissions_safe", passed=False,
            detail="skipped (no API keys in vault)",
        ),
        CheckResult(name="migration_0016_applied", passed=True, detail="ok"),
    ])


# ---------------------------------------------------------------------------
# Test 9 — chain_reader profile skips audit_chain_intact (4 checks only)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_chain_reader_profile_skips_audit_chain_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Call run_preflight(profile='chain_reader') — result must have 4 checks
    and audit_chain_intact must NOT appear regardless of chain state."""
    # Stub the 4 reader checks to pass; audit_chain_intact would fail if
    # called (so we'll detect any leak).
    monkeypatch.setattr(
        preflight_module, "check_master_passphrase",
        lambda: CheckResult("master_passphrase_set", True, "ok"),
    )
    monkeypatch.setattr(
        preflight_module, "check_vault_decrypt",
        lambda **_k: CheckResult("vault_decrypt_ok", True, "ok"),
    )

    async def _bp(**_k: Any) -> CheckResult:
        return CheckResult("binance_permissions_safe", True, "ok")
    monkeypatch.setattr(preflight_module, "check_binance_permissions", _bp)

    async def _mig(_s: Any) -> CheckResult:
        return CheckResult("migration_0016_applied", True, "ok")
    monkeypatch.setattr(preflight_module, "check_migration_0016_applied", _mig)

    chain_called: list[bool] = []

    async def _chain_should_not_run(_s: Any, **_k: Any) -> CheckResult:
        chain_called.append(True)
        return _make_chain_intact_fail()
    monkeypatch.setattr(
        preflight_module, "check_audit_chain_intact", _chain_should_not_run,
    )

    # Stub the secret decryption so the binance check sees keys.
    monkeypatch.setattr(
        preflight_module, "decrypt_secrets",
        lambda _b, *, passphrase: {
            "binance_api_key": "k", "binance_api_secret": "s",
        },
    )
    # Pretend the vault file exists.
    from pathlib import Path
    monkeypatch.setattr(
        Path, "exists", lambda self: True,
    )
    monkeypatch.setattr(
        Path, "read_bytes", lambda self: b"",
    )
    monkeypatch.setenv("MASTER_PASSPHRASE", "x" * 16)

    class _Sess:
        async def execute(self, *_a: Any, **_k: Any) -> Any:
            class _R:
                def scalar(self) -> Any:
                    return None
                def all(self) -> list[Any]:
                    return []
            return _R()

    result = await preflight_module.run_preflight(
        _Sess(),  # type: ignore[arg-type]
        profile="chain_reader",
    )

    assert result.all_passed, f"reader profile must pass: {result.failures()}"
    assert len(result.checks) == 4, (
        f"chain_reader must run 4 checks, got "
        f"{[c.name for c in result.checks]}"
    )
    assert "audit_chain_intact" not in [c.name for c in result.checks]
    assert chain_called == [], "audit_chain_intact must not be called"


# ---------------------------------------------------------------------------
# Test 10 — chain_writer profile (default) includes audit_chain_intact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_chain_writer_profile_includes_audit_chain_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """profile='chain_writer' with a broken chain — result has 5 checks and
    audit_chain_intact is among the failures."""
    monkeypatch.setattr(
        preflight_module, "check_master_passphrase",
        lambda: CheckResult("master_passphrase_set", True, "ok"),
    )
    monkeypatch.setattr(
        preflight_module, "check_vault_decrypt",
        lambda **_k: CheckResult("vault_decrypt_ok", True, "ok"),
    )

    async def _bp(**_k: Any) -> CheckResult:
        return CheckResult("binance_permissions_safe", True, "ok")
    monkeypatch.setattr(preflight_module, "check_binance_permissions", _bp)

    async def _mig(_s: Any) -> CheckResult:
        return CheckResult("migration_0016_applied", True, "ok")
    monkeypatch.setattr(preflight_module, "check_migration_0016_applied", _mig)

    async def _chain_fail(_s: Any, **_k: Any) -> CheckResult:
        return _make_chain_intact_fail()
    monkeypatch.setattr(
        preflight_module, "check_audit_chain_intact", _chain_fail,
    )

    monkeypatch.setattr(
        preflight_module, "decrypt_secrets",
        lambda _b, *, passphrase: {
            "binance_api_key": "k", "binance_api_secret": "s",
        },
    )
    from pathlib import Path
    monkeypatch.setattr(Path, "exists", lambda self: True)
    monkeypatch.setattr(Path, "read_bytes", lambda self: b"")
    monkeypatch.setenv("MASTER_PASSPHRASE", "x" * 16)

    class _Sess:
        async def execute(self, *_a: Any, **_k: Any) -> Any:
            class _R:
                def scalar(self) -> Any:
                    return None
                def all(self) -> list[Any]:
                    return []
            return _R()

    result = await preflight_module.run_preflight(
        _Sess(),  # type: ignore[arg-type]
        profile="chain_writer",
    )

    assert not result.all_passed
    assert len(result.checks) == 5
    failed_names = [c.name for c in result.failures()]
    assert "audit_chain_intact" in failed_names


# ---------------------------------------------------------------------------
# Test 11 — default profile is chain_writer (backwards-compat regression)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_default_profile_is_chain_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling run_preflight() with no profile arg must behave as
    chain_writer — 5 checks, audit_chain_intact present."""
    monkeypatch.setattr(
        preflight_module, "check_master_passphrase",
        lambda: CheckResult("master_passphrase_set", True, "ok"),
    )
    monkeypatch.setattr(
        preflight_module, "check_vault_decrypt",
        lambda **_k: CheckResult("vault_decrypt_ok", True, "ok"),
    )

    async def _bp(**_k: Any) -> CheckResult:
        return CheckResult("binance_permissions_safe", True, "ok")
    monkeypatch.setattr(preflight_module, "check_binance_permissions", _bp)

    async def _mig(_s: Any) -> CheckResult:
        return CheckResult("migration_0016_applied", True, "ok")
    monkeypatch.setattr(preflight_module, "check_migration_0016_applied", _mig)

    async def _chain_fail(_s: Any, **_k: Any) -> CheckResult:
        return _make_chain_intact_fail()
    monkeypatch.setattr(
        preflight_module, "check_audit_chain_intact", _chain_fail,
    )

    monkeypatch.setattr(
        preflight_module, "decrypt_secrets",
        lambda _b, *, passphrase: {
            "binance_api_key": "k", "binance_api_secret": "s",
        },
    )
    from pathlib import Path
    monkeypatch.setattr(Path, "exists", lambda self: True)
    monkeypatch.setattr(Path, "read_bytes", lambda self: b"")
    monkeypatch.setenv("MASTER_PASSPHRASE", "x" * 16)

    class _Sess:
        async def execute(self, *_a: Any, **_k: Any) -> Any:
            class _R:
                def scalar(self) -> Any:
                    return None
                def all(self) -> list[Any]:
                    return []
            return _R()

    # No profile= passed — must default to chain_writer.
    result = await preflight_module.run_preflight(
        _Sess(),  # type: ignore[arg-type]
    )

    assert len(result.checks) == 5
    assert "audit_chain_intact" in [c.name for c in result.checks]
    assert not result.all_passed


# ---------------------------------------------------------------------------
# Test 12 — chain broken: safety-net workers still spawn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_safety_net_workers_spawn_when_chain_broken_but_reader_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With chain broken + reader checks passing, the 3 safety-net workers
    must all spawn (telegram_poller / liquidation_monitor / live_exit_monitor).
    """
    _stub_lifespan_workers(monkeypatch)
    _patch_settings(monkeypatch)

    # reader passes (4 checks); writer chain check fails separately.
    async def fake_preflight(
        *_a: Any, profile: str = "chain_writer", **_k: Any,
    ) -> PreflightResult:
        assert profile == "chain_reader", (
            f"main.py must call run_preflight(profile='chain_reader'), "
            f"got profile={profile!r}"
        )
        return _make_reader_pass_chain_fail_result()

    monkeypatch.setattr(app_main, "run_preflight", fake_preflight)

    async def fake_chain_check(_session: Any, **_k: Any) -> CheckResult:
        return _make_chain_intact_fail()

    monkeypatch.setattr(
        app_main, "check_audit_chain_intact", fake_chain_check,
    )
    monkeypatch.setattr(app_main, "_record_heartbeat", _async_noop)

    async def fake_alert(_message: str, **_k: Any) -> bool:
        return True
    monkeypatch.setattr(app_main, "_route_alert", fake_alert)

    # Vault must succeed so the spawn block proceeds.
    monkeypatch.setattr(
        app_main, "initialize_vault_cache", lambda *_a, **_k: True,
    )

    class _Keys:
        binance_api_key = "k"
        binance_api_secret = "s"
    monkeypatch.setattr(app_main, "vault_keys", lambda: _Keys())

    # Telegram env so the poller branch is reached.
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")

    liq_calls: list[bool] = []
    lex_calls: list[bool] = []
    tp_calls: list[bool] = []

    def _liq(*_a: Any, **_k: Any) -> Any:
        liq_calls.append(True)
        return _NoopTask()

    def _lex(*_a: Any, **_k: Any) -> Any:
        lex_calls.append(True)
        return _NoopTask()

    def _tp(*_a: Any, **_k: Any) -> Any:
        tp_calls.append(True)
        return _NoopTask()

    monkeypatch.setattr(app_main, "start_liquidation_monitor", _liq)
    monkeypatch.setattr(app_main, "start_live_exit_monitor", _lex)
    monkeypatch.setattr(app_main, "start_telegram_poller", _tp)

    app = app_main.create_app()
    await _drive_lifespan(app)

    assert liq_calls == [True], "liquidation_monitor must spawn"
    assert lex_calls == [True], "live_exit_monitor must spawn"
    assert tp_calls == [True], "telegram_poller must spawn"


# ---------------------------------------------------------------------------
# Test 13 — chain broken: heartbeat status=reader_only_passed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chain_broken_emits_reader_only_passed_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_lifespan_workers(monkeypatch)
    _patch_settings(monkeypatch)

    async def fake_preflight(
        *_a: Any, profile: str = "chain_writer", **_k: Any,
    ) -> PreflightResult:
        return _make_reader_pass_chain_fail_result()

    monkeypatch.setattr(app_main, "run_preflight", fake_preflight)

    async def fake_chain_check(_session: Any, **_k: Any) -> CheckResult:
        return _make_chain_intact_fail()
    monkeypatch.setattr(
        app_main, "check_audit_chain_intact", fake_chain_check,
    )

    heartbeat_calls: list[dict[str, Any]] = []

    async def fake_record_heartbeat(
        _factory: Any, name: str, *, status: str = "ok",
        details: dict[str, Any] | None = None,
    ) -> None:
        heartbeat_calls.append({
            "name": name, "status": status, "details": details,
        })

    monkeypatch.setattr(app_main, "_record_heartbeat", fake_record_heartbeat)

    async def fake_alert(_message: str, **_k: Any) -> bool:
        return True
    monkeypatch.setattr(app_main, "_route_alert", fake_alert)

    app = app_main.create_app()
    await _drive_lifespan(app)

    preflight_beats = [b for b in heartbeat_calls
                       if b["name"] == "preflight_gate"]
    assert len(preflight_beats) == 1
    beat = preflight_beats[0]
    assert beat["status"] == "reader_only_passed", (
        f"expected status='reader_only_passed', got {beat['status']!r}"
    )
    assert beat["details"] is not None
    assert beat["details"]["failed_checks"] == ["audit_chain_intact"]
    assert beat["details"]["profile"] == "chain_reader"
    assert beat["details"]["passed_count"] == 4
    assert beat["details"]["total_count"] == 5


# ---------------------------------------------------------------------------
# Test 14 — chain broken: telegram alert "Audit chain WRITER blocked"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chain_broken_emits_audit_chain_writer_blocked_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_lifespan_workers(monkeypatch)
    _patch_settings(monkeypatch)

    async def fake_preflight(
        *_a: Any, profile: str = "chain_writer", **_k: Any,
    ) -> PreflightResult:
        return _make_reader_pass_chain_fail_result()
    monkeypatch.setattr(app_main, "run_preflight", fake_preflight)

    async def fake_chain_check(_session: Any, **_k: Any) -> CheckResult:
        return _make_chain_intact_fail()
    monkeypatch.setattr(
        app_main, "check_audit_chain_intact", fake_chain_check,
    )
    monkeypatch.setattr(app_main, "_record_heartbeat", _async_noop)

    alert_calls: list[dict[str, Any]] = []

    async def fake_alert(message: str, *, level: str = "warning") -> bool:
        alert_calls.append({"message": message, "level": level})
        return True
    monkeypatch.setattr(app_main, "_route_alert", fake_alert)

    app = app_main.create_app()
    await _drive_lifespan(app)

    assert len(alert_calls) == 1, (
        f"exactly one alert expected, got {len(alert_calls)}: {alert_calls}"
    )
    call = alert_calls[0]
    assert call["level"] == "critical"
    msg = call["message"]
    assert "Audit chain WRITER blocked" in msg
    assert "Safety-net workers RUNNING" in msg
    assert "FU-24 sweep" in msg
    # Must NOT be the old Preflight FAILED message — that's the failure path.
    assert "Preflight FAILED" not in msg


# ---------------------------------------------------------------------------
# Test 15 — chain intact: heartbeat status=passed, no alert (regression)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chain_intact_emits_passed_heartbeat_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity-check the happy path still works under the new split: chain
    intact + reader pass → status='passed', no alert."""
    _stub_lifespan_workers(monkeypatch)
    _patch_settings(monkeypatch)

    async def fake_preflight(
        *_a: Any, profile: str = "chain_writer", **_k: Any,
    ) -> PreflightResult:
        return _make_reader_pass_chain_fail_result()
    monkeypatch.setattr(app_main, "run_preflight", fake_preflight)

    async def fake_chain_check(_session: Any, **_k: Any) -> CheckResult:
        return _make_chain_intact_pass()
    monkeypatch.setattr(
        app_main, "check_audit_chain_intact", fake_chain_check,
    )

    heartbeat_calls: list[dict[str, Any]] = []

    async def fake_record_heartbeat(
        _factory: Any, name: str, *, status: str = "ok",
        details: dict[str, Any] | None = None,
    ) -> None:
        heartbeat_calls.append({
            "name": name, "status": status, "details": details,
        })

    monkeypatch.setattr(app_main, "_record_heartbeat", fake_record_heartbeat)

    alert_calls: list[dict[str, Any]] = []

    async def fake_alert(message: str, *, level: str = "warning") -> bool:
        alert_calls.append({"message": message, "level": level})
        return True
    monkeypatch.setattr(app_main, "_route_alert", fake_alert)

    app = app_main.create_app()
    await _drive_lifespan(app)

    preflight_beats = [b for b in heartbeat_calls
                       if b["name"] == "preflight_gate"]
    assert len(preflight_beats) == 1
    assert preflight_beats[0]["status"] == "passed"
    assert alert_calls == [], (
        f"alert must not be called on full pass; got: {alert_calls}"
    )


# ---------------------------------------------------------------------------
# Test 16 — reader fail uses existing PR-PREFLIGHT-ALERT failure path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reader_fail_uses_existing_pr_preflight_alert_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reader-side check fails — behavior must match PR-PREFLIGHT-ALERT
    exactly: OLD 'Preflight FAILED' alert, status='failed', NO safety-net
    workers spawned."""
    _stub_lifespan_workers(monkeypatch)
    _patch_settings(monkeypatch)

    async def fake_preflight(
        *_a: Any, profile: str = "chain_writer", **_k: Any,
    ) -> PreflightResult:
        return _make_reader_fail_result()
    monkeypatch.setattr(app_main, "run_preflight", fake_preflight)

    chain_called: list[bool] = []

    async def fake_chain_check(_session: Any, **_k: Any) -> CheckResult:
        chain_called.append(True)
        return _make_chain_intact_pass()
    monkeypatch.setattr(
        app_main, "check_audit_chain_intact", fake_chain_check,
    )

    heartbeat_calls: list[dict[str, Any]] = []

    async def fake_record_heartbeat(
        _factory: Any, name: str, *, status: str = "ok",
        details: dict[str, Any] | None = None,
    ) -> None:
        heartbeat_calls.append({
            "name": name, "status": status, "details": details,
        })
    monkeypatch.setattr(app_main, "_record_heartbeat", fake_record_heartbeat)

    alert_calls: list[dict[str, Any]] = []

    async def fake_alert(message: str, *, level: str = "warning") -> bool:
        alert_calls.append({"message": message, "level": level})
        return True
    monkeypatch.setattr(app_main, "_route_alert", fake_alert)

    # Track safety-net worker spawn — must NOT be called.
    liq_calls: list[bool] = []
    lex_calls: list[bool] = []
    tp_calls: list[bool] = []

    def _liq(*_a: Any, **_k: Any) -> Any:
        liq_calls.append(True)
        return _NoopTask()

    def _lex(*_a: Any, **_k: Any) -> Any:
        lex_calls.append(True)
        return _NoopTask()

    def _tp(*_a: Any, **_k: Any) -> Any:
        tp_calls.append(True)
        return _NoopTask()

    monkeypatch.setattr(app_main, "start_liquidation_monitor", _liq)
    monkeypatch.setattr(app_main, "start_live_exit_monitor", _lex)
    monkeypatch.setattr(app_main, "start_telegram_poller", _tp)

    app = app_main.create_app()
    await _drive_lifespan(app)

    # Alert: OLD message.
    assert len(alert_calls) == 1
    assert alert_calls[0]["level"] == "critical"
    assert "Preflight FAILED" in alert_calls[0]["message"]
    assert "Audit chain WRITER blocked" not in alert_calls[0]["message"]

    # Heartbeat: status='failed'.
    preflight_beats = [b for b in heartbeat_calls
                       if b["name"] == "preflight_gate"]
    assert len(preflight_beats) == 1
    assert preflight_beats[0]["status"] == "failed"

    # No safety-net worker spawned.
    assert liq_calls == []
    assert lex_calls == []
    assert tp_calls == []
