from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from statistics import fmean
from typing import Any

from sqlalchemy import and_, delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (
    AddressDailyHolding,
    AddressDailyNAV,
    AddressSyncState,
    TokenDailyPrice,
    WatchlistAddress,
)
from app.services.metrics import NavPointData, build_metrics, calculate_trade_frequency
from app.utils.normalizers import normalize_address

settings = get_settings()

FLOW_TOLERANCE = 1e-12
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


def _load_holdings(db: Session, address: str, start_date: date, end_date: date) -> list[AddressDailyHolding]:
    return list(
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


def _load_prices(
    db: Session,
    token_symbols: list[str],
    start_date: date,
    end_date: date,
) -> list[TokenDailyPrice]:
    if not token_symbols:
        return []
    return list(
        db.scalars(
            select(TokenDailyPrice)
            .where(
                and_(
                    TokenDailyPrice.token_symbol.in_(token_symbols),
                    TokenDailyPrice.date >= start_date,
                    TokenDailyPrice.date <= end_date,
                )
            )
            .order_by(TokenDailyPrice.date.asc(), TokenDailyPrice.token_symbol.asc())
        )
    )


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


def _round_or_none(value: float | None, digits: int = 6) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def _build_behavior_analysis(
    balance_snapshots: dict[date, dict[str, float]],
    nav_points: list[NavPointData],
    price_lookup: dict[tuple[str, date], PriceRecord],
    trade_frequency: float | None,
) -> dict[str, Any]:
    if not nav_points:
        return {
            "style": "insufficient_data",
            "return_driver": "unknown",
            "summary": "資料不足，無法判定是屯幣型還是中轉型地址。",
            "gross_inflow_usd": None,
            "gross_outflow_usd": None,
            "net_flow_usd": None,
            "market_pnl_usd": None,
            "turnover_ratio": None,
            "round_trip_ratio": None,
            "net_accumulation_ratio": None,
        }

    sorted_dates = sorted(balance_snapshots.keys())
    if len(sorted_dates) < 2:
        return {
            "style": "holding",
            "return_driver": "market_appreciation",
            "summary": "期間內幾乎沒有倉位變動，較接近單純持有。",
            "gross_inflow_usd": 0.0,
            "gross_outflow_usd": 0.0,
            "net_flow_usd": 0.0,
            "market_pnl_usd": _round_or_none(nav_points[-1].nav_usd - nav_points[0].nav_usd),
            "turnover_ratio": 0.0,
            "round_trip_ratio": 0.0,
            "net_accumulation_ratio": None,
        }

    gross_inflow_usd = 0.0
    gross_outflow_usd = 0.0
    round_trip_days = 0
    active_flow_days = 0
    average_nav = fmean(point.nav_usd for point in nav_points) if nav_points else 0.0
    significant_flow_threshold = average_nav * 0.02 if average_nav > 0 else 0.0

    for index in range(1, len(sorted_dates)):
        prev_date = sorted_dates[index - 1]
        curr_date = sorted_dates[index]
        prev_snapshot = balance_snapshots[prev_date]
        curr_snapshot = balance_snapshots[curr_date]

        inflow_usd = 0.0
        outflow_usd = 0.0

        for token in set(prev_snapshot.keys()) | set(curr_snapshot.keys()):
            delta = curr_snapshot.get(token, 0.0) - prev_snapshot.get(token, 0.0)
            if abs(delta) <= FLOW_TOLERANCE:
                continue

            price = price_lookup.get((token, curr_date)) or price_lookup.get((token, prev_date))
            if not price:
                continue

            delta_usd = delta * price.price_usd
            if delta_usd > 0:
                inflow_usd += delta_usd
            else:
                outflow_usd += abs(delta_usd)

        if inflow_usd > 0 or outflow_usd > 0:
            active_flow_days += 1
        if inflow_usd >= significant_flow_threshold and outflow_usd >= significant_flow_threshold:
            round_trip_days += 1

        gross_inflow_usd += inflow_usd
        gross_outflow_usd += outflow_usd

    net_flow_usd = gross_inflow_usd - gross_outflow_usd
    total_nav_change = nav_points[-1].nav_usd - nav_points[0].nav_usd
    market_pnl_usd = total_nav_change - net_flow_usd
    turnover_ratio = None if average_nav <= 0 else (gross_inflow_usd + gross_outflow_usd) / average_nav
    round_trip_ratio = None if active_flow_days == 0 else round_trip_days / active_flow_days
    net_accumulation_ratio = None if gross_inflow_usd <= 0 else net_flow_usd / gross_inflow_usd

    abs_market = abs(market_pnl_usd)
    abs_flow = abs(net_flow_usd)
    if abs_market > abs_flow * 1.5:
        return_driver = "market_appreciation"
    elif abs_flow > abs_market * 1.5:
        return_driver = "net_transfers"
    else:
        return_driver = "mixed"

    effective_trade_frequency = trade_frequency or 0.0
    if (round_trip_ratio or 0.0) >= 0.35 or (turnover_ratio or 0.0) >= 3.0:
        style = "transit"
    elif net_flow_usd > 0 and effective_trade_frequency <= 0.35 and (turnover_ratio or 0.0) <= 1.5:
        style = "accumulation"
    elif net_flow_usd < 0 and effective_trade_frequency <= 0.35 and (turnover_ratio or 0.0) <= 1.5:
        style = "distribution"
    elif effective_trade_frequency <= 0.2 and (turnover_ratio or 0.0) <= 0.6:
        style = "holding"
    else:
        style = "mixed"

    if style == "transit":
        summary = "此地址資金周轉偏高，較像中轉或換倉用途，不能直接視為長期屯幣。"
    elif style == "accumulation" and return_driver == "market_appreciation":
        summary = "此地址偏向累積持倉，且回報主要來自持幣後的市場上漲。"
    elif style == "accumulation":
        summary = "此地址偏向累積持倉，但淨值變化有相當一部分來自淨轉入。"
    elif style == "holding":
        summary = "此地址倉位變動不大，較接近長期持有型地址。"
    elif style == "distribution":
        summary = "此地址呈現逐步減倉，淨值表現不宜直接解讀為持幣回報。"
    else:
        summary = "此地址同時存在持幣與換倉行為，需搭配資金流解讀。"

    return {
        "style": style,
        "return_driver": return_driver,
        "summary": summary,
        "gross_inflow_usd": _round_or_none(gross_inflow_usd),
        "gross_outflow_usd": _round_or_none(gross_outflow_usd),
        "net_flow_usd": _round_or_none(net_flow_usd),
        "market_pnl_usd": _round_or_none(market_pnl_usd),
        "turnover_ratio": _round_or_none(turnover_ratio),
        "round_trip_ratio": _round_or_none(round_trip_ratio),
        "net_accumulation_ratio": _round_or_none(net_accumulation_ratio),
    }


def _build_meta(
    db: Session,
    address: str,
    source: str,
    used_cache: bool,
    runtime_seconds: float | None = None,
) -> dict[str, Any]:
    cache_age_minutes = None
    sync_state = db.scalar(select(AddressSyncState).where(AddressSyncState.address == address))
    if sync_state and sync_state.last_synced_at:
        synced_at = sync_state.last_synced_at
        if synced_at.tzinfo is None:
            synced_at = synced_at.replace(tzinfo=timezone.utc)
        cache_age_minutes = (datetime.now(timezone.utc) - synced_at).total_seconds() / 60

    return {
        "used_cache": used_cache,
        "cache_age_minutes": _round_or_none(cache_age_minutes, 2),
        "source": source,
        "runtime_seconds": _round_or_none(runtime_seconds, 3),
    }


def _build_interpretations(
    metrics: dict[str, Any],
    quality: dict[str, Any],
    behavior: dict[str, Any],
    meta: dict[str, Any],
) -> list[str]:
    notes: list[str] = []

    if meta["used_cache"]:
        notes.append("本次直接使用已同步資料，沒有重新查鏈上 API，因此回應會更快。")
    else:
        notes.append("本次包含鏈上同步與價格補齊，首次查詢時間通常會比重複查詢長。")

    notes.append(behavior["summary"])

    market_pnl = behavior.get("market_pnl_usd")
    net_flow = behavior.get("net_flow_usd")
    if market_pnl is not None and net_flow is not None:
        notes.append(
            f"淨值變動可拆成市場損益約 ${market_pnl:,.2f} 與淨轉入影響約 ${net_flow:,.2f}。"
        )

    turnover_ratio = behavior.get("turnover_ratio")
    round_trip_ratio = behavior.get("round_trip_ratio")
    if turnover_ratio is not None and round_trip_ratio is not None:
        notes.append(
            f"資金周轉比約 {turnover_ratio:.2f} 倍、同時轉入轉出日占比約 {round_trip_ratio:.2%}。"
        )

    if quality["missing_price_days"] > 0:
        notes.append(
            f"共有 {quality['missing_price_days']} 天存在缺價，績效解讀需保留折扣。"
        )
    elif metrics.get("trade_frequency") is not None:
        notes.append(
            f"交易頻率約 {metrics['trade_frequency']:.2%}，可作為是否長期持倉的輔助訊號。"
        )

    return notes


def _build_performance_response(
    db: Session,
    address: str,
    selected_tokens: list[str],
    nav_points: list[NavPointData],
    balance_snapshots: dict[date, dict[str, float]],
    price_lookup: dict[tuple[str, date], PriceRecord],
    price_sources: list[str],
    missing_price_days: int,
    source: str,
    used_cache: bool,
    runtime_seconds: float | None = None,
) -> dict[str, Any]:
    trade_frequency = calculate_trade_frequency(balance_snapshots)
    metrics = build_metrics(nav_points, trade_frequency=trade_frequency)
    quality_tags = _get_watchlist_quality_tags(db, address)
    quality = {
        "price_sources": price_sources,
        "missing_price_days": missing_price_days,
        **quality_tags,
    }
    behavior = _build_behavior_analysis(balance_snapshots, nav_points, price_lookup, trade_frequency)
    meta = _build_meta(
        db,
        address=address,
        source=source,
        used_cache=used_cache,
        runtime_seconds=runtime_seconds,
    )

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
        "quality": quality,
        "behavior": behavior,
        "interpretations": _build_interpretations(metrics, quality, behavior, meta),
        "meta": meta,
    }


def has_complete_cached_nav(
    db: Session,
    raw_address: str,
    start_date: date,
    end_date: date,
) -> bool:
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
    expected_days = (end_date - start_date).days + 1
    return len(nav_rows) == expected_days


def is_cached_sync_fresh(
    db: Session,
    raw_address: str,
    start_date: date,
    end_date: date,
) -> bool:
    address = normalize_address(raw_address)
    sync_state = db.scalar(select(AddressSyncState).where(AddressSyncState.address == address))
    if not sync_state or sync_state.last_status != "success" or not sync_state.last_synced_at:
        return False
    if sync_state.synced_start_date and sync_state.synced_start_date > start_date:
        return False
    if sync_state.synced_end_date and sync_state.synced_end_date < end_date:
        return False
    synced_at = sync_state.last_synced_at
    if synced_at.tzinfo is None:
        synced_at = synced_at.replace(tzinfo=timezone.utc)
    age_minutes = (datetime.now(timezone.utc) - synced_at).total_seconds() / 60
    return age_minutes <= settings.sync_cache_ttl_minutes and has_complete_cached_nav(
        db, address, start_date, end_date
    )


def recompute_address_performance(
    db: Session,
    raw_address: str,
    start_date: date,
    end_date: date,
    top_n_tokens: int,
    runtime_seconds: float | None = None,
    source: str = "recompute",
) -> dict[str, Any]:
    if end_date < start_date:
        raise ValueError("end_date must be greater than or equal to start_date")

    address = normalize_address(raw_address)
    holdings = _load_holdings(db, address, start_date, end_date)

    if not holdings:
        raise ValueError("no holdings found in date range")

    token_symbols = sorted({row.token_symbol.upper() for row in holdings})
    prices = _load_prices(db, token_symbols, start_date, end_date)
    price_lookup = _build_price_lookup(prices)
    selected_tokens = _select_tokens(holdings, price_lookup, top_n_tokens=top_n_tokens)
    holdings_by_date, balance_snapshots = _build_daily_holding_view(holdings, selected_tokens)
    nav_points, price_sources, missing_price_days = _compute_nav_curve(holdings_by_date, price_lookup)

    if not nav_points:
        raise ValueError("no holdings after token selection")

    _save_nav_curve(db, address, start_date, end_date, nav_points)
    return _build_performance_response(
        db,
        address=address,
        selected_tokens=selected_tokens,
        nav_points=nav_points,
        balance_snapshots=balance_snapshots,
        price_lookup=price_lookup,
        price_sources=price_sources,
        missing_price_days=missing_price_days,
        source=source,
        used_cache=False,
        runtime_seconds=runtime_seconds,
    )


def get_cached_address_performance(
    db: Session,
    raw_address: str,
    start_date: date,
    end_date: date,
    runtime_seconds: float | None = None,
    source: str = "cache",
) -> dict[str, Any]:
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

    holdings = _load_holdings(db, address, start_date, end_date)
    if not holdings:
        raise ValueError("no holdings found for cached nav")

    selected_tokens = sorted({row.token_symbol.upper() for row in holdings})
    balance_snapshots: dict[date, dict[str, float]] = defaultdict(dict)
    for row in holdings:
        balance_snapshots[row.date][row.token_symbol.upper()] = float(row.balance)

    prices = _load_prices(db, selected_tokens, start_date, end_date)
    price_lookup = _build_price_lookup(prices)

    price_source_set: set[str] = set()
    for row in nav_rows:
        if row.price_sources:
            price_source_set.update(row.price_sources)

    missing_price_days = sum(1 for row in nav_rows if row.missing_price_tokens > 0)
    return _build_performance_response(
        db,
        address=address,
        selected_tokens=selected_tokens,
        nav_points=nav_points,
        balance_snapshots=balance_snapshots,
        price_lookup=price_lookup,
        price_sources=sorted(price_source_set),
        missing_price_days=missing_price_days,
        source=source,
        used_cache=True,
        runtime_seconds=runtime_seconds,
    )
