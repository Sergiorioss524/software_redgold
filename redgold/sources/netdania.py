from redgold.config import NETDANIA_SOURCE
from redgold.sources.base import GoldPriceSource


class NetdaniaGoldPriceSource(GoldPriceSource):
    """Gold quote published on Netdania's mobile commodities page."""

    def __init__(self):
        super().__init__(NETDANIA_SOURCE)
