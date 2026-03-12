from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, getcontext
import time as time_module
from typing import Any

import httpx
from sqlalchemy import and_, delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AddressDailyHolding, AddressSyncState, TokenDailyPrice, WatchlistAddress
from app.services.performance import (
    get_cached_address_performance,
    is_cached_sync_fresh,
    recompute_address_performance,
)
from app.utils.normalizers import normalize_address, normalize_token_symbol

settings = get_settings()

ETH_DECIMAL_FACTOR = Decimal(10) ** 18
BALANCE_EPSILON = Decimal("1e-18")
ETHERSCAN_RESULT_WINDOW_LIMIT = 10_000
ETHERSCAN_RATE_LIMIT_STATUS = 429
COINGECKO_BAD_REQUEST_STATUS = 400
COINGECKO_RATE_LIMIT_STATUS = 429
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


def _iter_chunks(items: list[str], chunk_size: int):
    safe_chunk_size = max(1, chunk_size)
    for index in range(0, len(items), safe_chunk_size):
        yield items[index : index + safe_chunk_size]


def _coingecko_headers() -> dict[str, str]:
    if not settings.coingecko_api_key:
        return {}
    return {settings.coingecko_api_key_header: settings.coingecko_api_key}


def _get_retry_wait_seconds(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return max(0.1, float(retry_after))
        except ValueError:
            pass
    return max(0.1, settings.coingecko_backoff_seconds * (2**attempt))


def _coingecko_get_json(
    client: httpx.Client,
    url: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    headers = _coingecko_headers()
    max_retries = max(0, settings.coingecko_max_retries)

    for attempt in range(max_retries + 1):
        if settings.coingecko_request_interval_seconds > 0:
            time_module.sleep(settings.coingecko_request_interval_seconds)

        response = client.get(url, params=params, headers=headers or None)

        if response.status_code == COINGECKO_RATE_LIMIT_STATUS or response.status_code >= 500:
            if attempt >= max_retries:
                response.raise_for_status()
            time_module.sleep(_get_retry_wait_seconds(response, attempt))
            continue

        if response.status_code == 404:
            return {}

        response.raise_for_status()
        return response.json()

    return {}


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


def _resolve_etherscan_pagination() -> tuple[int, int]:
    requested_offset = max(1, settings.etherscan_page_offset)
    safe_offset = min(requested_offset, ETHERSCAN_RESULT_WINDOW_LIMIT)

    max_pages_by_window = max(1, ETHERSCAN_RESULT_WINDOW_LIMIT // safe_offset)
    requested_pages = max(1, settings.etherscan_max_pages)
    safe_pages = min(requested_pages, max_pages_by_window)

    return safe_offset, safe_pages


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


def _is_etherscan_rate_limit_error(error_text: str) -> bool:
    lowered = error_text.lower()
    return (
        "max calls per sec rate limit reached" in lowered
        or "max rate limit reached" in lowered
        or "rate limit reached" in lowered
    )


def _etherscan_wait_seconds(attempt: int) -> float:
    return max(0.1, settings.etherscan_backoff_seconds * (2**attempt))


def _fetch_etherscan_events(client: httpx.Client, address: str, action: str) -> list[dict[str, Any]]:
    page = 1
    offset, page_limit = _resolve_etherscan_pagination()
    all_rows: list[dict[str, Any]] = []
    max_retries = max(0, settings.etherscan_max_retries)
    retry_count = 0

    while page <= page_limit:
        if settings.etherscan_request_interval_seconds > 0:
            time_module.sleep(settings.etherscan_request_interval_seconds)

        response = client.get(
            settings.etherscan_base_url,
            params=_etherscan_params(address=address, action=action, page=page, offset=offset),
        )

        if response.status_code == ETHERSCAN_RATE_LIMIT_STATUS or response.status_code >= 500:
            if retry_count >= max_retries:
                response.raise_for_status()
            time_module.sleep(_etherscan_wait_seconds(retry_count))
            retry_count += 1
            continue

        response.raise_for_status()
        payload = response.json()

        try:
            batch_rows = _parse_etherscan_payload(payload)
        except ValueError as error:
            error_text = str(error).lower()
            if "result window is too large" in error_text and offset > 1:
                offset = max(1, ETHERSCAN_RESULT_WINDOW_LIMIT // page)
                page_limit = min(page_limit, max(1, ETHERSCAN_RESULT_WINDOW_LIMIT // offset))
                continue

            if _is_etherscan_rate_limit_error(error_text):
                if retry_count >= max_retries:
                    raise ValueError(
                        "etherscan rate limit reached (3/sec); please retry, "
                        "or lower ETHERSCAN_MAX_PAGES / increase ETHERSCAN_REQUEST_INTERVAL_SECONDS"
                    ) from error
                time_module.sleep(_etherscan_wait_seconds(retry_count))
                retry_count += 1
                continue
            raise

        retry_count = 0

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


def _build_token_balance_reference(snapshots: dict[date, dict[str, float]]) -> dict[str, float]:
    reference: dict[str, float] = defaultdict(float)
    for balances in snapshots.values():
        for token_key, balance in balances.items():
            reference[token_key] = max(reference.get(token_key, 0.0), balance)
    return dict(reference)


def _fetch_spot_prices(
    client: httpx.Client,
    token_contracts: dict[str, str | None],
    token_reference: dict[str, float],
) -> dict[str, float]:
    spot_prices: dict[str, float] = {}

    if "ETH" in token_reference:
        payload = _coingecko_get_json(
            client,
            f"{settings.coingecko_base_url}/simple/price",
            {"ids": "ethereum", "vs_currencies": "usd"},
        )
        eth_price = payload.get("ethereum", {}).get("usd") if payload else None
        if eth_price is not None:
            spot_prices["ETH"] = float(eth_price)

    contract_to_tokens: dict[str, list[str]] = defaultdict(list)
    candidate_tokens = [token_key for token_key in token_reference if token_key != "ETH"]
    candidate_tokens.sort(key=lambda token_key: token_reference.get(token_key, 0.0), reverse=True)
    candidate_tokens = candidate_tokens[: max(1, settings.coingecko_spot_contract_limit)]

    for token_key in candidate_tokens:
        contract = token_contracts.get(token_key)
        if not contract:
            continue

        normalized_contract = contract.lower()
        if not (normalized_contract.startswith("0x") and len(normalized_contract) == 42):
            continue

        contract_to_tokens[normalized_contract].append(token_key)

    contracts = sorted(contract_to_tokens.keys())
    if not contracts:
        return spot_prices

    for chunk in _iter_chunks(contracts, settings.coingecko_contract_chunk_size):
        chunk_prices = _fetch_spot_prices_chunk(
            client=client,
            contracts=chunk,
            contract_to_tokens=contract_to_tokens,
        )
        spot_prices.update(chunk_prices)

    return spot_prices


def _fetch_spot_prices_chunk(
    client: httpx.Client,
    contracts: list[str],
    contract_to_tokens: dict[str, list[str]],
) -> dict[str, float]:
    if not contracts:
        return {}

    try:
        payload = _coingecko_get_json(
            client,
            f"{settings.coingecko_base_url}/simple/token_price/{settings.coingecko_platform}",
            {
                "contract_addresses": ",".join(contracts),
                "vs_currencies": "usd",
            },
        )
    except httpx.HTTPStatusError as error:
        if error.response.status_code == COINGECKO_BAD_REQUEST_STATUS:
            if len(contracts) == 1:
                return {}

            middle = len(contracts) // 2
            left_prices = _fetch_spot_prices_chunk(client, contracts[:middle], contract_to_tokens)
            right_prices = _fetch_spot_prices_chunk(client, contracts[middle:], contract_to_tokens)
            left_prices.update(right_prices)
            return left_prices
        raise

    if not payload:
        return {}

    chunk_prices: dict[str, float] = {}
    for contract, entry in payload.items():
        usd = entry.get("usd") if isinstance(entry, dict) else None
        if usd is None:
            continue

        for token_key in contract_to_tokens.get(contract.lower(), []):
            chunk_prices[token_key] = float(usd)

    return chunk_prices


def _select_tokens_for_price_fetch(
    token_reference: dict[str, float],
    spot_prices: dict[str, float],
    top_n_tokens: int,
) -> list[str]:
    tokens = set(token_reference.keys())
    stable_set = {symbol.upper() for symbol in settings.stablecoins}
    native_set = {symbol.upper() for symbol in settings.native_tokens}

    stable_tokens = sorted(token for token in tokens if token in stable_set)
    native_tokens = sorted(token for token in tokens if token in native_set and token not in stable_tokens)

    remaining = tokens - set(stable_tokens) - set(native_tokens)
    ranked_remaining = sorted(
        remaining,
        key=lambda token: (
            token_reference.get(token, 0.0) * spot_prices.get(token, 0.0),
            token_reference.get(token, 0.0),
            token,
        ),
        reverse=True,
    )

    selected = stable_tokens + native_tokens + ranked_remaining[: max(1, top_n_tokens)]
    return selected


def _build_stablecoin_price_map(selected_tokens: list[str], start_date: date, end_date: date) -> dict[str, dict[date, float]]:
    stable_set = {symbol.upper() for symbol in settings.stablecoins}
    price_map: dict[str, dict[date, float]] = {}

    for token_key in selected_tokens:
        if token_key not in stable_set:
            continue
        price_map[token_key] = {row_date: 1.0 for row_date in _iter_dates(start_date, end_date)}

    return price_map


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

    payload = _coingecko_get_json(client, url, _coingecko_range_params(start_date, end_date))
    if not payload:
        return {}
    return _extract_daily_prices(payload, start_date, end_date)


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
    selected_tokens: list[str],
) -> None:
    selected_set = set(selected_tokens)

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
            if token_key not in selected_set:
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


def _upsert_sync_state(
    db: Session,
    address: str,
    start_date: date,
    end_date: date,
    runtime_seconds: float,
    status: str,
    error: str | None = None,
) -> None:
    sync_state = db.scalar(select(AddressSyncState).where(AddressSyncState.address == address))
    if sync_state is None:
        sync_state = AddressSyncState(address=address, chain="ethereum")
        db.add(sync_state)

    if status == "success":
        sync_state.synced_start_date = start_date
        sync_state.synced_end_date = end_date
        sync_state.last_synced_at = datetime.now(timezone.utc)
        sync_state.last_runtime_seconds = runtime_seconds
        sync_state.last_status = status
        sync_state.last_error = None
    elif sync_state.last_status is None:
        sync_state.last_runtime_seconds = runtime_seconds
        sync_state.last_status = status
        sync_state.last_error = (error or "")[:255] or None

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
    started_at = time_module.perf_counter()

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

            token_reference = _build_token_balance_reference(snapshots)
            spot_prices = _fetch_spot_prices(client, token_contracts, token_reference)
            selected_tokens = _select_tokens_for_price_fetch(
                token_reference=token_reference,
                spot_prices=spot_prices,
                top_n_tokens=top_n_tokens,
            )

            price_map = _build_stablecoin_price_map(selected_tokens, start_date, end_date)
            rate_limited_tokens: list[str] = []

            for token_key in selected_tokens:
                if token_key in price_map:
                    continue

                try:
                    token_prices = _fetch_token_daily_prices(
                        client=client,
                        token_key=token_key,
                        contract=token_contracts.get(token_key),
                        start_date=start_date,
                        end_date=end_date,
                    )
                except httpx.HTTPStatusError as error:
                    if error.response.status_code == COINGECKO_RATE_LIMIT_STATUS:
                        rate_limited_tokens.append(token_key)
                        continue
                    raise

                if token_prices:
                    price_map[token_key] = token_prices

            if not price_map:
                detail = ""
                if rate_limited_tokens:
                    detail = f" rate-limited tokens: {', '.join(rate_limited_tokens[:5])}"
                raise ValueError(
                    "coingecko rate-limited this request and no price data is available; "
                    "please shorten the date range or set COINGECKO_API_KEY" + detail
                )

    except httpx.HTTPStatusError as error:
        _upsert_sync_state(
            db,
            address=address,
            start_date=start_date,
            end_date=end_date,
            runtime_seconds=time_module.perf_counter() - started_at,
            status="error",
            error=str(error),
        )
        if error.response.status_code == COINGECKO_RATE_LIMIT_STATUS:
            raise ValueError(
                "coingecko rate limit reached (HTTP 429); please wait and retry, "
                "or set COINGECKO_API_KEY"
            ) from error
        raise ValueError(f"network query failed: {error}") from error
    except httpx.HTTPError as error:
        _upsert_sync_state(
            db,
            address=address,
            start_date=start_date,
            end_date=end_date,
            runtime_seconds=time_module.perf_counter() - started_at,
            status="error",
            error=str(error),
        )
        raise ValueError(f"network query failed: {error}") from error
    except ValueError as error:
        _upsert_sync_state(
            db,
            address=address,
            start_date=start_date,
            end_date=end_date,
            runtime_seconds=time_module.perf_counter() - started_at,
            status="error",
            error=str(error),
        )
        raise ValueError(f"network query failed: {error}") from error

    _ensure_watchlist_entry(db, address)
    _replace_holdings(db, address, start_date, end_date, snapshots, token_contracts, selected_tokens)
    _replace_prices(db, start_date, end_date, price_map)
    runtime_seconds = time_module.perf_counter() - started_at
    _upsert_sync_state(
        db,
        address=address,
        start_date=start_date,
        end_date=end_date,
        runtime_seconds=runtime_seconds,
        status="success",
    )

    return recompute_address_performance(
        db,
        raw_address=address,
        start_date=start_date,
        end_date=end_date,
        top_n_tokens=top_n_tokens,
        runtime_seconds=runtime_seconds,
        source="network-sync",
    )


def load_or_sync_address_performance(
    db: Session,
    raw_address: str,
    start_date: date,
    end_date: date,
    top_n_tokens: int,
    refresh: bool = False,
) -> dict[str, Any]:
    address = normalize_address(raw_address)
    started_at = time_module.perf_counter()

    if not refresh and is_cached_sync_fresh(db, address, start_date, end_date):
        return get_cached_address_performance(
            db,
            raw_address=address,
            start_date=start_date,
            end_date=end_date,
            runtime_seconds=time_module.perf_counter() - started_at,
            source="cache-hit",
        )

    return sync_address_from_network_and_recompute(
        db,
        raw_address=address,
        start_date=start_date,
        end_date=end_date,
        top_n_tokens=top_n_tokens,
    )


def batch_load_or_sync_address_performance(
    db: Session,
    addresses: list[str],
    start_date: date,
    end_date: date,
    top_n_tokens: int,
    refresh: bool = False,
) -> dict[str, Any]:
    if len(addresses) > settings.batch_address_limit:
        raise ValueError(f"addresses cannot exceed {settings.batch_address_limit} items")

    results: list[dict[str, Any]] = []
    completed = 0
    failed = 0

    for raw_address in addresses:
        address = normalize_address(raw_address)
        try:
            payload = load_or_sync_address_performance(
                db,
                raw_address=address,
                start_date=start_date,
                end_date=end_date,
                top_n_tokens=top_n_tokens,
                refresh=refresh,
            )
            nav_end_usd = payload["nav_curve"][-1]["nav_usd"] if payload["nav_curve"] else None
            results.append(
                {
                    "address": address,
                    "success": True,
                    "nav_end_usd": nav_end_usd,
                    "total_return": payload["metrics"]["total_return"],
                    "cagr": payload["metrics"]["cagr"],
                    "sharpe": payload["metrics"]["sharpe"],
                    "behavior_style": payload["behavior"]["style"],
                    "return_driver": payload["behavior"]["return_driver"],
                    "explanation": payload["behavior"]["summary"],
                    "used_cache": payload["meta"]["used_cache"],
                    "runtime_seconds": payload["meta"]["runtime_seconds"],
                    "error": None,
                }
            )
            completed += 1
        except ValueError as error:
            results.append(
                {
                    "address": address,
                    "success": False,
                    "nav_end_usd": None,
                    "total_return": None,
                    "cagr": None,
                    "sharpe": None,
                    "behavior_style": None,
                    "return_driver": None,
                    "explanation": None,
                    "used_cache": None,
                    "runtime_seconds": None,
                    "error": str(error),
                }
            )
            failed += 1

    return {
        "start_date": start_date,
        "end_date": end_date,
        "requested": len(addresses),
        "completed": completed,
        "failed": failed,
        "results": results,
    }
