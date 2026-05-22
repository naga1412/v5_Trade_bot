"""PR-PREFLIGHT-ALERT — surface preflight failures via telegram + heartbeat.

Tests that the preflight gate in ``app/main.py:282-394``:
  - On PASS: writes a heartbeat row with status='passed' and does NOT alert.
  - On FAIL: log.critical + alert via app.ops.alert_routing.alert_admin
    (level='critical') + heartbeat row with status='failed' and the
    failed_checks names in details.
  - On EXCEPTION (run_preflight raises): log.critical + alert + heartbeat
    row with status='raised'.
  - Alerter / heartbeat failure must NEVER break the lifespan.

All 8 cases drive the FastAPI lifespan directly (the existing
``test_app_startup`` pattern) and monkeypatch the alerter + heartbeat
helpers so we assert on calls + lifespan completion.
"""
from __future__ import annotations

import logging
from typing import Any

import pytest

from app import main as app_main
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
    return PreflightResult(checks=[
        CheckResult(name="master_passphrase_set", passed=True, detail="ok"),
        CheckResult(name="vault_decrypt_ok", passed=True, detail="ok"),
        CheckResult(name="binance_permissions_safe", passed=True, detail="ok"),
        CheckResult(name="migration_0016_applied", passed=True, detail="ok"),
        CheckResult(name="audit_chain_intact", passed=True, detail="ok"),
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
