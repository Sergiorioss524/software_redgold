#!/usr/bin/env python3
"""Cotizaciones del día del Banco Central de Bolivia (BCB).

Combina dos fuentes:

1. Tipo de cambio oficial USD/BOB (compra, venta, fecha de vigencia) --
   vía la API no oficial de CUCU (https://apibcb.cucu.bo/api/v1/tc/oficial),
   que republica la cifra publicada por el BCB.

2. Cotización de oro y plata en USD por onza troy -- el endpoint de oro de
   CUCU (tc/oro) suele quedar desactualizado varias semanas, así que en su
   lugar se lee directo de la página de metales preciosos del BCB
   (https://www.bcb.gob.bo/librerias/indicadores/metales/ultimo.php), que
   publica los precios en bolivianos por onza, y se convierten a USD
   usando el tipo de cambio oficial del paso 1.

Uso:
    python scripts/bcb_quotes.py                  # resumen legible
    python scripts/bcb_quotes.py --json            # solo JSON crudo
    python scripts/bcb_quotes.py --save            # además, acumula histórico local
    python scripts/bcb_quotes.py --save ruta.json  # histórico en una ruta custom

No requiere autenticación en ninguna de las dos fuentes. La API de CUCU
limita a 120 solicitudes/hora; este script hace una sola consulta por
fuente, así que no hay riesgo de tope aun corriéndolo varias veces al día.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

CUCU_TC_URL = "https://apibcb.cucu.bo/api/v1/tc/oficial"
BCB_METALES_URL = "https://www.bcb.gob.bo/librerias/indicadores/metales/ultimo.php"

HTTP_TIMEOUT_SECONDS = 15
USER_AGENT = "Mozilla/5.0 (compatible; RedGoldQuotesBot/1.0; +https://github.com/)"

DEFAULT_HISTORY_PATH = Path(__file__).resolve().parent.parent / "data" / "bcb_quotes_history.json"

_MESES_ES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}
_FECHA_RE = re.compile(r"(\d{1,2})\s+de\s+([A-Za-zÁÉÍÓÚáéíóú]+)\s+(\d{4})")
_METAL_LABEL_RE = re.compile(r"ONZA TROY (?:FINA )?(ORO|PLATA)", re.IGNORECASE)


class QuoteError(RuntimeError):
    """Raised when a source is unreachable or its data can't be parsed."""


@dataclass(frozen=True)
class OfficialRate:
    compra: float
    venta: float
    fecha_vigencia: str
    fecha_publicacion: str


@dataclass(frozen=True)
class MetalQuote:
    metal: str
    price_bs_per_oz: float
    price_usd_per_oz: float
    fecha: date


@dataclass(frozen=True)
class QuotesSummary:
    fetched_at: datetime
    official_rate: OfficialRate
    gold: MetalQuote
    silver: MetalQuote


def _get(url: str, *, source_label: str) -> requests.Response:
    try:
        response = requests.get(url, timeout=HTTP_TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
    except requests.Timeout as exc:
        raise QuoteError(f"{source_label}: tiempo de espera agotado consultando {url}") from exc
    except requests.RequestException as exc:
        raise QuoteError(f"{source_label}: error de red consultando {url}: {exc}") from exc
    return response


def fetch_official_rate() -> OfficialRate:
    response = _get(CUCU_TC_URL, source_label="Tipo de cambio oficial (CUCU)")
    try:
        payload = response.json()
    except ValueError as exc:
        raise QuoteError("Tipo de cambio oficial (CUCU): la respuesta no es JSON válido") from exc

    try:
        tc = payload["tc_oficial"]
        return OfficialRate(
            compra=float(tc["compra"]),
            venta=float(tc["venta"]),
            fecha_vigencia=str(tc["fecha"]),
            fecha_publicacion=str(tc.get("fecha_publicacion", "")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise QuoteError(
            f"Tipo de cambio oficial (CUCU): estructura de respuesta inesperada "
            f"(¿cambió el formato de la API?): {exc}"
        ) from exc


def _parse_spanish_date(text: str, *, context: str) -> date:
    match = _FECHA_RE.search(text)
    if not match:
        raise QuoteError(f"{context}: no se pudo interpretar la fecha '{text}'")
    day_str, month_name, year_str = match.groups()
    month = _MESES_ES.get(month_name.lower())
    if month is None:
        raise QuoteError(f"{context}: mes desconocido '{month_name}' en fecha '{text}'")
    try:
        return date(int(year_str), month, int(day_str))
    except ValueError as exc:
        raise QuoteError(f"{context}: fecha inválida '{text}'") from exc


def _parse_bs_number(text: str, *, context: str) -> float:
    """BCB formats numbers like '50 040,69120' (space thousands, comma decimal)."""
    cleaned = text.strip().replace("\xa0", " ")
    cleaned = re.sub(r"(?<=\d)\s+(?=\d)", "", cleaned)
    cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError as exc:
        raise QuoteError(f"{context}: no se pudo interpretar el número '{text}'") from exc


def fetch_metal_quotes_bs() -> dict[str, tuple[float, date]]:
    """Returns {'oro': (precio_bs_por_oz, fecha), 'plata': (precio_bs_por_oz, fecha)}."""
    response = _get(BCB_METALES_URL, source_label="Metales preciosos (BCB)")

    soup = BeautifulSoup(response.text, "lxml")
    table = soup.find("table")
    if table is None:
        raise QuoteError(
            "Metales preciosos (BCB): no se encontró ninguna tabla en la página "
            "(¿cambió la estructura del HTML?)"
        )

    results: dict[str, tuple[float, date]] = {}
    for row in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in row.find_all("td")]
        if not cells:
            continue
        match = _METAL_LABEL_RE.search(cells[0])
        if not match:
            continue
        metal = "oro" if match.group(1).upper() == "ORO" else "plata"
        if len(cells) < 3:
            raise QuoteError(
                f"Metales preciosos (BCB): fila de '{metal}' con menos columnas de "
                f"las esperadas: {cells}"
            )
        context = f"Metales preciosos (BCB), fila de '{metal}'"
        fecha = _parse_spanish_date(cells[1], context=context)
        precio_bs = _parse_bs_number(cells[2], context=context)
        results[metal] = (precio_bs, fecha)

    faltantes = {"oro", "plata"} - results.keys()
    if faltantes:
        raise QuoteError(
            "Metales preciosos (BCB): no se encontraron cotizaciones para "
            f"{', '.join(sorted(faltantes))} (¿cambió el HTML del BCB?)"
        )
    return results


def get_quotes() -> QuotesSummary:
    official = fetch_official_rate()
    metals_bs = fetch_metal_quotes_bs()

    def to_metal_quote(metal: str) -> MetalQuote:
        precio_bs, fecha = metals_bs[metal]
        # El TC "oficial" (== 'compra'/'base') es la cifra base publicada por
        # el BCB; 'venta' es solo un tope referencial (TCO + Bs 0.10), no una
        # segunda cotización independiente -- por eso se usa 'compra' para
        # convertir los metales, que vienen expresados en Bs.
        precio_usd = precio_bs / official.compra
        return MetalQuote(metal=metal, price_bs_per_oz=precio_bs, price_usd_per_oz=precio_usd, fecha=fecha)

    return QuotesSummary(
        fetched_at=datetime.now(timezone.utc),
        official_rate=official,
        gold=to_metal_quote("oro"),
        silver=to_metal_quote("plata"),
    )


def summary_to_dict(summary: QuotesSummary) -> dict:
    data = asdict(summary)
    data["fetched_at"] = summary.fetched_at.isoformat()
    data["gold"]["fecha"] = summary.gold.fecha.isoformat()
    data["silver"]["fecha"] = summary.silver.fecha.isoformat()
    return data


def print_human_summary(summary: QuotesSummary) -> None:
    rate = summary.official_rate
    print(f"Tipo de cambio oficial USD/BOB (vigente {rate.fecha_vigencia}, publicado {rate.fecha_publicacion}):")
    print(f"  Compra: {rate.compra:.2f} Bs/$   Venta: {rate.venta:.2f} Bs/$")
    print()
    for quote in (summary.gold, summary.silver):
        print(
            f"{quote.metal.capitalize():<6} ({quote.fecha.isoformat()}): "
            f"{quote.price_bs_per_oz:,.2f} Bs/oz  ->  {quote.price_usd_per_oz:,.2f} USD/oz"
        )


def _append_to_history(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    history: list = []
    if path.exists():
        try:
            history = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(history, list):
                raise ValueError("el contenido no es una lista JSON")
        except (json.JSONDecodeError, ValueError) as exc:
            raise QuoteError(f"No se pudo leer el histórico existente en {path}: {exc}") from exc
    history.append(entry)
    path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="Imprimir solo el JSON crudo del resultado")
    parser.add_argument(
        "--save",
        nargs="?",
        const=str(DEFAULT_HISTORY_PATH),
        default=None,
        metavar="ARCHIVO",
        help=f"Acumular el resultado en un JSON histórico (por defecto {DEFAULT_HISTORY_PATH})",
    )
    args = parser.parse_args(argv)

    try:
        summary = get_quotes()
        data = summary_to_dict(summary)
        if args.save is not None:
            _append_to_history(Path(args.save), data)
    except QuoteError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print_human_summary(summary)
        if args.save is not None:
            print(f"\nGuardado en {args.save}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
