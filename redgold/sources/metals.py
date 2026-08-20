"""BCB's precious-metals quote page (gold + silver, in Bs per troy ounce).

CUCU also republishes a gold rate (apibcb.cucu.bo/api/v1/tc/oro), but it
tends to lag the real BCB figure by several weeks, so this goes straight to
the BCB's own "Bolsín" page instead -- a plain server-rendered HTML table
(unlike cotizaciones_tc, no JS rendering to work around).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import requests
from bs4 import BeautifulSoup

from redgold import config

HTTP_TIMEOUT_SECONDS = 15
USER_AGENT = "Mozilla/5.0 (compatible; RedGoldQuotesBot/1.0; +https://github.com/)"

_METAL_LABELS = {
    "oro": re.compile(r"\bORO\b", re.IGNORECASE),
    "plata": re.compile(r"\bPLATA\b", re.IGNORECASE),
}


class MetalPriceUnavailableError(RuntimeError):
    """Raised when the BCB metals page can't be fetched or parsed."""


@dataclass(frozen=True)
class MetalQuote:
    metal: str
    price_bs_per_oz: float
    quote_date: str


def _parse_bs_number(raw: str) -> Optional[float]:
    """"51 884,12160" -> 51884.1216 (space = thousands, comma = decimal)."""
    cleaned = raw.replace("\xa0", " ").replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def fetch_metal_quotes() -> dict[str, MetalQuote]:
    """Fetch and parse the BCB "Última cotización de Metales preciosos"
    table. Returns quotes keyed by "oro" / "plata" -- keys not found on the
    page are simply absent, never partially filled."""
    try:
        response = requests.get(
            config.BCB_METALS_URL,
            timeout=HTTP_TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
    except requests.Timeout as exc:
        raise MetalPriceUnavailableError(
            f"tiempo de espera agotado consultando {config.BCB_METALS_URL}"
        ) from exc
    except requests.RequestException as exc:
        raise MetalPriceUnavailableError(
            f"error de red consultando {config.BCB_METALS_URL}: {exc}"
        ) from exc

    soup = BeautifulSoup(response.text, "lxml")
    quotes: dict[str, MetalQuote] = {}
    for row in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in row.find_all("td")]
        if len(cells) < 3:
            continue
        label, quote_date, value_text = cells[0], cells[1], cells[2]
        for metal, pattern in _METAL_LABELS.items():
            if metal in quotes or not pattern.search(label):
                continue
            price = _parse_bs_number(value_text)
            if price is not None:
                quotes[metal] = MetalQuote(metal=metal, price_bs_per_oz=price, quote_date=quote_date)

    if "oro" not in quotes:
        raise MetalPriceUnavailableError(
            f"no se encontró la cotización de oro en {config.BCB_METALS_URL}"
        )
    return quotes


def fetch_gold_quote() -> MetalQuote:
    """Convenience wrapper: the gold-only quote, in Bs per troy ounce."""
    return fetch_metal_quotes()["oro"]
