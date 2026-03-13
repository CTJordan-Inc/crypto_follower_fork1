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
    etherscan_page_offset: int = 1000
    etherscan_max_retries: int = 5
    etherscan_backoff_seconds: float = 0.6
    etherscan_request_interval_seconds: float = 0.35
    coingecko_base_url: str = "https://api.coingecko.com/api/v3"
    coingecko_platform: str = "ethereum"
    coingecko_api_key: str | None = None
    coingecko_api_key_header: str = "x-cg-demo-api-key"
    coingecko_max_retries: int = 4
    coingecko_backoff_seconds: float = 1.5
    coingecko_request_interval_seconds: float = 0.3
    coingecko_contract_chunk_size: int = 40
    coingecko_spot_contract_limit: int = 300
    http_timeout_seconds: int = 30
    default_lookback_days: int = 90
    sync_cache_ttl_minutes: int = 720
    batch_address_limit: int = 50
    saved_analysis_limit: int = 100
    random_address_block_window: int = 160
    random_address_sample_blocks: int = 12
    random_address_candidate_multiplier: int = 4

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
