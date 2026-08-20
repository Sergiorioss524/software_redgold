"""Smoke tests for the web dashboard's golden path: the same-day buy/sell
round-trip calculator.
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


def test_dashboard_loads(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "¿Comprar y vender al BCB hoy es rentable?".encode() in resp.data


def test_round_trip_calculator_computes_profit(client):
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
            "rt_commission": "0.0",
        },
    )
    assert resp.status_code == 200
    assert b"282,794.12" in resp.data  # gross profit_bs (no royalty deduction on the sale side)
    assert b"262,645.04" in resp.data  # net_profit_bs (after operating cost)
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
            "rt_commission": "0.0",
        },
    )
    assert resp.status_code == 200
    assert "No, hoy no conviene".encode() in resp.data
