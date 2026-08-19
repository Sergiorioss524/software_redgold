"""Shared scraping/parsing plumbing for gold price sources.

Both Banco Central de Bolivia's "Tabla de Cotizaciones" and Netdania's
commodities page publish their quotes as HTML tables, so the two concrete
sources (bcb.py, netdania.py) share the same fetch -> parse -> validate
pipeline and only differ in the URL, table selector, and which row/column
holds the gold quote.

The exact markup could not be verified from this environment (outbound
access to both bcb.gob.bo and netdania.com is blocked here), so parsing is
deliberately defensive: it scans every table matched by the CSS selector,
looks for a row whose label mentions gold ("oro" / "gold" / "xau"), and
pulls the first plausible numeric value out of that row. Adjust
`config.py`'s selectors, or override `_extract_price` in a subclass, once
you can inspect the live HTML.
"""
from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from redgold import config
from redgold.config import SourceConfig

logger = logging.getLogger(__name__)

# Matches numbers like 4,415.98 or 4415.98 or 4415,98 (latam decimal comma).
_NUMBER_RE = re.compile(r"[-+]?\d[\d.,]*\d|\d")
_GOLD_LABEL_RE = re.compile(r"\b(oro|gold|xau)\b", re.IGNORECASE)


class GoldPriceUnavailableError(RuntimeError):
    """Raised when a source could not produce a plausible gold price."""


@dataclass(frozen=True)
class GoldPriceQuote:
    source: str
    price_usd_per_oz: float
    fetched_at: datetime
    raw_text: str


class GoldPriceSource(ABC):
    """Fetches and parses a single day's gold quote from one provider."""

    def __init__(self, cfg: SourceConfig):
        self.cfg = cfg

    @property
    def name(self) -> str:
        return self.cfg.name

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
    )
    def _get_html(self) -> str:
        response = requests.get(
            self.cfg.url,
            timeout=config.HTTP_TIMEOUT_SECONDS,
            headers={"User-Agent": config.HTTP_USER_AGENT},
        )
        response.raise_for_status()
        return response.text

    def fetch(self) -> GoldPriceQuote:
        """Fetch the page and return today's gold quote.

        Raises GoldPriceUnavailableError if no plausible price is found.
        """
        html = self._get_html()
        price, raw_text = self._extract_price(html)
        if price is None:
            raise GoldPriceUnavailableError(
                f"{self.name}: could not locate a gold quote at {self.cfg.url}"
            )
        if not (config.MIN_PLAUSIBLE_PRICE <= price <= config.MAX_PLAUSIBLE_PRICE):
            raise GoldPriceUnavailableError(
                f"{self.name}: parsed price {price} is outside the plausible "
                f"range [{config.MIN_PLAUSIBLE_PRICE}, {config.MAX_PLAUSIBLE_PRICE}]"
            )
        return GoldPriceQuote(
            source=self.name,
            price_usd_per_oz=price,
            fetched_at=datetime.now(timezone.utc),
            raw_text=raw_text,
        )

    def _extract_price(self, html: str) -> tuple[Optional[float], str]:
        """Find the row mentioning gold in the configured tables and parse
        the first plausible number out of it. Subclasses can override this
        for site-specific markup instead of relying on the generic scan.
        """
        soup = BeautifulSoup(html, "lxml")
        tables = soup.select(self.cfg.selector)
        for table in tables:
            for row in table.find_all("tr"):
                cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
                if not cells:
                    continue
                row_text = " ".join(cells)
                if not _GOLD_LABEL_RE.search(row_text):
                    continue
                price = self._first_plausible_number(cells)
                if price is not None:
                    return price, row_text
        # Fallback: scan the whole page text near the word "oro"/"gold".
        text = soup.get_text(" ", strip=True)
        for match in _GOLD_LABEL_RE.finditer(text):
            window = text[match.start(): match.start() + 200]
            price = self._first_plausible_number([window])
            if price is not None:
                return price, window
        return None, ""

    @staticmethod
    def _first_plausible_number(cells: list[str]) -> Optional[float]:
        for cell in cells:
            for raw in _NUMBER_RE.findall(cell):
                value = _parse_number(raw)
                if value is not None and (
                    config.MIN_PLAUSIBLE_PRICE <= value <= config.MAX_PLAUSIBLE_PRICE
                ):
                    return value
        return None


def _parse_number(raw: str) -> Optional[float]:
    cleaned = raw.strip()
    if not cleaned:
        return None
    # Handle both "4,415.98" (thousands comma) and "4415,98" (decimal comma).
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None
