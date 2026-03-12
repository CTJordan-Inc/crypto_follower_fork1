from datetime import date, datetime, timezone

from app.services.network_sync import (
    _build_stablecoin_price_map,
    _iter_chunks,
    _is_etherscan_rate_limit_error,
    _resolve_etherscan_pagination,
    _select_tokens_for_price_fetch,
    build_daily_balances_from_events,
    settings,
)


def _ts(value: date) -> int:
    return int(datetime(value.year, value.month, value.day, tzinfo=timezone.utc).timestamp())


def test_build_daily_balances_tracks_eth_and_gas() -> None:
    address = "0xabc"
    start_date = date(2025, 1, 1)
    end_date = date(2025, 1, 3)

    eth_transactions = [
        {
            "timeStamp": _ts(date(2025, 1, 1)),
            "from": "0xsender",
            "to": address,
            "value": str(10**18),
            "gasUsed": "0",
            "gasPrice": "0",
            "isError": "0",
        },
        {
            "timeStamp": _ts(date(2025, 1, 2)),
            "from": address,
            "to": "0xreceiver",
            "value": str(int(0.4 * 10**18)),
            "gasUsed": "21000",
            "gasPrice": str(int(500 * 10**9)),
            "isError": "0",
        },
    ]

    snapshots, token_contracts = build_daily_balances_from_events(
        address=address,
        start_date=start_date,
        end_date=end_date,
        eth_transactions=eth_transactions,
        token_transfers=[],
    )

    assert token_contracts["ETH"] is None
    assert snapshots[date(2025, 1, 1)]["ETH"] == 1.0
    assert round(snapshots[date(2025, 1, 2)]["ETH"], 6) == 0.5895
    assert round(snapshots[date(2025, 1, 3)]["ETH"], 6) == 0.5895


def test_build_daily_balances_handles_same_symbol_different_contracts() -> None:
    address = "0xabc"
    start_date = date(2025, 1, 1)
    end_date = date(2025, 1, 2)
    contract_a = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    contract_b = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

    token_transfers = [
        {
            "timeStamp": _ts(date(2025, 1, 1)),
            "from": "0xsender",
            "to": address,
            "tokenSymbol": "USDC",
            "contractAddress": contract_a,
            "tokenDecimal": "6",
            "value": "100000000",
        },
        {
            "timeStamp": _ts(date(2025, 1, 2)),
            "from": "0xsender",
            "to": address,
            "tokenSymbol": "USDC",
            "contractAddress": contract_b,
            "tokenDecimal": "6",
            "value": "50000000",
        },
    ]

    snapshots, token_contracts = build_daily_balances_from_events(
        address=address,
        start_date=start_date,
        end_date=end_date,
        eth_transactions=[],
        token_transfers=token_transfers,
    )

    assert token_contracts["USDC"] == contract_a
    assert token_contracts["USDC_BBBBBB"] == contract_b
    assert snapshots[date(2025, 1, 1)]["USDC"] == 100.0
    assert snapshots[date(2025, 1, 2)]["USDC"] == 100.0
    assert snapshots[date(2025, 1, 2)]["USDC_BBBBBB"] == 50.0


def test_resolve_etherscan_pagination_obeys_window_limit() -> None:
    original_max_pages = settings.etherscan_max_pages
    original_offset = settings.etherscan_page_offset

    try:
        settings.etherscan_max_pages = 5
        settings.etherscan_page_offset = 10_000
        offset, pages = _resolve_etherscan_pagination()
        assert offset == 10_000
        assert pages == 1

        settings.etherscan_max_pages = 99
        settings.etherscan_page_offset = 1_000
        offset, pages = _resolve_etherscan_pagination()
        assert offset == 1_000
        assert pages == 10
    finally:
        settings.etherscan_max_pages = original_max_pages
        settings.etherscan_page_offset = original_offset


def test_select_tokens_for_price_fetch_uses_stable_native_and_top_value() -> None:
    token_reference = {
        "ETH": 1.0,
        "USDC": 5000.0,
        "AAA": 10.0,
        "BBB": 5.0,
        "CCC": 100.0,
    }
    spot_prices = {
        "ETH": 3000.0,
        "USDC": 1.0,
        "AAA": 2.0,
        "BBB": 100.0,
        "CCC": 0.1,
    }

    selected = _select_tokens_for_price_fetch(token_reference, spot_prices, top_n_tokens=2)
    assert "USDC" in selected
    assert "ETH" in selected
    assert "BBB" in selected
    assert "AAA" in selected
    assert "CCC" not in selected


def test_build_stablecoin_price_map_sets_peg_to_one() -> None:
    price_map = _build_stablecoin_price_map(
        selected_tokens=["USDC", "ETH"],
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 3),
    )
    assert "USDC" in price_map
    assert len(price_map["USDC"]) == 3
    assert set(price_map["USDC"].values()) == {1.0}
    assert "ETH" not in price_map


def test_detect_etherscan_rate_limit_error_text() -> None:
    assert _is_etherscan_rate_limit_error(
        "etherscan request failed: NOTOK Max calls per sec rate limit reached (3/sec)"
    )
    assert _is_etherscan_rate_limit_error("rate limit reached")
    assert not _is_etherscan_rate_limit_error("invalid api key")


def test_iter_chunks_splits_sequence_evenly() -> None:
    chunks = list(_iter_chunks(["a", "b", "c", "d", "e"], 2))
    assert chunks == [["a", "b"], ["c", "d"], ["e"]]
