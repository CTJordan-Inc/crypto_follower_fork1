from datetime import date

import app.api.routes.performance as performance_routes


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
