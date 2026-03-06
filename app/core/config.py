from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "crypto_follower"
    api_prefix: str = "/api/v1"
    database_url: str = "sqlite:///./crypto_follower.db"
    create_tables_on_startup: bool = True
    stablecoins: list[str] = ["USDT", "USDC", "DAI", "FDUSD"]
    native_tokens: list[str] = ["ETH"]
    candidate_min_market_cap: float = 10_000_000
    etherscan_base_url: str = "https://api.etherscan.io/v2/api"
    etherscan_chain_id: int = 1
    etherscan_api_key: str | None = None
    etherscan_max_pages: int = 5
    coingecko_base_url: str = "https://api.coingecko.com/api/v3"
    coingecko_platform: str = "ethereum"
    http_timeout_seconds: int = 30
    default_lookback_days: int = 90

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
