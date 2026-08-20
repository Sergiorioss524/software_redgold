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
    compute_mercado_interno_spread,
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


def _tc_minero_bcb(tc_oficial: Optional[float]) -> Optional[float]:
    if tc_oficial is None:
        return None
    return round(tc_oficial * (1 - config.DEFAULT_ROYALTY_PCT_BCB), 4)


def _tc_minero_pankara(tc_kibo: Optional[float], discount_pct: Optional[float]) -> Optional[float]:
    if tc_kibo is None:
        return None
    discount = discount_pct if discount_pct is not None else config.DEFAULT_PANKARA_DISCOUNT_PCT
    return round(tc_kibo * (1 - discount), 4)


def _average(*values: Optional[float]) -> Optional[float]:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return round(sum(present) / len(present), 4)


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

    netdania_price = history.get_quote(today, "netdania")

    official_rate = get_official_rate()

    bcb_gold = get_bcb_gold_quote()
    bcb_gold_price_usd = None
    if bcb_gold is not None and official_rate is not None:
        bcb_gold_price_usd = round(bcb_gold.price_bs_per_oz / official_rate.compra, 2)

    # "Tipo de cambio minero": the rate at which a miner effectively sells,
    # net of the BCB category's fixed royalty -- not fetched anywhere, but
    # derivable since the royalty is a fixed, known percentage.
    tc_minero = _tc_minero_bcb(official_rate.compra if official_rate else None)

    round_trip = _parse_round_trip_calc(request.args, "rt", latest_price)
    pankara = _parse_round_trip_calc(request.args, "pk", latest_price)

    # Mercado interno's TC minero compra/venta have no formula of their
    # own -- suggest starting both from the average of whichever mining
    # rates we do have (BCB, Pankara), then let the user spread them apart
    # by hand to build their own compra/venta.
    tc_minero_mi_avg = _average(tc_minero, pankara["buy_rate"] if pankara else None)

    mercado_interno = _parse_mercado_interno_calc(request.args, netdania_price)

    return render_template(
        "dashboard.html",
        latest_price=latest_price,
        netdania_price=netdania_price,
        official_rate=official_rate,
        bcb_gold=bcb_gold,
        bcb_gold_price_usd=bcb_gold_price_usd,
        tc_minero=tc_minero,
        tc_minero_mi_avg=tc_minero_mi_avg,
        round_trip=round_trip,
        pankara=pankara,
        mercado_interno=mercado_interno,
        default_purity=config.DEFAULT_PURITY_PCT,
        default_commission=config.DEFAULT_COMMISSION_PCT,
        default_pankara_discount=config.DEFAULT_PANKARA_DISCOUNT_PCT,
        today=today,
    )


def _parse_round_trip_calc(args, prefix, latest_price) -> Optional[dict]:
    """'Buy today, sell today' simulator -- pure what-if, never touches the
    ledger. Answers "if I bought and flipped this right now, what would I
    make," using compute_purchase_totals -> compute_sale_totals ->
    compute_cycle_profit exactly as the ledger does for a real trade.

    `prefix` namespaces the query args ("rt" for the BCB round trip, "pk"
    for Pankara) so multiple calculators can be parsed independently off
    the same request."""
    if f"{prefix}_weight_g" not in args:
        return None
    try:
        category = args.get(f"{prefix}_category", CATEGORY_EXPORT)
        weight_g = float(args[f"{prefix}_weight_g"])
        purity_pct = float(args.get(f"{prefix}_purity", config.DEFAULT_PURITY_PCT))
        buy_price = float(args[f"{prefix}_buy_price"])
        buy_rate = float(args[f"{prefix}_buy_rate"])
        # TC compra $ físico: the rate used to peg the physical-dollar Bs
        # cost shown live in the form. Not consumed by compute_purchase_totals
        # below (that still uses buy_rate) -- kept only so the field survives
        # a "Calcular" round trip instead of resetting to blank.
        buy_rate_fisico = float(args.get(f"{prefix}_buy_rate_fisico", buy_rate))
        # TC KIBO + descuento (Pankara only): the client suggests "tipo de
        # cambio minero" as KIBO x (1 - descuento), both entered by hand
        # since the discount isn't fixed. Neither is consumed below --
        # buy_rate is what's actually used -- they're kept only so the
        # fields survive a "Calcular" round trip.
        tc_kibo_raw = args.get(f"{prefix}_tc_kibo")
        tc_kibo = float(tc_kibo_raw) if tc_kibo_raw else None
        discount_raw = args.get(f"{prefix}_discount")
        discount_pct = float(discount_raw) if discount_raw else None
        sell_price = float(args.get(f"{prefix}_sell_price", buy_price))
        # No separate sell-side rate for Pankara -- they pay in USDT, which
        # gets converted to Bs at KIBO's own rate (undiscounted -- the
        # discount only applies when buying from the miner), not the
        # discounted "tipo de cambio minero". Falls back to buy_rate for
        # calculators with no KIBO rate at all (e.g. BCB, which always
        # submits its own explicit sell_rate anyway).
        sell_rate = float(args.get(f"{prefix}_sell_rate", tc_kibo if tc_kibo is not None else buy_rate))
        commission_pct = float(args.get(f"{prefix}_commission", config.DEFAULT_COMMISSION_PCT))
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
        "tc_kibo": tc_kibo,
        "discount_pct": discount_pct,
        "sell_price": sell_price,
        "sell_rate": sell_rate,
        "commission_pct": commission_pct,
        "purchase_totals": purchase_totals,
        "sale_totals": sale_totals,
        "profit": profit,
    }


def _parse_mercado_interno_calc(args, netdania_price) -> Optional[dict]:
    """"Venta a mercado interno": a much simpler what-if than the round-trip
    calculators -- one market price (from Netdania) on both sides, and two
    manually-entered TC minero rates (compra/venta). The profit is just the
    Bs spread between those two rates on the same USD value."""
    if "mi_weight_g" not in args:
        return None
    try:
        weight_g = float(args["mi_weight_g"])
        purity_pct = float(args.get("mi_purity", config.DEFAULT_PURITY_PCT))
        price = float(args["mi_price"])
        tc_compra = float(args["mi_tc_compra"])
        tc_venta = float(args["mi_tc_venta"])
    except (KeyError, ValueError):
        return None

    spread = compute_mercado_interno_spread(weight_g, purity_pct, price, tc_compra, tc_venta)
    return {
        "weight_g": weight_g,
        "purity_pct": purity_pct,
        "price": price,
        "tc_compra": tc_compra,
        "tc_venta": tc_venta,
        "spread": spread,
    }


@app.route("/comparador")
def comparador():
    history = get_history()

    today = date.today()
    latest_price = None
    for source in DEFAULT_SOURCES:
        quote = history.get_quote(today, source.name)
        if quote is not None:
            latest_price = quote
            break

    netdania_price = history.get_quote(today, "netdania")

    official_rate = get_official_rate()

    bcb_gold = get_bcb_gold_quote()
    bcb_gold_price_usd = None
    if bcb_gold is not None and official_rate is not None:
        bcb_gold_price_usd = round(bcb_gold.price_bs_per_oz / official_rate.compra, 2)

    comparison = _parse_comparador_calc(request.args)

    # Same averaging idea as the dashboard's mercado interno section, but
    # read from this page's own BCB/Pankara fields (whatever's been typed
    # in, falling back to the same defaults those fields prefill with).
    tc_oficial_raw = request.args.get("cp_bcb_tc_oficial")
    tc_oficial_for_avg = float(tc_oficial_raw) if tc_oficial_raw else (official_rate.compra if official_rate else None)
    tc_minero_bcb_for_avg = _tc_minero_bcb(tc_oficial_for_avg)

    tc_kibo_raw = request.args.get("cp_pk_tc_kibo")
    tc_kibo_for_avg = float(tc_kibo_raw) if tc_kibo_raw else None
    discount_raw = request.args.get("cp_pk_discount")
    discount_for_avg = float(discount_raw) if discount_raw else None
    tc_minero_pk_for_avg = _tc_minero_pankara(tc_kibo_for_avg, discount_for_avg)

    tc_minero_mi_avg = _average(tc_minero_bcb_for_avg, tc_minero_pk_for_avg)

    return render_template(
        "comparador.html",
        latest_price=latest_price,
        netdania_price=netdania_price,
        official_rate=official_rate,
        bcb_gold_price_usd=bcb_gold_price_usd,
        tc_minero_mi_avg=tc_minero_mi_avg,
        default_purity=config.DEFAULT_PURITY_PCT,
        default_commission=config.DEFAULT_COMMISSION_PCT,
        default_pankara_discount=config.DEFAULT_PANKARA_DISCOUNT_PCT,
        comparison=comparison,
        today=today,
    )


def _parse_comparador_calc(args) -> Optional[dict]:
    """Ranks the three sale channels (BCB, Pankara, mercado interno)
    against each other for the same peso/ley/bolsa, by running the exact
    same formulas each individual calculator uses. Nothing is fetched or
    saved here -- every channel-specific rate is either prefilled from the
    same sources the individual calculators use, or typed in by hand,
    exactly as on their own pages.

    A channel is only included if its own fields are fully filled in --
    partial data (e.g. only BCB filled) still ranks what's available."""
    if "cp_weight_g" not in args:
        return None
    try:
        weight_g = float(args["cp_weight_g"])
        purity_pct = float(args.get("cp_purity", config.DEFAULT_PURITY_PCT))
        bolsa = float(args["cp_bolsa"])
    except (KeyError, ValueError):
        return None

    results = {}

    try:
        tc_oficial = float(args["cp_bcb_tc_oficial"])
        bolsa_venta = float(args["cp_bcb_bolsa_venta"])
        commission_pct = float(args.get("cp_bcb_commission", config.DEFAULT_COMMISSION_PCT))
        tc_minero = _tc_minero_bcb(tc_oficial)
        purchase_totals = compute_purchase_totals(weight_g, purity_pct, bolsa, tc_minero)
        sale_totals = compute_sale_totals(purchase_totals.fine_oz, bolsa_venta, commission_pct, tc_oficial)
        profit = compute_cycle_profit(
            sale_totals, purchase_totals.total_usd, purchase_totals.total_bs, tc_oficial
        )
        results["bcb"] = {
            "label": "BCB", "tc_minero": tc_minero, "net_profit_bs": profit.net_profit_bs, "profit": profit,
        }
    except (KeyError, ValueError):
        pass

    try:
        tc_kibo = float(args["cp_pk_tc_kibo"])
        discount_pct = float(args.get("cp_pk_discount", config.DEFAULT_PANKARA_DISCOUNT_PCT))
        bolsa_venta = float(args["cp_pk_bolsa_venta"])
        commission_pct = float(args.get("cp_pk_commission", config.DEFAULT_COMMISSION_PCT))
        tc_minero = _tc_minero_pankara(tc_kibo, discount_pct)
        purchase_totals = compute_purchase_totals(weight_g, purity_pct, bolsa, tc_minero)
        # Pankara pays in USDT, converted to Bs at KIBO's own (undiscounted)
        # rate -- the discount only applies buying from the miner.
        sale_totals = compute_sale_totals(purchase_totals.fine_oz, bolsa_venta, commission_pct, tc_kibo)
        profit = compute_cycle_profit(
            sale_totals, purchase_totals.total_usd, purchase_totals.total_bs, tc_kibo
        )
        results["pankara"] = {
            "label": "Pankara", "tc_minero": tc_minero, "net_profit_bs": profit.net_profit_bs, "profit": profit,
        }
    except (KeyError, ValueError):
        pass

    try:
        tc_compra = float(args["cp_mi_tc_compra"])
        tc_venta = float(args["cp_mi_tc_venta"])
        spread = compute_mercado_interno_spread(weight_g, purity_pct, bolsa, tc_compra, tc_venta)
        results["mercado_interno"] = {
            "label": "Mercado interno", "net_profit_bs": spread.diferencia_bs, "spread": spread,
        }
    except (KeyError, ValueError):
        pass

    if not results:
        return None

    ranking = sorted(results.values(), key=lambda r: r["net_profit_bs"], reverse=True)
    return {
        "weight_g": weight_g,
        "purity_pct": purity_pct,
        "bolsa": bolsa,
        "results": results,
        "ranking": ranking,
        "best": ranking[0],
    }


@app.route("/grafico")
def gold_chart():
    return render_template("chart.html")


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
