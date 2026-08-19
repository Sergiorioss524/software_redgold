"""Gold price history (reference price only), backed by Postgres in
production or a local SQLite file in dev/tests -- see redgold/db.py."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Union

from sqlalchemy import select

from redgold import config
from redgold.db import daily_adjustments, gold_prices, make_engine


@dataclass(frozen=True)
class StoredQuote:
    quote_date: date
    source: str
    price_usd_per_oz: float
    fetched_at: datetime
    raw_text: str


class PriceHistory:
    def __init__(self, database_url: Optional[Union[str, Path]] = None):
        if isinstance(database_url, Path):
            database_url = f"sqlite:///{database_url}"
        self.engine = make_engine(database_url or config.DATABASE_URL)

    def save_quote(self, quote_date: date, source: str, price: float,
                    fetched_at: datetime, raw_text: str) -> None:
        values = dict(
            quote_date=quote_date.isoformat(),
            source=source,
            price_usd_per_oz=price,
            fetched_at=fetched_at.isoformat(),
            raw_text=raw_text,
        )
        with self.engine.begin() as conn:
            exists = conn.execute(
                select(gold_prices.c.quote_date).where(
                    gold_prices.c.quote_date == values["quote_date"],
                    gold_prices.c.source == source,
                )
            ).first()
            if exists:
                conn.execute(
                    gold_prices.update()
                    .where(
                        gold_prices.c.quote_date == values["quote_date"],
                        gold_prices.c.source == source,
                    )
                    .values(**values)
                )
            else:
                conn.execute(gold_prices.insert().values(**values))

    def get_quote(self, quote_date: date, source: str) -> Optional[StoredQuote]:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(gold_prices).where(
                    gold_prices.c.quote_date == quote_date.isoformat(),
                    gold_prices.c.source == source,
                )
            ).first()
        return _row_to_quote(row) if row else None

    def latest_quote_before(self, quote_date: date, source: str) -> Optional[StoredQuote]:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(gold_prices)
                .where(
                    gold_prices.c.source == source,
                    gold_prices.c.quote_date < quote_date.isoformat(),
                )
                .order_by(gold_prices.c.quote_date.desc())
                .limit(1)
            ).first()
        return _row_to_quote(row) if row else None

    def save_adjustment(self, quote_date: date, source: str, price: float,
                         previous_price: Optional[float], change_usd: Optional[float],
                         change_pct: Optional[float], adjustment_factor: float,
                         computed_at: datetime) -> None:
        values = dict(
            quote_date=quote_date.isoformat(),
            source=source,
            price_usd_per_oz=price,
            previous_price_usd_per_oz=previous_price,
            change_usd=change_usd,
            change_pct=change_pct,
            adjustment_factor=adjustment_factor,
            computed_at=computed_at.isoformat(),
        )
        with self.engine.begin() as conn:
            exists = conn.execute(
                select(daily_adjustments.c.quote_date).where(
                    daily_adjustments.c.quote_date == values["quote_date"]
                )
            ).first()
            if exists:
                conn.execute(
                    daily_adjustments.update()
                    .where(daily_adjustments.c.quote_date == values["quote_date"])
                    .values(**values)
                )
            else:
                conn.execute(daily_adjustments.insert().values(**values))


def _row_to_quote(row) -> StoredQuote:
    m = row._mapping
    return StoredQuote(
        quote_date=date.fromisoformat(m["quote_date"]),
        source=m["source"],
        price_usd_per_oz=m["price_usd_per_oz"],
        fetched_at=datetime.fromisoformat(m["fetched_at"]),
        raw_text=m["raw_text"] or "",
    )
