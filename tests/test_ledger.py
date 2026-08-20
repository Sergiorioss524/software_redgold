"""Regression tests: ledger math must reproduce the exact cell values from
the real BALANCE_ULTIMO.xlsx (COMPRA DE ORO 1 / VENTA EXPORT ORO / REDGOLD,
rows 3-18) that the ledger module was derived from -- except the sale-side
royalty deduction the workbook applied, which this module no longer does
(it's priced into "tipo de cambio minero" upstream instead), so those
particular numbers are recomputed without it rather than matching the sheet.
"""
from __future__ import annotations

import pytest

from redgold import ledger


def test_purchase_totals_match_workbook_compra_de_oro_1():
    totals = ledger.compute_purchase_totals(
        weight_g=2407.391641,
        purity_pct=0.95,
        price_usd_per_oz=4080,
        exchange_rate_bs_per_usd=10.7,
    )
    assert totals.fine_weight_g == pytest.approx(2287.02205895)
    assert totals.fine_oz == pytest.approx(73.52941176877201)
    assert totals.price_bs_per_gram == pytest.approx(1333.3933480154965)
    assert totals.total_bs == pytest.approx(3210000.0001775105)
    assert totals.total_usd == pytest.approx(300000.00001658977)


def test_sale_totals_have_no_royalty_deduction():
    fine_oz = ledger.compute_purchase_totals(2407.391641, 0.95, 4080, 10.7).fine_oz
    totals = ledger.compute_sale_totals(
        fine_oz_sold=fine_oz,
        sale_price_usd_per_oz=4060,
        commission_pct=0.0,
        exchange_rate_bs_per_usd=11.7,
    )
    assert totals.total_venta_usd == pytest.approx(298529.41178121435)
    assert totals.comision_usd == pytest.approx(0.0)
    assert totals.total_final_usd == pytest.approx(298529.41178121435)
    assert totals.total_bs == pytest.approx(3492794.1178402076)


def test_cycle_profit_without_royalty_deduction():
    purchase_totals = ledger.compute_purchase_totals(2407.391641, 0.95, 4080, 10.7)
    sale_totals = ledger.compute_sale_totals(
        purchase_totals.fine_oz, 4060, 0.0, 11.7
    )
    profit = ledger.compute_cycle_profit(
        sale_totals,
        cost_basis_usd=purchase_totals.total_usd,
        cost_basis_bs=purchase_totals.total_bs,
        sale_exchange_rate_bs_per_usd=11.7,
        operating_cost_pct=0.07125,
    )
    assert profit.profit_usd_direct == pytest.approx(-1470.5882353754132)
    assert profit.profit_bs == pytest.approx(282794.1176626971)
    assert profit.operating_cost_bs == pytest.approx(20149.080883467166)
    assert profit.net_profit_bs == pytest.approx(262645.03677922994)
    assert profit.profit_usd_equiv == pytest.approx(24170.43740706813)
    assert profit.operating_cost_usd_equiv == pytest.approx(1722.143665253604)
    assert profit.net_profit_usd_equiv == pytest.approx(22448.293741814527)


def test_price_per_gram_matches_workbook_cotizacion():
    price = ledger.compute_price_per_gram_bs(
        price_usd_per_oz=4670, purity_pct=0.95, exchange_rate_bs_per_usd=9.43
    )
    assert price == pytest.approx(1345.0638995611425)


def test_bcb_cycle_without_royalty_deduction():
    purchase_totals = ledger.compute_purchase_totals(50000, 0.95, 4080, 11.35)
    assert purchase_totals.total_bs == pytest.approx(70719693.92512096)
    assert purchase_totals.total_usd == pytest.approx(6230810.037455592)

    sale_totals = ledger.compute_sale_totals(
        purchase_totals.fine_oz, 4094, 0.0, 11.89
    )
    assert sale_totals.total_venta_usd == pytest.approx(6252190.267976273)
    assert sale_totals.total_bs == pytest.approx(74338542.28623788)

    profit = ledger.compute_cycle_profit(
        sale_totals, purchase_totals.total_usd, purchase_totals.total_bs, 11.89,
        operating_cost_pct=0.07125,
    )
    assert profit.profit_bs == pytest.approx(3618848.361116916)
    assert profit.operating_cost_bs == pytest.approx(257842.94572958024)
    assert profit.net_profit_bs == pytest.approx(3361005.4153873357)


def test_mercado_interno_spread_is_value_times_rate_difference():
    spread = ledger.compute_mercado_interno_spread(
        weight_g=100, purity_pct=0.95, price_usd_per_oz=4500,
        tc_minero_compra=10.9, tc_minero_venta=11.0,
    )
    assert spread.fine_oz == pytest.approx(3.0543186458115645)
    assert spread.value_usd == pytest.approx(13744.43390615204)
    assert spread.total_compra_bs == pytest.approx(149814.32957705724)
    assert spread.total_venta_bs == pytest.approx(151188.77296767244)
    # Same USD value both sides, so the difference is just value_usd x (venta - compra).
    assert spread.diferencia_bs == pytest.approx(spread.value_usd * 0.1)
    assert spread.diferencia_bs == pytest.approx(1374.4433906151971)
