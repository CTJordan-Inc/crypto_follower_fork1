from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


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
