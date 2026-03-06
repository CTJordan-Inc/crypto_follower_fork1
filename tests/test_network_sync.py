from datetime import date, datetime, timezone

from app.services.network_sync import build_daily_balances_from_events


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

