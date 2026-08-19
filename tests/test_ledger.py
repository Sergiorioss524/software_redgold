"""Regression tests: ledger math must reproduce the exact cell values from
the real BALANCE_ULTIMO.xlsx (COMPRA DE ORO 1 / VENTA EXPORT ORO / REDGOLD,
rows 3-18) that the ledger module was derived from.
"""
from __future__ import annotations

from datetime import date

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


def test_sale_totals_match_workbook_venta_export_oro():
    fine_oz = ledger.compute_purchase_totals(2407.391641, 0.95, 4080, 10.7).fine_oz
    totals = ledger.compute_sale_totals(
        fine_oz_sold=fine_oz,
        sale_price_usd_per_oz=4060,
        royalty_pct=0.009,
        commission_pct=0.0,
        exchange_rate_bs_per_usd=11.7,
    )
    assert totals.total_venta_usd == pytest.approx(298529.41178121435)
    assert totals.regalias_usd == pytest.approx(2686.7647060309296)
    assert totals.neto_venta_usd == pytest.approx(295842.6470751834)
    assert totals.comision_usd == pytest.approx(0.0)
    assert totals.total_final_usd == pytest.approx(295842.6470751834)
    assert totals.total_bs == pytest.approx(3461358.9707796457)


def test_cycle_profit_matches_workbook_redgold_block():
    purchase_totals = ledger.compute_purchase_totals(2407.391641, 0.95, 4080, 10.7)
    sale_totals = ledger.compute_sale_totals(
        purchase_totals.fine_oz, 4060, 0.009, 0.0, 11.7
    )
    profit = ledger.compute_cycle_profit(
        sale_totals,
        cost_basis_usd=purchase_totals.total_usd,
        cost_basis_bs=purchase_totals.total_bs,
        sale_exchange_rate_bs_per_usd=11.7,
        operating_cost_pct=0.07125,
    )
    assert profit.profit_usd_direct == pytest.approx(-4157.352941406367)
    assert profit.profit_bs == pytest.approx(251358.97060213517)
    assert profit.operating_cost_bs == pytest.approx(17909.32665540213)
    assert profit.net_profit_bs == pytest.approx(233449.64394673303)
    assert profit.profit_usd_equiv == pytest.approx(21483.672701037194)
    assert profit.operating_cost_usd_equiv == pytest.approx(1530.7116799489)
    assert profit.net_profit_usd_equiv == pytest.approx(19952.961021088297)


def test_price_per_gram_matches_workbook_cotizacion():
    price = ledger.compute_price_per_gram_bs(
        price_usd_per_oz=4670, purity_pct=0.95, exchange_rate_bs_per_usd=9.43
    )
    assert price == pytest.approx(1345.0638995611425)


def test_bcb_cycle_matches_workbook():
    purchase_totals = ledger.compute_purchase_totals(50000, 0.95, 4080, 11.35)
    assert purchase_totals.total_bs == pytest.approx(70719693.92512096)
    assert purchase_totals.total_usd == pytest.approx(6230810.037455592)

    sale_totals = ledger.compute_sale_totals(
        purchase_totals.fine_oz, 4094, 0.048, 0.0, 11.89
    )
    assert sale_totals.total_venta_usd == pytest.approx(6252190.267976273)
    assert sale_totals.regalias_usd == pytest.approx(300105.1328628611)
    assert sale_totals.total_bs == pytest.approx(70770292.25649847)

    profit = ledger.compute_cycle_profit(
        sale_totals, purchase_totals.total_usd, purchase_totals.total_bs, 11.89,
        operating_cost_pct=0.07125,
    )
    assert profit.profit_bs == pytest.approx(50598.331377506256)
    assert profit.operating_cost_bs == pytest.approx(3605.1311106473204)
    assert profit.net_profit_bs == pytest.approx(46993.200266858934)


# --------------------------------------------------------------------------
# Ledger storage behavior (not directly in the sheet, but required for a
# multi-transaction system rather than a single snapshot)
# --------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    return ledger.Ledger(f"sqlite:///{tmp_path / 'test.db'}")


def test_single_purchase_single_sale_reproduces_workbook(store):
    purchase = store.add_purchase(
        date(2026, 1, 1), ledger.CATEGORY_EXPORT, 2407.391641, 0.95, 4080, 10.7
    )
    sale = store.add_sale(
        date(2026, 1, 2), ledger.CATEGORY_EXPORT,
        purchase.totals.fine_oz, 4060, 0.009, 0.0, 11.7,
    )
    profit = store.sale_profit(sale)
    assert profit.profit_bs == pytest.approx(251358.97060213517)
    assert store.inventory_fine_oz(ledger.CATEGORY_EXPORT) == pytest.approx(0.0)


def test_sale_uses_weighted_average_cost_across_multiple_purchases(store):
    store.add_purchase(date(2026, 1, 1), ledger.CATEGORY_EXPORT, 1000, 0.95, 4000, 10.0)
    store.add_purchase(date(2026, 1, 2), ledger.CATEGORY_EXPORT, 1000, 0.95, 4200, 10.0)
    avg_usd, avg_bs = store.average_cost(ledger.CATEGORY_EXPORT)
    # equal-weight purchases at 4000 and 4200 USD/oz -> average should be their midpoint
    assert avg_usd == pytest.approx(4100, rel=1e-6)


def test_overselling_inventory_is_rejected(store):
    store.add_purchase(date(2026, 1, 1), ledger.CATEGORY_EXPORT, 100, 0.95, 4000, 10.0)
    available = store.inventory_fine_oz(ledger.CATEGORY_EXPORT)
    with pytest.raises(ledger.InsufficientInventoryError):
        store.add_sale(
            date(2026, 1, 2), ledger.CATEGORY_EXPORT,
            available + 10, 4000, 0.009, 0.0, 10.0,
        )


def test_categories_are_isolated(store):
    store.add_purchase(date(2026, 1, 1), ledger.CATEGORY_EXPORT, 100, 0.95, 4000, 10.0)
    assert store.inventory_fine_oz(ledger.CATEGORY_BCB) == 0.0


def test_invalid_category_rejected(store):
    with pytest.raises(ValueError):
        store.add_purchase(date(2026, 1, 1), "NOT_A_CATEGORY", 100, 0.95, 4000, 10.0)
