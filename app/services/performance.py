from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from sqlalchemy import and_, delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AddressDailyHolding, AddressDailyNAV, TokenDailyPrice, WatchlistAddress
from app.services.metrics import NavPointData, build_metrics, calculate_trade_frequency
from app.utils.normalizers import normalize_address

settings = get_settings()

SOURCE_PRIORITY = {
    "chainlink": 0,
    "coingecko": 1,
    "manual": 2,
}


@dataclass
class PriceRecord:
    price_usd: float
    source: str


@dataclass
class HoldingRecord:
    token_symbol: str
    balance: float


def _source_rank(source: str) -> int:
    return SOURCE_PRIORITY.get(source.lower(), 999)


def _build_price_lookup(price_rows: list[TokenDailyPrice]) -> dict[tuple[str, date], PriceRecord]:
    lookup: dict[tuple[str, date], PriceRecord] = {}
    for row in price_rows:
        token_key = row.token_symbol.upper()
        key = (token_key, row.date)
        source = row.source.lower()
        record = PriceRecord(price_usd=float(row.price_usd), source=source)
        existing = lookup.get(key)
        if existing is None or _source_rank(record.source) < _source_rank(existing.source):
            lookup[key] = record
    return lookup


def _select_tokens(
    holdings: list[AddressDailyHolding],
    price_lookup: dict[tuple[str, date], PriceRecord],
    top_n_tokens: int,
) -> list[str]:
    token_scores: dict[str, float] = defaultdict(float)
    token_set: set[str] = set()

    for row in holdings:
        token = row.token_symbol.upper()
        token_set.add(token)

        price = price_lookup.get((token, row.date))
        if not price:
            continue

        usd_value = float(row.balance) * price.price_usd
        token_scores[token] = max(token_scores[token], usd_value)

    stablecoin_set = {symbol.upper() for symbol in settings.stablecoins}
    native_token_set = {symbol.upper() for symbol in settings.native_tokens}

    stable_tokens = sorted(token_set & stablecoin_set)
    native_tokens = sorted((token_set & native_token_set) - set(stable_tokens))

    remaining_tokens = sorted(
        token_set - set(stable_tokens) - set(native_tokens),
        key=lambda symbol: (token_scores.get(symbol, 0.0), symbol),
        reverse=True,
    )

    top_tokens = remaining_tokens[:top_n_tokens]

    selected_tokens = stable_tokens + native_tokens + top_tokens
    if not selected_tokens:
        selected_tokens = remaining_tokens[:top_n_tokens]
    return selected_tokens


def _build_daily_holding_view(
    holdings: list[AddressDailyHolding],
    selected_tokens: list[str],
) -> tuple[dict[date, list[HoldingRecord]], dict[date, dict[str, float]]]:
    selected_token_set = set(selected_tokens)
    holdings_by_date: dict[date, list[HoldingRecord]] = defaultdict(list)
    balance_snapshots: dict[date, dict[str, float]] = defaultdict(dict)

    for row in holdings:
        token = row.token_symbol.upper()
        if token not in selected_token_set:
            continue

        balance_value = float(row.balance)
        holdings_by_date[row.date].append(HoldingRecord(token_symbol=token, balance=balance_value))
        balance_snapshots[row.date][token] = balance_value

    return holdings_by_date, balance_snapshots


def _compute_nav_curve(
    holdings_by_date: dict[date, list[HoldingRecord]],
    price_lookup: dict[tuple[str, date], PriceRecord],
) -> tuple[list[NavPointData], list[str], int]:
    nav_points: list[NavPointData] = []
    global_sources: set[str] = set()
    missing_price_days = 0

    for holding_date in sorted(holdings_by_date.keys()):
        nav_usd = 0.0
        missing_price_tokens = 0
        price_sources: set[str] = set()

        for holding in holdings_by_date[holding_date]:
            price = price_lookup.get((holding.token_symbol, holding_date))
            if not price:
                missing_price_tokens += 1
                continue

            nav_usd += holding.balance * price.price_usd
            price_sources.add(price.source)

        if missing_price_tokens > 0:
            missing_price_days += 1

        global_sources.update(price_sources)
        nav_points.append(
            NavPointData(
                date=holding_date,
                nav_usd=round(nav_usd, 8),
                missing_price_tokens=missing_price_tokens,
                price_sources=sorted(price_sources),
            )
        )

    return nav_points, sorted(global_sources), missing_price_days


def _save_nav_curve(
    db: Session,
    address: str,
    start_date: date,
    end_date: date,
    nav_points: list[NavPointData],
) -> None:
    db.execute(
        delete(AddressDailyNAV).where(
            and_(
                AddressDailyNAV.address == address,
                AddressDailyNAV.date >= start_date,
                AddressDailyNAV.date <= end_date,
            )
        )
    )

    for point in nav_points:
        db.add(
            AddressDailyNAV(
                address=address,
                date=point.date,
                nav_usd=point.nav_usd,
                missing_price_tokens=point.missing_price_tokens,
                price_sources=point.price_sources,
            )
        )

    db.commit()


def _get_watchlist_quality_tags(db: Session, address: str) -> dict[str, str | bool | None]:
    row = db.scalar(select(WatchlistAddress).where(WatchlistAddress.address == address))
    if not row:
        return {
            "label_source": None,
            "suspected_exchange": False,
            "suspected_contract": False,
        }
    return {
        "label_source": row.label_source,
        "suspected_exchange": row.is_exchange,
        "suspected_contract": row.is_contract,
    }


def recompute_address_performance(
    db: Session,
    raw_address: str,
    start_date: date,
    end_date: date,
    top_n_tokens: int,
) -> dict:
    if end_date < start_date:
        raise ValueError("end_date must be greater than or equal to start_date")

    address = normalize_address(raw_address)

    holdings = list(
        db.scalars(
            select(AddressDailyHolding)
            .where(
                and_(
                    AddressDailyHolding.address == address,
                    AddressDailyHolding.date >= start_date,
                    AddressDailyHolding.date <= end_date,
                )
            )
            .order_by(AddressDailyHolding.date.asc(), AddressDailyHolding.token_symbol.asc())
        )
    )

    if not holdings:
        raise ValueError("no holdings found in date range")

    token_symbols = sorted({row.token_symbol.upper() for row in holdings})
    prices = list(
        db.scalars(
            select(TokenDailyPrice)
            .where(
                and_(
                    TokenDailyPrice.token_symbol.in_(token_symbols),
                    TokenDailyPrice.date >= start_date,
                    TokenDailyPrice.date <= end_date,
                )
            )
            .order_by(TokenDailyPrice.date.asc())
        )
    )

    price_lookup = _build_price_lookup(prices)
    selected_tokens = _select_tokens(holdings, price_lookup, top_n_tokens=top_n_tokens)
    holdings_by_date, balance_snapshots = _build_daily_holding_view(holdings, selected_tokens)
    nav_points, price_sources, missing_price_days = _compute_nav_curve(holdings_by_date, price_lookup)

    if not nav_points:
        raise ValueError("no holdings after token selection")

    _save_nav_curve(db, address, start_date, end_date, nav_points)

    trade_frequency = calculate_trade_frequency(balance_snapshots)
    metrics = build_metrics(nav_points, trade_frequency=trade_frequency)
    quality_tags = _get_watchlist_quality_tags(db, address)

    return {
        "address": address,
        "start_date": nav_points[0].date,
        "end_date": nav_points[-1].date,
        "selected_tokens": selected_tokens,
        "nav_curve": [
            {
                "date": point.date,
                "nav_usd": point.nav_usd,
                "missing_price_tokens": point.missing_price_tokens,
                "price_sources": point.price_sources,
            }
            for point in nav_points
        ],
        "metrics": metrics,
        "quality": {
            "price_sources": price_sources,
            "missing_price_days": missing_price_days,
            **quality_tags,
        },
    }


def get_cached_address_performance(
    db: Session,
    raw_address: str,
    start_date: date,
    end_date: date,
) -> dict:
    address = normalize_address(raw_address)

    nav_rows = list(
        db.scalars(
            select(AddressDailyNAV)
            .where(
                and_(
                    AddressDailyNAV.address == address,
                    AddressDailyNAV.date >= start_date,
                    AddressDailyNAV.date <= end_date,
                )
            )
            .order_by(AddressDailyNAV.date.asc())
        )
    )

    if not nav_rows:
        raise ValueError("no cached nav found in date range")

    nav_points = [
        NavPointData(
            date=row.date,
            nav_usd=float(row.nav_usd),
            missing_price_tokens=row.missing_price_tokens,
            price_sources=row.price_sources or [],
        )
        for row in nav_rows
    ]

    holdings = list(
        db.scalars(
            select(AddressDailyHolding)
            .where(
                and_(
                    AddressDailyHolding.address == address,
                    AddressDailyHolding.date >= start_date,
                    AddressDailyHolding.date <= end_date,
                )
            )
            .order_by(AddressDailyHolding.date.asc())
        )
    )

    selected_tokens = sorted({row.token_symbol.upper() for row in holdings})
    balance_snapshots: dict[date, dict[str, float]] = defaultdict(dict)
    for row in holdings:
        balance_snapshots[row.date][row.token_symbol.upper()] = float(row.balance)

    trade_frequency = calculate_trade_frequency(balance_snapshots)
    metrics = build_metrics(nav_points, trade_frequency=trade_frequency)

    price_source_set: set[str] = set()
    for row in nav_rows:
        if row.price_sources:
            price_source_set.update(row.price_sources)

    quality_tags = _get_watchlist_quality_tags(db, address)
    missing_price_days = sum(1 for row in nav_rows if row.missing_price_tokens > 0)

    return {
        "address": address,
        "start_date": nav_points[0].date,
        "end_date": nav_points[-1].date,
        "selected_tokens": selected_tokens,
        "nav_curve": [
            {
                "date": point.date,
                "nav_usd": point.nav_usd,
                "missing_price_tokens": point.missing_price_tokens,
                "price_sources": point.price_sources,
            }
            for point in nav_points
        ],
        "metrics": metrics,
        "quality": {
            "price_sources": sorted(price_source_set),
            "missing_price_days": missing_price_days,
            **quality_tags,
        },
    }
