"""SP-7 Phase A4: app/ops package + app/ml/champion_challenger stubs exist.

These stubs are intentional import targets so downstream phases can write
their failing tests against `from app.ops.alerts import alert_admin`
without ImportError on the *module*. Each stub is allowed (and expected)
to raise NotImplementedError when invoked — that's what later phases turn
green.

This unit test only enforces the surface: the modules import cleanly and
expose the symbols their phase contracts require.
"""
from __future__ import annotations

import inspect

import pytest


def test_app_ops_package_imports() -> None:
    import app.ops  # noqa: F401


def test_monitoring_exposes_instrument_app_signature() -> None:
    from app.ops.monitoring import instrument_app

    sig = inspect.signature(instrument_app)
    # Spec §3.1: instrument_app(app: FastAPI) -> None
    assert list(sig.parameters) == ["app"]


def test_monitoring_instrument_app_is_a_stub() -> None:
    from fastapi import FastAPI

    from app.ops.monitoring import instrument_app

    with pytest.raises(NotImplementedError, match="Phase F4"):
        instrument_app(FastAPI())


def test_alerts_exposes_alert_admin_coroutine_signature() -> None:
    from app.ops.alerts import alert_admin

    assert inspect.iscoroutinefunction(alert_admin)
    sig = inspect.signature(alert_admin)
    # Spec §3.1: alert_admin(message: str, *, severity: str = "warning")
    params = sig.parameters
    assert "message" in params
    assert "severity" in params
    assert params["severity"].default == "warning"


@pytest.mark.asyncio
async def test_alerts_alert_admin_is_a_stub() -> None:
    from app.ops.alerts import alert_admin

    with pytest.raises(NotImplementedError, match="Phase D1"):
        await alert_admin("hello")


def test_verifier_scheduler_exposes_loop_and_task_starters() -> None:
    from app.ops.verifier_scheduler import (
        run_audit_verifier_loop,
        start_audit_verifier_task,
    )

    assert inspect.iscoroutinefunction(run_audit_verifier_loop)
    # start_audit_verifier_task is a sync helper that schedules the loop;
    # it itself is not a coroutine function.
    assert not inspect.iscoroutinefunction(start_audit_verifier_task)


def test_champion_challenger_exposes_evaluate_challenger() -> None:
    """Spec §6.5: champion/challenger gate lives under app.ml."""
    from app.ml.champion_challenger import evaluate_challenger

    # Stub may be sync or async — phase B/F will pin the contract; we only
    # care that the symbol is importable.
    assert callable(evaluate_challenger)
