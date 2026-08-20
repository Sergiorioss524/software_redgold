from pathlib import Path
from unittest.mock import Mock, patch

from redgold.config import BCB_SOURCE, NETDANIA_SOURCE
from redgold.sources.bcb import BCBGoldPriceSource
from redgold.sources.metals import fetch_metal_quotes
from redgold.sources.netdania import NetdaniaGoldPriceSource

FIXTURES = Path(__file__).parent / "fixtures"


def _html(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_bcb_source_parses_gold_row():
    source = BCBGoldPriceSource()
    with patch.object(source, "_get_html", return_value=_html("bcb_sample.html")):
        quote = source.fetch()
    assert quote.source == "bcb"
    assert quote.price_usd_per_oz == 4415.98


def test_netdania_source_parses_gold_row():
    source = NetdaniaGoldPriceSource()
    with patch.object(source, "_get_html", return_value=_html("netdania_sample.html")):
        quote = source.fetch()
    assert quote.source == "netdania"
    assert quote.price_usd_per_oz == 4418.50


def test_source_configs_point_at_expected_urls():
    assert "bcb.gob.bo" in BCB_SOURCE.url
    assert "netdania.com" in NETDANIA_SOURCE.url


def test_bcb_metals_parses_gold_and_silver_in_bs():
    response = Mock(text=_html("bcb_metals_sample.html"))
    response.raise_for_status = Mock()
    with patch("redgold.sources.metals.requests.get", return_value=response):
        quotes = fetch_metal_quotes()
    assert quotes["oro"].price_bs_per_oz == 51884.1216
    assert quotes["oro"].quote_date == "20 de Agosto 2026"
    assert quotes["plata"].price_bs_per_oz == 763.90618
