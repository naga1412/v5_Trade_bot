"""SP-8 Phase J — end-to-end dispatcher tests.

Exercises the full dispatcher.dispatch() routing for each of the three
trading modes against a fresh in-memory SQLite, with the BinanceLiveClient
constructor monkeypatched to a stub. Catches regressions in:

  - the mode → outcome routing (manual / telegram-approve / fully-auto)
  - the funding-rate + max-positions pre-conditions
  - the live_trades + telegram_signals row shape (hash-chained, FIFO-ready)

Sister to the unit tests in test_dispatcher.py + test_telegram_polling.py
which cover individual helpers; this file proves the helpers compose.
"""
from __future__ import annotations

from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.trading.execution import dispatcher as dispatcher_mod
from app.trading.execution.dispatcher import (
    SignalProposal,
    UserContext,
    dispatch,
)


async def _mk_engine() -> Any:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE users ("
            "id INTEGER PRIMARY KEY, "
            "trading_mode TEXT NOT NULL DEFAULT 'manual')"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE telegram_signals ("
            "id TEXT PRIMARY KEY, user_id INTEGER, symbol TEXT, "
            "direction TEXT, sent_at TEXT, payload TEXT, "
            "response TEXT, response_at TEXT, response_leverage INTEGER)"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE live_trades ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER, symbol TEXT, direction TEXT, "
            "margin_usdt REAL, leverage INTEGER, position_value_usdt REAL, "
            "entry_price REAL, stop_loss REAL, take_profit REAL, "
            "binance_order_id TEXT, opened_at TEXT, mode_at_open TEXT, "
            "approved_via TEXT, reasoning TEXT, inputs_hash TEXT, "
            "closed_at TEXT, pnl_usdt REAL, "
            "mtf_agreement INTEGER, mtf_dominant_tf TEXT, "
            "mtf_directions_json TEXT, "
            "prev_hash TEXT, row_hash TEXT)"
        ))
    return engine


async def _seed_user(engine: Any, *, mode: str) -> None:
    async with AsyncSession(engine) as s:
        await s.execute(sa.text(
            "INSERT INTO users (id, trading_mode) VALUES (1, :m)"
        ), {"m": mode})
        await s.commit()


def _proposal(**overrides: Any) -> SignalProposal:
    base: dict[str, Any] = dict(
        symbol="BTC/USDT", timeframe="1h", direction="LONG",
        entry_price=80_000.0, stop_loss_price=78_400.0,
        take_profit_price=83_280.0,
        confidence_pct=72, layer_summary={"L1": {"score": 0.85}},
        inputs_hash="abc", funding_rate_daily=-0.0002,
        chart_base_url="https://aji12.nagayuaj.com",
    )
    base.update(overrides)
    return SignalProposal(**base)


def _user(**overrides: Any) -> UserContext:
    base: dict[str, Any] = dict(
        user_id=1, mode="manual",
        binance_api_key="k", binance_api_secret="s",
        use_testnet=True, portfolio_value_usdt=1000,
        successful_trades=0, sizing_mode="fixed",
        fixed_size_usdt=30.0, max_leverage_cap=10,
        max_concurrent_positions=5, open_positions_count=0,
    )
    base.update(overrides)
    return UserContext(**base)


# ---- Stub Binance client -------------------------------------------------


class _StubBinance:
    instances: list["_StubBinance"] = []

    def __init__(
        self, *, api_key: str, api_secret: str, use_testnet: bool = True,
        http: Any = None,
    ) -> None:
        self._key = api_key
        self._closed = False
        _StubBinance.instances.append(self)

    async def aclose(self) -> None:
        self._closed = True

    async def place_order(
        self, *, symbol: str, side: str, quantity: float,
        leverage: int, order_type: str,
    ) -> Any:
        return type("Order", (), {
            "binance_order_id": "stub-order-1",
            "avg_fill_price": 80_100.0,
        })


@pytest.fixture(autouse=True)
def _patch_binance(monkeypatch: pytest.MonkeyPatch) -> None:
    _StubBinance.instances.clear()
    monkeypatch.setattr(
        dispatcher_mod, "BinanceLiveClient", _StubBinance,
    )


# ---- Tests ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_mode_emits_no_side_effects() -> None:
    engine = await _mk_engine()
    await _seed_user(engine, mode="manual")
    async with AsyncSession(engine) as s:
        result = await dispatch(s, proposal=_proposal(), user=_user())
        await s.commit()
    assert result.outcome == "emitted"
    async with AsyncSession(engine) as s:
        n_live = (await s.execute(sa.text(
            "SELECT count(*) FROM live_trades"
        ))).scalar()
        n_tg = (await s.execute(sa.text(
            "SELECT count(*) FROM telegram_signals"
        ))).scalar()
    assert n_live == 0
    assert n_tg == 0
    assert _StubBinance.instances == []  # never constructed


@pytest.mark.asyncio
async def test_telegram_approve_writes_signal_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telegram-approve mode persists the DB row + reports outcome
    truthfully. With creds set + outbound mocked to succeed, outcome
    is ``sent_telegram``. See ``test_telegram_approve_*`` siblings for
    the no-creds and send-failure paths."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    async def _fake_send(session, *, signal_id, config, http=None):
        return 9999  # fake telegram message_id (success)

    monkeypatch.setattr(
        dispatcher_mod, "send_trade_signal_message", _fake_send,
    )

    engine = await _mk_engine()
    await _seed_user(engine, mode="telegram-approve")
    async with AsyncSession(engine) as s:
        result = await dispatch(s, proposal=_proposal(), user=_user())
        await s.commit()
    assert result.outcome == "sent_telegram"
    assert result.signal_id and result.signal_id.startswith("sig_")
    async with AsyncSession(engine) as s:
        row = (await s.execute(sa.text(
            "SELECT user_id, symbol, direction, payload "
            "FROM telegram_signals"
        ))).first()
    assert row is not None
    assert row.user_id == 1
    assert row.symbol == "BTC/USDT"
    assert row.direction == "LONG"
    # payload is JSON; check it carries the rendered_body + inline_keyboard
    import json
    payload = json.loads(row.payload)
    assert "rendered_body" in payload
    assert "inline_keyboard" in payload
    assert payload["entry_price"] == 80_000.0
    # No live order placed in telegram-approve mode.
    assert _StubBinance.instances == []


@pytest.mark.asyncio
async def test_telegram_approve_sends_outbound_when_creds_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID are set, the dispatcher
    must POST the message to Telegram synchronously — not just write the
    DB row. Regression: the original code wrote the row but no worker
    was wired to send it, so operators never received notifications."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    sent: list[dict[str, object]] = []

    async def _capture_send(session, *, signal_id, config, http=None):
        sent.append({
            "signal_id": signal_id,
            "bot_token": config.bot_token,
            "chat_id": config.chat_id,
        })
        return 9999  # fake telegram message_id

    monkeypatch.setattr(
        dispatcher_mod, "send_trade_signal_message", _capture_send,
    )

    engine = await _mk_engine()
    await _seed_user(engine, mode="telegram-approve")
    async with AsyncSession(engine) as s:
        result = await dispatch(s, proposal=_proposal(), user=_user())
        await s.commit()

    assert result.outcome == "sent_telegram"
    assert result.signal_id and result.signal_id.startswith("sig_")
    assert len(sent) == 1, "send_trade_signal_message must be invoked once"
    assert sent[0]["signal_id"] == result.signal_id
    assert sent[0]["bot_token"] == "test-token"
    assert sent[0]["chat_id"] == "12345"


@pytest.mark.asyncio
async def test_telegram_approve_skips_outbound_when_creds_missing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """When creds are missing, dispatch must still write the DB row but
    report ``queued_send_failed`` outcome (the row is the source of truth
    and can be flushed later; the outcome label must reflect reality).
    It must NOT raise, and must log a clear warning.

    PR-FIX-DISPATCHER-TELEGRAM-OUTCOME-LIE updated this assertion: the
    pre-fix code reported ``sent_telegram`` even when the outbound POST
    was skipped — a lie that wasted operator triage time. The honest
    outcome is now ``queued_send_failed`` with detail naming the reason.
    """
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    sent: list[object] = []

    async def _should_not_call(*_a, **_k):
        sent.append(1)
        return 9999

    monkeypatch.setattr(
        dispatcher_mod, "send_trade_signal_message", _should_not_call,
    )

    engine = await _mk_engine()
    await _seed_user(engine, mode="telegram-approve")
    async with AsyncSession(engine) as s:
        result = await dispatch(s, proposal=_proposal(), user=_user())
        await s.commit()

    assert result.outcome == "queued_send_failed"
    assert "no_creds" in result.detail
    assert result.signal_id and result.signal_id.startswith("sig_")
    assert sent == []
    # Row still persisted so a manual retry can flush it.
    async with AsyncSession(engine) as s:
        n = (await s.execute(sa.text(
            "SELECT count(*) FROM telegram_signals"
        ))).scalar()
    assert n == 1


@pytest.mark.asyncio
async def test_fully_auto_places_live_order_and_writes_audit_chain() -> None:
    engine = await _mk_engine()
    await _seed_user(engine, mode="fully-auto")
    async with AsyncSession(engine) as s:
        result = await dispatch(
            s, proposal=_proposal(), user=_user(mode="fully-auto"),
        )
        await s.commit()
    assert result.outcome == "placed"
    assert result.binance_order_id == "stub-order-1"
    assert result.leverage_chosen and result.leverage_chosen >= 1

    # Stub Binance was constructed exactly once, then closed.
    assert len(_StubBinance.instances) == 1
    assert _StubBinance.instances[0]._closed is True

    async with AsyncSession(engine) as s:
        row = (await s.execute(sa.text(
            "SELECT user_id, symbol, direction, margin_usdt, leverage, "
            "binance_order_id, mode_at_open, approved_via, "
            "prev_hash, row_hash FROM live_trades"
        ))).first()
    assert row is not None
    assert row.user_id == 1
    assert row.symbol == "BTC/USDT"
    assert row.direction == "LONG"
    assert row.margin_usdt == 30.0  # fixed sizing
    assert row.binance_order_id == "stub-order-1"
    assert row.mode_at_open == "fully-auto"
    assert row.approved_via == "auto"
    # Audit chain: prev_hash + row_hash are populated by insert_with_chain.
    assert row.prev_hash
    assert row.row_hash
    assert row.row_hash != row.prev_hash


@pytest.mark.asyncio
async def test_funding_rate_blocks_long_when_high_positive() -> None:
    engine = await _mk_engine()
    await _seed_user(engine, mode="fully-auto")
    # 5% daily funding paid by longs → over the 1% guard.
    async with AsyncSession(engine) as s:
        result = await dispatch(
            s, proposal=_proposal(funding_rate_daily=0.05),
            user=_user(),
        )
    assert result.outcome == "blocked_funding"
    assert _StubBinance.instances == []


@pytest.mark.asyncio
async def test_max_concurrent_blocks_when_at_cap() -> None:
    engine = await _mk_engine()
    await _seed_user(engine, mode="fully-auto")
    async with AsyncSession(engine) as s:
        result = await dispatch(
            s, proposal=_proposal(),
            user=_user(open_positions_count=5, max_concurrent_positions=5),
        )
    assert result.outcome == "blocked_max_positions"
    assert _StubBinance.instances == []


@pytest.mark.asyncio
async def test_dispatch_ignores_stale_user_context_mode() -> None:
    """UserContext.mode='fully-auto' but DB mode='manual' → emit only."""
    engine = await _mk_engine()
    await _seed_user(engine, mode="manual")
    async with AsyncSession(engine) as s:
        result = await dispatch(
            s, proposal=_proposal(),
            user=_user(mode="fully-auto"),  # stale snapshot
        )
    assert result.outcome == "emitted"  # DB wins
    assert _StubBinance.instances == []


# ────────────────────────────────────────────────────────────────────────────
# PR-HYBRID-CONFIDENCE-ROUTING (2026-05-23)
# ────────────────────────────────────────────────────────────────────────────
#
# The hybrid setting is a *modifier* on telegram-approve mode. When
# HYBRID_AUTO_SCORE_THRESHOLD is set AND |entry_score| >= threshold, the
# dispatcher routes the signal to _place_live_order (auto-execute) INSTEAD
# of the Telegram-approval handshake. Below-threshold or no-score signals
# fall through to the existing telegram-approve path. Default None keeps
# the modifier dormant.
#
# Reuses the existing engine + stub + fixture machinery defined above.


def _override_setting(monkeypatch: pytest.MonkeyPatch, **kwargs: object) -> None:
    """Patch the `get_settings()` cached singleton for the duration of one
    test. The dispatcher reads HYBRID_AUTO_SCORE_THRESHOLD via
    get_settings() lazily inside the routing branch, so monkeypatching
    the module-level cache works without touching env vars.
    """
    from app.config import Settings, get_settings
    base = get_settings()
    # Pydantic-settings: model_copy(update=...) builds a fresh Settings
    # without mutating the cached instance.
    fake: Settings = base.model_copy(update=kwargs)  # type: ignore[arg-type]
    monkeypatch.setattr(
        "app.trading.execution.dispatcher.get_settings",
        lambda: fake,
        raising=False,
    )
    # Some sites import get_settings directly; patch the canonical too.
    monkeypatch.setattr("app.config.get_settings", lambda: fake)


@pytest.mark.asyncio
async def test_hybrid_routing_dormant_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity: with HYBRID_AUTO_SCORE_THRESHOLD=None (default), every
    telegram-approve signal goes through the Telegram path regardless
    of score. This is the behavior on every prod backend today.

    Creds + outbound mock are set so the assertion is about routing
    (telegram path vs auto-execute), not the orthogonal no-creds path
    asserted by ``test_telegram_approve_skips_outbound_when_creds_missing``.
    """
    _override_setting(monkeypatch, HYBRID_AUTO_SCORE_THRESHOLD=None)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    async def _fake_send(session, *, signal_id, config, http=None):
        return 9999

    monkeypatch.setattr(
        dispatcher_mod, "send_trade_signal_message", _fake_send,
    )

    engine = await _mk_engine()
    await _seed_user(engine, mode="telegram-approve")
    # A very high score that WOULD trigger auto-execute if threshold set.
    proposal = _proposal(entry_score=0.95)
    async with AsyncSession(engine) as s:
        result = await dispatch(s, proposal=proposal, user=_user())
        await s.commit()
    assert result.outcome == "sent_telegram", (
        f"threshold None must keep telegram routing; got {result.outcome}"
    )
    assert _StubBinance.instances == [], (
        "no Binance order should be placed when threshold is None"
    )


@pytest.mark.asyncio
async def test_hybrid_routing_below_threshold_routes_to_telegram(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Score below HYBRID_AUTO_SCORE_THRESHOLD → fall through to the
    existing telegram-approve handshake. (Lower bound is the existing
    entry-quality gate; this PR's threshold only affects the upper
    auto-execute bucket.)

    Creds + outbound mock are set so the assertion is about routing
    (below-threshold → telegram path), not the no-creds outcome.
    """
    _override_setting(monkeypatch, HYBRID_AUTO_SCORE_THRESHOLD=0.45)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    async def _fake_send(session, *, signal_id, config, http=None):
        return 9999

    monkeypatch.setattr(
        dispatcher_mod, "send_trade_signal_message", _fake_send,
    )

    engine = await _mk_engine()
    await _seed_user(engine, mode="telegram-approve")
    # Score above the entry-quality gate floor (0.30) but below the
    # hybrid auto-execute threshold (0.45).
    proposal = _proposal(entry_score=0.35)
    async with AsyncSession(engine) as s:
        result = await dispatch(s, proposal=proposal, user=_user())
        await s.commit()
    assert result.outcome == "sent_telegram", (
        f"|0.35| < 0.45 must route to telegram; got {result.outcome}"
    )
    assert _StubBinance.instances == [], (
        "no Binance order should be placed for below-threshold scores"
    )


@pytest.mark.asyncio
async def test_hybrid_routing_at_or_above_threshold_auto_executes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Score at/above HYBRID_AUTO_SCORE_THRESHOLD → route to
    _place_live_order, skip Telegram entirely. Outcome is
    `placed_hybrid` (NOT `placed` — distinguishable from fully-auto)."""
    _override_setting(monkeypatch, HYBRID_AUTO_SCORE_THRESHOLD=0.45)
    engine = await _mk_engine()
    await _seed_user(engine, mode="telegram-approve")
    proposal = _proposal(entry_score=0.55)  # well above threshold
    async with AsyncSession(engine) as s:
        result = await dispatch(s, proposal=proposal, user=_user())
        await s.commit()
    assert result.outcome == "placed_hybrid", (
        f"|0.55| >= 0.45 must auto-execute; got {result.outcome}"
    )
    assert result.binance_order_id == "stub-order-1"
    assert len(_StubBinance.instances) == 1
    # No Telegram signal row written — direct auto-execute path.
    async with AsyncSession(engine) as s:
        n_tg = (await s.execute(sa.text(
            "SELECT count(*) FROM telegram_signals"
        ))).scalar()
        n_live = (await s.execute(sa.text(
            "SELECT count(*) FROM live_trades"
        ))).scalar()
    assert n_tg == 0
    assert n_live == 1


@pytest.mark.asyncio
async def test_hybrid_routing_exact_threshold_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exact equality is INCLUSIVE — `|score| >= threshold` not `>`."""
    _override_setting(monkeypatch, HYBRID_AUTO_SCORE_THRESHOLD=0.45)
    engine = await _mk_engine()
    await _seed_user(engine, mode="telegram-approve")
    proposal = _proposal(entry_score=0.45)  # exact equality
    async with AsyncSession(engine) as s:
        result = await dispatch(s, proposal=proposal, user=_user())
        await s.commit()
    assert result.outcome == "placed_hybrid"


@pytest.mark.asyncio
async def test_hybrid_routing_short_signal_above_threshold_auto_executes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Threshold is on abs(score), so high-conviction SHORTs auto-execute
    too — unless DISABLE_SHORT_SIGNALS blocks them upstream (separate
    test).

    For SHORT, stop_loss is ABOVE entry and take_profit BELOW (inverse
    of LONG). The default proposal helper uses LONG-shaped levels, so
    we override to match the SHORT geometry — otherwise the recommended
    leverage falls out too low and notional gets rejected at the
    min_notional check.
    """
    _override_setting(monkeypatch, HYBRID_AUTO_SCORE_THRESHOLD=0.45)
    engine = await _mk_engine()
    await _seed_user(engine, mode="telegram-approve")
    proposal = _proposal(
        direction="SHORT", entry_score=-0.60,
        entry_price=80_000.0,
        stop_loss_price=81_600.0,    # 2% above entry (SHORT-appropriate)
        take_profit_price=76_720.0,  # ~4.1% below entry
    )
    async with AsyncSession(engine) as s:
        result = await dispatch(s, proposal=proposal, user=_user())
        await s.commit()
    assert result.outcome == "placed_hybrid", (
        f"|-0.60| >= 0.45 SHORT must auto-execute; got {result.outcome}"
    )


@pytest.mark.asyncio
async def test_hybrid_routing_short_blocked_by_disable_short_signals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DISABLE_SHORT_SIGNALS fires at the entry-quality gate which runs
    BEFORE the routing branch. SHORTs are blocked regardless of
    hybrid threshold or routing destination."""
    _override_setting(
        monkeypatch,
        HYBRID_AUTO_SCORE_THRESHOLD=0.45,
        DISABLE_SHORT_SIGNALS=True,
    )
    engine = await _mk_engine()
    await _seed_user(engine, mode="telegram-approve")
    proposal = _proposal(direction="SHORT", entry_score=-0.60)
    async with AsyncSession(engine) as s:
        result = await dispatch(s, proposal=proposal, user=_user())
        await s.commit()
    assert result.outcome == "blocked_entry_quality", (
        f"DISABLE_SHORT_SIGNALS must block before routing; got {result.outcome}"
    )
    assert _StubBinance.instances == []


@pytest.mark.asyncio
async def test_hybrid_routing_no_score_falls_through_to_telegram(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A SignalProposal without `entry_score` (admin manual test trade,
    older code path) MUST NOT crash the routing branch and MUST NOT
    accidentally auto-execute. Falls through to telegram-approve."""
    _override_setting(monkeypatch, HYBRID_AUTO_SCORE_THRESHOLD=0.45)
    engine = await _mk_engine()
    await _seed_user(engine, mode="telegram-approve")
    proposal = _proposal(entry_score=None)
    async with AsyncSession(engine) as s:
        result = await dispatch(s, proposal=proposal, user=_user())
        await s.commit()
    assert result.outcome == "sent_telegram", (
        f"entry_score=None must NOT auto-execute; got {result.outcome}"
    )
    assert _StubBinance.instances == []


@pytest.mark.asyncio
async def test_hybrid_routing_does_not_affect_fully_auto_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: setting HYBRID_AUTO_SCORE_THRESHOLD does NOT change
    fully-auto mode behavior. fully-auto already auto-executes every
    signal; the new code path is exclusive to telegram-approve mode."""
    _override_setting(monkeypatch, HYBRID_AUTO_SCORE_THRESHOLD=0.45)
    engine = await _mk_engine()
    await _seed_user(engine, mode="fully-auto")
    proposal = _proposal(entry_score=0.20)  # below threshold
    async with AsyncSession(engine) as s:
        result = await dispatch(
            s, proposal=proposal, user=_user(mode="fully-auto"),
        )
        await s.commit()
    # fully-auto's outcome stays `placed` — NOT `placed_hybrid` — and
    # NOT `sent_telegram` (the new threshold doesn't gate fully-auto).
    assert result.outcome == "placed", (
        f"fully-auto outcome must stay 'placed'; got {result.outcome}"
    )


@pytest.mark.asyncio
async def test_hybrid_routing_does_not_affect_manual_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: manual mode short-circuits BEFORE any gate or routing
    branch, regardless of HYBRID_AUTO_SCORE_THRESHOLD."""
    _override_setting(monkeypatch, HYBRID_AUTO_SCORE_THRESHOLD=0.45)
    engine = await _mk_engine()
    await _seed_user(engine, mode="manual")
    proposal = _proposal(entry_score=0.95)  # very high score
    async with AsyncSession(engine) as s:
        result = await dispatch(s, proposal=proposal, user=_user())
        await s.commit()
    assert result.outcome == "emitted"
    assert _StubBinance.instances == []


# ─── Reviewer-flagged hardening tests ────────────────────────────────────


@pytest.mark.asyncio
async def test_hybrid_routing_nan_score_falls_through_to_telegram(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NaN entry_score must NOT auto-execute. `abs(nan) >= threshold` is
    False in Python, so the existing guard happens to work — but the
    explicit isfinite() check makes the intent clear and protects
    against the inf case too."""
    import math
    _override_setting(monkeypatch, HYBRID_AUTO_SCORE_THRESHOLD=0.45)
    engine = await _mk_engine()
    await _seed_user(engine, mode="telegram-approve")
    proposal = _proposal(entry_score=math.nan)
    async with AsyncSession(engine) as s:
        result = await dispatch(s, proposal=proposal, user=_user())
        await s.commit()
    assert result.outcome == "sent_telegram", (
        f"NaN entry_score must NOT auto-execute; got {result.outcome}"
    )
    assert _StubBinance.instances == []


@pytest.mark.asyncio
async def test_hybrid_routing_inf_score_falls_through_to_telegram(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Positive infinity entry_score (a buggy-predictor sentinel) must
    NOT auto-execute. WITHOUT the isfinite() guard, abs(inf) >= 0.45
    is True and we'd execute on garbage. Critical regression test."""
    import math
    _override_setting(monkeypatch, HYBRID_AUTO_SCORE_THRESHOLD=0.45)
    engine = await _mk_engine()
    await _seed_user(engine, mode="telegram-approve")
    proposal = _proposal(entry_score=math.inf)
    async with AsyncSession(engine) as s:
        result = await dispatch(s, proposal=proposal, user=_user())
        await s.commit()
    assert result.outcome == "sent_telegram", (
        f"inf entry_score must NOT auto-execute; got {result.outcome}"
    )
    assert _StubBinance.instances == [], (
        "no Binance order should be placed for non-finite scores"
    )


def test_hybrid_threshold_validator_rejects_zero() -> None:
    """Operator typo `HYBRID_AUTO_SCORE_THRESHOLD=0` would auto-execute
    every non-zero-score signal. Pydantic validator must reject."""
    from app.config import Settings
    with pytest.raises(ValueError, match="HYBRID_AUTO_SCORE_THRESHOLD"):
        Settings(  # type: ignore[call-arg]
            database_url="postgresql+asyncpg://x:y@localhost/z",
            redis_url="redis://localhost",
            HYBRID_AUTO_SCORE_THRESHOLD=0.0,
        )


def test_hybrid_threshold_validator_rejects_negative() -> None:
    """Negative threshold: `abs(score) >= -0.45` is true for every score
    (since abs is non-negative). Pydantic validator must reject."""
    from app.config import Settings
    with pytest.raises(ValueError, match="HYBRID_AUTO_SCORE_THRESHOLD"):
        Settings(  # type: ignore[call-arg]
            database_url="postgresql+asyncpg://x:y@localhost/z",
            redis_url="redis://localhost",
            HYBRID_AUTO_SCORE_THRESHOLD=-0.45,
        )


def test_hybrid_threshold_validator_rejects_one_or_above() -> None:
    """Threshold >= 1.0 cannot be cleared (aggregator clamps |score| to
    1) — silently disables hybrid routing. Validator rejects so the
    operator sees the error at startup rather than dormant-by-typo."""
    from app.config import Settings
    with pytest.raises(ValueError, match="HYBRID_AUTO_SCORE_THRESHOLD"):
        Settings(  # type: ignore[call-arg]
            database_url="postgresql+asyncpg://x:y@localhost/z",
            redis_url="redis://localhost",
            HYBRID_AUTO_SCORE_THRESHOLD=1.0,
        )


def test_hybrid_threshold_validator_accepts_valid_range() -> None:
    """Sanity: values in (0, 1) load cleanly."""
    from app.config import Settings
    for v in (0.30, 0.45, 0.55, 0.99):
        s = Settings(  # type: ignore[call-arg]
            database_url="postgresql+asyncpg://x:y@localhost/z",
            redis_url="redis://localhost",
            HYBRID_AUTO_SCORE_THRESHOLD=v,
        )
        assert s.HYBRID_AUTO_SCORE_THRESHOLD == v


def test_hybrid_threshold_validator_accepts_none() -> None:
    """Sanity: explicit None / unset is the dormant default."""
    from app.config import Settings
    s = Settings(  # type: ignore[call-arg]
        database_url="postgresql+asyncpg://x:y@localhost/z",
        redis_url="redis://localhost",
    )
    assert s.HYBRID_AUTO_SCORE_THRESHOLD is None
