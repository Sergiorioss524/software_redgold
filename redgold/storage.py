"""SQLite-backed history of daily gold quotes and computed adjustments."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterator, Optional

from redgold import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS gold_prices (
    quote_date TEXT NOT NULL,
    source TEXT NOT NULL,
    price_usd_per_oz REAL NOT NULL,
    fetched_at TEXT NOT NULL,
    raw_text TEXT,
    PRIMARY KEY (quote_date, source)
);

CREATE TABLE IF NOT EXISTS daily_adjustments (
    quote_date TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    price_usd_per_oz REAL NOT NULL,
    previous_price_usd_per_oz REAL,
    change_usd REAL,
    change_pct REAL,
    adjustment_factor REAL,
    computed_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class StoredQuote:
    quote_date: date
    source: str
    price_usd_per_oz: float
    fetched_at: datetime
    raw_text: str


class PriceHistory:
    def __init__(self, db_path: Path | str = config.DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def save_quote(self, quote_date: date, source: str, price: float,
                    fetched_at: datetime, raw_text: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO gold_prices (quote_date, source, price_usd_per_oz, fetched_at, raw_text)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(quote_date, source) DO UPDATE SET
                    price_usd_per_oz=excluded.price_usd_per_oz,
                    fetched_at=excluded.fetched_at,
                    raw_text=excluded.raw_text
                """,
                (quote_date.isoformat(), source, price, fetched_at.isoformat(), raw_text),
            )

    def get_quote(self, quote_date: date, source: str) -> Optional[StoredQuote]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT quote_date, source, price_usd_per_oz, fetched_at, raw_text "
                "FROM gold_prices WHERE quote_date = ? AND source = ?",
                (quote_date.isoformat(), source),
            ).fetchone()
        if row is None:
            return None
        return StoredQuote(
            quote_date=date.fromisoformat(row[0]),
            source=row[1],
            price_usd_per_oz=row[2],
            fetched_at=datetime.fromisoformat(row[3]),
            raw_text=row[4] or "",
        )

    def latest_quote_before(self, quote_date: date, source: str) -> Optional[StoredQuote]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT quote_date, source, price_usd_per_oz, fetched_at, raw_text "
                "FROM gold_prices WHERE source = ? AND quote_date < ? "
                "ORDER BY quote_date DESC LIMIT 1",
                (source, quote_date.isoformat()),
            ).fetchone()
        if row is None:
            return None
        return StoredQuote(
            quote_date=date.fromisoformat(row[0]),
            source=row[1],
            price_usd_per_oz=row[2],
            fetched_at=datetime.fromisoformat(row[3]),
            raw_text=row[4] or "",
        )

    def save_adjustment(self, quote_date: date, source: str, price: float,
                         previous_price: Optional[float], change_usd: Optional[float],
                         change_pct: Optional[float], adjustment_factor: float,
                         computed_at: datetime) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO daily_adjustments
                    (quote_date, source, price_usd_per_oz, previous_price_usd_per_oz,
                     change_usd, change_pct, adjustment_factor, computed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(quote_date) DO UPDATE SET
                    source=excluded.source,
                    price_usd_per_oz=excluded.price_usd_per_oz,
                    previous_price_usd_per_oz=excluded.previous_price_usd_per_oz,
                    change_usd=excluded.change_usd,
                    change_pct=excluded.change_pct,
                    adjustment_factor=excluded.adjustment_factor,
                    computed_at=excluded.computed_at
                """,
                (
                    quote_date.isoformat(), source, price, previous_price,
                    change_usd, change_pct, adjustment_factor, computed_at.isoformat(),
                ),
            )
