"""Local web dashboard for the gold buy/sell/reinvest ledger.

Run with:
    python -m redgold.webapp
or:
    flask --app redgold.webapp run
"""
from __future__ import annotations

import os
from datetime import date, datetime

from flask import Flask, flash, redirect, render_template, request, url_for

from redgold import config
from redgold.ledger import (
    CATEGORIES,
    CATEGORY_BCB,
    CATEGORY_EXPORT,
    InsufficientInventoryError,
    Ledger,
    compute_affordable_grams,
    compute_price_per_gram_bs,
)
from redgold.pipeline import DEFAULT_SOURCES, run_daily_update
from redgold.sources.base import GoldPriceUnavailableError
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
        sales = ledger.list_sales(category)
        latest_sale = sales[-1] if sales else None
        latest_profit = ledger.sale_profit(latest_sale) if latest_sale else None
        cycles.append({
            "category": category,
            "label": CATEGORY_LABELS[category],
            "inventory_fine_oz": inventory,
            "avg_cost_usd_per_oz": avg_usd,
            "avg_cost_bs_per_oz": avg_bs,
            "purchase_count": len(ledger.list_purchases(category)),
            "sale_count": len(sales),
            "latest_sale": latest_sale,
            "latest_profit": latest_profit,
        })

    # Informational reference calculator (COTIZACION PRECIO POR GR AL DIA) --
    # never creates a transaction, purely a "what could today's profit buy" figure.
    calc = None
    try:
        purity_pct = float(request.args.get("purity", config.DEFAULT_PURITY_PCT))
        rate = request.args.get("rate")
        calc_category = request.args.get("calc_category", CATEGORY_EXPORT)
        if rate and latest_price and calc_category in CATEGORIES:
            exchange_rate = float(rate)
            price_per_gram_bs = compute_price_per_gram_bs(
                latest_price.price_usd_per_oz, purity_pct, exchange_rate
            )
            cycle = next(c for c in cycles if c["category"] == calc_category)
            net_profit_bs = (
                cycle["latest_profit"].net_profit_bs if cycle["latest_profit"] else 0.0
            )
            affordable_grams = compute_affordable_grams(net_profit_bs, price_per_gram_bs)
            calc = {
                "purity_pct": purity_pct,
                "exchange_rate": exchange_rate,
                "category": calc_category,
                "price_per_gram_bs": price_per_gram_bs,
                "net_profit_bs_used": net_profit_bs,
                "affordable_grams": affordable_grams,
            }
    except (TypeError, ValueError):
        calc = None

    return render_template(
        "dashboard.html",
        latest_price=latest_price,
        cycles=cycles,
        calc=calc,
        default_purity=config.DEFAULT_PURITY_PCT,
        today=today,
    )


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
        today=date.today(),
    )


if __name__ == "__main__":
    app.run(debug=True, port=int(os.getenv("PORT", "5000")))
