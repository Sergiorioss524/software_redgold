# software_redgold

Autonomous gold buy/sell/reinvest ledger for RedGold's operations, modeled
directly on the business logic that used to live as formulas inside
`BALANCE_ULTIMO.xlsx`. That workbook is no longer needed to run the
business day-to-day -- this system replaces it.

## What this does

RedGold runs two parallel cycles, both replicated here:

- **Export cycle**: buy raw gold from miners by weight/purity, later sell
  fine ounces abroad (minus royalties + commission), realize a profit.
- **BCB cycle**: buy bulk material, later sell it to Banco Central de
  Bolivia (different royalty rate), realize a profit.

Each cycle tracks, over time (not just a single snapshot like the old
sheet):

1. **Fetch** — the day's spot gold price, from BCB or (falling back)
   Netdania (`redgold/sources/`), stored in local history
   (`redgold/storage.py`) and used only as an informational reference on
   the dashboard.
2. **Purchases** — recorded by weight (g), purity ("ley"), price (USD/oz),
   and exchange rate (Bs/$). Fine-ounce weight and totals (Bs and USD) are
   derived with the same formulas as the original `COMPRA DE ORO` /
   `COMPRA DE MATERIAL` blocks.
3. **Sales** — recorded by fine ounces sold, sale price, royalty %,
   commission %, and exchange rate. Totals are derived exactly like
   `VENTA EXPORT ORO` / `VENTA BCB`.
4. **Inventory & cost basis** — each cycle's fine-oz on hand is
   `purchased - sold`; a sale's cost basis is the weighted-average cost
   (tracked independently in USD and Bs, since the sheet derived them from
   different exchange rates) of every purchase in that cycle up to the
   sale date. Selling more than is on hand is rejected.
5. **Profit** — mirrors the `REDGOLD` block: a direct USD profit (sale USD
   proceeds minus USD cost basis) and a Bs profit (sale Bs proceeds minus
   Bs cost basis, net of a configurable operating-cost %), each shown both
   in Bs and as a USD-equivalent at the sale's own exchange rate. These two
   profit figures intentionally differ -- see the docstring on
   `compute_cycle_profit` in `redgold/ledger.py`.
6. **Reference calculator** — an informational "how many extra grams could
   today's profit buy at today's price" figure (mirrors
   `COTIZACION PRECIO POR GR AL DIA`). It never auto-creates a purchase;
   every transaction is entered by hand.

All of this is exposed through a local web dashboard
(`redgold/webapp.py`) instead of writing back into Excel.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # adjust selectors/rates once you confirm live values
```

## Running the dashboard

```bash
python scripts/run_web.py
# then open http://127.0.0.1:5000
```

From the dashboard you can fetch today's reference price, record purchases
and sales for either cycle, and see inventory / profit per cycle.

## Storage: SQLite locally, Postgres in production

`redgold/db.py` defines one schema (SQLAlchemy Core) that works against
either backend, selected by `DATABASE_URL`:

- **Local dev / tests**: no `DATABASE_URL` set -> falls back to a SQLite
  file at `REDGOLD_DB_PATH` (default `data/redgold.db`).
- **Production (Vercel)**: deploying behind a serverless function means the
  filesystem is read-only and ephemeral, so SQLite doesn't persist between
  requests. Add a Postgres database to the Vercel project (Storage tab --
  this repo was set up against Prisma Postgres via Vercel's marketplace
  integration) and it auto-injects `DATABASE_URL` / `POSTGRES_URL`; no
  further config needed. `app.py` at the repo root is the entrypoint
  Vercel's Python builder auto-detects.

If you ever point a local `.env`'s `DATABASE_URL` at the same production
database (e.g. to debug something live), remember purchases/sales you
create locally are real writes to production data.

## Fetching just the daily reference price (e.g. from cron)

```bash
python scripts/run_daily_update.py
```

Schedule it once a day (cron, Task Scheduler, or a CI scheduled job) so the
dashboard's reference price and affordability calculator stay current --
this does not touch purchases/sales, which are entered by hand.

## Tuning the scrapers

BCB actively blocks the scraper's requests (`403 Forbidden` as of last
check) -- Netdania works and is used as the automatic fallback. Once you
can inspect BCB's live page yourself (or get an allowed User-Agent):

- Adjust `REDGOLD_BCB_SELECTOR` / `REDGOLD_NETDANIA_SELECTOR` in `.env` if
  the generic table scan is too broad.
- Or override `_extract_price` in `redgold/sources/bcb.py` /
  `netdania.py` for fully custom, site-specific parsing.
- `tests/fixtures/*.html` hold sample markup used by the unit tests.

## Project layout

```
app.py                     # root Flask entrypoint (Vercel auto-detects this)
redgold/
  config.py          # env-driven settings: URLs, selectors, DATABASE_URL, ledger defaults
  db.py                # SQLAlchemy Core schema + engine factory (SQLite or Postgres)
  sources/
    base.py           # shared fetch/parse/validate logic
    bcb.py             # Banco Central de Bolivia source
    netdania.py         # Netdania source
  storage.py           # price history (reference price only)
  adjustment.py         # day-over-day price change calculation
  ledger.py              # purchases, sales, inventory, cost basis, profit (the core model)
  pipeline.py             # orchestrates price fetch -> store -> adjust
  webapp.py               # Flask dashboard: record purchases/sales, view inventory & profit
  templates/                # dashboard + form + list HTML
scripts/
  run_web.py             # launch the dashboard
  run_daily_update.py      # CLI entrypoint for the daily reference-price fetch
tests/                       # pytest suite: ledger math verified against the real
                              # workbook's cell values, plus web dashboard smoke tests
```

## Tests

```bash
python -m pytest -q
```

`tests/test_ledger.py` reproduces the exact numbers from the original
`BALANCE_ULTIMO.xlsx` (COMPRA DE ORO 1, VENTA EXPORT ORO, REDGOLD,
COTIZACION, and the BCB cycle) to confirm the formulas were ported
correctly. `tests/test_webapp.py` drives the dashboard's golden paths
(record a purchase, record a sale, view profit, reject an oversell)
through Flask's test client.
