"""Tests for p_win_calibrator — PR5 isotonic implementation.

Contracts:
  - predict_p_win always returns None (deferred until threshold chosen).
  - fit_p_win_models returns None and logs gracefully when too few rows.
  - Path constants are correctly constructed.
  - sklearn lazy-import: module imports cleanly even if sklearn is broken
    (cold-start safety).
"""
from __future__ import annotations

import importlib
import logging
import sys
from unittest import mock

import pytest

from app.core.scoring.types import Direction


def _mock_session(rows: list) -> mock.AsyncMock:
    result = mock.MagicMock()
    result.fetchall.return_value = rows
    session = mock.AsyncMock()
    session.execute = mock.AsyncMock(return_value=result)
    return session


# ---------------------------------------------------------------------------
# predict_p_win — always None in PR1
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_predict_p_win_returns_none_for_long() -> None:
    """predict_p_win(0.5, Direction.LONG) must return None in PR1."""
    from app.core.scoring.p_win_calibrator import predict_p_win

    result = await predict_p_win(0.5, Direction.LONG)
    assert result is None


@pytest.mark.asyncio
async def test_predict_p_win_returns_none_for_short() -> None:
    """predict_p_win(-0.5, Direction.SHORT) must return None in PR1."""
    from app.core.scoring.p_win_calibrator import predict_p_win

    result = await predict_p_win(-0.5, Direction.SHORT)
    assert result is None


@pytest.mark.asyncio
async def test_predict_p_win_returns_none_for_neutral() -> None:
    """predict_p_win(0.0, Direction.NEUTRAL) must return None in PR1."""
    from app.core.scoring.p_win_calibrator import predict_p_win

    result = await predict_p_win(0.0, Direction.NEUTRAL)
    assert result is None


@pytest.mark.asyncio
async def test_predict_p_win_returns_none_for_extreme_scores() -> None:
    """Extreme scores (+1.0 LONG, -1.0 SHORT) must both return None in PR1."""
    from app.core.scoring.p_win_calibrator import predict_p_win

    assert await predict_p_win(1.0, Direction.LONG) is None
    assert await predict_p_win(-1.0, Direction.SHORT) is None


# ---------------------------------------------------------------------------
# fit_p_win_models — callable NOOP
# ---------------------------------------------------------------------------


async def test_fit_p_win_models_skips_gracefully_with_zero_rows() -> None:
    """fit_p_win_models with 0 rows must not raise and must return None."""
    from app.core.scoring.p_win_calibrator import fit_p_win_models

    result = await fit_p_win_models(_mock_session([]))
    assert result is None


async def test_fit_p_win_models_logs_skip_when_too_few_rows(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """fit_p_win_models must log an INFO message about skipping when < 50 rows."""
    from app.core.scoring.p_win_calibrator import fit_p_win_models

    with caplog.at_level(logging.INFO, logger="app.core.scoring.p_win_calibrator"):
        await fit_p_win_models(_mock_session([]))

    assert any(
        "skipping fit" in record.message
        for record in caplog.records
        if record.name == "app.core.scoring.p_win_calibrator"
    ), (
        "Expected an INFO log containing 'skipping fit' from p_win_calibrator; "
        f"got records: {[r.message for r in caplog.records]}"
    )


# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------


def test_model_paths_constructed_per_direction() -> None:
    """P_WIN_MODEL_PATH_LONG and P_WIN_MODEL_PATH_SHORT must point to the
    expected .pkl filenames inside P_WIN_MODEL_DIR."""
    from app.core.scoring.p_win_calibrator import (
        P_WIN_MODEL_DIR,
        P_WIN_MODEL_PATH_LONG,
        P_WIN_MODEL_PATH_SHORT,
    )

    assert P_WIN_MODEL_PATH_LONG == P_WIN_MODEL_DIR / "long.pkl"
    assert P_WIN_MODEL_PATH_SHORT == P_WIN_MODEL_DIR / "short.pkl"
    assert P_WIN_MODEL_PATH_LONG.name == "long.pkl"
    assert P_WIN_MODEL_PATH_SHORT.name == "short.pkl"


# ---------------------------------------------------------------------------
# Cold-start safety: sklearn lazy-import
# ---------------------------------------------------------------------------


def test_sklearn_import_is_lazy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Module must import even if sklearn is unavailable — fit/predict
    can fail at call time, but import-time must always succeed for
    cold-start safety.

    Since the PR1 stub never imports sklearn at all (only referenced in
    comments/docstrings), this trivially passes — confirming the contract.
    PR5 will need to re-verify when the lazy import is actually added
    inside predict_p_win.
    """
    # Save original reference (may not exist if sklearn isn't installed)
    original_sklearn = sys.modules.get("sklearn.isotonic")

    # Make sklearn.isotonic unimportable by inserting None as a sentinel
    monkeypatch.setitem(sys.modules, "sklearn.isotonic", None)  # type: ignore[arg-type]

    # Remove the calibrator module so importlib.import_module re-executes it
    module_key = "app.core.scoring.p_win_calibrator"
    if module_key in sys.modules:
        del sys.modules[module_key]

    # Import must NOT raise, even with a broken sklearn.isotonic
    mod = importlib.import_module(module_key)
    assert mod is not None, "Module import returned None unexpectedly"

    # Restore original state for other tests
    if original_sklearn is not None:
        monkeypatch.setitem(sys.modules, "sklearn.isotonic", original_sklearn)
    else:
        # If sklearn wasn't in sys.modules before, undo the None sentinel
        monkeypatch.delitem(sys.modules, "sklearn.isotonic", raising=False)
