from functools import lru_cache
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


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
