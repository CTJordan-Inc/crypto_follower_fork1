from datetime import date, datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.services.network_sync as network_sync
from app.models import AddressDailyHolding, Base
from app.services.network_sync import (
    _estimate_market_cap_from_snapshots,
    _build_token_active_dates,
    _build_stablecoin_price_map,
    _iter_chunks,
    _is_etherscan_rate_limit_error,
    _load_latest_holding_snapshot_before,
    _resolve_effective_end_date,
    _resolve_etherscan_pagination,
    _select_random_recent_blocks,
    _series_covers_active_dates,
    _select_tokens_for_price_fetch,
    _replace_holdings,
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


def test_select_random_recent_blocks_respects_window_and_sample_size() -> None:
    blocks = _select_random_recent_blocks(latest_block=1_000, block_window=20, sample_blocks=5)

    assert len(blocks) == 5
    assert blocks == sorted(blocks, reverse=True)
    assert all(980 <= block <= 1_000 for block in blocks)


def test_series_covers_active_dates_requires_all_held_days() -> None:
    active_dates = _build_token_active_dates(
        {
            date(2025, 1, 1): {"ETH": 1.0},
            date(2025, 1, 2): {"ETH": 1.2},
            date(2025, 1, 3): {},
        }
    )

    assert _series_covers_active_dates(
        {date(2025, 1, 1): 3200.0, date(2025, 1, 2): 3300.0},
        active_dates["ETH"],
    )
    assert not _series_covers_active_dates(
        {date(2025, 1, 1): 3200.0},
        active_dates["ETH"],
    )


def test_replace_holdings_persists_all_positive_tokens() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    with Session(engine) as session:
        _replace_holdings(
            db=session,
            address="0xabc",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 2),
            snapshots={
                date(2025, 1, 1): {"ETH": 1.0, "MEME": 5000.0},
                date(2025, 1, 2): {"ETH": 0.8, "MEME": 4000.0, "USDC": 1000.0},
            },
            token_contracts={"ETH": None, "MEME": "0xmeme", "USDC": "0xusdc"},
        )

        rows = list(
            session.scalars(
                select(AddressDailyHolding).order_by(
                    AddressDailyHolding.date.asc(),
                    AddressDailyHolding.token_symbol.asc(),
                )
            )
        )

        assert [row.token_symbol for row in rows] == ["ETH", "MEME", "ETH", "MEME", "USDC"]


def test_generate_random_recent_addresses_limits_unsaved_network_screening(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    screened_addresses: list[str] = []
    original_limit = settings.random_address_network_screen_limit
    original_budget = settings.random_address_screen_time_budget_seconds
    original_pages = settings.random_address_screen_max_pages

    try:
        settings.random_address_network_screen_limit = 2
        settings.random_address_screen_time_budget_seconds = 999
        settings.random_address_screen_max_pages = 1

        monkeypatch.setattr(
            network_sync,
            "_sample_recent_sender_addresses",
            lambda **kwargs: ["0xaaa", "0xbbb", "0xccc", "0xddd"],
        )
        monkeypatch.setattr(
            network_sync,
            "get_saved_analysis_snapshot_by_key",
            lambda *args, **kwargs: None,
        )

        def fake_estimate(**kwargs):
            screened_addresses.append(kwargs["address"])
            return 20_000.0

        monkeypatch.setattr(
            network_sync,
            "_estimate_address_market_cap_from_network",
            fake_estimate,
        )

        with Session(engine) as session:
            addresses = network_sync.generate_random_recent_addresses(
                db=session,
                count=3,
                start_date=date(2025, 1, 1),
                end_date=date(2025, 3, 31),
                top_n_tokens=10,
                min_market_cap_usd=10_000,
                market_cap_basis="max_nav",
                exclude_saved=True,
            )

        assert screened_addresses == ["0xaaa", "0xbbb"]
        assert addresses == ["0xaaa", "0xbbb"]
    finally:
        settings.random_address_network_screen_limit = original_limit
        settings.random_address_screen_time_budget_seconds = original_budget
        settings.random_address_screen_max_pages = original_pages


def test_build_daily_balances_can_start_from_initial_snapshot() -> None:
    snapshots, token_contracts = build_daily_balances_from_events(
        address="0xabc",
        start_date=date(2025, 1, 2),
        end_date=date(2025, 1, 3),
        eth_transactions=[],
        token_transfers=[
            {
                "timeStamp": _ts(date(2025, 1, 3)),
                "from": "0xsender",
                "to": "0xabc",
                "tokenSymbol": "USDC",
                "contractAddress": "0xusdc",
                "tokenDecimal": "6",
                "value": "500000000",
            }
        ],
        initial_balances={"ETH": 1.5},
        initial_token_contracts={"ETH": None},
    )

    assert token_contracts["ETH"] is None
    assert snapshots[date(2025, 1, 2)]["ETH"] == 1.5
    assert snapshots[date(2025, 1, 3)]["ETH"] == 1.5
    assert snapshots[date(2025, 1, 3)]["USDC"] == 500.0


def test_load_latest_holding_snapshot_before_returns_latest_day() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    with Session(engine) as session:
        _replace_holdings(
            db=session,
            address="0xabc",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 3),
            snapshots={
                date(2025, 1, 1): {"ETH": 1.0},
                date(2025, 1, 2): {"ETH": 1.2, "USDC": 100.0},
                date(2025, 1, 3): {"ETH": 1.4},
            },
            token_contracts={"ETH": None, "USDC": "0xusdc"},
        )

        snapshot = _load_latest_holding_snapshot_before(session, "0xabc", date(2025, 1, 3))

        assert snapshot is not None
        snapshot_date, balances, token_contracts = snapshot
        assert snapshot_date == date(2025, 1, 2)
        assert balances == {"ETH": 1.2, "USDC": 100.0}
        assert token_contracts == {"ETH": None, "USDC": "0xusdc"}


def test_resolve_effective_end_date_clamps_future_timestamp() -> None:
    effective_end_date, effective_end_timestamp = _resolve_effective_end_date(
        end_date=date(2025, 1, 20),
        now_dt=datetime(2025, 1, 10, 12, 0, tzinfo=timezone.utc),
    )

    assert effective_end_date == date(2025, 1, 10)
    assert effective_end_timestamp == int(datetime(2025, 1, 10, 12, 0, tzinfo=timezone.utc).timestamp())


def test_estimate_market_cap_from_snapshots_supports_max_and_average_basis() -> None:
    snapshots = {
        date(2025, 1, 1): {"ETH": 1.0, "USDC": 1000.0},
        date(2025, 1, 2): {"ETH": 2.0, "USDC": 500.0},
    }
    spot_prices = {"ETH": 3000.0}

    assert _estimate_market_cap_from_snapshots(snapshots, spot_prices, basis="max_nav") == 6500.0
    assert _estimate_market_cap_from_snapshots(snapshots, spot_prices, basis="average_nav") == 5250.0
