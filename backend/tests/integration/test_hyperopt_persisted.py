"""Integration test: persist_hyperopt_result writes to hyperopt_studies."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
import sqlalchemy as sa

from tools.hyperopt import HyperoptResult, persist_hyperopt_result


@pytest.mark.asyncio
async def test_persist_hyperopt_writes_row(bot_status_factory) -> None:  # type: ignore[no-untyped-def]
    """A HyperoptResult is persisted with status='completed' and is readable back."""
    session_factory = bot_status_factory
    async with session_factory() as session:
        result = HyperoptResult(
            best_weights={i: 1 / 9 for i in range(1, 10)},
            best_sharpe=1.42, n_trials=20, study=None,
        )
        new_id = await persist_hyperopt_result(
            session, result=result,
            symbol="BTC/USDT", timeframe="1h",
            train_window=(datetime(2025, 1, 1, tzinfo=timezone.utc),
                          datetime(2025, 6, 30, tzinfo=timezone.utc)),
            val_window=(datetime(2025, 7, 1, tzinfo=timezone.utc),
                        datetime(2025, 12, 31, tzinfo=timezone.utc)),
            triggered_by_user_id=1,
        )
        await session.commit()

        row = (await session.execute(sa.text(
            "SELECT * FROM hyperopt_studies WHERE id = :i"
        ), {"i": new_id})).first()
    assert row is not None
    assert row.symbol == "BTC/USDT"
    assert row.timeframe == "1h"
    assert row.best_sharpe == 1.42
    assert row.n_trials == 20
    assert row.status == "completed"
    assert row.triggered_by == 1


@pytest.mark.asyncio
async def test_persist_hyperopt_nullable_user(bot_status_factory) -> None:  # type: ignore[no-untyped-def]
    """triggered_by_user_id may be NULL (CLI-triggered runs)."""
    session_factory = bot_status_factory
    async with session_factory() as session:
        result = HyperoptResult(
            best_weights={i: 1 / 9 for i in range(1, 10)},
            best_sharpe=0.7, n_trials=5, study=None,
        )
        new_id = await persist_hyperopt_result(
            session, result=result,
            symbol="ETH/USDT", timeframe="4h",
            train_window=(datetime(2025, 1, 1, tzinfo=timezone.utc),
                          datetime(2025, 6, 30, tzinfo=timezone.utc)),
            val_window=(datetime(2025, 7, 1, tzinfo=timezone.utc),
                        datetime(2025, 12, 31, tzinfo=timezone.utc)),
            triggered_by_user_id=None,
        )
        await session.commit()
        row = (await session.execute(sa.text(
            "SELECT * FROM hyperopt_studies WHERE id = :i"
        ), {"i": new_id})).first()
    assert row is not None
    assert row.triggered_by is None
    assert row.symbol == "ETH/USDT"
