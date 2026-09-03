"""Regression tests for the P1 SL/TP penny-rounding bug.

Before the fix, _build_trade_setup applied round(x, 2) to entry/sl/tp.
For ONDO @ $0.38 with ATR=0.0027:
  - SL = 0.38 - 1.5×0.0027 = 0.37595  →  round to  0.37   (−2.6% instead of −1.07%)
  - TP = 0.38 + 3.0×0.0027 = 0.38810  →  round to  0.39   (+2.6% instead of +2.13%)
  - RR = 2.6% / 2.6% = 1.0 instead of 2.0

Fix: remove round(x, 2) from _build_trade_setup; apply quantize_price(price,
tick_size) in _place_live_order after the existing quantize_qty call; apply
adaptive _fmt_price in the Telegram card renderer.
"""
from __future__ import annotations

import re
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.core.predictor import _build_trade_setup  # type: ignore[attr-defined]
from app.core.scoring.types import Direction
from app.exchanges.binance_filters import SymbolFilters, quantize_price
from app.telegram.signals import SignalCandidate, _fmt_price, render_message  # type: ignore[attr-defined]


# ─── 1. _build_trade_setup preserves geometry ─────────────────────────────


def test_sub_dollar_symbol_preserves_2_to_1_rr() -> None:
    """ONDO @ $0.38, ATR=0.0027 → RR must be ≈ 2:1 to 4+ sig figs."""
    ts = _build_trade_setup(Direction.LONG, last_close=0.38, atr=0.0027)
    assert ts.entry is not None
    assert ts.stop_loss is not None
    assert ts.take_profit is not None

    sl_dist = abs(ts.entry - ts.stop_loss)
    tp_dist = abs(ts.take_profit - ts.entry)
    rr = tp_dist / sl_dist

    # Should be 2:1 to better than 4 significant figures.
    assert abs(rr - 2.0) < 0.0001, (
        f"Expected RR≈2.0, got {rr:.6f} "
        f"(entry={ts.entry}, sl={ts.stop_loss}, tp={ts.take_profit})"
    )


def test_sub_dollar_symbol_sl_is_not_2_decimal_rounded() -> None:
    """SL must NOT be the bugged round(0.37595, 2)=0.37."""
    ts = _build_trade_setup(Direction.LONG, last_close=0.38, atr=0.0027)
    assert ts.stop_loss is not None
    # Buggy value: 0.37; correct: 0.37595
    assert ts.stop_loss > 0.375, (
        f"SL {ts.stop_loss} looks like it was rounded to 2 dp (should be ~0.37595)"
    )


def test_btc_priced_symbol_geometry_unchanged() -> None:
    """BTC @ $80000, ATR=1000 → still correct 2:1 RR (round(x,2) was fine here)."""
    ts = _build_trade_setup(Direction.LONG, last_close=80_000.0, atr=1000.0)
    assert ts.entry == 80_000.0
    assert ts.stop_loss == pytest.approx(80_000.0 - 1500.0)
    assert ts.take_profit == pytest.approx(80_000.0 + 3000.0)
    sl_dist = abs(ts.entry - ts.stop_loss)
    tp_dist = abs(ts.take_profit - ts.entry)
    assert pytest.approx(tp_dist / sl_dist, rel=1e-6) == 2.0


def test_short_direction_sl_tp_orientation() -> None:
    """SHORT: SL above entry, TP below entry, 2:1 RR preserved."""
    ts = _build_trade_setup(Direction.SHORT, last_close=0.38, atr=0.0027)
    assert ts.stop_loss is not None and ts.take_profit is not None
    assert ts.stop_loss > ts.entry  # type: ignore[operator]
    assert ts.take_profit < ts.entry  # type: ignore[operator]
    sl_dist = abs(ts.stop_loss - ts.entry)  # type: ignore[operator]
    tp_dist = abs(ts.entry - ts.take_profit)  # type: ignore[operator]
    assert pytest.approx(tp_dist / sl_dist, rel=1e-4) == 2.0


# ─── 2. _fmt_price adaptive decimals ─────────────────────────────────────


def test_fmt_price_large_price_2dp_with_comma() -> None:
    assert _fmt_price(79_000.0) == "79,000.00"


def test_fmt_price_mid_price_2dp() -> None:
    assert _fmt_price(1.50) == "1.50"


def test_fmt_price_sub_dollar_6dp() -> None:
    # ONDO @ $0.38
    result = _fmt_price(0.38)
    assert result == "0.380000", f"Got {result!r}"


def test_fmt_price_sub_dollar_shows_sl_tp_geometry() -> None:
    # After fix: SL=0.37595 must show as something distinguishable from 0.38.
    entry_str = _fmt_price(0.38)
    sl_str = _fmt_price(0.37595)
    assert entry_str != sl_str, "Entry and SL round to the same display value"


def test_fmt_price_tiny_coin_8dp() -> None:
    assert _fmt_price(0.00001580) == "0.00001580"


# ─── 2b. quantize_price rounding direction ────────────────────────────────
#
# quantize_price uses math.floor (not round-to-nearest), so:
#   - SL (below entry for LONG): floors DOWN  → stop moves 0-1 tick FARTHER from entry
#     (slightly wider stop — won't trigger on a tick that barely touches; safe direction)
#   - TP (above entry for LONG): floors DOWN  → TP moves 0-1 tick CLOSER to entry
#     (1 tick less profit per win — acceptable; avoids Binance "precision" rejection)
#
# The key invariant: quantized price is always within ONE tick_size of the raw price.
# A wrong rounding MODE (e.g. round-to-nearest) could flip by half a tick unexpectedly;
# floor is deterministic and always conservative.


def test_quantize_price_floors_not_rounds_to_nearest() -> None:
    """quantize_price must floor, not round to nearest.

    0.37595 with tick_size=0.0001:
      nearest → 0.3760   (rounds up because .5 digit)
      floor   → 0.3759   (what we actually want — conservative, deterministic)
    """
    tick = 0.0001
    raw_sl = 0.37595
    result = quantize_price(raw_sl, tick)
    assert result == pytest.approx(0.3759, abs=1e-9), (
        f"Expected floor=0.3759, got {result} "
        f"(round-to-nearest would give 0.3760)"
    )


def test_quantize_price_result_within_one_tick_of_raw() -> None:
    """Quantized price must be within [raw - tick_size, raw] (floor guarantee)."""
    tick = 0.0001
    for raw in [0.37595, 0.38810, 0.37600, 0.38000, 0.99999]:
        q = quantize_price(raw, tick)
        assert q <= raw + 1e-12, f"Quantized {q} exceeds raw {raw} for tick={tick}"
        assert q >= raw - tick - 1e-12, (
            f"Quantized {q} is more than one tick below raw {raw} (tick={tick})"
        )


def test_quantize_price_long_sl_floors_away_from_entry() -> None:
    """For a LONG SL below entry, floor moves it farther from entry (slightly wider)."""
    tick = 0.0001
    entry = 0.38
    raw_sl = entry - 1.5 * 0.0027   # 0.37595
    q_sl = quantize_price(raw_sl, tick)
    # SL should be AT or below raw_sl (floor direction).
    assert q_sl <= raw_sl + 1e-12
    # It must still be above the entry - 2 ticks floor (i.e. not catastrophically far).
    assert q_sl >= raw_sl - tick - 1e-12


def test_quantize_price_long_tp_floors_toward_entry() -> None:
    """For a LONG TP above entry, floor brings it 0-1 tick closer to entry."""
    tick = 0.0001
    entry = 0.38
    raw_tp = entry + 3.0 * 0.0027   # 0.38810
    q_tp = quantize_price(raw_tp, tick)
    assert q_tp <= raw_tp + 1e-12
    assert q_tp >= raw_tp - tick - 1e-12


# ─── 3. render_message card uses adaptive precision ───────────────────────


def _ondo_candidate(**overrides) -> SignalCandidate:
    base = dict(
        signal_id="ondo1",
        symbol="ONDO/USDT",
        timeframe="1h",
        direction="LONG",
        entry_price=0.38,
        stop_loss_price=0.37595,
        take_profit_price=0.38810,
        confidence_pct=65,
        layer_summary={},
        margin_usdt=20.0,
        funding_rate_daily=0.0,
        chart_url="https://example.com/chart",
        sl_distance_pct=abs(0.38 - 0.37595) / 0.38,
        rr_ratio=2.0,
    )
    base.update(overrides)
    return SignalCandidate(**base)


def test_render_message_sub_dollar_shows_6dp_entry() -> None:
    msg = render_message(_ondo_candidate(), leverage=5)
    # Entry line must show more than 2 decimal places for ONDO.
    assert "0.380000" in msg.body, (
        f"Expected 6dp entry in card; body excerpt:\n{msg.body[:500]}"
    )


def test_render_message_sub_dollar_shows_sl_tp_distinct() -> None:
    msg = render_message(_ondo_candidate(), leverage=5)
    # SL and TP must be visually distinct from entry.
    assert "0.375950" in msg.body, f"SL not shown with 6dp:\n{msg.body[:500]}"
    assert "0.388100" in msg.body, f"TP not shown with 6dp:\n{msg.body[:500]}"


def test_render_message_btc_still_2dp() -> None:
    from datetime import datetime, timezone

    now = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)
    from app.telegram.signals import SignalCandidate

    cand = SignalCandidate(
        signal_id="btc1",
        symbol="BTC/USDT",
        timeframe="1h",
        direction="LONG",
        entry_price=79_000.0,
        stop_loss_price=77_420.0,
        take_profit_price=82_160.0,
        confidence_pct=70,
        layer_summary={},
        margin_usdt=30.0,
        funding_rate_daily=-0.0002,
        chart_url="https://example.com/chart",
        sl_distance_pct=0.02,
        rr_ratio=2.0,
    )
    msg = render_message(cand, leverage=5, now=now)
    assert "79,000.00" in msg.body, f"BTC entry not comma-formatted:\n{msg.body[:500]}"


# ─── 4. Dispatcher quantizes SL/TP with tickSize ─────────────────────────


@pytest.mark.asyncio
async def test_place_live_order_quantizes_sl_tp_to_tick_size() -> None:
    """After the fix, Binance should receive SL/TP rounded to tick_size=0.0001,
    not the old round(x, 2)=0.37 / 0.39 which destroyed the 2:1 geometry."""
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from app.trading.execution.dispatcher import SignalProposal, UserContext, dispatch

    # Minimal SQLite schema matching what _place_live_order needs.
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, "
            "trading_mode TEXT NOT NULL DEFAULT 'manual')"
        ))
        await conn.execute(sa.text(
            "INSERT INTO users (id, trading_mode) VALUES (1, 'fully-auto')"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE telegram_signals ("
            "id TEXT PRIMARY KEY, user_id INTEGER, symbol TEXT, "
            "direction TEXT, sent_at TEXT, payload TEXT, response TEXT)"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE live_trades ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER, symbol TEXT, direction TEXT, "
            "margin_usdt REAL, leverage INTEGER, "
            "position_value_usdt REAL, entry_price REAL, "
            "stop_loss REAL, take_profit REAL, "
            "binance_order_id TEXT, opened_at TEXT, "
            "mode_at_open TEXT, approved_via TEXT, "
            "reasoning TEXT, inputs_hash TEXT, "
            "mtf_agreement INTEGER, mtf_dominant_tf TEXT, "
            "mtf_directions_json TEXT, "
            "status TEXT NOT NULL DEFAULT 'pending', "
            "sl_order_id TEXT, tp_order_id TEXT, failure_reason TEXT, "
            # Phase 4 Task 9 (alembic 0038): cohort tag, NOT NULL DEFAULT.
            "symbol_source TEXT NOT NULL DEFAULT 'established_top20', "
            "prev_hash TEXT, row_hash TEXT)"
        ))

    # Capture the stopPrice values sent to Binance.
    captured_stop_prices: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        # Capture stopPrice for SL/TP orders.
        if "type=STOP_MARKET" in url or "type=TAKE_PROFIT_MARKET" in url:
            m = re.search(r"[?&]stopPrice=([^&]+)", url)
            sp = m.group(1) if m else ""
            captured_stop_prices.append(sp)
            return httpx.Response(200, json={
                "orderId": 7777 + len(captured_stop_prices),
                "symbol": "ONDOUSDT", "side": "SELL",
                "executedQty": "100", "avgPrice": "0",
                "status": "NEW",
                "type": "STOP_MARKET" if "STOP" in url else "TAKE_PROFIT_MARKET",
            })
        if "/leverage" in url:
            return httpx.Response(200, json={"leverage": 5})
        # MARKET entry
        return httpx.Response(200, json={
            "orderId": 9999, "symbol": "ONDOUSDT", "side": "BUY",
            "executedQty": "100", "avgPrice": "0.38",
            "status": "FILLED",
        })

    fake_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    # ONDO filters: tick_size=0.0001, so:
    #   quantize_price(0.37595, 0.0001) = 0.3759
    #   quantize_price(0.38810, 0.0001) = 0.3881
    ondo_filters = SymbolFilters(
        symbol="ONDOUSDT",
        step_size=1.0,
        min_qty=1.0,
        tick_size=0.0001,
        min_notional=5.0,
    )

    proposal = SignalProposal(
        symbol="ONDO/USDT",
        timeframe="1h",
        direction="LONG",
        entry_price=0.38,
        stop_loss_price=0.37595,   # full precision from fixed _build_trade_setup
        take_profit_price=0.38810,
        confidence_pct=65,
        layer_summary={},
        inputs_hash="test_hash",
        funding_rate_daily=0.0,
        chart_base_url="https://example.com",
    )
    user = UserContext(
        user_id=1,
        mode="fully-auto",
        binance_api_key="kk",
        binance_api_secret="ss",
        use_testnet=True,
        portfolio_value_usdt=1000,
        successful_trades=50,
        sizing_mode="fixed",
        fixed_size_usdt=20.0,
        max_leverage_cap=10,
        max_concurrent_positions=5,
        open_positions_count=0,
    )

    from app.exchanges import binance_live as bl

    real_init = bl.BinanceLiveClient.__init__

    def patched_init(self, **kwargs: Any) -> None:
        kwargs["http"] = fake_http
        real_init(self, **kwargs)

    def _test_session_factory() -> AsyncSession:
        return AsyncSession(engine)

    with (
        patch("app.trading.execution.dispatcher.get_symbol_filters",
              new_callable=AsyncMock, return_value=ondo_filters),
        patch.object(bl.BinanceLiveClient, "__init__", patched_init),
    ):
        async with AsyncSession(engine) as s:
            r = await dispatch(
                s,
                proposal=proposal,
                user=user,
                session_factory=_test_session_factory,  # type: ignore[arg-type]
            )
            await s.commit()

    await fake_http.aclose()

    assert r.outcome == "placed", f"Expected placed; got {r.outcome}: {r.detail}"
    assert len(captured_stop_prices) == 2, (
        f"Expected 2 stop orders (SL + TP); got {captured_stop_prices}"
    )

    sl_sent, tp_sent = captured_stop_prices

    # Buggy (pre-fix): "0.37" and "0.39" — both 2dp, RR 1:1
    # Correct (post-fix): quantize_price(0.37595, 0.0001)=0.3759
    #                     quantize_price(0.38810, 0.0001)=0.3881
    expected_sl = str(quantize_price(0.37595, 0.0001)).rstrip("0").rstrip(".")
    expected_tp = str(quantize_price(0.38810, 0.0001)).rstrip("0").rstrip(".")

    assert sl_sent == expected_sl, (
        f"SL stopPrice {sl_sent!r} ≠ expected {expected_sl!r} "
        f"(old buggy value would be '0.37')"
    )
    assert tp_sent == expected_tp, (
        f"TP stopPrice {tp_sent!r} ≠ expected {expected_tp!r} "
        f"(old buggy value would be '0.39')"
    )

    # Verify the RR of the SENT prices is 2:1 (not 1:1 as the bug produced).
    sl_f = float(sl_sent)
    tp_f = float(tp_sent)
    entry = 0.38
    rr = abs(tp_f - entry) / abs(entry - sl_f)
    assert abs(rr - 2.0) < 0.05, (
        f"RR of sent prices is {rr:.3f}:1, expected ≈2:1 "
        f"(SL={sl_sent}, TP={tp_sent})"
    )
