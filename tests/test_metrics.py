from datetime import date, timedelta

from app.services.metrics import (
    NavPointData,
    build_metrics,
    calculate_max_drawdown,
    calculate_trade_frequency,
)


def _build_nav_points(values: list[float]) -> list[NavPointData]:
    start = date(2025, 1, 1)
    return [
        NavPointData(
            date=start + timedelta(days=index),
            nav_usd=value,
            missing_price_tokens=0,
            price_sources=["manual"],
        )
        for index, value in enumerate(values)
    ]


def test_max_drawdown_is_peak_to_trough() -> None:
    nav_points = _build_nav_points([100, 120, 90, 130])
    assert calculate_max_drawdown(nav_points) == -0.25


def test_trade_frequency_detects_position_change_days() -> None:
    snapshots = {
        date(2025, 1, 1): {"ETH": 1.0, "USDC": 1000},
        date(2025, 1, 2): {"ETH": 1.0, "USDC": 1000},
        date(2025, 1, 3): {"ETH": 0.8, "USDC": 1300},
        date(2025, 1, 4): {"ETH": 0.8, "USDC": 1300},
    }
    assert calculate_trade_frequency(snapshots) == 1 / 3


def test_build_metrics_contains_core_fields() -> None:
    nav_points = _build_nav_points([100, 105, 110, 108, 120])
    metrics = build_metrics(nav_points, trade_frequency=0.2)
    assert metrics["cagr"] is not None
    assert metrics["mdd"] == (108 / 110) - 1
    assert metrics["trade_frequency"] == 0.2
    assert metrics["max_single_day_drop"] == (108 / 110) - 1
    assert metrics["total_return"] == 0.2

