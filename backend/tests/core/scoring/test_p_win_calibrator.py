"""Tests for p_win_calibrator — PR5 isotonic implementation.

Contracts:
  - predict_p_win returns None when NEUTRAL, regardless of model state
    (permanent — no model is ever fitted for NEUTRAL).
  - predict_p_win returns None when no model has been fitted yet (no
    .pkl on disk) — the pre-first-nightly-refit / cold-start state.
  - predict_p_win returns a real calibrated probability in [0, 1] when a
    model IS available, via the isotonic transform on abs(final_score).
  - fit_p_win_models returns None and logs gracefully when too few rows.
  - Path constants are correctly constructed.
  - sklearn lazy-import: module imports cleanly even if sklearn is broken
    (cold-start safety).

2026-08-05: model-prediction tests use a duck-typed fake (an object with
a .predict() method) rather than a real fitted IsotonicRegression, so
this file's coverage of predict_p_win's loading/caching/clamping logic
doesn't depend on sklearn being installed in the environment running the
tests — fit_p_win_models' OWN correctness (real isotonic fit) is covered
separately in tests/unit/test_pwin_calibrator.py, which does need sklearn.
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


@pytest.fixture(autouse=True)
def _clear_model_cache():
    """_LOADED_MODELS is a module-level dict shared across the whole test
    session. Without this, a test elsewhere that runs fit_p_win_models
    (test_pwin_calibrator.py) leaves a REAL fitted model cached, and
    tests here asserting "no model available" would then observe a real
    prediction instead of None — a cross-file test-order dependency.
    Clear before AND after so this file can't pollute others either."""
    import app.core.scoring.p_win_calibrator as mod
    mod._LOADED_MODELS.clear()
    yield
    mod._LOADED_MODELS.clear()


# ---------------------------------------------------------------------------
# predict_p_win — NEUTRAL is permanently None; no-model-yet is None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_predict_p_win_returns_none_when_no_model_fitted_yet_long() -> None:
    """No .pkl on disk yet (cold-start / before first nightly refit)."""
    from app.core.scoring.p_win_calibrator import predict_p_win

    result = await predict_p_win(0.5, Direction.LONG)
    assert result is None


@pytest.mark.asyncio
async def test_predict_p_win_returns_none_when_no_model_fitted_yet_short() -> None:
    from app.core.scoring.p_win_calibrator import predict_p_win

    result = await predict_p_win(-0.5, Direction.SHORT)
    assert result is None


@pytest.mark.asyncio
async def test_predict_p_win_returns_none_for_neutral_even_with_a_model_available() -> None:
    """NEUTRAL must return None permanently — it never reaches the model
    lookup at all (no direction→path mapping exists for it), so this
    holds even when LONG/SHORT models ARE cached and available."""
    import app.core.scoring.p_win_calibrator as mod

    mod._LOADED_MODELS[Direction.LONG] = _FakeModel(0.9)
    mod._LOADED_MODELS[Direction.SHORT] = _FakeModel(0.9)
    result = await mod.predict_p_win(0.0, Direction.NEUTRAL)
    assert result is None


@pytest.mark.asyncio
async def test_predict_p_win_returns_none_for_extreme_scores_with_no_model() -> None:
    """Extreme scores (+1.0 LONG, -1.0 SHORT) with no model fitted yet."""
    from app.core.scoring.p_win_calibrator import predict_p_win

    assert await predict_p_win(1.0, Direction.LONG) is None
    assert await predict_p_win(-1.0, Direction.SHORT) is None


# ---------------------------------------------------------------------------
# predict_p_win — real prediction path (duck-typed fake model, no sklearn
# dependency; fit_p_win_models' actual isotonic-fit correctness is
# covered in tests/unit/test_pwin_calibrator.py)
# ---------------------------------------------------------------------------


class _FakeModel:
    """Duck-types sklearn's IsotonicRegression.predict() interface."""

    def __init__(self, value: float) -> None:
        self._value = value

    def predict(self, x: list[float]) -> list[float]:
        return [self._value for _ in x]


@pytest.mark.asyncio
async def test_predict_p_win_uses_cached_model_when_available() -> None:
    """When _LOADED_MODELS already has a model (e.g. this process just ran
    the nightly refit), predict_p_win must use it directly — no disk I/O."""
    import app.core.scoring.p_win_calibrator as mod

    mod._LOADED_MODELS[Direction.LONG] = _FakeModel(0.73)
    result = await mod.predict_p_win(0.5, Direction.LONG)
    assert result == pytest.approx(0.73)


@pytest.mark.asyncio
async def test_predict_p_win_lazy_loads_from_disk(tmp_path) -> None:
    """A model pickled by a PRIOR process (not this one) must still be
    picked up — predict_p_win can't assume fit_p_win_models ran in the
    same process."""
    import pickle

    import app.core.scoring.p_win_calibrator as mod

    path = tmp_path / "long.pkl"
    with open(path, "wb") as f:
        pickle.dump(_FakeModel(0.61), f)

    with mock.patch.object(mod, "_MODEL_PATH_FOR_DIRECTION", {Direction.LONG: path}):
        result = await mod.predict_p_win(0.5, Direction.LONG)
    assert result == pytest.approx(0.61)


@pytest.mark.asyncio
async def test_predict_p_win_clamps_out_of_range_predictions() -> None:
    """Defensive clamp to [0, 1] even if a model somehow predicts outside
    that range (IsotonicRegression with y_min/y_max shouldn't, but the
    clamp is cheap insurance against a differently-configured model)."""
    import app.core.scoring.p_win_calibrator as mod

    mod._LOADED_MODELS[Direction.LONG] = _FakeModel(1.4)
    assert await mod.predict_p_win(0.9, Direction.LONG) == pytest.approx(1.0)

    mod._LOADED_MODELS[Direction.SHORT] = _FakeModel(-0.2)
    assert await mod.predict_p_win(-0.9, Direction.SHORT) == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_predict_p_win_caches_no_model_sentinel_and_does_not_reread_disk(
    tmp_path,
) -> None:
    """First call with no .pkl caches _NO_MODEL; a second call must not
    re-stat the filesystem (verified by deleting the dir and confirming
    the cached None is still returned, not an exception)."""
    import app.core.scoring.p_win_calibrator as mod

    missing_path = tmp_path / "does_not_exist" / "long.pkl"
    with mock.patch.object(
        mod, "_MODEL_PATH_FOR_DIRECTION", {Direction.LONG: missing_path},
    ):
        first = await mod.predict_p_win(0.5, Direction.LONG)
        second = await mod.predict_p_win(0.5, Direction.LONG)
    assert first is None
    assert second is None
    assert mod._LOADED_MODELS[Direction.LONG] is mod._NO_MODEL


@pytest.mark.asyncio
async def test_predict_p_win_fails_open_on_corrupt_pickle(tmp_path) -> None:
    """An unreadable/corrupt .pkl must not raise — predict_p_win is on
    the live prediction path and must never break it over a calibration
    file issue."""
    import app.core.scoring.p_win_calibrator as mod

    path = tmp_path / "long.pkl"
    path.write_bytes(b"this is not a valid pickle stream")

    with mock.patch.object(mod, "_MODEL_PATH_FOR_DIRECTION", {Direction.LONG: path}):
        result = await mod.predict_p_win(0.5, Direction.LONG)
    assert result is None


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
