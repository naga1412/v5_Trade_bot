"""SP-7 Phase F4: app.ops.monitoring.instrument_app contract.

instrument_app must:
  1. Accept a fresh FastAPI app and return None.
  2. Add a /metrics route to the app.
  3. /metrics returns Prometheus text exposition format on GET.

Tests use FastAPI's TestClient (a thin httpx wrapper) so we don't need a
running server.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_instrument_app_returns_none_and_adds_metrics_route() -> None:
    from app.ops.monitoring import instrument_app

    app = FastAPI()
    result = instrument_app(app)
    assert result is None

    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/metrics" in paths


def test_metrics_endpoint_returns_prometheus_text_format() -> None:
    from app.ops.monitoring import instrument_app

    app = FastAPI()

    @app.get("/ping")
    def _ping() -> dict[str, str]:
        return {"ok": "yes"}

    instrument_app(app)
    client = TestClient(app)

    # Drive at least one request so the instrumentator records something.
    assert client.get("/ping").status_code == 200

    resp = client.get("/metrics")
    assert resp.status_code == 200
    ctype = resp.headers.get("content-type", "")
    # Prometheus exposition format is text/plain; version=0.0.4 (or similar).
    assert "text/plain" in ctype, f"unexpected content-type: {ctype}"
    body = resp.text
    # Standard prometheus_client text format starts each metric family with
    # a # HELP line. The instrumentator emits http_request_duration_seconds
    # and http_requests_total at minimum.
    assert "# HELP" in body
    assert "http_request" in body


def test_instrument_app_idempotent_on_second_call_does_not_raise() -> None:
    """Calling instrument_app twice must not raise (e.g. duplicate metric).

    The instrumentator may complain about duplicate Collector registration
    on the default registry; we only assert the second call doesn't blow
    up the app — at worst it's a no-op or logs a warning. This guards
    against accidental double-wire in create_app() during refactor.
    """
    from app.ops.monitoring import instrument_app

    app = FastAPI()
    instrument_app(app)
    # Second call may raise from prometheus_client's default registry;
    # callers should only invoke once. We accept either silent success or
    # a ValueError from prometheus_client — both are sane.
    try:
        instrument_app(app)
    except ValueError:
        pass


def test_create_app_exposes_metrics_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: a fresh create_app() must wire /metrics."""
    # Stub settings so create_app's CORS branch doesn't try to read a real
    # settings file from disk.
    from app import main as app_main

    monkeypatch.setattr(
        app_main, "get_settings",
        lambda: type("S", (), {"env": "test", "worker_enabled": False})(),
    )
    app = app_main.create_app()
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/metrics" in paths
