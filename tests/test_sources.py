from pathlib import Path
from unittest.mock import patch

from redgold.config import BCB_SOURCE, NETDANIA_SOURCE
from redgold.sources.bcb import BCBGoldPriceSource
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
