"""Shared SQLAlchemy Core schema + engine factory, used by both storage.py
(price history) and ledger.py (purchases/sales). One set of table
definitions works against either backend selected by config.DATABASE_URL:

- Postgres in production (Vercel Postgres or any DATABASE_URL), so data
  survives serverless cold starts / redeploys.
- A local SQLite file for local dev and tests, created lazily (only when
  actually used) so importing this module never touches the filesystem in
  a read-only serverless environment.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import Column, Float, Integer, MetaData, String, Table, Text, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

metadata = MetaData()

gold_prices = Table(
    "gold_prices", metadata,
    Column("quote_date", String, primary_key=True),
    Column("source", String, primary_key=True),
    Column("price_usd_per_oz", Float, nullable=False),
    Column("fetched_at", String, nullable=False),
    Column("raw_text", Text),
)

daily_adjustments = Table(
    "daily_adjustments", metadata,
    Column("quote_date", String, primary_key=True),
    Column("source", String, nullable=False),
    Column("price_usd_per_oz", Float, nullable=False),
    Column("previous_price_usd_per_oz", Float),
    Column("change_usd", Float),
    Column("change_pct", Float),
    Column("adjustment_factor", Float),
    Column("computed_at", String, nullable=False),
)

purchases = Table(
    "purchases", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("purchase_date", String, nullable=False),
    Column("category", String, nullable=False),
    Column("weight_g", Float, nullable=False),
    Column("purity_pct", Float, nullable=False),
    Column("price_usd_per_oz", Float, nullable=False),
    Column("exchange_rate_bs_per_usd", Float, nullable=False),
    Column("notes", Text),
    Column("created_at", String, nullable=False),
)

sales = Table(
    "sales", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("sale_date", String, nullable=False),
    Column("category", String, nullable=False),
    Column("fine_oz_sold", Float, nullable=False),
    Column("sale_price_usd_per_oz", Float, nullable=False),
    Column("royalty_pct", Float, nullable=False),
    Column("commission_pct", Float, nullable=False),
    Column("exchange_rate_bs_per_usd", Float, nullable=False),
    Column("notes", Text),
    Column("created_at", String, nullable=False),
)


def normalize_database_url(url: str) -> str:
    """Providers (Vercel/Prisma included) inject postgres://... or
    postgresql://... URLs; point SQLAlchemy at the psycopg3 driver so it
    doesn't need one registered as the default dialect."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def make_engine(database_url: str) -> Engine:
    database_url = normalize_database_url(database_url)
    kwargs: dict = {}
    if database_url.startswith("sqlite"):
        raw_path = database_url.split("sqlite:///", 1)[-1]
        if raw_path and raw_path != ":memory:":
            Path(raw_path).parent.mkdir(parents=True, exist_ok=True)
    elif database_url.startswith("postgresql"):
        # Serverless: don't hold a connection pool across invocations --
        # the provider's own pooler (e.g. Vercel Postgres/pgbouncer)
        # handles reuse.
        kwargs["poolclass"] = NullPool
    engine = create_engine(database_url, **kwargs)
    metadata.create_all(engine)
    return engine
