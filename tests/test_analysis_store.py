from datetime import date

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.models import Base, SavedAnalysisSnapshot
from app.services.analysis_store import (
    get_saved_addresses,
    list_saved_analysis_summaries,
    list_saved_analysis_snapshots,
    save_analysis_snapshot,
)


def _build_payload(address: str, total_return: float) -> dict:
    return {
        "address": address,
        "start_date": date(2025, 1, 1),
        "end_date": date(2025, 1, 31),
        "selected_tokens": ["ETH", "USDC"],
        "nav_curve": [
            {
                "date": date(2025, 1, 1),
                "nav_usd": 100_000,
                "missing_price_tokens": 0,
                "price_sources": ["coingecko"],
            },
            {
                "date": date(2025, 1, 31),
                "nav_usd": 120_000,
                "missing_price_tokens": 0,
                "price_sources": ["coingecko"],
            },
        ],
        "metrics": {
            "cagr": 0.24,
            "mdd": -0.05,
            "sharpe": 1.1,
            "trade_frequency": 0.2,
            "max_single_day_drop": -0.03,
            "total_return": total_return,
        },
        "quality": {
            "price_sources": ["coingecko"],
            "label_source": "manual",
            "suspected_exchange": False,
            "suspected_contract": False,
            "missing_price_days": 0,
        },
        "behavior": {
            "style": "holding",
            "return_driver": "market_appreciation",
            "summary": "test summary",
            "gross_inflow_usd": 0,
            "gross_outflow_usd": 0,
            "net_flow_usd": 0,
            "market_pnl_usd": 20_000,
            "turnover_ratio": 0.1,
            "round_trip_ratio": 0,
            "net_accumulation_ratio": 0.8,
        },
        "interpretations": ["test"],
        "meta": {
            "used_cache": False,
            "cache_age_minutes": None,
            "source": "network-sync",
            "runtime_seconds": 1.2,
        },
    }


def test_save_analysis_snapshot_upserts_same_address_range() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    with Session(engine) as session:
        first = save_analysis_snapshot(
            session,
            _build_payload("0xABC", total_return=0.2),
            top_n_tokens=10,
        )
        second = save_analysis_snapshot(
            session,
            _build_payload("0xabc", total_return=0.35),
            top_n_tokens=10,
        )

        count = session.scalar(select(func.count()).select_from(SavedAnalysisSnapshot))
        saved = list_saved_analysis_snapshots(session, limit=10)

        assert first.id == second.id
        assert count == 1
        assert len(saved) == 1
        assert saved[0]["address"] == "0xabc"
        assert saved[0]["total_return"] == 0.35


def test_get_saved_addresses_returns_normalized_values() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    with Session(engine) as session:
        save_analysis_snapshot(session, _build_payload("0xABC", total_return=0.2), top_n_tokens=10)
        save_analysis_snapshot(session, _build_payload("0xDEF", total_return=0.1), top_n_tokens=20)

        saved_addresses = get_saved_addresses(session)

        assert saved_addresses == {"0xabc", "0xdef"}


def test_list_saved_analysis_summaries_can_sort_by_market_cap() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    with Session(engine) as session:
        high = _build_payload("0xAAA", total_return=0.2)
        high["nav_curve"][-1]["nav_usd"] = 200_000
        low = _build_payload("0xBBB", total_return=0.3)
        low["nav_curve"][-1]["nav_usd"] = 80_000

        save_analysis_snapshot(session, high, top_n_tokens=10)
        save_analysis_snapshot(session, low, top_n_tokens=10)

        items = list_saved_analysis_summaries(
            session,
            limit=10,
            sort_by="market_cap_usd",
            sort_order="desc",
        )

        assert [item["address"] for item in items] == ["0xaaa", "0xbbb"]
