"""Local web dashboard for the gold buy/sell/reinvest ledger.

Run with:
    python -m redgold.webapp
or:
    flask --app redgold.webapp run
"""
from __future__ import annotations

import os
from datetime import date, datetime
from typing import Optional

from flask import Flask, flash, redirect, render_template, request, url_for

from redgold import config
from redgold.ledger import (
    CATEGORIES,
    CATEGORY_BCB,
    CATEGORY_EXPORT,
    InsufficientInventoryError,
    Ledger,
    compute_cycle_profit,
    compute_purchase_totals,
    compute_sale_totals,
)
from redgold.pipeline import DEFAULT_SOURCES, run_daily_update
from redgold.sources.base import GoldPriceUnavailableError
from redgold.sources.exchange_rate import ExchangeRateUnavailableError, fetch_official_rate
from redgold.storage import PriceHistory

app = Flask(__name__)
app.secret_key = os.getenv("REDGOLD_SECRET_KEY", "dev-only-secret-change-me")

CATEGORY_LABELS = {
    CATEGORY_EXPORT: "Oro → Exportación",
    CATEGORY_BCB: "Material → BCB",
}

DEFAULT_ROYALTY_PCT = {
    CATEGORY_EXPORT: config.DEFAULT_ROYALTY_PCT_EXPORT,
    CATEGORY_BCB: config.DEFAULT_ROYALTY_PCT_BCB,
}


def get_ledger() -> Ledger:
    return Ledger(config.DATABASE_URL)


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


@app.context_processor
def inject_globals():
    return {"category_labels": CATEGORY_LABELS, "categories": CATEGORIES}


@app.route("/")
def dashboard():
    ledger = get_ledger()
    history = get_history()

    today = date.today()
    latest_price = None
    for source in DEFAULT_SOURCES:
        quote = history.get_quote(today, source.name)
        if quote is not None:
            latest_price = quote
            break

    cycles = []
    for category in CATEGORIES:
        inventory = ledger.inventory_fine_oz(category)
        avg_usd, avg_bs = ledger.average_cost(category)
        cycles.append({
            "category": category,
            "label": CATEGORY_LABELS[category],
            "inventory_fine_oz": inventory,
            "avg_cost_usd_per_oz": avg_usd,
            "avg_cost_bs_per_oz": avg_bs,
        })

    official_rate = get_official_rate()

    round_trip = _parse_round_trip_calc(request.args, latest_price)
    inventory_sale = _parse_inventory_sale_calc(request.args, ledger)

    return render_template(
        "dashboard.html",
        latest_price=latest_price,
        official_rate=official_rate,
        cycles=cycles,
        round_trip=round_trip,
        inventory_sale=inventory_sale,
        default_purity=config.DEFAULT_PURITY_PCT,
        default_royalty=DEFAULT_ROYALTY_PCT,
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
        royalty_pct = float(args.get("rt_royalty", DEFAULT_ROYALTY_PCT.get(category, 0.0)))
        commission_pct = float(args.get("rt_commission", config.DEFAULT_COMMISSION_PCT))
    except (KeyError, ValueError):
        return None

    purchase_totals = compute_purchase_totals(weight_g, purity_pct, buy_price, buy_rate)
    sale_totals = compute_sale_totals(
        purchase_totals.fine_oz, sell_price, royalty_pct, commission_pct, sell_rate
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
        "royalty_pct": royalty_pct,
        "commission_pct": commission_pct,
        "purchase_totals": purchase_totals,
        "sale_totals": sale_totals,
        "profit": profit,
    }


def _parse_inventory_sale_calc(args, ledger: Ledger) -> Optional[dict]:
    """'Sell what I already own, today' simulator -- uses the REAL
    weighted-average cost basis from recorded purchases in the ledger,
    compared against a hypothetical today's sale. Nothing is saved."""
    if "inv_sell_price" not in args:
        return None
    try:
        category = args.get("inv_category", CATEGORY_EXPORT)
        if category not in CATEGORIES:
            return None
        sell_price = float(args["inv_sell_price"])
        sell_rate = float(args["inv_sell_rate"])
        royalty_pct = float(args.get("inv_royalty", DEFAULT_ROYALTY_PCT.get(category, 0.0)))
        commission_pct = float(args.get("inv_commission", config.DEFAULT_COMMISSION_PCT))
    except (KeyError, ValueError):
        return None

    available = ledger.inventory_fine_oz(category)
    try:
        fine_oz_sold = float(args.get("inv_fine_oz", available))
    except ValueError:
        return None
    if fine_oz_sold <= 0:
        return None

    avg_usd, avg_bs = ledger.average_cost(category)
    sale_totals = compute_sale_totals(
        fine_oz_sold, sell_price, royalty_pct, commission_pct, sell_rate
    )
    profit = compute_cycle_profit(
        sale_totals, fine_oz_sold * avg_usd, fine_oz_sold * avg_bs, sell_rate
    )
    return {
        "category": category,
        "available_fine_oz": available,
        "fine_oz_sold": fine_oz_sold,
        "avg_cost_usd_per_oz": avg_usd,
        "avg_cost_bs_per_oz": avg_bs,
        "sell_price": sell_price,
        "sell_rate": sell_rate,
        "royalty_pct": royalty_pct,
        "commission_pct": commission_pct,
        "sale_totals": sale_totals,
        "profit": profit,
        "oversell": fine_oz_sold > available + 1e-9,
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


@app.route("/purchases")
def list_purchases():
    ledger = get_ledger()
    category = request.args.get("category")
    purchases = ledger.list_purchases(category if category in CATEGORIES else None)
    return render_template("purchases.html", purchases=purchases, filter_category=category)


@app.route("/purchases/new", methods=["GET", "POST"])
def new_purchase():
    if request.method == "POST":
        try:
            ledger = get_ledger()
            purchase = ledger.add_purchase(
                purchase_date=datetime.strptime(request.form["purchase_date"], "%Y-%m-%d").date(),
                category=request.form["category"],
                weight_g=float(request.form["weight_g"]),
                purity_pct=float(request.form["purity_pct"]),
                price_usd_per_oz=float(request.form["price_usd_per_oz"]),
                exchange_rate_bs_per_usd=float(request.form["exchange_rate_bs_per_usd"]),
                notes=request.form.get("notes", ""),
            )
            flash(
                f"Purchase #{purchase.id} recorded: {purchase.totals.fine_oz:.4f} fine oz "
                f"for {purchase.totals.total_usd:,.2f} USD.",
                "success",
            )
            return redirect(url_for("list_purchases"))
        except (KeyError, ValueError) as exc:
            flash(f"Could not save purchase: {exc}", "error")

    return render_template(
        "purchase_form.html",
        default_purity=config.DEFAULT_PURITY_PCT,
        today=date.today(),
    )


@app.route("/sales")
def list_sales():
    ledger = get_ledger()
    category = request.args.get("category")
    sales = ledger.list_sales(category if category in CATEGORIES else None)
    rows = [(sale, ledger.sale_profit(sale)) for sale in sales]
    return render_template("sales.html", rows=rows, filter_category=category)


@app.route("/sales/new", methods=["GET", "POST"])
def new_sale():
    ledger = get_ledger()
    if request.method == "POST":
        try:
            category = request.form["category"]
            sale = ledger.add_sale(
                sale_date=datetime.strptime(request.form["sale_date"], "%Y-%m-%d").date(),
                category=category,
                fine_oz_sold=float(request.form["fine_oz_sold"]),
                sale_price_usd_per_oz=float(request.form["sale_price_usd_per_oz"]),
                royalty_pct=float(request.form["royalty_pct"]),
                commission_pct=float(request.form["commission_pct"]),
                exchange_rate_bs_per_usd=float(request.form["exchange_rate_bs_per_usd"]),
                notes=request.form.get("notes", ""),
            )
            profit = ledger.sale_profit(sale)
            flash(
                f"Sale #{sale.id} recorded: net profit {profit.net_profit_bs:,.2f} Bs "
                f"({profit.net_profit_usd_equiv:,.2f} USD equiv.).",
                "success",
            )
            return redirect(url_for("list_sales"))
        except InsufficientInventoryError as exc:
            flash(str(exc), "error")
        except (KeyError, ValueError) as exc:
            flash(f"Could not save sale: {exc}", "error")

    inventory = {category: ledger.inventory_fine_oz(category) for category in CATEGORIES}
    return render_template(
        "sale_form.html",
        inventory=inventory,
        default_royalty=DEFAULT_ROYALTY_PCT,
        default_commission=config.DEFAULT_COMMISSION_PCT,
        official_rate=get_official_rate(),
        today=date.today(),
    )


if __name__ == "__main__":
    app.run(debug=True, port=int(os.getenv("PORT", "5000")))
