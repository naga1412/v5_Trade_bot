"""Unit tests for p_win calibration fitting (PR5 implementation)."""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest


@pytest.fixture(autouse=True)
def _clear_model_cache():
    """_LOADED_MODELS is a module-level dict shared across the whole test
    session. Without this, test_fit_creates_pkl_files (and friends,
    below) leave a REAL fitted model cached after fit_p_win_models runs
    for real (this file's tests run with sklearn installed, unlike some
    local dev environments) — a later test in THIS file or ANY other
    file asserting "no model available" would then observe a real
    prediction instead of None. Caught in CI (2026-08-05): had only
    added this fixture to tests/core/scoring/test_p_win_calibrator.py,
    not here — test_predict_p_win_still_returns_none failed with
    "assert 1.0 is None" because an earlier test in this same file had
    already populated the cache."""
    import app.core.scoring.p_win_calibrator as mod
    mod._LOADED_MODELS.clear()
    yield
    mod._LOADED_MODELS.clear()


def _make_rows(n_long: int = 60, n_short: int = 40) -> list:
    """Synthetic shadow_trade rows with a monotone signal→win relationship."""
    rows = []
    for i in range(n_long):
        score = 0.1 + (i / n_long) * 0.9
        rows.append(
            mock.MagicMock(
                entry_score=score,
                direction="LONG",
                won=1 if score > 0.5 else 0,
            )
        )
    for i in range(n_short):
        score = -(0.1 + (i / n_short) * 0.9)
        rows.append(
            mock.MagicMock(
                entry_score=score,
                direction="SHORT",
                won=1 if abs(score) > 0.5 else 0,
            )
        )
    return rows


def _mock_session(rows: list) -> mock.AsyncMock:
    result = mock.MagicMock()
    result.fetchall.return_value = rows
    session = mock.AsyncMock()
    session.execute = mock.AsyncMock(return_value=result)
    return session


@pytest.mark.asyncio
async def test_fit_creates_pkl_files(tmp_path: Path) -> None:
    """fit_p_win_models writes both .pkl files given ≥50 monotone training rows."""
    import app.core.scoring.p_win_calibrator as mod

    session = _mock_session(_make_rows())
    with (
        mock.patch.object(mod, "P_WIN_MODEL_DIR", tmp_path),
        mock.patch.object(mod, "P_WIN_MODEL_PATH_LONG", tmp_path / "long.pkl"),
        mock.patch.object(mod, "P_WIN_MODEL_PATH_SHORT", tmp_path / "short.pkl"),
    ):
        await mod.fit_p_win_models(session)

    assert (tmp_path / "long.pkl").exists(), "LONG model pkl not created"
    assert (tmp_path / "short.pkl").exists(), "SHORT model pkl not created"


@pytest.mark.asyncio
async def test_fit_pkl_contains_sklearn_model(tmp_path: Path) -> None:
    """Pickled file must be a fitted IsotonicRegression with predict() method."""
    import pickle

    import app.core.scoring.p_win_calibrator as mod

    session = _mock_session(_make_rows())
    with (
        mock.patch.object(mod, "P_WIN_MODEL_DIR", tmp_path),
        mock.patch.object(mod, "P_WIN_MODEL_PATH_LONG", tmp_path / "long.pkl"),
        mock.patch.object(mod, "P_WIN_MODEL_PATH_SHORT", tmp_path / "short.pkl"),
    ):
        await mod.fit_p_win_models(session)

    with open(tmp_path / "long.pkl", "rb") as f:
        ir = pickle.load(f)

    assert hasattr(ir, "predict"), "Loaded model must have predict()"
    preds = ir.predict([0.3, 0.6, 0.9])
    assert all(0.0 <= p <= 1.0 for p in preds), "Predictions must be in [0, 1]"


@pytest.mark.asyncio
async def test_fit_skips_when_too_few_rows(tmp_path: Path) -> None:
    """fit_p_win_models must not write any pkl when total rows < 50."""
    import app.core.scoring.p_win_calibrator as mod

    session = _mock_session(_make_rows(n_long=10, n_short=10))
    with (
        mock.patch.object(mod, "P_WIN_MODEL_DIR", tmp_path),
        mock.patch.object(mod, "P_WIN_MODEL_PATH_LONG", tmp_path / "long.pkl"),
        mock.patch.object(mod, "P_WIN_MODEL_PATH_SHORT", tmp_path / "short.pkl"),
    ):
        await mod.fit_p_win_models(session)

    assert not (tmp_path / "long.pkl").exists(), "must not write pkl with < 50 rows"
    assert not (tmp_path / "short.pkl").exists()


@pytest.mark.asyncio
async def test_fit_skips_direction_with_insufficient_rows(tmp_path: Path) -> None:
    """A direction with < 20 training rows must be skipped; the other still fits."""
    import app.core.scoring.p_win_calibrator as mod

    # 60 LONG rows (enough) but only 5 SHORT rows (< 20 threshold)
    session = _mock_session(_make_rows(n_long=60, n_short=5))
    with (
        mock.patch.object(mod, "P_WIN_MODEL_DIR", tmp_path),
        mock.patch.object(mod, "P_WIN_MODEL_PATH_LONG", tmp_path / "long.pkl"),
        mock.patch.object(mod, "P_WIN_MODEL_PATH_SHORT", tmp_path / "short.pkl"),
    ):
        await mod.fit_p_win_models(session)

    assert (tmp_path / "long.pkl").exists(), "LONG should be fitted"
    # 5 SHORT rows → only 4 in the 80% train split → < 20 → skip
    assert not (tmp_path / "short.pkl").exists(), "SHORT should be skipped"


@pytest.mark.asyncio
async def test_predict_p_win_returns_none_with_no_model_on_disk() -> None:
    """No model has been fitted at the real P_WIN_MODEL_PATH_{LONG,SHORT}
    paths in a fresh checkout (these are runtime-generated artifacts,
    not committed) — predict_p_win must return None, not raise, and
    (per the autouse fixture above) this isn't polluted by the fit
    tests above it in this same file having cached a real model."""
    from app.core.scoring.p_win_calibrator import predict_p_win
    from app.core.scoring.types import Direction

    result = await predict_p_win(final_score=0.9, direction=Direction.LONG)
    assert result is None

    result = await predict_p_win(final_score=-0.7, direction=Direction.SHORT)
    assert result is None
