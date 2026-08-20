"""Gold buy/sell round-trip math -- the business logic that used to live as
formulas inside BALANCE_ULTIMO.xlsx (COMPRA DE ORO/MATERIAL -> VENTA EXPORT
ORO/BCB -> REDGOLD profit split), reduced to pure "buy today, sell today"
calculators. Nothing here is persisted -- each calculation is a what-if
against numbers the user types in.

All formulas below were checked against the real cell values in the
original workbook (see tests/test_ledger.py).
"""
from __future__ import annotations

from dataclasses import dataclass

from redgold import config

CATEGORY_EXPORT = "EXPORT"
CATEGORY_BCB = "BCB"
CATEGORIES = (CATEGORY_EXPORT, CATEGORY_BCB)


# --------------------------------------------------------------------------
# Pure calculations (mirror the workbook's formulas exactly)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class PurchaseTotals:
    fine_weight_g: float
    fine_oz: float
    price_bs_per_gram: float
    total_bs: float
    total_usd: float


def compute_purchase_totals(
    weight_g: float,
    purity_pct: float,
    price_usd_per_oz: float,
    exchange_rate_bs_per_usd: float,
) -> PurchaseTotals:
    """Mirrors COMPRA DE ORO / COMPRA DE MATERIAL (sheet rows 5 and 45)."""
    fine_weight_g = weight_g * purity_pct
    fine_oz = fine_weight_g / config.TROY_OUNCE_GRAMS
    price_bs_per_gram = (
        price_usd_per_oz / config.TROY_OUNCE_GRAMS * (purity_pct * exchange_rate_bs_per_usd)
    )
    total_bs = price_bs_per_gram * weight_g
    total_usd = fine_oz * price_usd_per_oz
    return PurchaseTotals(fine_weight_g, fine_oz, price_bs_per_gram, total_bs, total_usd)


@dataclass(frozen=True)
class SaleTotals:
    total_venta_usd: float
    comision_usd: float
    total_final_usd: float
    total_bs: float


def compute_sale_totals(
    fine_oz_sold: float,
    sale_price_usd_per_oz: float,
    commission_pct: float,
    exchange_rate_bs_per_usd: float,
) -> SaleTotals:
    """Mirrors VENTA EXPORT ORO / VENTA BCB (sheet rows 11 and 51), minus the
    royalty deduction that used to be withheld here -- it's already priced
    in upstream, since "tipo de cambio minero" is derived as TC oficial x
    (1 - regalías BCB fija)."""
    total_venta_usd = fine_oz_sold * sale_price_usd_per_oz
    comision_usd = total_venta_usd * commission_pct
    total_final_usd = total_venta_usd - comision_usd
    total_bs = total_final_usd * exchange_rate_bs_per_usd
    return SaleTotals(total_venta_usd, comision_usd, total_final_usd, total_bs)


@dataclass(frozen=True)
class CycleProfit:
    """Mirrors the REDGOLD block (sheet rows 16-18 / 56-58).

    profit_usd_direct compares the sale's USD proceeds against the
    purchase's USD cost basis directly (both in USD, at their own rates).

    profit_bs / operating_cost_bs / net_profit_bs compare the sale's Bs
    proceeds (converted at the SALE's exchange rate) against the purchase's
    Bs cost (converted at the PURCHASE's exchange rate) -- so this is not
    just a currency conversion of profit_usd_direct, it captures the
    Bs-denominated arbitrage between the two different exchange rates,
    which is how the original workbook modeled it. The *_usd_equiv fields
    are that Bs profit re-expressed in USD at the sale's exchange rate,
    matching D18/E18/F18 in the sheet -- they will generally differ from
    profit_usd_direct.
    """
    profit_usd_direct: float
    profit_bs: float
    operating_cost_bs: float
    net_profit_bs: float
    profit_usd_equiv: float
    operating_cost_usd_equiv: float
    net_profit_usd_equiv: float


def compute_cycle_profit(
    sale_totals: SaleTotals,
    cost_basis_usd: float,
    cost_basis_bs: float,
    sale_exchange_rate_bs_per_usd: float,
    operating_cost_pct: float = config.OPERATING_COST_PCT,
) -> CycleProfit:
    profit_usd_direct = sale_totals.total_final_usd - cost_basis_usd
    profit_bs = sale_totals.total_bs - cost_basis_bs
    operating_cost_bs = profit_bs * operating_cost_pct
    net_profit_bs = profit_bs - operating_cost_bs
    profit_usd_equiv = profit_bs / sale_exchange_rate_bs_per_usd
    operating_cost_usd_equiv = operating_cost_bs / sale_exchange_rate_bs_per_usd
    net_profit_usd_equiv = net_profit_bs / sale_exchange_rate_bs_per_usd
    return CycleProfit(
        profit_usd_direct, profit_bs, operating_cost_bs, net_profit_bs,
        profit_usd_equiv, operating_cost_usd_equiv, net_profit_usd_equiv,
    )


def compute_price_per_gram_bs(
    price_usd_per_oz: float, purity_pct: float, exchange_rate_bs_per_usd: float,
) -> float:
    """Mirrors COTIZACION PRECIO POR GR AL DIA (sheet row 24, F24)."""
    return price_usd_per_oz / config.TROY_OUNCE_GRAMS * (purity_pct * exchange_rate_bs_per_usd)


def compute_affordable_grams(net_profit_bs: float, price_per_gram_bs: float) -> float:
    """Informational only (mirrors G24): how many extra grams today's net
    profit could buy at today's price. Does not create a purchase."""
    if price_per_gram_bs <= 0:
        return 0.0
    return net_profit_bs / price_per_gram_bs
