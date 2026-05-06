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


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
