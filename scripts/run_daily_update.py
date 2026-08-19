#!/usr/bin/env python3
"""Daily entrypoint: fetch today's gold price and store it in the local
price history (used by the ledger's reference calculator on the dashboard).

Usage:
    python scripts/run_daily_update.py

Intended to be run once a day (cron / Task Scheduler / CI schedule).
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from redgold.pipeline import run_daily_update
from redgold.sources.base import GoldPriceUnavailableError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        adjustment = run_daily_update()
    except GoldPriceUnavailableError as exc:
        print(f"ERROR: could not fetch today's gold price: {exc}", file=sys.stderr)
        return 1

    print(
        f"{adjustment.quote_date} [{adjustment.source}]: "
        f"{adjustment.price_usd_per_oz:.2f} USD/oz "
        f"(factor={adjustment.adjustment_factor:.4f})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
