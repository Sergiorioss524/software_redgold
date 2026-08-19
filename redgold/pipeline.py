"""Orchestrates one daily run: fetch -> validate -> store -> adjust."""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from redgold import config
from redgold.adjustment import DailyAdjustment, compute_daily_adjustment
from redgold.sources.base import GoldPriceSource, GoldPriceUnavailableError
from redgold.sources.bcb import BCBGoldPriceSource
from redgold.sources.netdania import NetdaniaGoldPriceSource
from redgold.storage import PriceHistory

logger = logging.getLogger(__name__)

DEFAULT_SOURCES: list[GoldPriceSource] = [
    BCBGoldPriceSource(),
    NetdaniaGoldPriceSource(),
]


def run_daily_update(
    history: Optional[PriceHistory] = None,
    sources: Optional[list[GoldPriceSource]] = None,
    quote_date: Optional[date] = None,
) -> DailyAdjustment:
    """Try each configured source in priority order and record the first
    plausible quote. Returns the computed day-over-day adjustment.

    Raises GoldPriceUnavailableError if every source fails.
    """
    history = history or PriceHistory()
    sources = sources if sources is not None else DEFAULT_SOURCES
    quote_date = quote_date or date.today()

    errors: list[str] = []
    for source in sources:
        if not source.cfg.enabled:
            continue
        try:
            quote = source.fetch()
        except GoldPriceUnavailableError as exc:
            logger.warning("Source %s unavailable: %s", source.name, exc)
            errors.append(f"{source.name}: {exc}")
            continue
        except Exception as exc:  # network errors, HTTP errors, etc.
            logger.warning("Source %s failed: %s", source.name, exc)
            errors.append(f"{source.name}: {exc}")
            continue

        history.save_quote(
            quote_date=quote_date,
            source=quote.source,
            price=quote.price_usd_per_oz,
            fetched_at=quote.fetched_at,
            raw_text=quote.raw_text,
        )
        stored = history.get_quote(quote_date, quote.source)
        assert stored is not None
        adjustment = compute_daily_adjustment(history, stored)
        logger.info(
            "Recorded %s gold price for %s: %.2f USD/oz (factor=%.4f)",
            quote.source, quote_date, quote.price_usd_per_oz, adjustment.adjustment_factor,
        )
        return adjustment

    raise GoldPriceUnavailableError(
        "All configured sources failed: " + "; ".join(errors)
    )
