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
from redgold.regression_demo import build_fitted_latex, fit_regression, generate_synthetic_history
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


def _average(*values: Optional[float]) -> Optional[float]:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return round(sum(present) / len(present), 4)


def _tc_minero_mi_avg_from_args(args, prefix, official_rate) -> Optional[float]:
    """Suggests a starting "TC minero compra/venta" for mercado interno --
    the average of whichever mining rates are available (BCB, Pankara) in
    this same request, read from that page's own BCB/Pankara fields
    (falling back to the same defaults those fields prefill with)."""
    tc_oficial_raw = args.get(f"{prefix}_bcb_tc_oficial")
    tc_oficial = float(tc_oficial_raw) if tc_oficial_raw else (official_rate.compra if official_rate else None)
    tc_minero_bcb = _tc_minero_bcb(tc_oficial)

    # Pankara buys from the miner at raw KIBO (the discount applies
    # selling to Pankara, not here), so that's the rate worth averaging in.
    tc_kibo_raw = args.get(f"{prefix}_pk_tc_kibo")
    tc_minero_pk = float(tc_kibo_raw) if tc_kibo_raw else None

    return _average(tc_minero_bcb, tc_minero_pk)


def _compute_bcb_channel(weight_g, purity_pct, bolsa, args, prefix) -> dict:
    """Raises KeyError/ValueError if this channel's fields aren't filled in
    -- callers catch that to mean "skip this channel"."""
    tc_oficial = float(args[f"{prefix}_bcb_tc_oficial"])
    bolsa_venta = float(args[f"{prefix}_bcb_bolsa_venta"])
    commission_pct = float(args.get(f"{prefix}_bcb_commission", config.DEFAULT_COMMISSION_PCT))
    tc_minero = _tc_minero_bcb(tc_oficial)
    purchase_totals = compute_purchase_totals(weight_g, purity_pct, bolsa, tc_minero)
    sale_totals = compute_sale_totals(purchase_totals.fine_oz, bolsa_venta, commission_pct, tc_oficial)
    profit = compute_cycle_profit(
        sale_totals, purchase_totals.total_usd, purchase_totals.total_bs, tc_oficial
    )
    return {"label": "BCB", "tc_minero": tc_minero, "net_profit_bs": profit.net_profit_bs, "profit": profit}


def _compute_pankara_channel(weight_g, purity_pct, bolsa, args, prefix) -> dict:
    tc_kibo = float(args[f"{prefix}_pk_tc_kibo"])
    discount_pct = float(args.get(f"{prefix}_pk_discount", config.DEFAULT_PANKARA_DISCOUNT_PCT))
    bolsa_venta = float(args[f"{prefix}_pk_bolsa_venta"])
    commission_pct = float(args.get(f"{prefix}_pk_commission", config.DEFAULT_COMMISSION_PCT))
    # Buy from the miner at raw KIBO. Pankara docks its discount off the
    # sale itself (like an extra commission on the USD proceeds), then
    # that net USDT converts to Bs at KIBO's raw rate.
    effective_bolsa_venta = bolsa_venta * (1 - discount_pct)
    purchase_totals = compute_purchase_totals(weight_g, purity_pct, bolsa, tc_kibo)
    sale_totals = compute_sale_totals(purchase_totals.fine_oz, effective_bolsa_venta, commission_pct, tc_kibo)
    profit = compute_cycle_profit(
        sale_totals, purchase_totals.total_usd, purchase_totals.total_bs, tc_kibo
    )
    return {"label": "Pankara", "tc_minero": tc_kibo, "net_profit_bs": profit.net_profit_bs, "profit": profit}


def _compute_mercado_interno_channel(weight_g, purity_pct, bolsa, args, prefix) -> dict:
    tc_compra = float(args[f"{prefix}_mi_tc_compra"])
    tc_venta = float(args[f"{prefix}_mi_tc_venta"])
    spread = compute_mercado_interno_spread(weight_g, purity_pct, bolsa, tc_compra, tc_venta)
    return {"label": "Mercado interno", "net_profit_bs": spread.diferencia_bs, "spread": spread}


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
    # Dropped to a whole number everywhere it's used (display and math
    # alike) -- Netdania's own decimals aren't meaningful at this scale.
    netdania_price_usd = round(netdania_price.price_usd_per_oz) if netdania_price else None

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
        netdania_price_usd=netdania_price_usd,
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
        # TC KIBO + descuento (Pankara only): "tipo de cambio minero" above
        # is now fully manual (you type it in directly). These two apply on
        # the SALE instead: Pankara docks its discount off what it pays you
        # for the gold (like an extra commission), then that already-net
        # USDT amount converts to Bs at KIBO's raw, undiscounted rate.
        tc_kibo_raw = args.get(f"{prefix}_tc_kibo")
        tc_kibo = float(tc_kibo_raw) if tc_kibo_raw else None
        discount_raw = args.get(f"{prefix}_discount")
        discount_pct = float(discount_raw) if discount_raw else None
        sell_price = float(args.get(f"{prefix}_sell_price", buy_price))
        if tc_kibo is not None:
            discount = discount_pct if discount_pct is not None else config.DEFAULT_PANKARA_DISCOUNT_PCT
            effective_sell_price = sell_price * (1 - discount)
            sell_rate = float(args.get(f"{prefix}_sell_rate", tc_kibo))
        else:
            effective_sell_price = sell_price
            sell_rate = float(args.get(f"{prefix}_sell_rate", buy_rate))
        commission_pct = float(args.get(f"{prefix}_commission", config.DEFAULT_COMMISSION_PCT))
    except (KeyError, ValueError):
        return None

    purchase_totals = compute_purchase_totals(weight_g, purity_pct, buy_price, buy_rate)
    sale_totals = compute_sale_totals(
        purchase_totals.fine_oz, effective_sell_price, commission_pct, sell_rate
    )
    # Utilidad neta en USD is the Bs profit re-expressed via "TC compra $
    # físico", not the sale's own exchange rate.
    profit = compute_cycle_profit(
        sale_totals, purchase_totals.total_usd, purchase_totals.total_bs, buy_rate_fisico
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
    netdania_price_usd = round(netdania_price.price_usd_per_oz) if netdania_price else None

    official_rate = get_official_rate()

    bcb_gold = get_bcb_gold_quote()
    bcb_gold_price_usd = None
    if bcb_gold is not None and official_rate is not None:
        bcb_gold_price_usd = round(bcb_gold.price_bs_per_oz / official_rate.compra, 2)

    comparison = _parse_comparador_calc(request.args)
    tc_minero_mi_avg = _tc_minero_mi_avg_from_args(request.args, "cp", official_rate)

    return render_template(
        "comparador.html",
        latest_price=latest_price,
        netdania_price=netdania_price,
        netdania_price_usd=netdania_price_usd,
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
    for key, compute_channel in (
        ("bcb", _compute_bcb_channel),
        ("pankara", _compute_pankara_channel),
        ("mercado_interno", _compute_mercado_interno_channel),
    ):
        try:
            results[key] = compute_channel(weight_g, purity_pct, bolsa, args, "cp")
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


@app.route("/proyeccion")
def proyeccion():
    history = get_history()

    today = date.today()
    latest_price = None
    for source in DEFAULT_SOURCES:
        quote = history.get_quote(today, source.name)
        if quote is not None:
            latest_price = quote
            break

    netdania_price = history.get_quote(today, "netdania")
    netdania_price_usd = round(netdania_price.price_usd_per_oz) if netdania_price else None

    official_rate = get_official_rate()

    bcb_gold = get_bcb_gold_quote()
    bcb_gold_price_usd = None
    if bcb_gold is not None and official_rate is not None:
        bcb_gold_price_usd = round(bcb_gold.price_bs_per_oz / official_rate.compra, 2)

    proyeccion_calc = _parse_proyeccion_calc(request.args)
    tc_minero_mi_avg = _tc_minero_mi_avg_from_args(request.args, "pr", official_rate)

    return render_template(
        "proyeccion.html",
        latest_price=latest_price,
        netdania_price=netdania_price,
        tc_minero_mi_avg=tc_minero_mi_avg,
        netdania_price_usd=netdania_price_usd,
        official_rate=official_rate,
        bcb_gold_price_usd=bcb_gold_price_usd,
        default_purity=config.DEFAULT_PURITY_PCT,
        default_commission=config.DEFAULT_COMMISSION_PCT,
        default_pankara_discount=config.DEFAULT_PANKARA_DISCOUNT_PCT,
        proyeccion=proyeccion_calc,
        today=today,
    )


def _parse_proyeccion_calc(args) -> Optional[dict]:
    """Projects each channel's single-transaction result across N repeated
    exportaciones. Today's real gold price/TC (same sources the other
    calculators use) stand in as a flat estimate for every repetition --
    this is deliberately linear scaling of a known-good calculation, not a
    price forecast, since there's no trend model backing one. Channel
    rates are typed in by hand, exactly as on the comparator."""
    if "pr_weight_g" not in args:
        return None
    try:
        weight_g = float(args["pr_weight_g"])
        purity_pct = float(args.get("pr_purity", config.DEFAULT_PURITY_PCT))
        bolsa = float(args["pr_bolsa"])
        veces = float(args["pr_veces"])
        dias_raw = args.get("pr_dias")
        dias = float(dias_raw) if dias_raw else None
    except (KeyError, ValueError):
        return None
    if veces <= 0:
        return None

    results = {}
    for key, compute_channel in (
        ("bcb", _compute_bcb_channel),
        ("pankara", _compute_pankara_channel),
        ("mercado_interno", _compute_mercado_interno_channel),
    ):
        try:
            channel = compute_channel(weight_g, purity_pct, bolsa, args, "pr")
        except (KeyError, ValueError):
            continue
        total = channel["net_profit_bs"] * veces
        channel["net_profit_bs_total"] = total
        channel["net_profit_bs_per_dia"] = (total / dias) if dias else None
        results[key] = channel

    if not results:
        return None

    ranking = sorted(results.values(), key=lambda r: r["net_profit_bs_total"], reverse=True)
    return {
        "weight_g": weight_g,
        "purity_pct": purity_pct,
        "bolsa": bolsa,
        "veces": veces,
        "dias": dias,
        "results": results,
        "ranking": ranking,
        "best": ranking[0],
    }


@app.route("/grafico")
def gold_chart():
    return render_template("chart.html")


def _build_regression_chart(rows, predictions):
    """Lays out an actual-vs-predicted line chart as ready-to-render SVG
    coordinates -- no charting library, same approach as the rest of the
    dashboard's hand-rolled templates."""
    width, height = 760, 320
    margin_left, margin_right = 56, 16
    margin_top, margin_bottom = 16, 34
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom

    actual = [r.tc_minero for r in rows]
    all_values = actual + predictions
    y_min, y_max = min(all_values), max(all_values)
    pad = (y_max - y_min) * 0.08 or 0.05
    y_min, y_max = y_min - pad, y_max + pad

    n = len(rows)

    def x_px(i):
        return margin_left + (plot_w * i / (n - 1) if n > 1 else 0)

    def y_px(v):
        return margin_top + plot_h * (1 - (v - y_min) / (y_max - y_min))

    def path_for(values):
        return "M" + " L".join(f"{x_px(i):.1f},{y_px(v):.1f}" for i, v in enumerate(values))

    n_ticks = 5
    y_ticks = [
        {"y": round(y_px(y_min + (y_max - y_min) * k / n_ticks), 1), "label": f"{y_min + (y_max - y_min) * k / n_ticks:.2f}"}
        for k in range(n_ticks + 1)
    ]

    step = max(1, n // 6)
    x_ticks = [{"x": round(x_px(i), 1), "label": rows[i].day.strftime("%d-%b")} for i in range(0, n, step)]
    if (n - 1) % step:
        x_ticks.append({"x": round(x_px(n - 1), 1), "label": rows[-1].day.strftime("%d-%b")})

    return {
        "width": width,
        "height": height,
        "margin_left": margin_left,
        "plot_right": width - margin_right,
        "plot_bottom": height - margin_bottom,
        "path_actual": path_for(actual),
        "path_predicted": path_for(predictions),
        "y_ticks": y_ticks,
        "x_ticks": x_ticks,
    }


@app.route("/regresion-exportacion")
def regresion_exportacion():
    official_rate = get_official_rate()
    rows = generate_synthetic_history(
        oficial_anchor=official_rate.compra if official_rate else 11.97
    )
    result = fit_regression(rows)
    chart = _build_regression_chart(rows, result.predictions)

    table_rows = [
        {"row": r, "predicted": pred, "diff": r.tc_minero - pred}
        for r, pred in list(zip(rows, result.predictions))[:10]
    ]

    return render_template(
        "regresion.html",
        result=result,
        fitted_latex=build_fitted_latex(result),
        chart=chart,
        table_rows=table_rows,
        n_rows=len(rows),
        official_rate=official_rate,
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


if __name__ == "__main__":
    app.run(debug=True, port=int(os.getenv("PORT", "5000")))
