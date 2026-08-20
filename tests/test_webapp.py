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


def test_pankara_calculator_converts_sale_at_raw_kibo_rate(client):
    resp = client.get(
        "/",
        query_string={
            "pk_weight_g": "100",
            "pk_purity": "0.95",
            "pk_buy_price": "4080",
            "pk_tc_kibo": "11.0",
            "pk_discount": "0.056",
            "pk_buy_rate": "10.384",  # 11.0 x (1 - 0.056), as the client-side JS would suggest
            "pk_sell_price": "4520",
            # No pk_sell_rate -- Pankara pays in USDT, converted to Bs at the
            # raw (undiscounted) TC KIBO, not the discounted buy_rate.
            "pk_commission": "0.0",
        },
    )
    assert resp.status_code == 200
    assert b"20,859.04" in resp.data  # net_profit_bs
    assert "Sí, hoy conviene".encode() in resp.data
    assert b"pk_sell_rate" not in resp.data
    assert b"pk_discount" in resp.data


def test_mercado_interno_calculator_computes_spread(client):
    resp = client.get(
        "/",
        query_string={
            "mi_weight_g": "100",
            "mi_purity": "0.95",
            "mi_price": "4500",
            "mi_tc_compra": "10.9",
            "mi_tc_venta": "11.0",
        },
    )
    assert resp.status_code == 200
    assert b"149,814.33" in resp.data  # total_compra_bs
    assert b"151,188.77" in resp.data  # total_venta_bs
    assert b"1,374.44" in resp.data  # diferencia_bs
    assert "Sí, hoy conviene".encode() in resp.data


def test_comparador_ranks_all_three_channels(client):
    resp = client.get(
        "/comparador",
        query_string={
            "cp_weight_g": "100",
            "cp_purity": "0.95",
            "cp_bolsa": "4500",
            "cp_bcb_tc_oficial": "11.52",
            "cp_bcb_bolsa_venta": "4510",
            "cp_bcb_commission": "0.0",
            "cp_pk_tc_kibo": "11.0",
            "cp_pk_discount": "0.056",
            "cp_pk_bolsa_venta": "4510",
            "cp_pk_commission": "0.0",
            "cp_mi_tc_compra": "10.9",
            "cp_mi_tc_venta": "11.0",
        },
    )
    assert resp.status_code == 200
    assert b"8,175.36" in resp.data  # Pankara net_profit_bs -- best of the three
    assert b"7,385.91" in resp.data  # BCB net_profit_bs
    assert b"1,374.44" in resp.data  # mercado interno diferencia_bs -- worst of the three
    assert "Conviene más".encode() in resp.data
    # Ranked best to worst: Pankara, then BCB, then mercado interno.
    pk_pos = resp.data.find(b"1. Pankara")
    bcb_pos = resp.data.find(b"2. BCB")
    mi_pos = resp.data.find(b"3. Mercado interno")
    assert 0 < pk_pos < bcb_pos < mi_pos


def test_comparador_handles_partial_data(client):
    resp = client.get(
        "/comparador",
        query_string={
            "cp_weight_g": "100",
            "cp_purity": "0.95",
            "cp_bolsa": "4500",
            "cp_bcb_tc_oficial": "11.52",
            "cp_bcb_bolsa_venta": "4510",
            "cp_bcb_commission": "0.0",
        },
    )
    assert resp.status_code == 200
    assert "Faltan datos".encode() in resp.data
    assert "Pankara".encode() in resp.data
    assert "mercado interno".encode() in resp.data
