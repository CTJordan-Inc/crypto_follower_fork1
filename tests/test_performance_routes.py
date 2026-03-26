from datetime import date

import pytest
from fastapi import BackgroundTasks, HTTPException

import app.api.routes.performance as performance_routes
from app.schemas import BatchRecomputeRequest


def test_get_random_addresses_defers_market_cap_filter_to_batch_results(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_generate_random_recent_addresses(db, **kwargs):
        captured["db"] = db
        captured.update(kwargs)
        return ["0xabc"]

    monkeypatch.setattr(
        performance_routes,
        "generate_random_recent_addresses",
        fake_generate_random_recent_addresses,
    )

    response = performance_routes.get_random_addresses(
        count=50,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 3, 31),
        top_n_tokens=10,
        min_market_cap_usd=10_000,
        market_cap_basis="average_nav",
        exclude_saved=True,
        db=None,
    )

    assert captured["min_market_cap_usd"] == 0
    assert response["min_market_cap_usd"] == 10_000
    assert response["market_cap_basis"] == "average_nav"
    assert response["filter_stage"] == "batch-results"


class _FakeHoldingsDB:
    def __init__(self, holders: list[str]) -> None:
        self.holders = {address.lower() for address in holders}

    def scalar(self, query: str) -> str | None:
        return query if query in self.holders else None


def test_enqueue_batch_job_filters_addresses_without_holdings(monkeypatch) -> None:
    monkeypatch.setattr(
        performance_routes,
        "_build_holdings_query",
        lambda address, start_date, end_date: address,
    )

    captured: dict[str, list[str]] = {}

    class DummyJob:
        def __init__(self, addresses: list[str]) -> None:
            self.id = 12
            self.status = "pending"
            self.total_addresses = len(addresses)

    def fake_create_batch_job(
        db,
        addresses,
        start_date,
        end_date,
        top_n_tokens,
        market_cap_basis,
        refresh,
    ) -> DummyJob:
        captured["addresses"] = addresses
        return DummyJob(addresses)

    monkeypatch.setattr(performance_routes, "create_batch_job", fake_create_batch_job)

    payload = BatchRecomputeRequest(
        addresses=["0xAbC", "0xEmpty"],
        start_date=date(2025, 6, 1),
        end_date=date(2025, 6, 5),
        filter_empty_holdings=True,
    )
    response = performance_routes.enqueue_batch_job(
        payload,
        background_tasks=BackgroundTasks(),
        db=_FakeHoldingsDB(holders=["0xabc"]),
    )

    assert captured["addresses"] == ["0xabc"]
    assert response["requested"] == 1
    assert response["filtered_addresses"] == ["0xempty"]


def test_enqueue_batch_job_errors_if_all_addresses_filtered(monkeypatch) -> None:
    monkeypatch.setattr(
        performance_routes,
        "_build_holdings_query",
        lambda address, start_date, end_date: address,
    )

    payload = BatchRecomputeRequest(
        addresses=["0xAbC", "0xEmpty"],
        start_date=date(2025, 6, 1),
        end_date=date(2025, 6, 5),
        filter_empty_holdings=True,
    )

    with pytest.raises(HTTPException) as excinfo:
        performance_routes.enqueue_batch_job(
            payload,
            background_tasks=BackgroundTasks(),
            db=_FakeHoldingsDB(holders=[]),
        )

    assert excinfo.value.status_code == 400
    assert "no addresses with holdings" in excinfo.value.detail
