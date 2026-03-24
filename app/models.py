from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, JSON, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class WatchlistAddress(Base):
    __tablename__ = "watchlist_addresses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    address: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    chain: Mapped[str] = mapped_column(String(30), nullable=False, default="ethereum")
    label: Mapped[str | None] = mapped_column(String(160), nullable=True)
    label_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    is_exchange: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_contract: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AddressCandidate(Base):
    __tablename__ = "address_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    address: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    chain: Mapped[str] = mapped_column(String(30), nullable=False, default="ethereum")
    label: Mapped[str | None] = mapped_column(String(160), nullable=True)
    market_cap_usd: Mapped[float] = mapped_column(Float, nullable=False)
    nav_cagr_90d: Mapped[float | None] = mapped_column(Float, nullable=True)
    nav_volatility_90d: Mapped[float | None] = mapped_column(Float, nullable=True)
    trade_frequency_90d: Mapped[float | None] = mapped_column(Float, nullable=True)
    stable_growth_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AddressDailyHolding(Base):
    __tablename__ = "address_daily_holdings"
    __table_args__ = (UniqueConstraint("address", "date", "token_symbol", name="uq_holding_row"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    address: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    chain: Mapped[str] = mapped_column(String(30), nullable=False, default="ethereum")
    date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    token_symbol: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    token_contract: Mapped[str | None] = mapped_column(String(100), nullable=True)
    balance: Mapped[float] = mapped_column(Numeric(38, 18), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TokenDailyPrice(Base):
    __tablename__ = "token_daily_prices"
    __table_args__ = (
        UniqueConstraint("token_symbol", "date", "source", name="uq_price_row"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_symbol: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    price_usd: Mapped[float] = mapped_column(Numeric(30, 12), nullable=False)
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="manual")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AddressDailyNAV(Base):
    __tablename__ = "address_daily_nav"
    __table_args__ = (UniqueConstraint("address", "date", name="uq_address_nav_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    address: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    nav_usd: Mapped[float] = mapped_column(Numeric(30, 8), nullable=False)
    missing_price_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    price_sources: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AddressSyncState(Base):
    __tablename__ = "address_sync_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    address: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    chain: Mapped[str] = mapped_column(String(30), nullable=False, default="ethereum")
    synced_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    synced_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_runtime_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(255), nullable=True)


class SavedAnalysisSnapshot(Base):
    __tablename__ = "saved_analysis_snapshots"
    __table_args__ = (
        UniqueConstraint("address", "start_date", "end_date", "top_n_tokens", name="uq_saved_analysis"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    address: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    chain: Mapped[str] = mapped_column(String(30), nullable=False, default="ethereum")
    start_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    top_n_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    nav_end_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    cagr: Mapped[float | None] = mapped_column(Float, nullable=True)
    sharpe: Mapped[float | None] = mapped_column(Float, nullable=True)
    behavior_style: Mapped[str | None] = mapped_column(String(30), nullable=True)
    return_driver: Mapped[str | None] = mapped_column(String(30), nullable=True)
    analysis_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    saved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class BatchJob(Base):
    __tablename__ = "batch_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    addresses: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    top_n_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    market_cap_basis: Mapped[str] = mapped_column(String(20), nullable=False, default="max_nav")
    refresh: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    total_addresses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fault_text: Mapped[str | None] = mapped_column(String(512), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    results: Mapped[list["BatchJobResult"]] = relationship("BatchJobResult", back_populates="batch_job")


class BatchJobResult(Base):
    __tablename__ = "batch_job_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_job_id: Mapped[int] = mapped_column(ForeignKey("batch_jobs.id"), nullable=False, index=True)
    address: Mapped[str] = mapped_column(String(100), nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    market_cap_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_cap_basis: Mapped[str | None] = mapped_column(String(20), nullable=True)
    nav_end_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    cagr: Mapped[float | None] = mapped_column(Float, nullable=True)
    sharpe: Mapped[float | None] = mapped_column(Float, nullable=True)
    behavior_style: Mapped[str | None] = mapped_column(String(30), nullable=True)
    return_driver: Mapped[str | None] = mapped_column(String(30), nullable=True)
    explanation: Mapped[str | None] = mapped_column(String(512), nullable=True)
    used_cache: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    runtime_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    batch_job: Mapped[BatchJob] = relationship("BatchJob", back_populates="results")
