"""Prometheus instrumentation hooks for the FastAPI app (SP-7 Phase F4).

`instrument_app(app)` wires `prometheus-fastapi-instrumentator` into the
given FastAPI instance and exposes a `/metrics` endpoint that serves the
standard Prometheus text exposition format. The metric families produced
include `http_request_duration_seconds_*` (histogram) and
`http_requests_total` (counter), both of which the Phase F2 prometheus.yml
scrape job consumes and the Phase F2 alert_rules.yml HighLatency rule
queries.

Spec §3.1: must be called from `app.main:create_app()` AFTER all routers
are added, so every route is observed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prometheus_fastapi_instrumentator import Instrumentator

if TYPE_CHECKING:
    from fastapi import FastAPI


def instrument_app(app: "FastAPI") -> None:
    """Wire Prometheus metrics + expose /metrics on the given FastAPI app.

    The endpoint is intentionally registered without an auth dependency:
    in production it sits behind the Cloudflare Access policy applied at
    the tunnel layer (spec §2.6), and in dev it's served from the
    localhost-only port mapping in docker-compose.yml.
    """
    Instrumentator().instrument(app).expose(
        app,
        endpoint="/metrics",
        include_in_schema=False,
    )
