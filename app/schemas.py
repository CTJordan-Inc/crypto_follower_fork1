from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.utils.normalizers import normalize_address, normalize_token_symbol


class WatchlistCreate(BaseModel):
    address: str
    chain: str = "ethereum"
    label: str | None = None
    label_source: str | None = "manual"
    is_exchange: bool = False
    is_contract: bool = False

    @field_validator("address")
    @classmethod
    def validate_address(cls, value: str) -> str:
        return normalize_address(value)


class WatchlistRead(BaseModel):
    id: int
    address: str
    chain: str
    label: str | None
    label_source: str | None
    is_exchange: bool
    is_contract: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class HoldingIn(BaseModel):
    address: str
    date: date
    token_symbol: str
    balance: Decimal = Field(..., gt=0)
    chain: str = "ethereum"
    token_contract: str | None = None

    @field_validator("address")
    @classmethod
    def validate_address(cls, value: str) -> str:
        return normalize_address(value)

    @field_validator("token_symbol")
    @classmethod
    def validate_symbol(cls, value: str) -> str:
        return normalize_token_symbol(value)


class PriceIn(BaseModel):
    token_symbol: str
    date: date
    price_usd: Decimal = Field(..., gt=0)
    source: str = "manual"

    @field_validator("token_symbol")
    @classmethod
    def validate_symbol(cls, value: str) -> str:
        return normalize_token_symbol(value)

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str) -> str:
        return value.strip().lower()


class CandidateIn(BaseModel):
    address: str
    chain: str = "ethereum"
    label: str | None = None
    market_cap_usd: float = Field(..., gt=0)
    nav_cagr_90d: float | None = None
    nav_volatility_90d: float | None = None
    trade_frequency_90d: float | None = None
    stable_growth_score: float = 0
    source: str | None = "manual"

    @field_validator("address")
    @classmethod
    def validate_address(cls, value: str) -> str:
        return normalize_address(value)


class AddressRecommendation(BaseModel):
    address: str
    chain: str
    label: str | None
    market_cap_usd: float
    nav_cagr_90d: float | None
    nav_volatility_90d: float | None
    trade_frequency_90d: float | None
    stable_growth_score: float
    source: str | None

    model_config = ConfigDict(from_attributes=True)


class UpsertSummary(BaseModel):
    upserted: int


class NavPoint(BaseModel):
    date: date
    nav_usd: float
    missing_price_tokens: int
    price_sources: list[str]


class PerformanceMetrics(BaseModel):
    cagr: float | None
    mdd: float | None
    sharpe: float | None
    trade_frequency: float | None
    max_single_day_drop: float | None
    total_return: float | None


class QualityTags(BaseModel):
    price_sources: list[str]
    label_source: str | None
    suspected_exchange: bool
    suspected_contract: bool
    missing_price_days: int


class RecomputeRequest(BaseModel):
    start_date: date
    end_date: date
    top_n_tokens: int = Field(default=10, ge=1, le=100)

    @model_validator(mode="after")
    def validate_dates(self) -> "RecomputeRequest":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be greater than or equal to start_date")
        return self


class NetworkRecomputeRequest(RecomputeRequest):
    refresh: bool = False


class BatchRecomputeRequest(BaseModel):
    addresses: list[str] = Field(..., min_length=1, max_length=50)
    start_date: date
    end_date: date
    top_n_tokens: int = Field(default=10, ge=1, le=100)
    market_cap_basis: str = "max_nav"
    refresh: bool = False

    @field_validator("addresses")
    @classmethod
    def validate_addresses(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            address = normalize_address(value)
            if not address or address in seen:
                continue
            seen.add(address)
            normalized.append(address)
        if not normalized:
            raise ValueError("addresses cannot be empty")
        if len(normalized) > 50:
            raise ValueError("addresses cannot exceed 50 items")
        return normalized

    @model_validator(mode="after")
    def validate_dates(self) -> "BatchRecomputeRequest":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be greater than or equal to start_date")
        if self.market_cap_basis not in {"max_nav", "average_nav"}:
            raise ValueError("market_cap_basis must be one of: max_nav, average_nav")
        return self


class PerformanceBehavior(BaseModel):
    style: str
    return_driver: str
    summary: str
    gross_inflow_usd: float | None
    gross_outflow_usd: float | None
    net_flow_usd: float | None
    market_pnl_usd: float | None
    turnover_ratio: float | None
    round_trip_ratio: float | None
    net_accumulation_ratio: float | None


class PerformanceMeta(BaseModel):
    used_cache: bool
    cache_age_minutes: float | None
    source: str
    runtime_seconds: float | None
    address_market_cap_usd: float | None
    address_market_cap_basis: str
    address_peak_nav_usd: float | None
    address_average_nav_usd: float | None


class PerformanceResponse(BaseModel):
    address: str
    start_date: date
    end_date: date
    selected_tokens: list[str]
    nav_curve: list[NavPoint]
    metrics: PerformanceMetrics
    quality: QualityTags
    behavior: PerformanceBehavior
    interpretations: list[str]
    meta: PerformanceMeta


class BatchPerformanceItem(BaseModel):
    address: str
    success: bool
    market_cap_usd: float | None = None
    market_cap_basis: str | None = None
    nav_end_usd: float | None = None
    total_return: float | None = None
    cagr: float | None = None
    sharpe: float | None = None
    behavior_style: str | None = None
    return_driver: str | None = None
    explanation: str | None = None
    used_cache: bool | None = None
    runtime_seconds: float | None = None
    error: str | None = None


class BatchPerformanceResponse(BaseModel):
    start_date: date
    end_date: date
    requested: int
    completed: int
    failed: int
    results: list[BatchPerformanceItem]


class SavedAnalysisSummary(BaseModel):
    id: int
    address: str
    start_date: date
    end_date: date
    top_n_tokens: int
    market_cap_usd: float | None
    market_cap_basis: str
    nav_end_usd: float | None
    total_return: float | None
    cagr: float | None
    sharpe: float | None
    behavior_style: str | None
    return_driver: str | None
    analysis_source: str | None
    saved_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SavedAnalysisListResponse(BaseModel):
    items: list[SavedAnalysisSummary]


class RandomAddressesResponse(BaseModel):
    count: int
    addresses: list[str]
    source: str
    min_market_cap_usd: float
    market_cap_basis: str
    filter_stage: str
