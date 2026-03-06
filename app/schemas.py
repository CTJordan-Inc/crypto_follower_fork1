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


class PerformanceResponse(BaseModel):
    address: str
    start_date: date
    end_date: date
    selected_tokens: list[str]
    nav_curve: list[NavPoint]
    metrics: PerformanceMetrics
    quality: QualityTags
