"""Estimate daily cost from monthly bills.

The portal's daily "dollar" view is really cubic feet, but its monthly view has the
real bill amounts. Each bill gives an effective rate (bill $ / billed HCF, which
spreads the fixed charge over usage); each day is priced at the rate of the bill
that covers it.

Bills come without period dates. Matching billed HCF against sums of daily reads
showed a bill labelled month M covers roughly the 11th of the previous month through
the 10th of M, so that is the assumed cycle. A day outside every bill (usually the
current, not-yet-billed period) uses the closest bill's rate.
"""

from __future__ import annotations

from datetime import date, timedelta

GALLONS_PER_HCF = 748.052
BILL_CYCLE_END_DAY = 10


def bill_month_for(day: date) -> date:
    """Return the month label (first of month) of the bill that covers ``day``."""
    shifted = day - timedelta(days=BILL_CYCLE_END_DAY)
    if shifted.month == 12:
        return date(shifted.year + 1, 1, 1)
    return date(shifted.year, shifted.month + 1, 1)


def bill_rates(bill_hcf: dict[date, float], bill_dollars: dict[date, float]) -> dict[date, float]:
    """Return $/HCF per bill month, skipping bills with no usage or no amount."""
    return {
        month: bill_dollars[month] / hcf
        for month, hcf in bill_hcf.items()
        if hcf > 0 and bill_dollars.get(month, 0) > 0
    }


def rate_for(day: date, rates: dict[date, float]) -> float | None:
    """Return the $/HCF for ``day``: its own bill's rate, else the closest bill's."""
    if not rates:
        return None
    month = bill_month_for(day)
    if month in rates:
        return rates[month]
    closest = min(rates, key=lambda m: (abs((m - month).days), -m.toordinal()))
    return rates[closest]


def estimate_daily_cost(gallons: dict[date, float], rates: dict[date, float]) -> dict[date, float]:
    """Return estimated dollars per day for each day of ``gallons``."""
    result: dict[date, float] = {}
    for day, value in gallons.items():
        rate = rate_for(day, rates)
        if rate is not None:
            result[day] = round(value / GALLONS_PER_HCF * rate, 2)
    return result
