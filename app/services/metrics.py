from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date


@dataclass
class NavPointData:
    date: date
    nav_usd: float
    missing_price_tokens: int
    price_sources: list[str]


def _round_metric(value: float | None, digits: int = 12) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def calculate_daily_returns(nav_points: list[NavPointData]) -> list[float]:
    if len(nav_points) < 2:
        return []

    returns: list[float] = []
    for index in range(1, len(nav_points)):
        prev_nav = nav_points[index - 1].nav_usd
        curr_nav = nav_points[index].nav_usd
        if prev_nav <= 0:
            continue
        returns.append((curr_nav / prev_nav) - 1)
    return returns


def calculate_cagr(start_value: float, end_value: float, start_date: date, end_date: date) -> float | None:
    period_days = (end_date - start_date).days
    if period_days <= 0 or start_value <= 0 or end_value <= 0:
        return None
    return (end_value / start_value) ** (365 / period_days) - 1


def calculate_max_drawdown(nav_points: list[NavPointData]) -> float | None:
    if not nav_points:
        return None

    peak = nav_points[0].nav_usd
    max_drawdown = 0.0

    for point in nav_points:
        peak = max(peak, point.nav_usd)
        if peak <= 0:
            continue
        drawdown = (point.nav_usd / peak) - 1
        max_drawdown = min(max_drawdown, drawdown)
    return max_drawdown


def calculate_sharpe_ratio(daily_returns: list[float]) -> float | None:
    if len(daily_returns) < 2:
        return None

    volatility = statistics.stdev(daily_returns)
    if volatility == 0:
        return None

    mean_return = statistics.fmean(daily_returns)
    return (mean_return / volatility) * math.sqrt(365)


def calculate_trade_frequency(
    balance_snapshots: dict[date, dict[str, float]],
    tolerance: float = 1e-12,
) -> float | None:
    sorted_dates = sorted(balance_snapshots.keys())
    if len(sorted_dates) < 2:
        return None

    changed_days = 0
    compared_periods = 0

    for index in range(1, len(sorted_dates)):
        prev_snapshot = balance_snapshots[sorted_dates[index - 1]]
        curr_snapshot = balance_snapshots[sorted_dates[index]]
        tokens = set(prev_snapshot.keys()) | set(curr_snapshot.keys())

        compared_periods += 1
        has_change = any(
            abs(curr_snapshot.get(token, 0.0) - prev_snapshot.get(token, 0.0)) > tolerance
            for token in tokens
        )
        if has_change:
            changed_days += 1

    if compared_periods == 0:
        return None
    return changed_days / compared_periods


def build_metrics(nav_points: list[NavPointData], trade_frequency: float | None) -> dict[str, float | None]:
    if not nav_points:
        return {
            "cagr": None,
            "mdd": None,
            "sharpe": None,
            "trade_frequency": trade_frequency,
            "max_single_day_drop": None,
            "total_return": None,
        }

    daily_returns = calculate_daily_returns(nav_points)
    start_nav = nav_points[0].nav_usd
    end_nav = nav_points[-1].nav_usd

    total_return = None
    if start_nav > 0:
        total_return = (end_nav / start_nav) - 1

    return {
        "cagr": _round_metric(
            calculate_cagr(start_nav, end_nav, nav_points[0].date, nav_points[-1].date)
        ),
        "mdd": _round_metric(calculate_max_drawdown(nav_points)),
        "sharpe": _round_metric(calculate_sharpe_ratio(daily_returns)),
        "trade_frequency": _round_metric(trade_frequency),
        "max_single_day_drop": _round_metric(min(daily_returns) if daily_returns else None),
        "total_return": _round_metric(total_return),
    }
