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

TC oficial and TC paralelo are two genuinely independent series, not one
derived from the other: TC oficial is what BCB publishes (pegged, barely
moves); TC paralelo is Binance P2P's USDT/BOB buy rate -- its own market,
driven by dollar scarcity, not by BCB's peg. Modeling TC paralelo as a
fixed multiple of TC oficial (an earlier version of this file did) makes
them collinear by construction and the regression can't tell them apart
-- that's a modeling bug, not a finding about the real channel.
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


def generate_synthetic_history(n_days: int = 84, seed: int = 7) -> list[RegressionRow]:
    """One fabricated export deal per business day, most recent `n_days`
    ending today. See module docstring for the generating rule.

    TC oficial and TC paralelo are generated as two independent processes
    (BCB's peg vs. Binance P2P's own USDT/BOB market), not one derived from
    the other -- otherwise they'd be collinear by construction."""
    rng = np.random.default_rng(seed)
    rows: list[RegressionRow] = []
    d = date.today() - timedelta(days=int(n_days * 1.5) + 10)

    # Binance P2P (compra USDT/BOB) as its own random walk with a mild
    # upward drift -- dollar-scarcity dynamics, unrelated to BCB's peg.
    paralelo_level = 8.6

    while len(rows) < n_days:
        if d.weekday() < 5:
            t = len(rows) / n_days
            tc_oficial = 6.96 + rng.normal(0, 0.01)

            paralelo_level += rng.normal(0.006, 0.07)
            tc_paralelo = paralelo_level

            precio_oro = 2650 + 90 * np.sin(t * 6) + rng.normal(0, 18)
            comision_refineria = max(0.004, 0.014 + rng.normal(0, 0.0025))
            tc_minero = (
                tc_paralelo * (1 - comision_refineria)
                - 0.00015 * (precio_oro - 2650)
                + rng.normal(0, 0.02)
            )
            rows.append(
                RegressionRow(
                    day=d,
                    tc_oficial=float(tc_oficial),
                    tc_paralelo=float(tc_paralelo),
                    precio_oro=float(precio_oro),
                    comision_refineria=float(comision_refineria),
                    tc_minero=float(tc_minero),
                )
            )
        d += timedelta(days=1)
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
