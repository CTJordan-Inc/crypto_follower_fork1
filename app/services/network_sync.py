from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, getcontext
import random
import time as time_module
from typing import Any

import httpx
from sqlalchemy import and_, delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AddressDailyHolding, AddressSyncState, TokenDailyPrice, WatchlistAddress
from app.services.analysis_store import (
    get_saved_addresses,
    get_saved_analysis_snapshot_by_key,
    save_analysis_snapshot,
)
from app.services.performance import (
    get_cached_address_performance,
    is_cached_sync_fresh,
    recompute_address_performance,
    resolve_market_cap_from_response,
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


def _etherscan_params(
    address: str,
    action: str,
    page: int,
    offset: int,
    start_block: int = 0,
    end_block: int = 99_999_999,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "chainid": settings.etherscan_chain_id,
        "module": "account",
        "action": action,
        "address": address,
        "startblock": start_block,
        "endblock": end_block,
        "page": page,
        "offset": offset,
        "sort": "asc",
    }

    if settings.etherscan_api_key:
        params["apikey"] = settings.etherscan_api_key
    return params


def _etherscan_proxy_params(action: str, **extra: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "chainid": settings.etherscan_chain_id,
        "module": "proxy",
        "action": action,
    }
    params.update(extra)
    if settings.etherscan_api_key:
        params["apikey"] = settings.etherscan_api_key
    return params


def _select_random_recent_blocks(
    latest_block: int,
    block_window: int,
    sample_blocks: int,
) -> list[int]:
    earliest_block = max(0, latest_block - max(0, block_window))
    candidates = list(range(earliest_block, latest_block + 1))
    if not candidates:
        return []

    target_size = min(len(candidates), max(1, sample_blocks))
    selected = random.sample(candidates, k=target_size)
    selected.sort(reverse=True)
    return selected


def _resolve_effective_end_date(
    end_date: date,
    now_dt: datetime | None = None,
) -> tuple[date, int]:
    current_dt = now_dt or datetime.now(timezone.utc)
    effective_end_date = min(end_date, current_dt.date())
    effective_end_dt = datetime.combine(effective_end_date, time.max, tzinfo=timezone.utc)
    effective_end_timestamp = int(min(effective_end_dt, current_dt).timestamp())
    return effective_end_date, effective_end_timestamp


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


def _etherscan_proxy_json(client: httpx.Client, action: str, **extra: Any) -> dict[str, Any]:
    max_retries = max(0, settings.etherscan_max_retries)
    retry_count = 0

    while True:
        if settings.etherscan_request_interval_seconds > 0:
            time_module.sleep(settings.etherscan_request_interval_seconds)

        response = client.get(
            settings.etherscan_base_url,
            params=_etherscan_proxy_params(action, **extra),
        )

        if response.status_code == ETHERSCAN_RATE_LIMIT_STATUS or response.status_code >= 500:
            if retry_count >= max_retries:
                response.raise_for_status()
            time_module.sleep(_etherscan_wait_seconds(retry_count))
            retry_count += 1
            continue

        response.raise_for_status()
        payload = response.json()
        error_payload = payload.get("error")
        if error_payload:
            error_text = str(error_payload)
            if _is_etherscan_rate_limit_error(error_text):
                if retry_count >= max_retries:
                    raise ValueError(
                        "etherscan rate limit reached (3/sec); please retry, "
                        "or increase ETHERSCAN_REQUEST_INTERVAL_SECONDS"
                    )
                time_module.sleep(_etherscan_wait_seconds(retry_count))
                retry_count += 1
                continue
            raise ValueError(f"etherscan proxy request failed: {error_text}")

        return payload


def _etherscan_block_json(client: httpx.Client, timestamp: int, closest: str) -> dict[str, Any]:
    max_retries = max(0, settings.etherscan_max_retries)
    retry_count = 0

    while True:
        if settings.etherscan_request_interval_seconds > 0:
            time_module.sleep(settings.etherscan_request_interval_seconds)

        response = client.get(
            settings.etherscan_base_url,
            params={
                "chainid": settings.etherscan_chain_id,
                "module": "block",
                "action": "getblocknobytime",
                "timestamp": timestamp,
                "closest": closest,
                **({"apikey": settings.etherscan_api_key} if settings.etherscan_api_key else {}),
            },
        )

        if response.status_code == ETHERSCAN_RATE_LIMIT_STATUS or response.status_code >= 500:
            if retry_count >= max_retries:
                response.raise_for_status()
            time_module.sleep(_etherscan_wait_seconds(retry_count))
            retry_count += 1
            continue

        response.raise_for_status()
        payload = response.json()
        result = str(payload.get("result", ""))
        message = str(payload.get("message", ""))
        if payload.get("status") != "1":
            error_text = f"{message} {result}"
            if _is_etherscan_rate_limit_error(error_text):
                if retry_count >= max_retries:
                    raise ValueError(
                        "etherscan rate limit reached (3/sec); please retry, "
                        "or increase ETHERSCAN_REQUEST_INTERVAL_SECONDS"
                    )
                time_module.sleep(_etherscan_wait_seconds(retry_count))
                retry_count += 1
                continue
            raise ValueError(f"etherscan block lookup failed: {error_text}")

        return payload


def _get_block_number_by_timestamp(
    client: httpx.Client,
    timestamp: int,
    closest: str,
) -> int:
    payload = _etherscan_block_json(client, timestamp, closest)
    result = payload.get("result")
    return int(result)


def _resolve_block_range(
    client: httpx.Client,
    start_date: date,
    end_date: date,
) -> tuple[int, int]:
    current_date = datetime.now(timezone.utc).date()
    if start_date > current_date:
        raise ValueError("start_date cannot be in the future")
    start_timestamp = int(datetime.combine(start_date, time.min, tzinfo=timezone.utc).timestamp())
    _, end_timestamp = _resolve_effective_end_date(end_date)
    start_block = _get_block_number_by_timestamp(client, start_timestamp, "after")
    end_block = _get_block_number_by_timestamp(client, end_timestamp, "before")
    return max(0, start_block), max(0, end_block)


def _fetch_etherscan_events(
    client: httpx.Client,
    address: str,
    action: str,
    start_block: int = 0,
    end_block: int = 99_999_999,
    max_pages: int | None = None,
) -> list[dict[str, Any]]:
    page = 1
    offset, page_limit = _resolve_etherscan_pagination()
    if max_pages is not None:
        page_limit = min(page_limit, max(1, max_pages))
    all_rows: list[dict[str, Any]] = []
    max_retries = max(0, settings.etherscan_max_retries)
    retry_count = 0

    while page <= page_limit:
        if settings.etherscan_request_interval_seconds > 0:
            time_module.sleep(settings.etherscan_request_interval_seconds)

        response = client.get(
            settings.etherscan_base_url,
            params=_etherscan_params(
                address=address,
                action=action,
                page=page,
                offset=offset,
                start_block=start_block,
                end_block=end_block,
            ),
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


def _load_latest_holding_snapshot_before(
    db: Session,
    address: str,
    before_date: date,
) -> tuple[date, dict[str, float], dict[str, str | None]] | None:
    snapshot_date = db.scalar(
        select(AddressDailyHolding.date)
        .where(
            and_(
                AddressDailyHolding.address == address,
                AddressDailyHolding.date < before_date,
            )
        )
        .order_by(AddressDailyHolding.date.desc())
        .limit(1)
    )

    if snapshot_date is None:
        return None

    rows = list(
        db.scalars(
            select(AddressDailyHolding)
            .where(
                and_(
                    AddressDailyHolding.address == address,
                    AddressDailyHolding.date == snapshot_date,
                )
            )
            .order_by(AddressDailyHolding.token_symbol.asc())
        )
    )
    if not rows:
        return None

    balances = {row.token_symbol.upper(): float(row.balance) for row in rows}
    token_contracts = {row.token_symbol.upper(): row.token_contract for row in rows}
    token_contracts.setdefault("ETH", None)
    return snapshot_date, balances, token_contracts


def build_daily_balances_from_events(
    address: str,
    start_date: date,
    end_date: date,
    eth_transactions: list[dict[str, Any]],
    token_transfers: list[dict[str, Any]],
    initial_balances: dict[str, float] | None = None,
    initial_token_contracts: dict[str, str | None] | None = None,
    initial_snapshot_date: date | None = None,
) -> tuple[dict[date, dict[str, float]], dict[str, str | None]]:
    normalized_address = normalize_address(address)

    daily_deltas: dict[date, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    token_contracts: dict[str, str | None] = {"ETH": None}
    if initial_token_contracts:
        token_contracts.update(initial_token_contracts)
    primary_contract_by_symbol: dict[str, str] = {}
    for token_key, contract in token_contracts.items():
        if token_key == "ETH" or not contract:
            continue
        if "_" in token_key:
            continue
        primary_contract_by_symbol[token_key] = contract

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
    if initial_balances:
        for token_key, balance in initial_balances.items():
            if balance > 0:
                balance_state[token_key] = Decimal(str(balance))

    if initial_balances and initial_snapshot_date is not None:
        for delta_date in sorted(
            delta for delta in daily_deltas.keys() if initial_snapshot_date < delta < start_date
        ):
            for token_key, delta in daily_deltas[delta_date].items():
                balance_state[token_key] += delta
                if abs(balance_state[token_key]) <= BALANCE_EPSILON:
                    balance_state.pop(token_key, None)
    elif not initial_balances:
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


def _build_token_active_dates(snapshots: dict[date, dict[str, float]]) -> dict[str, set[date]]:
    active_dates: dict[str, set[date]] = defaultdict(set)
    for row_date, balances in snapshots.items():
        for token_key, balance in balances.items():
            if balance > 0:
                active_dates[token_key].add(row_date)
    return dict(active_dates)


def _estimate_market_cap_from_snapshots(
    snapshots: dict[date, dict[str, float]],
    spot_prices: dict[str, float],
    basis: str,
) -> float | None:
    if not snapshots:
        return None

    stable_set = {symbol.upper() for symbol in settings.stablecoins}
    nav_values: list[float] = []
    for balances in snapshots.values():
        nav_value = 0.0
        for token_key, balance in balances.items():
            if token_key in stable_set:
                nav_value += balance
                continue

            spot_price = spot_prices.get(token_key)
            if spot_price is None:
                continue
            nav_value += balance * spot_price
        nav_values.append(nav_value)

    if not nav_values:
        return None
    if basis == "average_nav":
        return round(sum(nav_values) / len(nav_values), 2)
    return round(max(nav_values), 2)


def _load_recent_cached_coingecko_prices(
    db: Session,
    token_keys: list[str],
    as_of_date: date,
) -> dict[str, float]:
    if not token_keys:
        return {}

    rows = list(
        db.scalars(
            select(TokenDailyPrice)
            .where(
                and_(
                    TokenDailyPrice.token_symbol.in_(token_keys),
                    TokenDailyPrice.source == "coingecko",
                    TokenDailyPrice.date <= as_of_date,
                )
            )
            .order_by(TokenDailyPrice.date.desc(), TokenDailyPrice.token_symbol.asc())
        )
    )

    latest_prices: dict[str, float] = {}
    for row in rows:
        token_key = row.token_symbol.upper()
        if token_key not in latest_prices:
            latest_prices[token_key] = float(row.price_usd)
    return latest_prices


def _load_cached_coingecko_price_series(
    db: Session,
    token_keys: list[str],
    start_date: date,
    end_date: date,
) -> dict[str, dict[date, float]]:
    if not token_keys:
        return {}

    rows = list(
        db.scalars(
            select(TokenDailyPrice)
            .where(
                and_(
                    TokenDailyPrice.token_symbol.in_(token_keys),
                    TokenDailyPrice.source == "coingecko",
                    TokenDailyPrice.date >= start_date,
                    TokenDailyPrice.date <= end_date,
                )
            )
            .order_by(TokenDailyPrice.token_symbol.asc(), TokenDailyPrice.date.asc())
        )
    )

    price_map: dict[str, dict[date, float]] = defaultdict(dict)
    for row in rows:
        price_map[row.token_symbol.upper()][row.date] = float(row.price_usd)
    return dict(price_map)


def _series_covers_active_dates(series: dict[date, float], active_dates: set[date]) -> bool:
    if not active_dates:
        return True
    if not series:
        return False
    return active_dates.issubset(series.keys())


def _fetch_spot_prices(
    db: Session,
    client: httpx.Client,
    token_contracts: dict[str, str | None],
    token_reference: dict[str, float],
    as_of_date: date,
) -> dict[str, float]:
    spot_prices = _load_recent_cached_coingecko_prices(
        db=db,
        token_keys=sorted(token_reference.keys()),
        as_of_date=as_of_date,
    )

    if "ETH" in token_reference and "ETH" not in spot_prices:
        payload = _coingecko_get_json(
            client,
            f"{settings.coingecko_base_url}/simple/price",
            {"ids": "ethereum", "vs_currencies": "usd"},
        )
        eth_price = payload.get("ethereum", {}).get("usd") if payload else None
        if eth_price is not None:
            spot_prices["ETH"] = float(eth_price)

    contract_to_tokens: dict[str, list[str]] = defaultdict(list)
    candidate_tokens = [
        token_key for token_key in token_reference if token_key != "ETH" and token_key not in spot_prices
    ]
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
    else:
        sync_state.last_runtime_seconds = runtime_seconds
        sync_state.last_status = status
        sync_state.last_error = (error or "")[:255] or None

    db.commit()


def _load_network_snapshots(
    db: Session,
    client: httpx.Client,
    address: str,
    start_date: date,
    end_date: date,
) -> tuple[date, dict[date, dict[str, float]], dict[str, str | None]]:
    effective_end_date, _ = _resolve_effective_end_date(end_date)
    if effective_end_date < start_date:
        raise ValueError("start_date cannot be in the future")

    base_snapshot = _load_latest_holding_snapshot_before(db, address, start_date)
    initial_balances: dict[str, float] | None = None
    initial_token_contracts: dict[str, str | None] | None = None
    snapshot_date: date | None = None

    if base_snapshot is not None:
        snapshot_date, balances, token_contracts = base_snapshot
        initial_balances = balances
        initial_token_contracts = token_contracts

    if base_snapshot is not None:
        next_date = snapshot_date + timedelta(days=1)
        if next_date <= effective_end_date:
            start_block, end_block = _resolve_block_range(client, next_date, effective_end_date)
            should_fetch_events = True
        else:
            start_block, end_block = 0, 0
            should_fetch_events = False
    else:
        _, end_block = _resolve_block_range(client, start_date, effective_end_date)
        start_block = 0
        should_fetch_events = True

    if should_fetch_events and start_block <= end_block:
        eth_transactions = _fetch_etherscan_events(
            client,
            address,
            action="txlist",
            start_block=start_block,
            end_block=end_block,
        )
        token_transfers = _fetch_etherscan_events(
            client,
            address,
            action="tokentx",
            start_block=start_block,
            end_block=end_block,
        )
    else:
        eth_transactions = []
        token_transfers = []

    snapshots, token_contracts = build_daily_balances_from_events(
        address=address,
        start_date=start_date,
        end_date=effective_end_date,
        eth_transactions=eth_transactions,
        token_transfers=token_transfers,
        initial_balances=initial_balances,
        initial_token_contracts=initial_token_contracts,
        initial_snapshot_date=snapshot_date if base_snapshot is not None else None,
    )
    return effective_end_date, snapshots, token_contracts


def _sample_recent_sender_addresses(
    db: Session,
    target_count: int,
    exclude_saved: bool = True,
) -> list[str]:
    if not settings.etherscan_api_key:
        raise ValueError("ETHERSCAN_API_KEY is required for random address generation")

    excluded_addresses = get_saved_addresses(db) if exclude_saved else set()
    sampled_addresses: list[str] = []
    seen = set(excluded_addresses)

    with httpx.Client(timeout=settings.http_timeout_seconds) as client:
        latest_block_payload = _etherscan_proxy_json(client, "eth_blockNumber")
        latest_block = int(latest_block_payload["result"], 16)
        candidate_blocks = _select_random_recent_blocks(
            latest_block=latest_block,
            block_window=settings.random_address_block_window,
            sample_blocks=settings.random_address_sample_blocks,
        )

        for block_number in candidate_blocks:
            block_payload = _etherscan_proxy_json(
                client,
                "eth_getBlockByNumber",
                tag=hex(block_number),
                boolean="true",
            )
            block = block_payload.get("result") or {}
            transactions = list(block.get("transactions") or [])
            random.shuffle(transactions)

            for transaction in transactions:
                from_address = normalize_address(transaction.get("from", ""))
                if not from_address or from_address in seen:
                    continue
                seen.add(from_address)
                sampled_addresses.append(from_address)
                if len(sampled_addresses) >= target_count:
                    return sampled_addresses

    return sampled_addresses


def _estimate_address_market_cap_from_network(
    db: Session,
    client: httpx.Client,
    address: str,
    start_date: date,
    end_date: date,
    basis: str,
    screening_max_pages: int | None = None,
) -> float | None:
    if screening_max_pages is None:
        effective_end_date, snapshots, token_contracts = _load_network_snapshots(
            db=db,
            client=client,
            address=address,
            start_date=start_date,
            end_date=end_date,
        )
    else:
        effective_end_date, _ = _resolve_effective_end_date(end_date)
        if effective_end_date < start_date:
            raise ValueError("start_date cannot be in the future")

        start_block, end_block = _resolve_block_range(client, start_date, effective_end_date)
        eth_transactions = _fetch_etherscan_events(
            client,
            address,
            action="txlist",
            start_block=start_block,
            end_block=end_block,
            max_pages=screening_max_pages,
        )
        token_transfers = _fetch_etherscan_events(
            client,
            address,
            action="tokentx",
            start_block=start_block,
            end_block=end_block,
            max_pages=screening_max_pages,
        )
        snapshots, token_contracts = build_daily_balances_from_events(
            address=address,
            start_date=start_date,
            end_date=effective_end_date,
            eth_transactions=eth_transactions,
            token_transfers=token_transfers,
        )

    if not snapshots:
        return None

    token_reference = _build_token_balance_reference(snapshots)
    if not token_reference:
        return None

    spot_prices = _fetch_spot_prices(
        db=db,
        client=client,
        token_contracts=token_contracts,
        token_reference=token_reference,
        as_of_date=effective_end_date,
    )
    return _estimate_market_cap_from_snapshots(snapshots, spot_prices, basis=basis)


def generate_random_recent_addresses(
    db: Session,
    count: int,
    start_date: date,
    end_date: date,
    top_n_tokens: int,
    min_market_cap_usd: float = 0,
    market_cap_basis: str = "max_nav",
    exclude_saved: bool = True,
) -> list[str]:
    target_count = min(max(1, count), settings.batch_address_limit)
    basis = market_cap_basis if market_cap_basis in {"max_nav", "average_nav"} else "max_nav"
    candidate_count = target_count
    if min_market_cap_usd > 0:
        candidate_count = min(
            settings.batch_address_limit * settings.random_address_candidate_multiplier,
            target_count * settings.random_address_candidate_multiplier,
        )

    candidate_addresses = _sample_recent_sender_addresses(
        db=db,
        target_count=max(target_count, candidate_count),
        exclude_saved=exclude_saved,
    )

    if min_market_cap_usd <= 0:
        return candidate_addresses[:target_count]

    qualified_addresses: list[str] = []
    screening_started_at = time_module.perf_counter()
    network_screened = 0
    with httpx.Client(timeout=settings.http_timeout_seconds) as client:
        for address in candidate_addresses:
            if (
                time_module.perf_counter() - screening_started_at
                > settings.random_address_screen_time_budget_seconds
            ):
                break

            saved_snapshot = get_saved_analysis_snapshot_by_key(
                db,
                address=address,
                start_date=start_date,
                end_date=end_date,
                top_n_tokens=top_n_tokens,
            )
            if saved_snapshot is not None:
                market_cap_usd = resolve_market_cap_from_response(
                    saved_snapshot.payload or {},
                    basis=basis,
                )
            else:
                if network_screened >= settings.random_address_network_screen_limit:
                    continue
                network_screened += 1
                try:
                    market_cap_usd = _estimate_address_market_cap_from_network(
                        db=db,
                        client=client,
                        address=address,
                        start_date=start_date,
                        end_date=end_date,
                        basis=basis,
                        screening_max_pages=settings.random_address_screen_max_pages,
                    )
                except ValueError:
                    continue

            if market_cap_usd is None or market_cap_usd < min_market_cap_usd:
                continue

            qualified_addresses.append(address)
            if len(qualified_addresses) >= target_count:
                break

    return qualified_addresses


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
            effective_end_date, snapshots, token_contracts = _load_network_snapshots(
                db=db,
                client=client,
                address=address,
                start_date=start_date,
                end_date=end_date,
            )

            if not snapshots:
                raise ValueError("network query returned no snapshots")

            token_keys = sorted({token for balances in snapshots.values() for token in balances})
            if not token_keys:
                raise ValueError("network query found no non-zero balances in this time range")

            token_reference = _build_token_balance_reference(snapshots)
            token_active_dates = _build_token_active_dates(snapshots)
            spot_prices = _fetch_spot_prices(
                db,
                client,
                token_contracts,
                token_reference,
                as_of_date=effective_end_date,
            )
            selected_tokens = _select_tokens_for_price_fetch(
                token_reference=token_reference,
                spot_prices=spot_prices,
                top_n_tokens=top_n_tokens,
            )

            price_map = _build_stablecoin_price_map(selected_tokens, start_date, effective_end_date)
            cached_price_map = _load_cached_coingecko_price_series(
                db=db,
                token_keys=[token_key for token_key in selected_tokens if token_key not in price_map],
                start_date=start_date,
                end_date=effective_end_date,
            )
            rate_limited_tokens: list[str] = []

            for token_key in selected_tokens:
                if token_key in price_map:
                    continue

                cached_series = cached_price_map.get(token_key, {})
                if _series_covers_active_dates(
                    cached_series,
                    token_active_dates.get(token_key, set()),
                ):
                    price_map[token_key] = cached_series
                    continue

                merged_series = dict(cached_series)

                try:
                    token_prices = _fetch_token_daily_prices(
                        client=client,
                        token_key=token_key,
                        contract=token_contracts.get(token_key),
                        start_date=start_date,
                        end_date=effective_end_date,
                    )
                except httpx.HTTPStatusError as error:
                    if error.response.status_code == COINGECKO_RATE_LIMIT_STATUS:
                        rate_limited_tokens.append(token_key)
                        if merged_series:
                            price_map[token_key] = merged_series
                        continue
                    raise

                if token_prices:
                    merged_series.update(token_prices)
                if merged_series:
                    price_map[token_key] = merged_series

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
    _replace_holdings(db, address, start_date, effective_end_date, snapshots, token_contracts)
    _replace_prices(db, start_date, effective_end_date, price_map)
    runtime_seconds = time_module.perf_counter() - started_at
    _upsert_sync_state(
        db,
        address=address,
        start_date=start_date,
        end_date=effective_end_date,
        runtime_seconds=runtime_seconds,
        status="success",
    )

    return recompute_address_performance(
        db,
        raw_address=address,
        start_date=start_date,
        end_date=effective_end_date,
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
        payload = get_cached_address_performance(
            db,
            raw_address=address,
            start_date=start_date,
            end_date=end_date,
            top_n_tokens=top_n_tokens,
            runtime_seconds=time_module.perf_counter() - started_at,
            source="cache-hit",
        )
        save_analysis_snapshot(db, payload, top_n_tokens=top_n_tokens)
        return payload

    payload = sync_address_from_network_and_recompute(
        db,
        raw_address=address,
        start_date=start_date,
        end_date=end_date,
        top_n_tokens=top_n_tokens,
    )
    save_analysis_snapshot(db, payload, top_n_tokens=top_n_tokens)
    return payload


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
                    "market_cap_usd": payload["meta"].get("address_market_cap_usd"),
                    "market_cap_basis": payload["meta"].get("address_market_cap_basis"),
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
                    "market_cap_usd": None,
                    "market_cap_basis": None,
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
