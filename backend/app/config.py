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


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
