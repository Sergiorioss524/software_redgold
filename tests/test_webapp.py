"""Smoke tests for the web dashboard's golden paths: view dashboard, record
a purchase, record a sale against it, view the lists, reject an oversell.
"""
from __future__ import annotations

import pytest

from redgold import ledger as ledger_module
from redgold import webapp


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "test_web.db"
    monkeypatch.setattr(webapp.config, "DATABASE_URL", f"sqlite:///{db_path}")
    webapp.app.config.update(TESTING=True)
    return webapp.app.test_client()


def test_dashboard_loads_with_empty_ledger(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "¿Comprar y vender hoy es rentable?".encode() in resp.data


def test_purchase_form_loads(client):
    resp = client.get("/purchases/new")
    assert resp.status_code == 200
    assert b"Registrar compra" in resp.data


def test_record_purchase_then_sale_golden_path(client):
    resp = client.post(
        "/purchases/new",
        data={
            "purchase_date": "2026-01-01",
            "category": ledger_module.CATEGORY_EXPORT,
            "weight_g": "2407.391641",
            "purity_pct": "0.95",
            "price_usd_per_oz": "4080",
            "exchange_rate_bs_per_usd": "10.7",
            "notes": "test purchase",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "recorded".encode() in resp.data or "registrada".encode() in resp.data or b"Purchase" in resp.data

    resp = client.get("/purchases")
    assert b"2407.3916" in resp.data or b"2407.4" in resp.data

    resp = client.post(
        "/sales/new",
        data={
            "sale_date": "2026-01-02",
            "category": ledger_module.CATEGORY_EXPORT,
            "fine_oz_sold": "73.52941176877201",
            "sale_price_usd_per_oz": "4060",
            "royalty_pct": "0.009",
            "commission_pct": "0.0",
            "exchange_rate_bs_per_usd": "11.7",
            "notes": "test sale",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    resp = client.get("/sales")
    assert resp.status_code == 200
    assert b"251358.97" in resp.data  # profit_bs from the workbook regression numbers


def test_oversell_is_rejected_via_form(client):
    client.post(
        "/purchases/new",
        data={
            "purchase_date": "2026-01-01",
            "category": ledger_module.CATEGORY_EXPORT,
            "weight_g": "100",
            "purity_pct": "0.95",
            "price_usd_per_oz": "4000",
            "exchange_rate_bs_per_usd": "10.0",
            "notes": "",
        },
    )
    resp = client.post(
        "/sales/new",
        data={
            "sale_date": "2026-01-02",
            "category": ledger_module.CATEGORY_EXPORT,
            "fine_oz_sold": "999",
            "sale_price_usd_per_oz": "4000",
            "royalty_pct": "0.009",
            "commission_pct": "0.0",
            "exchange_rate_bs_per_usd": "10.0",
            "notes": "",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"cannot sell" in resp.data


def test_round_trip_calculator_matches_workbook_profit(client):
    resp = client.get(
        "/",
        query_string={
            "rt_category": ledger_module.CATEGORY_EXPORT,
            "rt_weight_g": "2407.391641",
            "rt_purity": "0.95",
            "rt_buy_price": "4080",
            "rt_buy_rate": "10.7",
            "rt_sell_price": "4060",
            "rt_sell_rate": "11.7",
            "rt_royalty": "0.009",
            "rt_commission": "0.0",
        },
    )
    assert resp.status_code == 200
    assert b"251358.97" in resp.data  # gross profit_bs from the workbook regression numbers
    assert b"233449.64" in resp.data  # net_profit_bs (after operating cost)
    assert "Sí, hoy conviene".encode() in resp.data


def test_round_trip_calculator_flags_a_loss(client):
    resp = client.get(
        "/",
        query_string={
            "rt_category": ledger_module.CATEGORY_EXPORT,
            "rt_weight_g": "100",
            "rt_purity": "0.95",
            "rt_buy_price": "4200",
            "rt_buy_rate": "11.0",
            "rt_sell_price": "3900",
            "rt_sell_rate": "10.5",
            "rt_royalty": "0.009",
            "rt_commission": "0.0",
        },
    )
    assert resp.status_code == 200
    assert "No, hoy no conviene".encode() in resp.data


def test_inventory_sale_calculator_uses_real_cost_basis(client):
    client.post(
        "/purchases/new",
        data={
            "purchase_date": "2026-01-01",
            "category": ledger_module.CATEGORY_EXPORT,
            "weight_g": "2407.391641",
            "purity_pct": "0.95",
            "price_usd_per_oz": "4080",
            "exchange_rate_bs_per_usd": "10.7",
            "notes": "",
        },
    )
    resp = client.get(
        "/",
        query_string={
            "inv_category": ledger_module.CATEGORY_EXPORT,
            "inv_sell_price": "4060",
            "inv_sell_rate": "11.7",
            "inv_royalty": "0.009",
            "inv_commission": "0.0",
        },
    )
    assert resp.status_code == 200
    assert b"251358.97" in resp.data


def test_inventory_sale_calculator_flags_oversell(client):
    client.post(
        "/purchases/new",
        data={
            "purchase_date": "2026-01-01",
            "category": ledger_module.CATEGORY_EXPORT,
            "weight_g": "100",
            "purity_pct": "0.95",
            "price_usd_per_oz": "4000",
            "exchange_rate_bs_per_usd": "10.0",
            "notes": "",
        },
    )
    resp = client.get(
        "/",
        query_string={
            "inv_category": ledger_module.CATEGORY_EXPORT,
            "inv_fine_oz": "999",
            "inv_sell_price": "4000",
            "inv_sell_rate": "10.0",
            "inv_royalty": "0.009",
            "inv_commission": "0.0",
        },
    )
    assert resp.status_code == 200
    assert "hipotético".encode() in resp.data
