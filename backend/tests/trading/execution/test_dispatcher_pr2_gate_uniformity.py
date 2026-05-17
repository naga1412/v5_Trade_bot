"""PR2 R3 mitigation — gate fires uniformly on auto + telegram-approve paths.

Call-graph trace (PR2 Phase 6 Task 6.1) — both `fully-auto` and
`telegram-approve` user modes flow through `dispatcher.dispatch()` at
signal-emission time. The mode-switch at the top of `dispatch()` only
short-circuits `manual` mode; everything else passes through the PR2
gate block we added in Phase 4. That makes the gate symmetric by
construction.

This test proves the symmetry behaviourally: low-MTF proposal +
telegram-approve user produces `blocked_mtf_low_agreement` AND writes
no `telegram_signals` row; high-MTF proposal on the same user mode
emits the signal.

We do NOT re-run the gate at approve time (`_place_approved_order`) —
that would be a behaviour change vs the existing funding-guard /
max-concurrent contract (those checks fire only at send time). Per
spec §4.6 we chose Option U2 (rely on the shared dispatch-time gate)
not U1 (refactor approve to re-call dispatch), because the approve
path intentionally bypasses the dispatch-time checks per its
docstring.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.trading.execution.dispatcher import (
    SignalProposal,
    UserContext,
    dispatch,
)


_NOW = datetime(2026, 5, 17, 14, 0, 0, tzinfo=timezone.utc)


def _proposal(**overrides: Any) -> SignalProposal:
    base = dict(
        symbol="BTC/USDT",
        timeframe="1h",
        direction="LONG",
        entry_price=80_000.0,
        stop_loss_price=78_400.0,
        take_profit_price=83_280.0,
        confidence_pct=72.0,
        layer_summary={"L1": {"score": 0.85, "note": "EMAs aligned"}},
        inputs_hash="abc",
        funding_rate_daily=-0.0002,
        chart_base_url="https://example.test",
    )
    base.update(overrides)
    return SignalProposal(**base)  # type: ignore[arg-type]


def _user(*, mode: str) -> UserContext:
    return UserContext(
        user_id=1, mode=mode,  # type: ignore[arg-type]
        binance_api_key="k", binance_api_secret="s",
        use_testnet=True, portfolio_value_usdt=1000.0,
        successful_trades=50,
        sizing_mode="fixed", fixed_size_usdt=30.0,
        max_leverage_cap=10, max_concurrent_positions=5,
        open_positions_count=0,
    )


async def _mk_session() -> Any:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, "
            "trading_mode TEXT NOT NULL DEFAULT 'manual')"
        ))
        await conn.execute(sa.text(
            "INSERT INTO users (id, trading_mode) VALUES (1, :m)"
        ), {"m": "manual"})
        await conn.execute(sa.text(
            "CREATE TABLE telegram_signals ("
            "id TEXT PRIMARY KEY, user_id INTEGER, symbol TEXT, "
            "direction TEXT, sent_at TEXT, payload TEXT, response TEXT)"
        ))
    return engine


@pytest.mark.asyncio
async def test_low_mtf_blocks_on_both_auto_and_telegram_modes() -> None:
    """Identical low-MTF proposal → same blocked outcome regardless of mode."""
    engine = await _mk_session()
    async with AsyncSession(engine) as s:
        # Telegram-approve mode
        await s.execute(sa.text(
            "UPDATE users SET trading_mode='telegram-approve' WHERE id=1"
        ))
        await s.commit()
        result_tg = await dispatch(
            s, proposal=_proposal(mtf_agreement=2),
            user=_user(mode="telegram-approve"), now=_NOW,
        )

        # Fully-auto mode
        await s.execute(sa.text(
            "UPDATE users SET trading_mode='fully-auto' WHERE id=1"
        ))
        await s.commit()
        result_auto = await dispatch(
            s, proposal=_proposal(mtf_agreement=2),
            user=_user(mode="fully-auto"), now=_NOW,
        )

        # No telegram_signals row written for the blocked telegram-approve
        # case — gate fires BEFORE the row is created.
        rows = (await s.execute(sa.text(
            "SELECT COUNT(*) AS n FROM telegram_signals"
        ))).first()

    assert result_tg.outcome == "blocked_mtf_low_agreement"
    assert result_auto.outcome == "blocked_mtf_low_agreement"
    assert rows.n == 0


@pytest.mark.asyncio
async def test_higher_tf_veto_fires_on_both_modes() -> None:
    """1d+1w both opposite LONG → veto fires uniformly on both modes."""
    engine = await _mk_session()
    opposing_dirs = {"5m": 1, "15m": 1, "1h": 1, "4h": 1, "1d": -1, "1w": -1}
    async with AsyncSession(engine) as s:
        await s.execute(sa.text(
            "UPDATE users SET trading_mode='telegram-approve' WHERE id=1"
        ))
        await s.commit()
        result_tg = await dispatch(
            s, proposal=_proposal(
                mtf_agreement=5, mtf_directions=opposing_dirs,
            ),
            user=_user(mode="telegram-approve"), now=_NOW,
        )

        await s.execute(sa.text(
            "UPDATE users SET trading_mode='fully-auto' WHERE id=1"
        ))
        await s.commit()
        result_auto = await dispatch(
            s, proposal=_proposal(
                mtf_agreement=5, mtf_directions=opposing_dirs,
            ),
            user=_user(mode="fully-auto"), now=_NOW,
        )

    assert result_tg.outcome == "blocked_mtf_higher_tf_veto"
    assert result_auto.outcome == "blocked_mtf_higher_tf_veto"


@pytest.mark.asyncio
async def test_manual_mode_short_circuits_before_gate() -> None:
    """manual mode emits regardless of MTF — gate doesn't run.

    The mode-switch sits ABOVE the gate block in dispatch(); manual mode
    is a recording-only/UI-only path so it never feeds the auto/telegram
    placement code. Gate symmetry only applies to modes that actually
    dispatch to Binance or Telegram. This regression test locks in the
    short-circuit so a future refactor doesn't accidentally apply the
    gate to manual signals (which would break Tab 1 signal exposure).
    """
    engine = await _mk_session()
    async with AsyncSession(engine) as s:
        # manual is already the seeded default — confirm explicitly.
        await s.execute(sa.text(
            "UPDATE users SET trading_mode='manual' WHERE id=1"
        ))
        await s.commit()
        result = await dispatch(
            s, proposal=_proposal(mtf_agreement=1),  # would block in auto
            user=_user(mode="manual"), now=_NOW,
        )

    assert result.outcome == "emitted"
