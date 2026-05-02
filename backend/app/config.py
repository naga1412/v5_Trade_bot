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


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
