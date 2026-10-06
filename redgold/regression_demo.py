"""Prototype: can a multivariable regression suggest a "tipo de cambio
minero" for direct export through an external refinery -- not Pankara, not
BCB -- from the other market variables that move it?

There are zero real purchases/sales in the ledger yet (the `purchases` /
`sales` tables are empty), so there is nothing to fit a regression against.
Everything in this module runs on fabricated history instead, purely to
check whether the approach is worth pursuing once real export deals start
getting recorded. Nothing here is persisted or wired into the real
calculators.

Unlike BCB -- where the royalty is a fixed, known percentage, so "tipo de
cambio minero" is a plain formula (see `redgold.webapp._tc_minero_bcb`) --
a direct-export deal with an external refinery has no fixed percentage:
it's negotiated per shipment against the refinery's own treatment/assay
charge, and those dollars clear through the parallel market rather than
BCB's official channel. The fabricated "true" rule below reflects that:
it's driven by the parallel rate and the refinery's commission, with a
small gold-price effect, and deliberately gives TC oficial no real effect
-- so the regression recovering that (near-zero weight on TC oficial) is
itself a useful check that the method isn't just fitting noise.

TC oficial and TC paralelo are two independent series, not one derived
from the other: TC oficial is what BCB publishes; TC paralelo is Binance
P2P's USDT/BOB buy rate -- its own market. Modeling TC paralelo as a
fixed multiple of TC oficial (an earlier version of this file did) makes
them collinear by construction and the regression can't tell them apart
-- that's a modeling bug, not a finding about the real channel.

Both series are anchored to the real 2026 regime, not placeholder numbers:
BCB abandoned its Bs 6.96/$ peg (fixed since 2011) on 2026-06-29 for a
"managed float" -- published rates since: ~9.73 (29-jun), 9.96 (07-jul),
11.54 (28-jul), 11.58 (15-ago), ~12.26 (23-sep); this app's own live fetch
(`fetch_official_rate`, reused below) reads ~11.97 as of this writing.
TC paralelo used to run 30-40% above the old fixed peg (it hit a historic
Bs 19.25 gap in May 2025), but the float has largely closed that premium:
on 15-ago-2026 Binance P2P's USDT/BOB closed at Bs 11.43 -- BELOW that
day's oficial of Bs 11.58. Gold itself is anchored near $4,450/oz, this
app's own stored Netdania quotes (see `data/redgold.db`, `gold_prices`)
rather than the ~$2,650 that was current when this module was written.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

FEATURE_LABELS = {
    "tc_oficial": "TC oficial BCB (Bs/$)",
    "tc_paralelo": "TC paralelo Binance P2P, compra USDT/BOB (Bs/$)",
    "precio_oro": "Precio oro bolsa ($/oz)",
    "comision_refineria": "Comisión + tratamiento refinería (%)",
}
FEATURES = list(FEATURE_LABELS)


@dataclass(frozen=True)
class RegressionRow:
    day: date
    tc_oficial: float
    tc_paralelo: float
    precio_oro: float
    comision_refineria: float
    tc_minero: float


def generate_synthetic_history(
    n_days: int = 84,
    seed: int = 7,
    oficial_anchor: float = 11.97,
) -> list[RegressionRow]:
    """One fabricated export deal per business day, most recent `n_days`
    ending today. See module docstring for the real anchors behind these
    numbers -- the *rows* are fabricated, but the levels and volatility
    they're drawn around are not.

    `oficial_anchor` is today's real TC oficial -- pass in a fresh live
    read (see `webapp.get_official_rate`) so the window's endpoint tracks
    reality instead of going stale; it falls back to this function's
    default (BCB's ~11.97 read as of this writing) when a live fetch isn't
    available.

    Both FX series share a common "regime" trend (both are BOB/USD rates
    living through the same 2026 float) but get their own independent
    noise on top -- correlated, like the real ones, but not one computed
    from the other, which would make them collinear by construction."""
    rng = np.random.default_rng(seed)
    days = []
    d = date.today()
    while len(days) < n_days:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    days.reverse()

    regime_start = 9.75  # TC oficial just after the float began, 29-jun-2026
    regime_end = oficial_anchor
    # Paralelo ran well above oficial before the float; by 15-ago-2026 it
    # had closed to (and briefly dipped below) oficial -- model that spread
    # shrinking from a modest premium to roughly flat over the window.
    spread_start, spread_end = 0.35, -0.05

    rows: list[RegressionRow] = []
    for i, day in enumerate(days):
        frac = i / (len(days) - 1) if len(days) > 1 else 1.0
        regime_trend = regime_start + (regime_end - regime_start) * frac
        spread = spread_start + (spread_end - spread_start) * frac

        tc_oficial = regime_trend + rng.normal(0, 0.07)
        tc_paralelo = regime_trend + spread + rng.normal(0, 0.18)

        precio_oro = 4450 + 120 * np.sin(frac * 6) + rng.normal(0, 55)
        comision_refineria = max(0.004, 0.014 + rng.normal(0, 0.0025))
        tc_minero = (
            tc_paralelo * (1 - comision_refineria)
            - 0.00015 * (precio_oro - 4450)
            + rng.normal(0, 0.03)
        )
        rows.append(
            RegressionRow(
                day=day,
                tc_oficial=float(tc_oficial),
                tc_paralelo=float(tc_paralelo),
                precio_oro=float(precio_oro),
                comision_refineria=float(comision_refineria),
                tc_minero=float(tc_minero),
            )
        )
    return rows


@dataclass(frozen=True)
class RegressionResult:
    intercept: float
    coefficients: dict[str, float]
    r_squared: float
    predictions: list[float]


def fit_regression(rows: list[RegressionRow]) -> RegressionResult:
    """Ordinary least squares, solved directly with numpy -- no new
    dependency beyond what pandas already pulls in."""
    X = np.array([[getattr(r, f) for f in FEATURES] for r in rows], dtype=float)
    y = np.array([r.tc_minero for r in rows], dtype=float)

    design = np.column_stack([np.ones(len(rows)), X])
    coef, *_ = np.linalg.lstsq(design, y, rcond=None)
    intercept, *betas = coef

    predictions = design @ coef
    residuals = y - predictions
    ss_res = float(np.sum(residuals**2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    return RegressionResult(
        intercept=float(intercept),
        coefficients=dict(zip(FEATURES, (float(b) for b in betas))),
        r_squared=r_squared,
        predictions=[float(p) for p in predictions],
    )


LATEX_SYMBOLS = {
    "tc_oficial": r"TC_{\text{oficial, BCB}}",
    "tc_paralelo": r"TC_{\text{paralelo, P2P}}",
    "precio_oro": r"P_{\text{oro}}",
    "comision_refineria": r"C_{\text{refinería}}",
}


def build_fitted_latex(result: RegressionResult) -> str:
    """The fitted equation (actual coefficients, not symbolic betas), as a
    LaTeX string ready for KaTeX -- or for pasting into a paper/doc."""
    terms = [f"{result.intercept:.3f}"]
    for feature in FEATURES:
        coef = result.coefficients[feature]
        sign = "+" if coef >= 0 else "-"
        terms.append(f"{sign} {abs(coef):.4f}\\, {LATEX_SYMBOLS[feature]}")
    return r"TC_{\text{minero}} \approx " + " ".join(terms)
