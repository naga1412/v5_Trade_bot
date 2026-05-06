"""Prometheus instrumentation hooks for the FastAPI app.

Implementation lands in Phase F4. This stub exists so app.main can import
the future symbol without a circular-import surprise during Phase B/C/D
work.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI


def instrument_app(app: "FastAPI") -> None:  # pragma: no cover — stub
    """Wire prometheus-fastapi-instrumentator. Implemented in Phase F4.

    TODO(SP-7 Phase F4): replace with real Instrumentator().instrument(app).expose(app)
    plus the per-route metric whitelist from spec §3.1.
    """
    raise NotImplementedError("instrument_app: Phase F4 deliverable")
