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
    assert b"Ciclos" in resp.data


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


def test_dashboard_reference_calculator(client, monkeypatch):
    from datetime import date

    history = webapp.PriceHistory(webapp.config.DATABASE_URL)
    from datetime import datetime, timezone
    history.save_quote(date.today(), "netdania", 4670.0, datetime.now(timezone.utc), "raw")

    resp = client.get("/?calc_category=EXPORT&purity=0.95&rate=9.43")
    assert resp.status_code == 200
    assert b"1345.06" in resp.data  # PRECIO GR Bs from the workbook regression numbers
