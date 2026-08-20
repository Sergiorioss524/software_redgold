"""Local web dashboard: a same-day buy/sell-to-BCB profitability calculator.

Run with:
    python -m redgold.webapp
or:
    flask --app redgold.webapp run
"""
from __future__ import annotations

import os
from datetime import date
from typing import Optional

from flask import Flask, flash, redirect, render_template, request, url_for

from redgold import config
from redgold.ledger import (
    CATEGORIES,
    CATEGORY_BCB,
    CATEGORY_EXPORT,
    compute_cycle_profit,
    compute_purchase_totals,
    compute_sale_totals,
)
from redgold.pipeline import DEFAULT_SOURCES, run_daily_update
from redgold.sources.base import GoldPriceUnavailableError
from redgold.sources.exchange_rate import ExchangeRateUnavailableError, fetch_official_rate
from redgold.sources.metals import MetalPriceUnavailableError, fetch_gold_quote
from redgold.storage import PriceHistory

app = Flask(__name__)
app.secret_key = os.getenv("REDGOLD_SECRET_KEY", "dev-only-secret-change-me")

CATEGORY_LABELS = {
    CATEGORY_EXPORT: "Oro → Exportación",
    CATEGORY_BCB: "Material → BCB",
}


@app.template_filter("money")
def format_money(value, decimals=2):
    """Thousands-separated number, e.g. 50040.6912 -> '50,040.69'."""
    return f"{value:,.{decimals}f}"


def get_history() -> PriceHistory:
    return PriceHistory(config.DATABASE_URL)


def get_official_rate():
    """Best-effort fetch of the BCB's official USD/BOB buying rate, used
    only to prefill "TC de venta" inputs. Returns None on any failure so
    callers fall back to letting the user type the rate in by hand."""
    try:
        return fetch_official_rate()
    except ExchangeRateUnavailableError:
        return None


def get_bcb_gold_quote():
    """Best-effort fetch of the BCB's own gold quote (Bs per troy ounce),
    used only to prefill "Bolsa de venta". Returns None on any failure so
    callers fall back to letting the user type the price in by hand."""
    try:
        return fetch_gold_quote()
    except MetalPriceUnavailableError:
        return None


@app.context_processor
def inject_globals():
    return {"category_labels": CATEGORY_LABELS, "categories": CATEGORIES}


@app.route("/")
def dashboard():
    history = get_history()

    today = date.today()
    latest_price = None
    for source in DEFAULT_SOURCES:
        quote = history.get_quote(today, source.name)
        if quote is not None:
            latest_price = quote
            break

    official_rate = get_official_rate()

    bcb_gold = get_bcb_gold_quote()
    bcb_gold_price_usd = None
    if bcb_gold is not None and official_rate is not None:
        bcb_gold_price_usd = round(bcb_gold.price_bs_per_oz / official_rate.compra, 2)

    # "Tipo de cambio minero": the rate at which a miner effectively sells,
    # net of the BCB category's fixed royalty -- not fetched anywhere, but
    # derivable since the royalty is a fixed, known percentage.
    tc_minero = None
    if official_rate is not None:
        tc_minero = round(official_rate.compra * (1 - config.DEFAULT_ROYALTY_PCT_BCB), 4)

    round_trip = _parse_round_trip_calc(request.args, latest_price)

    return render_template(
        "dashboard.html",
        latest_price=latest_price,
        official_rate=official_rate,
        bcb_gold=bcb_gold,
        bcb_gold_price_usd=bcb_gold_price_usd,
        tc_minero=tc_minero,
        round_trip=round_trip,
        default_purity=config.DEFAULT_PURITY_PCT,
        default_commission=config.DEFAULT_COMMISSION_PCT,
        today=today,
    )


def _parse_round_trip_calc(args, latest_price) -> Optional[dict]:
    """'Buy today, sell today' simulator -- pure what-if, never touches the
    ledger. Answers "if I bought and flipped this right now, what would I
    make," using compute_purchase_totals -> compute_sale_totals ->
    compute_cycle_profit exactly as the ledger does for a real trade."""
    if "rt_weight_g" not in args:
        return None
    try:
        category = args.get("rt_category", CATEGORY_EXPORT)
        weight_g = float(args["rt_weight_g"])
        purity_pct = float(args.get("rt_purity", config.DEFAULT_PURITY_PCT))
        buy_price = float(args["rt_buy_price"])
        buy_rate = float(args["rt_buy_rate"])
        # TC compra $ físico: the rate used to peg the physical-dollar Bs
        # cost shown live in the form. Not consumed by compute_purchase_totals
        # below (that still uses buy_rate) -- kept only so the field survives
        # a "Calcular" round trip instead of resetting to blank.
        buy_rate_fisico = float(args.get("rt_buy_rate_fisico", buy_rate))
        sell_price = float(args.get("rt_sell_price", buy_price))
        sell_rate = float(args.get("rt_sell_rate", buy_rate))
        commission_pct = float(args.get("rt_commission", config.DEFAULT_COMMISSION_PCT))
    except (KeyError, ValueError):
        return None

    purchase_totals = compute_purchase_totals(weight_g, purity_pct, buy_price, buy_rate)
    sale_totals = compute_sale_totals(
        purchase_totals.fine_oz, sell_price, commission_pct, sell_rate
    )
    profit = compute_cycle_profit(
        sale_totals, purchase_totals.total_usd, purchase_totals.total_bs, sell_rate
    )
    return {
        "category": category,
        "weight_g": weight_g,
        "purity_pct": purity_pct,
        "buy_price": buy_price,
        "buy_rate": buy_rate,
        "buy_rate_fisico": buy_rate_fisico,
        "sell_price": sell_price,
        "sell_rate": sell_rate,
        "commission_pct": commission_pct,
        "purchase_totals": purchase_totals,
        "sale_totals": sale_totals,
        "profit": profit,
    }


@app.route("/price/fetch", methods=["POST"])
def fetch_price():
    try:
        adjustment = run_daily_update()
        flash(
            f"Fetched {adjustment.price_usd_per_oz:.2f} USD/oz from "
            f"{adjustment.source} for {adjustment.quote_date}.",
            "success",
        )
    except GoldPriceUnavailableError as exc:
        flash(f"Could not fetch today's price: {exc}", "error")
    return redirect(url_for("dashboard"))


if __name__ == "__main__":
    app.run(debug=True, port=int(os.getenv("PORT", "5000")))
