"""Central configuration for the gold price adjustment pipeline.

Values are read from environment variables (or a local .env file) so the
scraping targets and CSS selectors can be tuned without touching code once
the real page markup has been inspected -- BCB and Netdania occasionally
change their HTML, and outbound network access is not always available
from every environment.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = Path(os.getenv("REDGOLD_DB_PATH", DATA_DIR / "redgold.db"))

HTTP_TIMEOUT_SECONDS = float(os.getenv("REDGOLD_HTTP_TIMEOUT", "15"))
HTTP_USER_AGENT = os.getenv(
    "REDGOLD_USER_AGENT",
    "Mozilla/5.0 (compatible; RedGoldPriceBot/1.0; +https://github.com/)",
)


@dataclass(frozen=True)
class SourceConfig:
    name: str
    url: str
    # CSS selector pointing at the element/row that holds the gold quote.
    # Left generic on purpose: fill in once the live markup is confirmed.
    selector: str
    enabled: bool = True


BCB_SOURCE = SourceConfig(
    name="bcb",
    url=os.getenv(
        "REDGOLD_BCB_URL",
        "https://www.bcb.gob.bo/?q=content/tabla-de-cotizaciones",
    ),
    selector=os.getenv("REDGOLD_BCB_SELECTOR", "table"),
)

NETDANIA_SOURCE = SourceConfig(
    name="netdania",
    url=os.getenv("REDGOLD_NETDANIA_URL", "https://m.netdania.com/commodities"),
    selector=os.getenv("REDGOLD_NETDANIA_SELECTOR", "table"),
)

# Order matters: the pipeline tries sources in this order and falls back
# to the next one if a fetch fails or returns an implausible value.
SOURCE_PRIORITY = [BCB_SOURCE, NETDANIA_SOURCE]

# Sanity bounds (USD per troy ounce) used to reject obviously broken scrapes
# (e.g. the page changed and we grabbed the wrong number).
MIN_PLAUSIBLE_PRICE = float(os.getenv("REDGOLD_MIN_PRICE", "500"))
MAX_PLAUSIBLE_PRICE = float(os.getenv("REDGOLD_MAX_PRICE", "20000"))

# --- Ledger defaults (from the original BALANCE_ULTIMO.xlsx model) --------
# Grams per troy ounce, used throughout the purchase/sale math.
TROY_OUNCE_GRAMS = 31.1035

# Default purity ("ley") assumed for a new purchase, as a 0-1 fraction.
DEFAULT_PURITY_PCT = float(os.getenv("REDGOLD_DEFAULT_PURITY_PCT", "0.95"))

# Default royalty ("regalias") withheld on a sale, as a 0-1 fraction.
# Editable per sale -- these just pre-fill the form. The export cycle's
# rate in the source workbook was ambiguous (label said 5.6%, the live
# formula computed 0.9%); we default to the formula's rate and let the
# actual figure be confirmed/overridden per sale.
DEFAULT_ROYALTY_PCT_EXPORT = float(os.getenv("REDGOLD_ROYALTY_EXPORT", "0.009"))
DEFAULT_ROYALTY_PCT_BCB = float(os.getenv("REDGOLD_ROYALTY_BCB", "0.048"))

# Default commission taken on a sale, as a 0-1 fraction.
DEFAULT_COMMISSION_PCT = float(os.getenv("REDGOLD_DEFAULT_COMMISSION_PCT", "0.0"))

# Operating cost applied to each cycle's Bs profit before it becomes net
# profit/reinvestment capital.
OPERATING_COST_PCT = float(os.getenv("REDGOLD_OPERATING_COST_PCT", "0.07125"))
