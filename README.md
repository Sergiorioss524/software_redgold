# software_redgold

A same-day profitability calculator for RedGold's gold buy/sell operations:
"is it worth buying or selling gold today?" Built on the business logic
that used to live as formulas inside `BALANCE_ULTIMO.xlsx`. That workbook
is no longer needed to run the business day-to-day -- this system replaces
it.

## What this does

RedGold runs two parallel cycles, both modeled here:

- **Export cycle**: buy raw gold from miners by weight/purity, later sell
  fine ounces abroad (minus royalties + commission).
- **BCB cycle**: buy bulk material, later sell it to Banco Central de
  Bolivia (different royalty rate).

The dashboard (`redgold/webapp.py`) leads with two what-if calculators --
neither saves anything, both answer "is today a good day to trade":

1. **Comprar y vender hoy** — simulate buying gold today and flipping it
   the same day: enter weight/purity/buy price/sell price/rates/fees, get
   the round-trip margin (gross profit, operating cost, net profit, both
   in Bs and USD).
2. **Vender mi inventario hoy** — simulate selling gold you've already
   bought, using the *real* weighted-average cost basis from your recorded
   purchases, against a hypothetical sale price/rate today.

Both reuse the same formulas (`redgold/ledger.py`) ported from the
workbook's `COMPRA DE ORO` / `VENTA EXPORT ORO` / `VENTA BCB` / `REDGOLD`
blocks -- verified against the sheet's real numbers in
`tests/test_ledger.py`.

Underneath the calculators, actual purchases/sales can still be recorded
and kept as a running ledger (inventory, cost basis, history) -- that's
what feeds the "sell my inventory" calculator's real cost basis. A sale's
profit mirrors the `REDGOLD` block: a direct USD profit (sale USD proceeds
minus USD cost basis) and a Bs profit (sale Bs proceeds minus Bs cost
basis, net of a configurable operating-cost %) -- these two intentionally
differ, see the docstring on `compute_cycle_profit` in `redgold/ledger.py`.

The day's spot gold price (BCB, falling back to Netdania --
`redgold/sources/`) is fetched from the dashboard to prefill both
calculators.

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
