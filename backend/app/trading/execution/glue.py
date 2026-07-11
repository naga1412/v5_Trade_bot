"""SP-8 Phase J — wiring glue between live_prediction worker + dispatcher.

The live worker (``app/ws/live_prediction.py``) produces a Prediction
each closed candle. The dispatcher (``app/trading/execution/dispatcher.py``)
turns one signal into a Telegram message or live order based on user
mode. This module is the bridge: it converts a Prediction into a
SignalProposal + builds the UserContext from the DB + decrypted vault.

Vault keys are cached at module level after lifespan calls
``initialize_vault_cache(passphrase, secrets_path)``. A ``vault_keys()``
accessor returns the cached pair (or None when uninitialised — typical
in dev/test where AUTONOMOUS_TRADING_ENABLED=false).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.secrets.vault import VaultDecryptError, decrypt_secrets
from app.trading.execution.dispatcher import (
    DispatchResult,
    SignalProposal,
    UserContext,
    dispatch,
)


log = logging.getLogger(__name__)


# Module-level cache of decrypted Binance keys. Populated once at startup
# from initialize_vault_cache(); read by build_user_context() each tick.
_vault_keys: dict[str, str] = {}


@dataclass(frozen=True)
class VaultKeys:
    binance_api_key: str
    binance_api_secret: str


def initialize_vault_cache(
    *, passphrase: str, secrets_path: Path,
) -> bool:
    """Decrypt secrets.enc once at startup. Returns True on success.

    Caller (lifespan) calls this when AUTONOMOUS_TRADING_ENABLED is true
    AND pre-flight has passed. On failure (wrong passphrase, missing
    keys, etc.), returns False — caller logs + skips dispatching.
    """
    global _vault_keys
    if not secrets_path.exists():
        log.warning("initialize_vault_cache: %s missing", secrets_path)
        return False
    try:
        secrets = decrypt_secrets(secrets_path.read_bytes(), passphrase=passphrase)
    except VaultDecryptError as e:
        log.error("initialize_vault_cache: decrypt failed: %s", e)
        return False
    if not secrets.get("binance_api_key") or not secrets.get("binance_api_secret"):
        log.warning(
            "initialize_vault_cache: vault is missing binance_api_key or "
            "binance_api_secret; dispatcher will skip",
        )
        return False
    _vault_keys = {
        "binance_api_key": secrets["binance_api_key"],
        "binance_api_secret": secrets["binance_api_secret"],
    }
    log.info("vault cache initialised (2 keys)")
    return True


def vault_keys() -> VaultKeys | None:
    """Return cached keys; None if initialize_vault_cache was never called
    or it failed."""
    if not _vault_keys.get("binance_api_key"):
        return None
    return VaultKeys(
        binance_api_key=_vault_keys["binance_api_key"],
        binance_api_secret=_vault_keys["binance_api_secret"],
    )


def reset_vault_cache_for_tests() -> None:
    """Test helper. Production never calls this."""
    global _vault_keys
    _vault_keys = {}


# ---- Prediction -> SignalProposal ---------------------------------------


def _parse_mtf_adx_by_tf_json(raw: str | None) -> dict[str, float] | None:
    """Parse `LivePredictionOut.mtf_adx_by_tf_json` into a `dict[str, float]`.

    Fail-open contract (mirrors `_parse_mtf_directions_json`): malformed JSON,
    non-dict roots, or non-numeric values all return None so the dispatcher's
    ADX gate falls open ("no opinion, don't block") instead of raising.
    """
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        log.warning("mtf_adx_by_tf_json parse failed: %s (raw=%r)", exc, raw[:80])
        return None
    if not isinstance(parsed, dict):
        log.warning(
            "mtf_adx_by_tf_json parsed to non-dict (%s); treating as None",
            type(parsed).__name__,
        )
        return None
    result: dict[str, float] = {}
    for k, v in parsed.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            log.warning(
                "mtf_adx_by_tf_json value at key=%r is %s (not numeric); "
                "treating dict as None to keep the gate unbiased",
                k, type(v).__name__,
            )
            return None
        result[str(k)] = float(v)
    return result


def _parse_mtf_directions_json(raw: str | None) -> dict[str, int] | None:
    """Parse `LivePredictionOut.mtf_directions_json` into a `dict[str, int]`.

    Fail-open: returns None on None input AND on parse failure (with a
    WARNING log). A malformed JSON column must NEVER poison dispatch —
    the dispatcher gate that consumes the parsed dict treats None as
    "no MTF direction data" and passes through (matches the PR1
    `mtf_agreement is None` fail-open contract).

    Rejects non-dict roots (list, scalar) — the watchdog can drill into
    the log; the gate stays unbiased.

    Direction values MUST be plain ``int`` (PR1 schema: -1 / 0 / +1).
    Floats and other numeric types are rejected — we do NOT silently
    truncate ``int(0.7) → 0`` because that would bias the higher-TF
    veto (a downstream rounding artifact like ``-0.4`` would neutralize
    a real opposing-TF signal). `bool` is an int subclass and is
    accepted for compatibility with True/False JSON inputs.
    """
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        log.warning(
            "mtf_directions_json parse failed: %s (raw=%r)", exc, raw[:80],
        )
        return None
    if not isinstance(parsed, dict):
        log.warning(
            "mtf_directions_json parsed to non-dict (%s); treating as None",
            type(parsed).__name__,
        )
        return None
    result: dict[str, int] = {}
    for k, v in parsed.items():
        # bool is a subclass of int — accept for True/False JSON inputs.
        # Floats fail `isinstance(v, int)` so they trip this guard,
        # which is exactly what we want (no silent int(0.7) → 0).
        if not isinstance(v, int):
            log.warning(
                "mtf_directions_json value at key=%r is %s (not int); "
                "treating dict as None to keep the gate unbiased",
                k, type(v).__name__,
            )
            return None
        result[str(k)] = int(v)
    return result


def proposal_from_prediction(
    *,
    symbol: str,
    timeframe: str,
    pred_direction: str,
    pred_confidence: float,
    # Mixed-shape: per-layer dicts (LayerScoreOut.model_dump shape),
    # abstained-layer None entries, AND prediction_extras (float/str/list)
    # merged in by live_prediction._layer_payload. See `_format_layers`
    # in app/telegram/signals.py for the type-aware render path.
    layer_summary: dict[str, Any],
    inputs_hash: str,
    entry_price: float,
    stop_loss_price: float,
    take_profit_price: float,
    funding_rate_daily: float = 0.0,
    chart_base_url: str = "",
    # PR2: MTF fields threaded from LivePredictionOut. All three default
    # None so PR1 call sites continue to construct the proposal exactly
    # as before. The dispatcher gate (Phase 4) reads these on the
    # constructed SignalProposal.
    mtf_agreement: int | None = None,
    mtf_dominant_tf: str | None = None,
    mtf_directions_json: str | None = None,
    # PR-strategy-1: aggregator's final signed score (`pred.final.score`)
    # — the same metric the shadow worker passes through the entry-quality
    # gate. None when the caller didn't wire it (admin_test_trade /
    # manual operator path). Defaults preserve the pre-PR-strategy-1
    # call-site contract.
    pred_score: float | None = None,
    # PR-PLUMBING-1 Fix 1: per-day funding rate threaded from
    # `LivePredictionOut.funding_rate_daily`. None when the predictor's
    # intermarket lookup returned None — coerce to 0.0 below so the
    # SignalProposal default (0.0) is preserved and the dispatcher's
    # funding-rate kill-switch falls open (0.0 < 0.01 threshold).
    # Callers that pre-date PR-PLUMBING-1 (admin_test_trade, telegram
    # callback) can keep omitting this kwarg.
    pred_funding_rate_daily: float | None = None,
    # PR-BOT-INTELLIGENCE-UPGRADE — context the extended entry-quality
    # gate consumes. All three default None so admin_test_trade /
    # telegram-callback call sites stay unchanged. live_prediction
    # extracts these from pred.layer_scores["2"] and the predictor's
    # mtf_adx_by_tf_json field.
    layer2_direction: str | None = None,
    layer2_confidence: float | None = None,
    mtf_adx_by_tf_json: str | None = None,
) -> SignalProposal | None:
    """Build a SignalProposal from a Prediction. Returns None for
    NEUTRAL signals (nothing to dispatch)."""
    direction = pred_direction.upper()
    if direction not in ("LONG", "SHORT"):
        return None
    if entry_price <= 0 or stop_loss_price <= 0 or take_profit_price <= 0:
        return None
    # PR-PLUMBING-1 Fix 1: prefer the threaded per-day rate when supplied.
    # `is not None` (not truthiness) so a legitimate 0.0 from the predictor
    # is preserved and not silently overridden by the legacy
    # `funding_rate_daily` kwarg. None still falls back, keeping
    # pre-PR-PLUMBING-1 callers that omit the kwarg byte-identical.
    threaded_funding_rate_daily = (
        pred_funding_rate_daily
        if pred_funding_rate_daily is not None
        else funding_rate_daily
    )
    return SignalProposal(
        symbol=symbol, timeframe=timeframe,
        direction=direction,  # type: ignore[arg-type]
        entry_price=entry_price,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price,
        confidence_pct=pred_confidence * 100.0,
        layer_summary=layer_summary,
        inputs_hash=inputs_hash,
        funding_rate_daily=threaded_funding_rate_daily,
        chart_base_url=chart_base_url,
        mtf_agreement=mtf_agreement,
        mtf_dominant_tf=mtf_dominant_tf,
        mtf_directions=_parse_mtf_directions_json(mtf_directions_json),
        entry_score=pred_score,
        layer2_direction=layer2_direction,
        layer2_confidence=layer2_confidence,
        mtf_adx_by_tf=_parse_mtf_adx_by_tf_json(mtf_adx_by_tf_json),
    )


# ---- User row -> UserContext --------------------------------------------


async def build_user_context(
    session: AsyncSession,
    *,
    user_id: int,
    use_testnet: bool,
) -> UserContext | None:
    """Read users + open-positions count; build the UserContext.

    Returns None when the vault isn't loaded (autonomous trading off)
    OR the user row has incomplete settings — caller skips dispatch.
    """
    keys = vault_keys()
    if keys is None:
        return None

    row = (await session.execute(
        sa.text(
            "SELECT trading_mode, position_sizing_mode, "
            "       fixed_size_min_usdt, fixed_size_max_usdt, "
            "       max_leverage_cap, max_concurrent_positions "
            "FROM users WHERE id = :u"
        ),
        {"u": user_id},
    )).first()
    if row is None:
        return None

    open_count = (await session.execute(
        sa.text(
            "SELECT count(*) FROM live_trades "
            "WHERE user_id = :u AND closed_at IS NULL"
        ),
        {"u": user_id},
    )).scalar() or 0

    # Fixed sizing: pick the midpoint of the configured range as the
    # deterministic value. The spec §5.1 also supports random within
    # range — that lives in compute_position_margin via FixedSizingConfig
    # ranges, but for the initial wiring we use a single value to keep
    # signals reproducible.
    lo = float(row.fixed_size_min_usdt or 30)
    hi = float(row.fixed_size_max_usdt or lo)
    fixed_size = (lo + hi) / 2

    successful_trades = (await session.execute(
        sa.text(
            "SELECT count(*) FROM live_trades "
            "WHERE user_id = :u AND closed_at IS NOT NULL "
            "  AND pnl_usdt > -margin_usdt"
        ),
        {"u": user_id},
    )).scalar() or 0

    portfolio = max(lo * 5, 100.0)  # conservative placeholder until wallet API wired

    return UserContext(
        user_id=user_id,
        mode=row.trading_mode,
        binance_api_key=keys.binance_api_key,
        binance_api_secret=keys.binance_api_secret,
        use_testnet=use_testnet,
        portfolio_value_usdt=portfolio,
        successful_trades=int(successful_trades),
        sizing_mode=row.position_sizing_mode or "fixed",
        fixed_size_usdt=fixed_size if row.position_sizing_mode == "fixed" else None,
        max_leverage_cap=int(row.max_leverage_cap or 10),
        max_concurrent_positions=int(row.max_concurrent_positions or 5),
        open_positions_count=int(open_count),
    )


# ---- Top-level entry point ----------------------------------------------


async def dispatch_if_eligible(
    session: AsyncSession,
    *,
    user_id: int,
    use_testnet: bool,
    proposal_kwargs: dict[str, Any],
    now: datetime | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> DispatchResult | None:
    """Build context + proposal, then call dispatcher.dispatch().

    Returns None when the vault isn't loaded or the proposal is NEUTRAL/
    invalid. Caller (live worker) logs the DispatchResult.

    `session_factory` is threaded into `dispatch()` for the
    PR-FIX-GHOST-POSITIONS pending->open lifecycle. Production callers
    (live worker) pass the global factory so the dispatcher's
    `_place_live_order` Phase 1 INSERT and Phase 3 UPDATE happen in
    sessions independent of this caller's session.
    """
    user = await build_user_context(
        session, user_id=user_id, use_testnet=use_testnet,
    )
    if user is None:
        return None
    proposal = proposal_from_prediction(**proposal_kwargs)
    if proposal is None:
        return None
    return await dispatch(
        session, proposal=proposal, user=user, now=now,
        session_factory=session_factory,
    )


__all__ = [
    "VaultKeys",
    "build_user_context",
    "dispatch_if_eligible",
    "initialize_vault_cache",
    "proposal_from_prediction",
    "reset_vault_cache_for_tests",
    "vault_keys",
]
