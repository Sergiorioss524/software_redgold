"""Computes the daily adjustment applied on top of the previous gold quote."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

from redgold.storage import PriceHistory, StoredQuote


@dataclass(frozen=True)
class DailyAdjustment:
    quote_date: date
    source: str
    price_usd_per_oz: float
    previous_price_usd_per_oz: Optional[float]
    change_usd: Optional[float]
    change_pct: Optional[float]
    # 1.0 = no change, 1.02 = priced up 2%, 0.98 = priced down 2%.
    adjustment_factor: float


def compute_daily_adjustment(history: PriceHistory, quote: StoredQuote) -> DailyAdjustment:
    """Compare today's quote against the most recent prior quote from the
    same source and derive the day-over-day adjustment factor.

    On the very first recorded day for a source there is no prior price to
    compare against, so the adjustment is neutral (factor = 1.0).
    """
    previous = history.latest_quote_before(quote.quote_date, quote.source)

    if previous is None:
        adjustment = DailyAdjustment(
            quote_date=quote.quote_date,
            source=quote.source,
            price_usd_per_oz=quote.price_usd_per_oz,
            previous_price_usd_per_oz=None,
            change_usd=None,
            change_pct=None,
            adjustment_factor=1.0,
        )
    else:
        change_usd = quote.price_usd_per_oz - previous.price_usd_per_oz
        change_pct = change_usd / previous.price_usd_per_oz
        adjustment = DailyAdjustment(
            quote_date=quote.quote_date,
            source=quote.source,
            price_usd_per_oz=quote.price_usd_per_oz,
            previous_price_usd_per_oz=previous.price_usd_per_oz,
            change_usd=change_usd,
            change_pct=change_pct,
            adjustment_factor=1.0 + change_pct,
        )

    history.save_adjustment(
        quote_date=adjustment.quote_date,
        source=adjustment.source,
        price=adjustment.price_usd_per_oz,
        previous_price=adjustment.previous_price_usd_per_oz,
        change_usd=adjustment.change_usd,
        change_pct=adjustment.change_pct,
        adjustment_factor=adjustment.adjustment_factor,
        computed_at=datetime.now(timezone.utc),
    )
    return adjustment
