"""Official USD/BOB exchange rate, used to prefill the "TC de venta" field.

When this business sells gold for USD, it converts those USD back into Bs
by selling them to a bank -- at roughly the BCB's official *buying* rate
("compra"). That's the number this module fetches: the BCB's own
cotizaciones_tc page renders its table client-side (no plain-HTTP scrape
possible from here), so we go through CUCU's API instead, which republishes
the same BCB figure.
"""
from __future__ import annotations

from dataclasses import dataclass

import requests

from redgold import config

HTTP_TIMEOUT_SECONDS = 15
USER_AGENT = "Mozilla/5.0 (compatible; RedGoldQuotesBot/1.0; +https://github.com/)"


class ExchangeRateUnavailableError(RuntimeError):
    """Raised when the official rate can't be fetched or parsed."""


@dataclass(frozen=True)
class OfficialRate:
    compra: float
    venta: float
    fecha_vigencia: str
    fecha_publicacion: str


def fetch_official_rate() -> OfficialRate:
    try:
        response = requests.get(
            config.CUCU_TC_URL, timeout=HTTP_TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT}
        )
        response.raise_for_status()
    except requests.Timeout as exc:
        raise ExchangeRateUnavailableError(
            f"tiempo de espera agotado consultando {config.CUCU_TC_URL}"
        ) from exc
    except requests.RequestException as exc:
        raise ExchangeRateUnavailableError(
            f"error de red consultando {config.CUCU_TC_URL}: {exc}"
        ) from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise ExchangeRateUnavailableError("la respuesta de CUCU no es JSON válido") from exc

    try:
        tc = payload["tc_oficial"]
        return OfficialRate(
            compra=float(tc["compra"]),
            venta=float(tc["venta"]),
            fecha_vigencia=str(tc["fecha"]),
            fecha_publicacion=str(tc.get("fecha_publicacion", "")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ExchangeRateUnavailableError(
            f"estructura de respuesta inesperada de CUCU (¿cambió el formato de la API?): {exc}"
        ) from exc
