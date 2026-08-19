from redgold.config import BCB_SOURCE
from redgold.sources.base import GoldPriceSource


class BCBGoldPriceSource(GoldPriceSource):
    """Gold quote published on Banco Central de Bolivia's cotizaciones page."""

    def __init__(self):
        super().__init__(BCB_SOURCE)
