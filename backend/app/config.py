from functools import lru_cache
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "development"
    log_level: str = "INFO"

    database_url: str
    redis_url: str

    cf_access_team_domain: str = ""
    cf_access_aud: str = ""

    binance_use_testnet: bool = True
    # When false, the live-prediction WS worker does not start in the lifespan.
    # Default true for prod/dev; set false in test fixtures or CI.
    worker_enabled: bool = True

    # SP-0.7 §F: master passphrase for AES-256-GCM per-user secret encryption.
    # MUST be set in production (>=16 chars). Test/dev fall back to a stable
    # value; the test conftest sets this explicitly so encryption helpers work.
    master_passphrase: str = "test-passphrase-only-for-tests-do-not-use-in-prod"

    # SP-9 Phase B1: CryptoPanic free-tier API key.
    # Empty string in dev/test causes the adapter to raise ValueError, so the
    # ingest worker is gated separately on settings.env in app.main:lifespan.
    cryptopanic_api_key: str = ""

    # SP-8 Phase J: master switch for the autonomous-trading subsystem.
    # When false (default), the lifespan does NOT run pre-flight or start
    # any of the live-trading workers. Operator opts in by setting this
    # to true in /opt/trading-radar/.env AFTER:
    #   1. tools/secrets/encrypt.py has produced secrets.enc
    #   2. MASTER_PASSPHRASE is set to the matching value
    #   3. The Binance API key has Reading + Futures only (sec 9.3)
    # Pre-flight gates the actual workers — even with this true, any
    # failed check refuses to start them.
    autonomous_trading_enabled: bool = False

    # SP-8 Phase J.2: unattended mode auto-promotion. When the relevant
    # flag is true AND the spec sec 4 gates pass for N consecutive days,
    # the daily 03:30 UTC worker upgrades the user's mode without
    # hardware-confirm. Designed for the case where the operator can't
    # be available to manually flip modes (e.g. Claude subscription ended).
    # Default OFF — opt in per direction:
    auto_promote_to_telegram_enabled: bool = False
    auto_promote_to_fullyauto_enabled: bool = False
    # Number of consecutive days the gates must hold before auto-promote
    # fires. Default 7 — overrides the spec's "any one snapshot" trigger
    # to absorb single-day variance.
    auto_promote_consecutive_days: int = 7

    # --- PR2: MTF gate (active in PR2; recording-only in PR1) -------------
    # MTF_MIN_AGREEMENT_1H=0 is the single-env-var rollback path (gate
    # passes for all agreement values when set to 0). Default 3 = 3-of-6
    # TF majority. Tunable post-launch via env var.
    MTF_MIN_AGREEMENT_1H: int = 3
    MTF_HIGHER_TF_VETO: bool = True

    # --- PR2: SHORT-side safety (default OFF; per-env enable) -------------
    # All 3 flags must default False — spec §6.1 hard bound. Env var
    # override allowed per-environment.
    SHORT_FUNDING_HALVE_HOLD: bool = False
    SHORT_TIGHTEN_SL_LOW_MTF: bool = False
    SHORT_VETO_HIGH_BORROW: bool = False

    # --- PR2: SHORT-side thresholds (only consulted when flag ON) --------
    SHORT_FUNDING_HALVE_THRESHOLD_PCT: float = 0.05   # %/8h
    SHORT_VETO_BORROW_APR_PCT: float = 10.0           # % APR
    SHORT_TIGHTEN_SL_MTF_CUTOFF: int = 5
    SHORT_TIGHTEN_SL_PCT: float = 0.20

    # --- PR3: Multi-resolution shadow ------------------------------------
    # SHADOW_TIMEFRAMES default ["1h", "15m"] — the one explicit behavior
    # flip from PR2's effective ["1h"]. Rollback: set ["1h"] in env (spec §8).
    SHADOW_TIMEFRAMES: list[str] = ["1h", "15m"]
    SHADOW_PREWARM_BARS: int = 200  # matches MTF cache cap; setup() reuses cache
    # Per-TF cooldown in hours. Both default 0.5h (30 min) — matches the
    # pre-PR3 COOLDOWN_MINUTES=30 module constant. Dict shape future-proofs
    # asymmetric values without API churn.
    SHADOW_COOLDOWN_HOURS: dict[str, float] = {"1h": 0.5, "15m": 0.5}
    # Non-empty list = intersect with top-30 universe. Empty = full top-30.
    # Empty intersection logs WARN and falls back to full (fail-loud-then-open).
    SHADOW_NARROW_UNIVERSE: list[str] = []
    # Excludes 15m from /promotion-gate combined aggregate when False.
    # Records 15m trades regardless; just doesn't gamble promotion on them
    # until staging win-rate validates. Operator flips per-env after.
    SHADOW_15M_ELIGIBLE_FOR_PROMOTION: bool = False

    # --- Amendment 3 (2026-07-31): breakeven-variant lane ----------------
    # Paired-comparison variant lane on shadow_trades. Each base close
    # spawns one row per active trigger into `shadow_trade_variants`.
    # Variant lane writes NO cooldowns and does NOT touch
    # open_shadow_positions (confirmations required by operator). Base
    # lane behavior is unchanged in every way.
    # Default OFF so shipping is a zero-behavior-change deploy.
    SHADOW_BREAKEVEN_VARIANTS_ENABLED: bool = False
    # Trigger R values to simulate. Dual lane 0.40 + 0.50 default so the
    # trigger-selection question is settled in the same measurement cycle
    # instead of waiting another 2+ months. Empty list is equivalent to
    # _ENABLED=False (worker short-circuits).
    SHADOW_BREAKEVEN_VARIANT_TRIGGERS_R: list[float] = [0.40, 0.50]
    # In-memory bar-history buffer cap per open ShadowPosition. 500 × 15m
    # = ~5 days, comfortably exceeds any real hold before TIMEOUT_BARS
    # fires (96 for 15m, 24 for 1h). Prevents unbounded memory in
    # pathological cases.
    SHADOW_BREAKEVEN_BAR_HISTORY_CAP: int = 500

    # --- PR3 G1: Hold/TP scaling by mtf_agreement (spec §4.6b) -----------
    # Default OFF — scaling does NOT apply; positions use the per-TF
    # baseline timeout (TIMEOUT_BARS_PER_TF) and engine-computed TP.
    # G2 (IC auto-weighting) and G3 (regime-conditional weights) stay
    # deferred — need 30+ days of MTF shadow data which only starts
    # accruing post-PR3 deploy.
    HOLD_TP_SCALING_ENABLED: bool = False
    # Lookup: mtf_agreement -> (timeout_bars, tp_multiplier).
    # timeout_bars values are the 1h-baseline; for non-1h TFs the worker
    # applies the multiplier (table_bars / 24) against the per-TF baseline
    # (TIMEOUT_BARS_PER_TF[tf]) — see app/shadow/scaling.py.
    # Stop-loss is INVARIANT under scaling.
    HOLD_TP_SCALING_TABLE: dict[int, tuple[int, float]] = {
        3: (24, 1.0),
        4: (48, 1.25),
        5: (96, 1.5),
        6: (168, 2.0),
    }

    # --- PR8: Outcome-adaptive cooldown (live trades) --------------------
    # Default OFF for prod safety. Operator flips per env once soak verifies.
    LIVE_COOLDOWN_ENABLED: bool = False
    # Per-outcome cooldown duration (hours). Pulled from spec §5:
    #   stop_loss     8.0  — long enough to let next-bar conditions develop
    #   take_profit   1.0  — short, fast re-entry on a winning setup
    #   timeout       4.0  — baseline middle ground
    #   manual_close  0.0  — operator override, operator decides re-entry
    #   external_close 0.0 — Binance closed without our consent (rare)
    #   liquidation_buffer_breach 24.0 — sizing/leverage was off
    LIVE_COOLDOWN_HOURS_BY_OUTCOME: dict[str, float] = {
        "stop_loss": 8.0,
        "take_profit": 1.0,
        "timeout": 4.0,
        "manual_close": 0.0,
        "external_close": 0.0,
        "liquidation_buffer_breach": 24.0,
    }
    # After SL: require strictly-greater mtf_agreement on the new signal
    # to clear the cooldown even after calendar time elapses. Defends
    # against "same losing setup keeps firing every 8h".
    LIVE_COOLDOWN_SL_REQUIRES_FRESH_MTF: bool = True
    # Forward-compat hook for a future regime-aware classifier. No live
    # regime detector exists today, so this is a no-op until that lands.
    LIVE_COOLDOWN_REGIME_AWARE: bool = False

    # --- PR9: Dynamic sizing (Kelly-fractional × balance tier) -----------
    # Default OFF for prod safety + operator carve-out: PR9 is the ONLY
    # PR in the rollout where dev→main requires explicit "ship it".
    DYNAMIC_SIZING_ENABLED: bool = False
    # Forward-compat for PR5: when predict_p_win() returns non-None,
    # sizing uses that probability instead of confidence_pct/100. False
    # forces the confidence proxy regardless.
    SIZING_USE_P_WIN_WHEN_AVAILABLE: bool = True
    # Quarter-Kelly is industry-standard defensive. Half-Kelly (0.5) is
    # 2x more aggressive; eighth-Kelly (0.125) is 2x more conservative.
    SIZING_FRACTIONAL_KELLY: float = 0.25
    # Per-tier hard caps as fraction of bankroll. Structural floor —
    # Kelly result is clamped to <= these regardless of confidence.
    SIZING_TIER_MAX_FRACTION: dict[str, float] = {
        "small": 0.01,
        "medium": 0.02,
        "large": 0.05,
        "whale": 0.10,
    }
    # Tier bucket boundaries (USDT). Inclusive on the upper side:
    #   balance < small_max          → "small"
    #   small_max <= balance < ...   → "medium"
    SIZING_TIER_BOUNDARIES: dict[str, float] = {
        "small_max": 1_000.0,
        "medium_max": 10_000.0,
        "large_max": 100_000.0,
    }
    # Multi-entry split kicks in when confidence_pct/100 < this threshold.
    SIZING_MULTI_ENTRY_THRESHOLD: float = 0.75
    # Tranche ratios — must sum to 1.0. Validated at sizing time.
    SIZING_MULTI_ENTRY_RATIOS: list[float] = [0.6, 0.4]
    # DCA band — tranche 2 placed when price moves this pct against signal.
    SIZING_MULTI_ENTRY_DCA_BAND_PCT: float = 0.5

    # --- PR10 symbol allowlist + stablecoin filter -----------------------
    # Default-OFF for safe deploy. Operator flips after observing the
    # `/api/v1/bot-status/symbol-allowlist` endpoint for ~24h.
    SYMBOL_ALLOWLIST_ENABLED: bool = False
    # Quote-stripped base asset names excluded from real-money dispatch.
    # Shadow trading on these symbols continues (controlled by
    # SHADOW_NARROW_UNIVERSE) so per-symbol stats keep accruing.
    SHADOW_STABLECOIN_EXCLUDE_LIST: list[str] = [
        "USDC", "FDUSD", "USD1", "BUSD", "TUSD", "DAI",
    ]
    # New-symbol grace: < this many closed trades → allowlisted regardless
    # of Sharpe. Prevents excluding new symbols before meaningful data.
    SYMBOL_ALLOWLIST_GRACE_TRADES: int = 50
    # Rolling window: Sharpe over min(WINDOW_TRADES most-recent closed,
    # trades in last WINDOW_DAYS days) — whichever set is smaller.
    SYMBOL_ALLOWLIST_WINDOW_TRADES: int = 100
    SYMBOL_ALLOWLIST_WINDOW_DAYS: int = 30
    # In-memory allowlist cache TTL. Comfortably faster than daily refresh
    # so cache rebuilds read fresh snapshot data.
    SYMBOL_ALLOWLIST_CACHE_TTL_SECONDS: int = 3600

    # --- PR10.5 / FU-28 UI freshness monitor ----------------------------
    # The monitor itself is observation-on by default. Only auto-recycle
    # is gated: shadow_worker is currently stateful=True in worker_registry,
    # so calling worker_supervisor.restart on it is unsafe without further
    # design work. The flag exists for forward-compat.
    FU28_POLL_INTERVAL_SECONDS: int = 300
    FU28_STALE_PNL_TICK_THRESHOLD_SECONDS: int = 1800
    FU28_AUTO_RECYCLE_ENABLED: bool = False

    # --- PR10.7: SPOT-invalid symbols ------------------------------------
    # Symbols that appear in the asset_universe (or persist in open
    # positions) but FAIL on Binance SPOT /api/v3/ticker/price. Without
    # blacklisting, fetch_spot_prices falls back to per-symbol calls
    # (resilient but slow). Filtering upstream is cleaner.
    #
    # Known-bad as of 2026-05-20 diagnostic run 26147552565:
    # - EDENUSDT: futures-only (never on SPOT)
    # - LUNCUSDT: delisted from .com SPOT
    # - PAXGUSDT / XAUTUSDT: gold-pegged tokens, region restricted
    #
    # PR-CLEANUP-BATCH-1 (2026-05-22): removed UUSDT — verified priceable
    # via `GET https://api.binance.com/api/v3/ticker/price?symbol=UUSDT`
    # → {"symbol":"UUSDT","price":"1.00100000"}. PR10.7's blacklist
    # entry was correct at the time but is now stale.
    SHADOW_SPOT_BLACKLIST: list[str] = [
        "EDENUSDT", "LUNCUSDT", "PAXGUSDT", "XAUTUSDT",
        # 2026-07-11: new stablecoins/pegged tokens that entered SPOT top-30
        # but have no Binance Futures perpetual — caused ~100 premiumIndex
        # 400 Bad Request errors/hour in the intermarket worker.
        "RLUSDUSDT", "USD1USDT", "USDEUSDT", "SPCXBUSDT",
        "EURUSDT", "FDUSDUSDT", "SNDKBUSDT",
        # 2026-07-18: USDCUSDT — confirmed stablecoin contaminating shadow
        # universe (avg ATR 0.007%, 81.2% SL rate on 30d LONG autopsy).
        "USDCUSDT",
    ]

    # --- PR-strategy-1: entry-quality gate -------------------------------
    # Both flags default OFF so this deploy is a zero-behavior-change ship.
    # Operator flips per env after staging soak.
    #
    # MIN_ENTRY_SCORE_LONG: when not None, the shadow worker + dispatcher
    # reject LONG entries with `entry_score < threshold`. Closes the
    # decile-9 hole found in the PR-DIAG-1.5 ledger (decile-9 was WR 31%
    # / avg -1.96% PnL; decile-10 was WR 50% / avg +1.24%).
    # DISABLE_SHORT_SIGNALS: when True, rejects all SHORT entries.
    # Validated against 89 SHORT trades / WR 19.1% vs 74 LONG / WR 27.0% —
    # the model's score distribution lacks a high-conviction SHORT tail.
    MIN_ENTRY_SCORE_LONG: float | None = None
    DISABLE_SHORT_SIGNALS: bool = False
    SHADOW_ALLOW_SHORTS: bool = False
    # SHADOW_IGNORE_MIN_SCORE: when True, the shadow worker overrides a
    # below_long_threshold gate denial and allows the shadow entry anyway.
    # Live dispatch is completely unaffected — the gate itself still denies.
    # Mirror of SHADOW_ALLOW_SHORTS (#303). Default False.
    SHADOW_IGNORE_MIN_SCORE: bool = False
    # L8_GHOST_SCORING_ENABLED: when True, the L8 Conv-LSTM ghost-candle
    # vote is threaded into build_prediction() and participates in the
    # aggregator's equal-weight static score (full 1/N_active vote,
    # same as any other active layer) at all 3 production call sites
    # (tab1 REST, live_prediction WS, shadow worker). When False
    # (default), ghost is still computed and persisted/displayed exactly
    # as before, but never reaches build_prediction — L8 stays None,
    # bit-identical to pre-wiring behaviour. Default False pending a
    # correlation check: the ghost forecast's predictive value has never
    # been measured against realized outcomes in this pipeline.
    L8_GHOST_SCORING_ENABLED: bool = False

    # --- PR-HYBRID-CONFIDENCE-ROUTING (2026-05-23) -----------------------
    # Confidence-tiered routing modifier on the existing telegram-approve
    # mode. None (default) keeps every signal routed to Telegram approval.
    # When set: for `trading_mode='telegram-approve'`, signals whose
    # `abs(proposal.entry_score) >= HYBRID_AUTO_SCORE_THRESHOLD` route
    # directly to `_place_live_order` (auto-execute) instead of the
    # Telegram-approval handshake. Below-threshold signals continue to
    # route via Telegram as today. The lower bound is the existing
    # entry-quality gate (MIN_ENTRY_SCORE_LONG on LONGs + DISABLE_SHORT_
    # SIGNALS on SHORTs) — this setting adds an upper-tier auto-execute
    # bucket, it does NOT replace the entry filter.
    #
    # Operator-facing: this is a behavior-modifier on telegram-approve
    # mode. It does NOT introduce a new `trading_mode` value. The choice
    # avoids touching the alembic CHECK constraint on `users.trading_mode`,
    # the promotion-gate TargetMode Literal, and the frontend mode
    # selector. The per-trade routing decision is logged at dispatch
    # time as `outcome="placed_hybrid"` vs `outcome="sent_telegram"` —
    # full audit trail at the per-trade level.
    #
    # Default None keeps PR-HYBRID-CONFIDENCE-ROUTING dormant. To enable:
    # set HYBRID_AUTO_SCORE_THRESHOLD=0.45 (conservative — only the
    # strongest signals auto-execute) and **recreate the backend
    # container** (`docker compose up -d backend`). `get_settings()` is
    # `@lru_cache`d, so changes to the host `.env` are NOT picked up
    # by a running process — env unset + restart is the only path.
    # Reversibility: remove or empty the env var, recreate the container.
    #
    # Validation: rejects values <= 0 and >= 1.0. Score magnitude is
    # bounded to [0, 1] (the aggregator clamps |score| to 1), so any
    # threshold outside (0, 1) is a typo or misconfiguration that
    # would silently auto-execute every signal (or none).
    HYBRID_AUTO_SCORE_THRESHOLD: float | None = None

    @field_validator("HYBRID_AUTO_SCORE_THRESHOLD")
    @classmethod
    def _validate_hybrid_threshold(cls, v: float | None) -> float | None:
        """Reject foot-gun values:
        * <= 0   → `abs(score) >= 0` is true for every non-zero score,
                   so every telegram-approve signal would auto-execute.
        * >= 1.0 → no score can clear; equivalent to unset but silently.
        Typos like 0.045 (intended 0.45) DO pass this validator — that
        risk is inherent to the operator's threshold-tuning loop.
        """
        if v is None:
            return None
        if v <= 0.0:
            raise ValueError(
                f"HYBRID_AUTO_SCORE_THRESHOLD={v} <= 0 would auto-execute "
                "every signal. Set to a positive value in (0, 1) or unset."
            )
        if v >= 1.0:
            raise ValueError(
                f"HYBRID_AUTO_SCORE_THRESHOLD={v} >= 1.0 cannot be cleared "
                "by any score (aggregator clamps |score| to 1). Use a "
                "value in (0, 1) or unset to disable hybrid routing."
            )
        return v

    # --- PR-MAKE-APPROVAL-TIMEOUT-AND-DRIFT-CONFIGURABLE (2026-05-26) ----
    # Two safety knobs on the telegram-approve flow:
    #
    # 1. TELEGRAM_APPROVAL_TIMEOUT_SECONDS — server-side auto-skip window.
    #    The telegram_poller cycle (~30s) marks any telegram_signals row
    #    older than this with response='auto_skipped'. Replaces the
    #    previously-cosmetic "Auto-skip in 30s" UI string (which had no
    #    enforcement — signals stayed pending forever, defeating the
    #    operator's expectation of a bounded approval window).
    #
    # 2. APPROVAL_MAX_PRICE_DRIFT_PCT — drift guard at approve-time.
    #    BEFORE `_place_approved_order` calls Binance, fetch current
    #    mark price; if |current - entry| / entry × 100 > this threshold,
    #    reject with outcome=stale_price. Prevents approving a 10-minute-
    #    old signal where the entry zone is already invalidated.
    #
    # Both default-conservative for the operator's "can sleep through a
    # candle close" use case: 600s window + 0.5% drift threshold means
    # a signal placed at the 1h-candle close is valid for up to 10
    # minutes provided the market hasn't moved more than 50bps.
    #
    # Validation: both must be positive. Timeout in seconds (int).
    # Drift in percent (float; 0.5 means 0.5%, NOT 50%).
    TELEGRAM_APPROVAL_TIMEOUT_SECONDS: int = 600
    APPROVAL_MAX_PRICE_DRIFT_PCT: float = 0.5

    @field_validator("TELEGRAM_APPROVAL_TIMEOUT_SECONDS")
    @classmethod
    def _validate_approval_timeout(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(
                f"TELEGRAM_APPROVAL_TIMEOUT_SECONDS={v} must be > 0. "
                "Zero or negative would auto-skip every signal the moment "
                "it's sent. Set a positive value (default 600 = 10 min)."
            )
        return v

    @field_validator("APPROVAL_MAX_PRICE_DRIFT_PCT")
    @classmethod
    def _validate_approval_drift_pct(cls, v: float) -> float:
        if v <= 0.0:
            raise ValueError(
                f"APPROVAL_MAX_PRICE_DRIFT_PCT={v} must be > 0. Zero or "
                "negative would reject every approval (any drift exceeds 0). "
                "Set a positive value (default 0.5 = 0.5 percent)."
            )
        return v

    # --- FU-33: slippage circuit-breaker ---------------------------------
    # Default-OFF — operator flips per-env after observing the metric.
    # When True:
    #   * shadow_worker._maybe_close_position calls check_slippage on SL
    #     exit. A trigger (realized SL pct / expected > threshold) writes
    #     a row to symbol_halt_state + sends a critical Telegram alert.
    #   * shadow_worker._maybe_open_position blocks new entries on any
    #     symbol carrying an unexpired halted_until.
    SLIPPAGE_GUARD_ENABLED: bool = False
    # Halt threshold: realized SL pct / expected SL pct > this → halt.
    # 3.0 = "slippage was 3x worse than expected" — generous default that
    # only catches obvious-outlier liquidity events (FIDAUSDT was ~14×).
    SLIPPAGE_THRESHOLD_MULTIPLIER: float = 3.0
    # How long to halt the symbol after a trigger. 4h gives enough time
    # for the operator to investigate without missing >1 candle of
    # opportunity on the 1h TF.
    SLIPPAGE_HALT_COOLDOWN_HOURS: int = 4

    # === BRAIN ACTION MULTIPLIER (PR-BRAIN-SOFTER-ACTIONS) ===
    # Maximum signed multiplier offset from 1.0 produced by
    # app.rl.inference.action_to_brain_adjust. With SPREAD=0.15 the brain's
    # effect on the aggregator's final score is bounded to [0.85, 1.15] ×
    # static. Symmetric mapping with linear half-steps:
    #   FLAT       → 1 − SPREAD       (full-strength suppress)
    #   disagree   → 1 − SPREAD / 2   (half-strength suppress)
    #   NEUTRAL    → 1.0              (no-op)
    #   agree-half → 1 + SPREAD / 2   (half-strength boost)
    #   agree-full → 1 + SPREAD       (full-strength boost)
    # Lower = brain is more conservative; higher = brain has more sway.
    # Default 0.15 chosen post-PR-BRAIN-BACKTEST-PHASEB5 to reduce blast
    # radius of bad decisions during v1 (id=3 holdout Sharpe = −3.21).
    BRAIN_ACTION_MULTIPLIER_SPREAD: float = 0.15

    # === PR-BOT-INTELLIGENCE-UPGRADE — entry-quality gate extensions ===
    # All eight flags default OFF / no-op so this PR ships as a zero-
    # behaviour-change deploy. Each sub-gate is independently flippable
    # so the operator can A/B compare in shadow before turning any on.

    # CHANGE 1 — bull/bear market regime gate. When True, deny LONG in
    # confirmed bear regime and SHORT in confirmed bull regime UNLESS
    # Layer 2 emits a same-direction pattern with confidence ≥
    # REGIME_OPPOSITE_PATTERN_OVERRIDE.
    REGIME_GATE_ENABLED: bool = False
    REGIME_OPPOSITE_PATTERN_OVERRIDE: float = 0.8

    # CHANGE 2 — Layer-2 pattern boost. When True, a LONG signal whose
    # Layer-2 vote agrees at confidence ≥ MIN_CONFIDENCE gets
    # +PATTERN_BOOST_AMOUNT added to its entry_score before the
    # MIN_ENTRY_SCORE_LONG comparison; an opposing high-confidence L2
    # subtracts PATTERN_PENALTY_AMOUNT. PR-strategy-1 behaviour is
    # unchanged when this is False (entry_score is compared raw).
    PATTERN_BOOST_ENABLED: bool = False
    PATTERN_BOOST_MIN_CONFIDENCE: float = 0.6
    PATTERN_BOOST_AMOUNT: float = 0.10
    PATTERN_PENALTY_AMOUNT: float = 0.15

    # CHANGE 3 — Global signal-side ADX trend strength gate. When True,
    # deny entries whose MTF dominant-TF ADX(14) is below
    # MIN_ADX_TREND_STRENGTH. Independent of mtf_confluence's internal
    # per-TF ADX floor (kept at 20.0) which only affects per-TF voting.
    ADX_GATE_ENABLED: bool = False
    MIN_ADX_TREND_STRENGTH: float = 25.0


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
