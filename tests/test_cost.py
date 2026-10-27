"""Tests for the cost estimate."""

from __future__ import annotations

from datetime import date

import pytest

from custom_components.newport_news_water.api import parse_monthly
from custom_components.newport_news_water.cost import (
    GALLONS_PER_HCF,
    bill_month_for,
    bill_rates,
    estimate_daily_cost,
    rate_for,
)


@pytest.mark.parametrize(
    ("day", "bill"),
    [
        (date(2026, 9, 11), date(2026, 10, 1)),
        (date(2026, 10, 10), date(2026, 10, 1)),
        (date(2026, 10, 11), date(2026, 11, 1)),
        (date(2026, 12, 11), date(2027, 1, 1)),
        (date(2027, 1, 5), date(2027, 1, 1)),
    ],
)
def test_bill_month_for(day: date, bill: date) -> None:
    """A bill labelled M covers the 11th of the previous month to the 10th of M."""
    assert bill_month_for(day) == bill


def test_rates_and_fallback() -> None:
    """Rates come from bill $/HCF; unbilled days use the closest bill, newest on ties."""
    rates = bill_rates(
        {date(2026, 8, 1): 4.0, date(2026, 9, 1): 2.0, date(2026, 10, 1): 0.0},
        {date(2026, 8, 1): 30.0, date(2026, 9, 1): 25.0, date(2026, 10, 1): 22.0},
    )
    assert rates == {date(2026, 8, 1): 7.5, date(2026, 9, 1): 12.5}
    assert rate_for(date(2026, 8, 5), rates) == 7.5
    assert rate_for(date(2026, 10, 20), rates) == 12.5  # after the last bill
    assert rate_for(date(2025, 1, 1), rates) == 7.5  # before the first bill
    assert rate_for(date(2026, 1, 1), {}) is None


def test_estimate_daily_cost() -> None:
    """Gallons are converted to HCF and priced at the covering bill's rate."""
    rates = {date(2026, 9, 1): 10.0}
    cost = estimate_daily_cost(
        {date(2026, 8, 20): GALLONS_PER_HCF / 2, date(2026, 8, 21): 0}, rates
    )
    assert cost == {date(2026, 8, 20): 5.0, date(2026, 8, 21): 0.0}
    assert estimate_daily_cost({date(2026, 8, 20): 100}, {}) == {}


def test_parse_monthly() -> None:
    """Monthly rows are keyed by bill month; bad rows are skipped."""
    assert parse_monthly(
        {
            "objUsageGenerationResultSetTwo": [
                {"Month": "10", "Year": "2026", "UsageValue": "30.81"},
                {"Month": "9", "Year": "2026", "UsageValue": "3.11"},
                {"Month": "x", "Year": "2026", "UsageValue": "1"},
                {"Month": "8", "Year": "2026", "UsageValue": None},
            ]
        }
    ) == {date(2026, 10, 1): 30.81, date(2026, 9, 1): 3.11}
    assert parse_monthly(None) == {}
