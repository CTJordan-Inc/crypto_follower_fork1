from datetime import date

from app.models import AddressDailyHolding
from app.services.performance import _build_daily_holding_view


def test_build_daily_holding_view_keeps_zero_holding_days() -> None:
    holdings = [
        AddressDailyHolding(address="0xabc", date=date(2025, 1, 1), token_symbol="ETH", balance=1.0),
        AddressDailyHolding(address="0xabc", date=date(2025, 1, 3), token_symbol="ETH", balance=2.0),
    ]

    holdings_by_date, balance_snapshots = _build_daily_holding_view(
        holdings,
        selected_tokens=["ETH"],
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 3),
    )

    assert list(sorted(holdings_by_date.keys())) == [
        date(2025, 1, 1),
        date(2025, 1, 2),
        date(2025, 1, 3),
    ]
    assert balance_snapshots[date(2025, 1, 2)] == {}
