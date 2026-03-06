from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, getcontext
from typing import Any

import httpx
from sqlalchemy import and_, delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AddressDailyHolding, TokenDailyPrice, WatchlistAddress
from app.services.performance import recompute_address_performance
from app.utils.normalizers import normalize_address, normalize_token_symbol

settings = get_settings()

ETH_DECIMAL_FACTOR = Decimal(10) ** 18
BALANCE_EPSILON = Decimal("1e-18")
getcontext().prec = 50


def _safe_decimal(raw_value: str | int | None) -> Decimal:
    if raw_value is None:
        return Decimal(0)
    try:
        return Decimal(str(raw_value))
    except (InvalidOperation, ValueError):
        return Decimal(0)


def _ts_to_date(raw_ts: str | int) -> date:
    try:
        ts_value = int(raw_ts)
    except (TypeError, ValueError):
        ts_value = 0
    return datetime.fromtimestamp(ts_value, tz=timezone.utc).date()


def _iter_dates(start_date: date, end_date: date):
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def _build_token_key(
    symbol: str,
    contract: str,
    primary_contract_by_symbol: dict[str, str],
) -> str:
    if symbol not in primary_contract_by_symbol:
        primary_contract_by_symbol[symbol] = contract
        return symbol

    if primary_contract_by_symbol[symbol] == contract:
        return symbol

    if contract.startswith("0x") and len(contract) >= 8:
        suffix = contract[2:8].upper()
    else:
        suffix = "ALT"
    return f"{symbol}_{suffix}"


def _etherscan_params(address: str, action: str, page: int, offset: int) -> dict[str, Any]:
    params: dict[str, Any] = {
        "chainid": settings.etherscan_chain_id,
        "module": "account",
        "action": action,
        "address": address,
        "startblock": 0,
        "endblock": 99_999_999,
        "page": page,
        "offset": offset,
        "sort": "asc",
    }

    if settings.etherscan_api_key:
        params["apikey"] = settings.etherscan_api_key
    return params


def _parse_etherscan_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    status = str(payload.get("status", ""))
    result = payload.get("result")
    message = str(payload.get("message", ""))

    if status == "1" and isinstance(result, list):
        return result

    if status == "0" and isinstance(result, list) and not result:
        return []

    text = f"{message} {result}".lower()
    if "no transactions found" in text:
        return []

    raise ValueError(f"etherscan request failed: {message} {result}")


def _fetch_etherscan_events(client: httpx.Client, address: str, action: str) -> list[dict[str, Any]]:
    page = 1
    offset = 10_000
    all_rows: list[dict[str, Any]] = []

    while page <= max(1, settings.etherscan_max_pages):
        response = client.get(
            settings.etherscan_base_url,
            params=_etherscan_params(address=address, action=action, page=page, offset=offset),
        )
        response.raise_for_status()
        batch_rows = _parse_etherscan_payload(response.json())
        if not batch_rows:
            break

        all_rows.extend(batch_rows)
        if len(batch_rows) < offset:
            break
        page += 1

    return all_rows


def build_daily_balances_from_events(
    address: str,
    start_date: date,
    end_date: date,
    eth_transactions: list[dict[str, Any]],
    token_transfers: list[dict[str, Any]],
) -> tuple[dict[date, dict[str, float]], dict[str, str | None]]:
    normalized_address = normalize_address(address)

    daily_deltas: dict[date, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    token_contracts: dict[str, str | None] = {"ETH": None}
    primary_contract_by_symbol: dict[str, str] = {}

    for tx in eth_transactions:
        event_date = _ts_to_date(tx.get("timeStamp", 0))
        if event_date > end_date:
            continue

        from_address = normalize_address(tx.get("from", ""))
        to_address = normalize_address(tx.get("to", ""))
        is_error = str(tx.get("isError", "0")) == "1"

        value_eth = _safe_decimal(tx.get("value")) / ETH_DECIMAL_FACTOR
        gas_eth = (
            _safe_decimal(tx.get("gasUsed")) * _safe_decimal(tx.get("gasPrice"))
        ) / ETH_DECIMAL_FACTOR

        if from_address == normalized_address:
            daily_deltas[event_date]["ETH"] -= gas_eth
            if not is_error:
                daily_deltas[event_date]["ETH"] -= value_eth

        if to_address == normalized_address and not is_error:
            daily_deltas[event_date]["ETH"] += value_eth

    for transfer in token_transfers:
        event_date = _ts_to_date(transfer.get("timeStamp", 0))
        if event_date > end_date:
            continue

        symbol = normalize_token_symbol(transfer.get("tokenSymbol", "UNKNOWN"))
        contract = normalize_address(transfer.get("contractAddress", ""))
        token_key = _build_token_key(symbol, contract, primary_contract_by_symbol)

        decimals = int(transfer.get("tokenDecimal") or 0)
        scale = Decimal(10) ** max(decimals, 0)
        amount = _safe_decimal(transfer.get("value")) / scale

        from_address = normalize_address(transfer.get("from", ""))
        to_address = normalize_address(transfer.get("to", ""))

        if from_address == normalized_address:
            daily_deltas[event_date][token_key] -= amount
        if to_address == normalized_address:
            daily_deltas[event_date][token_key] += amount

        token_contracts[token_key] = contract

    balance_state: dict[str, Decimal] = defaultdict(Decimal)

    for delta_date in sorted(d for d in daily_deltas.keys() if d < start_date):
        for token_key, delta in daily_deltas[delta_date].items():
            balance_state[token_key] += delta
            if abs(balance_state[token_key]) <= BALANCE_EPSILON:
                balance_state.pop(token_key, None)

    snapshots: dict[date, dict[str, float]] = {}

    for current_date in _iter_dates(start_date, end_date):
        for token_key, delta in daily_deltas.get(current_date, {}).items():
            balance_state[token_key] += delta
            if abs(balance_state[token_key]) <= BALANCE_EPSILON:
                balance_state.pop(token_key, None)

        snapshots[current_date] = {
            token_key: float(balance)
            for token_key, balance in balance_state.items()
            if balance > BALANCE_EPSILON
        }

    return snapshots, token_contracts


def _coingecko_range_params(start_date: date, end_date: date) -> dict[str, Any]:
    from_dt = datetime.combine(start_date - timedelta(days=1), time.min, tzinfo=timezone.utc)
    to_dt = datetime.combine(end_date + timedelta(days=1), time.max, tzinfo=timezone.utc)
    return {
        "vs_currency": "usd",
        "from": int(from_dt.timestamp()),
        "to": int(to_dt.timestamp()),
    }


def _extract_daily_prices(
    payload: dict[str, Any],
    start_date: date,
    end_date: date,
) -> dict[date, float]:
    rows = payload.get("prices", [])
    raw_daily: dict[date, float] = {}

    for row in rows:
        if not isinstance(row, list) or len(row) < 2:
            continue
        timestamp_ms, raw_price = row[0], row[1]
        price_date = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).date()
        if start_date <= price_date <= end_date:
            raw_daily[price_date] = float(raw_price)

    filled_daily: dict[date, float] = {}
    last_price: float | None = None

    for current_date in _iter_dates(start_date, end_date):
        if current_date in raw_daily:
            last_price = raw_daily[current_date]
        if last_price is not None:
            filled_daily[current_date] = last_price

    return filled_daily


def _fetch_token_daily_prices(
    client: httpx.Client,
    token_key: str,
    contract: str | None,
    start_date: date,
    end_date: date,
) -> dict[date, float]:
    if token_key == "ETH":
        url = f"{settings.coingecko_base_url}/coins/ethereum/market_chart/range"
    elif contract:
        url = (
            f"{settings.coingecko_base_url}/coins/{settings.coingecko_platform}/contract/"
            f"{contract}/market_chart/range"
        )
    else:
        return {}

    response = client.get(url, params=_coingecko_range_params(start_date, end_date))

    if response.status_code == 404:
        return {}

    response.raise_for_status()
    return _extract_daily_prices(response.json(), start_date, end_date)


def _ensure_watchlist_entry(db: Session, address: str) -> None:
    existing = db.scalar(select(WatchlistAddress).where(WatchlistAddress.address == address))
    if existing:
        return

    db.add(
        WatchlistAddress(
            address=address,
            chain="ethereum",
            label=None,
            label_source="etherscan",
            is_exchange=False,
            is_contract=False,
        )
    )
    db.commit()


def _replace_holdings(
    db: Session,
    address: str,
    start_date: date,
    end_date: date,
    snapshots: dict[date, dict[str, float]],
    token_contracts: dict[str, str | None],
) -> None:
    db.execute(
        delete(AddressDailyHolding).where(
            and_(
                AddressDailyHolding.address == address,
                AddressDailyHolding.date >= start_date,
                AddressDailyHolding.date <= end_date,
            )
        )
    )

    for row_date, balances in snapshots.items():
        for token_key, balance in balances.items():
            if balance <= 0:
                continue

            db.add(
                AddressDailyHolding(
                    address=address,
                    chain="ethereum",
                    date=row_date,
                    token_symbol=token_key,
                    token_contract=token_contracts.get(token_key),
                    balance=balance,
                )
            )

    db.commit()


def _replace_prices(
    db: Session,
    start_date: date,
    end_date: date,
    price_map: dict[str, dict[date, float]],
) -> None:
    token_keys = sorted(price_map.keys())
    if not token_keys:
        return

    db.execute(
        delete(TokenDailyPrice).where(
            and_(
                TokenDailyPrice.token_symbol.in_(token_keys),
                TokenDailyPrice.source == "coingecko",
                TokenDailyPrice.date >= start_date,
                TokenDailyPrice.date <= end_date,
            )
        )
    )

    for token_key, series in price_map.items():
        for row_date, price_usd in series.items():
            db.add(
                TokenDailyPrice(
                    token_symbol=token_key,
                    date=row_date,
                    price_usd=price_usd,
                    source="coingecko",
                )
            )

    db.commit()


def sync_address_from_network_and_recompute(
    db: Session,
    raw_address: str,
    start_date: date,
    end_date: date,
    top_n_tokens: int,
) -> dict[str, Any]:
    if end_date < start_date:
        raise ValueError("end_date must be greater than or equal to start_date")

    if not settings.etherscan_api_key:
        raise ValueError("ETHERSCAN_API_KEY is required for network query")

    address = normalize_address(raw_address)

    try:
        with httpx.Client(timeout=settings.http_timeout_seconds) as client:
            eth_transactions = _fetch_etherscan_events(client, address, action="txlist")
            token_transfers = _fetch_etherscan_events(client, address, action="tokentx")

            snapshots, token_contracts = build_daily_balances_from_events(
                address=address,
                start_date=start_date,
                end_date=end_date,
                eth_transactions=eth_transactions,
                token_transfers=token_transfers,
            )

            if not snapshots:
                raise ValueError("network query returned no snapshots")

            token_keys = sorted({token for balances in snapshots.values() for token in balances})
            if not token_keys:
                raise ValueError("network query found no non-zero balances in this time range")

            price_map: dict[str, dict[date, float]] = {}
            for token_key in token_keys:
                token_prices = _fetch_token_daily_prices(
                    client=client,
                    token_key=token_key,
                    contract=token_contracts.get(token_key),
                    start_date=start_date,
                    end_date=end_date,
                )
                if token_prices:
                    price_map[token_key] = token_prices

            if not price_map:
                raise ValueError("network query found balances but no price data")

    except httpx.HTTPError as error:
        raise ValueError(f"network query failed: {error}") from error

    _ensure_watchlist_entry(db, address)
    _replace_holdings(db, address, start_date, end_date, snapshots, token_contracts)
    _replace_prices(db, start_date, end_date, price_map)

    return recompute_address_performance(
        db,
        raw_address=address,
        start_date=start_date,
        end_date=end_date,
        top_n_tokens=top_n_tokens,
    )
