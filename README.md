# software_redgold

Daily gold-price pipeline for RedGold's operations: fetches the day's spot
gold price, keeps a local history, computes the day-over-day adjustment,
and (optionally) logs it into the business's Excel workbook.

## What this base does

1. **Fetch** — tries Banco Central de Bolivia's cotizaciones page first,
   falls back to Netdania's mobile commodities page if BCB is unavailable
   or the parsed value looks implausible (`redgold/sources/`).
2. **Store** — every fetched quote is saved to a local SQLite database
   (`data/price_history.db`) so historical adjustments can always be
   recomputed (`redgold/storage.py`).
3. **Adjust** — compares today's price to the most recent prior quote from
   the same source and derives a change ($, %) and an adjustment factor
   (`redgold/adjustment.py`).
4. **Excel (optional)** — appends one row per day to a
   `Historial_Cotizaciones` sheet in your real workbook, without touching
   any existing formula or cell (`redgold/excel_writer.py`).

## Why the Excel integration is deliberately conservative

Your workbook (`BALANCE_ULTIMO.xlsx`) is a full gold trading/refining P&L
model with a section literally named **"COTIZACION PRECIO POR GR AL DIA"**
(rows 22-24), where `B24` ("BOLSA DE COMPRA") is the day's spot price that
the rest of that block computes from. That's the natural target for daily
automation -- but the same sheet also has several other manual transaction
blocks (COMPRA DE ORO 1/2, VENTA EXPORT ORO, COMPRA DE MATERIAL, VENTA BCB)
that record individual purchases/sales at whatever price applied *at the
time*, and should never be silently overwritten.

So by default the pipeline only **appends to a new history sheet** — it
never writes into `B24` or any other live cell. Once you're confident in
the fetched values, `redgold/excel_writer.py:overwrite_daily_quote_cell`
is ready to be wired into the daily run to write straight into `B24`.

The real workbook is **not** committed to this repo (see `.gitignore`) —
it contains your live business figures. Point the pipeline at your local
copy via `--workbook` or the `REDGOLD_WORKBOOK_PATH` env var.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # adjust selectors/paths once you confirm the live HTML
```

## Running a daily update

```bash
python scripts/run_daily_update.py
# or, to also log into your workbook's history sheet:
python scripts/run_daily_update.py --workbook /path/to/BALANCE_ULTIMO.xlsx
```

Schedule it once a day (cron, Task Scheduler, or a CI scheduled job) for
fully automatic daily adjustment.

## Tuning the scrapers

BCB and Netdania were not reachable for live inspection while this base
was built (both domains were blocked from the build environment), so the
scrapers use a defensive generic strategy: scan every `<table>` matched by
a configurable CSS selector, find the row mentioning "oro"/"gold"/"xau",
and pull the first plausible number (sanity-bounded between
`REDGOLD_MIN_PRICE` and `REDGOLD_MAX_PRICE` USD/oz, see `.env.example`).

Once you can inspect the live pages yourself:
- Adjust `REDGOLD_BCB_SELECTOR` / `REDGOLD_NETDANIA_SELECTOR` in `.env` to
  target the exact table if the generic scan is too broad.
- Or override `_extract_price` in `redgold/sources/bcb.py` /
  `netdania.py` for fully custom, site-specific parsing.
- `tests/fixtures/*.html` hold sample markup used by the unit tests —
  replace them with real captured HTML to keep the tests representative.

## Project layout

```
redgold/
  config.py          # env-driven settings: URLs, selectors, DB path, workbook path
  sources/
    base.py           # shared fetch/parse/validate logic
    bcb.py             # Banco Central de Bolivia source
    netdania.py         # Netdania source
  storage.py           # SQLite price + adjustment history
  adjustment.py         # day-over-day adjustment calculation
  excel_writer.py        # workbook history-sheet integration
  pipeline.py             # orchestrates fetch -> store -> adjust
scripts/
  run_daily_update.py      # CLI entrypoint, safe to schedule daily
tests/                       # pytest suite with offline fixtures
```

## Tests

```bash
python -m pytest -q
```
# software_redgold
# software_redgold
