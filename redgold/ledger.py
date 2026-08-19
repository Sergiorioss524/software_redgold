"""Gold buy/sell/reinvest ledger -- the business logic that used to live as
formulas inside BALANCE_ULTIMO.xlsx, generalized from a single snapshot
into a running SQLite ledger.

The workbook ran two parallel cycles:

  EXPORT cycle: COMPRA DE ORO   -> VENTA EXPORT ORO -> REDGOLD profit split
  BCB cycle:    COMPRA DE MATERIAL -> VENTA BCB     -> REDGOLD profit split

Each cycle buys gold by weight/purity, then later sells fine ounces out of
that inventory. The sheet paired exactly one purchase with one sale; here
purchases and sales accumulate over time, so a sale's cost basis is the
weighted-average cost (per fine ounce, tracked separately in USD and Bs --
see `average_cost` for why those two bases don't need to agree) of every
prior purchase in the same cycle.

All formulas below were checked against the real cell values in the
original workbook (see tests/test_ledger.py).
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from redgold import config

CATEGORY_EXPORT = "EXPORT"
CATEGORY_BCB = "BCB"
CATEGORIES = (CATEGORY_EXPORT, CATEGORY_BCB)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    purchase_date TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('EXPORT', 'BCB')),
    weight_g REAL NOT NULL,
    purity_pct REAL NOT NULL,
    price_usd_per_oz REAL NOT NULL,
    exchange_rate_bs_per_usd REAL NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_date TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('EXPORT', 'BCB')),
    fine_oz_sold REAL NOT NULL,
    sale_price_usd_per_oz REAL NOT NULL,
    royalty_pct REAL NOT NULL,
    commission_pct REAL NOT NULL,
    exchange_rate_bs_per_usd REAL NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL
);
"""


# --------------------------------------------------------------------------
# Pure calculations (mirror the workbook's formulas exactly)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class PurchaseTotals:
    fine_weight_g: float
    fine_oz: float
    price_bs_per_gram: float
    total_bs: float
    total_usd: float


def compute_purchase_totals(
    weight_g: float,
    purity_pct: float,
    price_usd_per_oz: float,
    exchange_rate_bs_per_usd: float,
) -> PurchaseTotals:
    """Mirrors COMPRA DE ORO / COMPRA DE MATERIAL (sheet rows 5 and 45)."""
    fine_weight_g = weight_g * purity_pct
    fine_oz = fine_weight_g / config.TROY_OUNCE_GRAMS
    price_bs_per_gram = (
        price_usd_per_oz / config.TROY_OUNCE_GRAMS * (purity_pct * exchange_rate_bs_per_usd)
    )
    total_bs = price_bs_per_gram * weight_g
    total_usd = fine_oz * price_usd_per_oz
    return PurchaseTotals(fine_weight_g, fine_oz, price_bs_per_gram, total_bs, total_usd)


@dataclass(frozen=True)
class SaleTotals:
    total_venta_usd: float
    regalias_usd: float
    neto_venta_usd: float
    comision_usd: float
    total_final_usd: float
    total_bs: float


def compute_sale_totals(
    fine_oz_sold: float,
    sale_price_usd_per_oz: float,
    royalty_pct: float,
    commission_pct: float,
    exchange_rate_bs_per_usd: float,
) -> SaleTotals:
    """Mirrors VENTA EXPORT ORO / VENTA BCB (sheet rows 11 and 51)."""
    total_venta_usd = fine_oz_sold * sale_price_usd_per_oz
    regalias_usd = total_venta_usd * royalty_pct
    neto_venta_usd = total_venta_usd - regalias_usd
    comision_usd = neto_venta_usd * commission_pct
    total_final_usd = neto_venta_usd - comision_usd
    total_bs = total_final_usd * exchange_rate_bs_per_usd
    return SaleTotals(
        total_venta_usd, regalias_usd, neto_venta_usd, comision_usd, total_final_usd, total_bs
    )


@dataclass(frozen=True)
class CycleProfit:
    """Mirrors the REDGOLD block (sheet rows 16-18 / 56-58).

    profit_usd_direct compares the sale's USD proceeds against the
    purchase's USD cost basis directly (both in USD, at their own rates).

    profit_bs / operating_cost_bs / net_profit_bs compare the sale's Bs
    proceeds (converted at the SALE's exchange rate) against the purchase's
    Bs cost (converted at the PURCHASE's exchange rate) -- so this is not
    just a currency conversion of profit_usd_direct, it captures the
    Bs-denominated arbitrage between the two different exchange rates,
    which is how the original workbook modeled it. The *_usd_equiv fields
    are that Bs profit re-expressed in USD at the sale's exchange rate,
    matching D18/E18/F18 in the sheet -- they will generally differ from
    profit_usd_direct.
    """
    profit_usd_direct: float
    profit_bs: float
    operating_cost_bs: float
    net_profit_bs: float
    profit_usd_equiv: float
    operating_cost_usd_equiv: float
    net_profit_usd_equiv: float


def compute_cycle_profit(
    sale_totals: SaleTotals,
    cost_basis_usd: float,
    cost_basis_bs: float,
    sale_exchange_rate_bs_per_usd: float,
    operating_cost_pct: float = config.OPERATING_COST_PCT,
) -> CycleProfit:
    profit_usd_direct = sale_totals.total_final_usd - cost_basis_usd
    profit_bs = sale_totals.total_bs - cost_basis_bs
    operating_cost_bs = profit_bs * operating_cost_pct
    net_profit_bs = profit_bs - operating_cost_bs
    profit_usd_equiv = profit_bs / sale_exchange_rate_bs_per_usd
    operating_cost_usd_equiv = operating_cost_bs / sale_exchange_rate_bs_per_usd
    net_profit_usd_equiv = net_profit_bs / sale_exchange_rate_bs_per_usd
    return CycleProfit(
        profit_usd_direct, profit_bs, operating_cost_bs, net_profit_bs,
        profit_usd_equiv, operating_cost_usd_equiv, net_profit_usd_equiv,
    )


def compute_price_per_gram_bs(
    price_usd_per_oz: float, purity_pct: float, exchange_rate_bs_per_usd: float,
) -> float:
    """Mirrors COTIZACION PRECIO POR GR AL DIA (sheet row 24, F24)."""
    return price_usd_per_oz / config.TROY_OUNCE_GRAMS * (purity_pct * exchange_rate_bs_per_usd)


def compute_affordable_grams(net_profit_bs: float, price_per_gram_bs: float) -> float:
    """Informational only (mirrors G24): how many extra grams today's net
    profit could buy at today's price. Does not create a purchase."""
    if price_per_gram_bs <= 0:
        return 0.0
    return net_profit_bs / price_per_gram_bs


# --------------------------------------------------------------------------
# Records + storage
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Purchase:
    id: int
    purchase_date: date
    category: str
    weight_g: float
    purity_pct: float
    price_usd_per_oz: float
    exchange_rate_bs_per_usd: float
    notes: str
    created_at: datetime

    @property
    def totals(self) -> PurchaseTotals:
        return compute_purchase_totals(
            self.weight_g, self.purity_pct, self.price_usd_per_oz, self.exchange_rate_bs_per_usd
        )


@dataclass(frozen=True)
class Sale:
    id: int
    sale_date: date
    category: str
    fine_oz_sold: float
    sale_price_usd_per_oz: float
    royalty_pct: float
    commission_pct: float
    exchange_rate_bs_per_usd: float
    notes: str
    created_at: datetime

    @property
    def totals(self) -> SaleTotals:
        return compute_sale_totals(
            self.fine_oz_sold, self.sale_price_usd_per_oz,
            self.royalty_pct, self.commission_pct, self.exchange_rate_bs_per_usd,
        )


class InsufficientInventoryError(ValueError):
    """Raised when a sale would sell more fine oz than the category has on hand."""


class Ledger:
    def __init__(self, db_path: Path | str = config.DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # -- purchases ---------------------------------------------------------

    def add_purchase(
        self,
        purchase_date: date,
        category: str,
        weight_g: float,
        purity_pct: float,
        price_usd_per_oz: float,
        exchange_rate_bs_per_usd: float,
        notes: str = "",
    ) -> Purchase:
        _validate_category(category)
        created_at = datetime.now(timezone.utc)
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO purchases
                    (purchase_date, category, weight_g, purity_pct, price_usd_per_oz,
                     exchange_rate_bs_per_usd, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    purchase_date.isoformat(), category, weight_g, purity_pct,
                    price_usd_per_oz, exchange_rate_bs_per_usd, notes, created_at.isoformat(),
                ),
            )
            purchase_id = cur.lastrowid
        return self.get_purchase(purchase_id)

    def get_purchase(self, purchase_id: int) -> Optional[Purchase]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, purchase_date, category, weight_g, purity_pct, price_usd_per_oz, "
                "exchange_rate_bs_per_usd, notes, created_at FROM purchases WHERE id = ?",
                (purchase_id,),
            ).fetchone()
        return _row_to_purchase(row) if row else None

    def list_purchases(self, category: Optional[str] = None) -> list[Purchase]:
        query = (
            "SELECT id, purchase_date, category, weight_g, purity_pct, price_usd_per_oz, "
            "exchange_rate_bs_per_usd, notes, created_at FROM purchases "
        )
        params: tuple = ()
        if category:
            query += "WHERE category = ? "
            params = (category,)
        query += "ORDER BY purchase_date, id"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_purchase(row) for row in rows]

    # -- sales ---------------------------------------------------------

    def add_sale(
        self,
        sale_date: date,
        category: str,
        fine_oz_sold: float,
        sale_price_usd_per_oz: float,
        royalty_pct: float,
        commission_pct: float,
        exchange_rate_bs_per_usd: float,
        notes: str = "",
        allow_oversell: bool = False,
    ) -> Sale:
        _validate_category(category)
        available = self.inventory_fine_oz(category)
        if not allow_oversell and fine_oz_sold > available + 1e-9:
            raise InsufficientInventoryError(
                f"{category}: cannot sell {fine_oz_sold:.4f} fine oz, only "
                f"{available:.4f} fine oz on hand"
            )
        created_at = datetime.now(timezone.utc)
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO sales
                    (sale_date, category, fine_oz_sold, sale_price_usd_per_oz,
                     royalty_pct, commission_pct, exchange_rate_bs_per_usd, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sale_date.isoformat(), category, fine_oz_sold, sale_price_usd_per_oz,
                    royalty_pct, commission_pct, exchange_rate_bs_per_usd, notes,
                    created_at.isoformat(),
                ),
            )
            sale_id = cur.lastrowid
        return self.get_sale(sale_id)

    def get_sale(self, sale_id: int) -> Optional[Sale]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, sale_date, category, fine_oz_sold, sale_price_usd_per_oz, "
                "royalty_pct, commission_pct, exchange_rate_bs_per_usd, notes, created_at "
                "FROM sales WHERE id = ?",
                (sale_id,),
            ).fetchone()
        return _row_to_sale(row) if row else None

    def list_sales(self, category: Optional[str] = None) -> list[Sale]:
        query = (
            "SELECT id, sale_date, category, fine_oz_sold, sale_price_usd_per_oz, "
            "royalty_pct, commission_pct, exchange_rate_bs_per_usd, notes, created_at "
            "FROM sales "
        )
        params: tuple = ()
        if category:
            query += "WHERE category = ? "
            params = (category,)
        query += "ORDER BY sale_date, id"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_sale(row) for row in rows]

    # -- inventory / cost basis ---------------------------------------------

    def average_cost(self, category: str, as_of: Optional[date] = None) -> tuple[float, float]:
        """Weighted-average cost per fine oz for `category`, in (USD, Bs),
        over every purchase up to and including `as_of` (default: all).

        These two averages are computed independently -- USD cost basis
        from each purchase's total_usd, Bs cost basis from each purchase's
        total_bs -- because the workbook derives them from different
        exchange rates rather than converting one into the other.
        """
        purchases = self.list_purchases(category)
        if as_of is not None:
            purchases = [p for p in purchases if p.purchase_date <= as_of]
        total_fine_oz = sum(p.totals.fine_oz for p in purchases)
        if total_fine_oz <= 0:
            return 0.0, 0.0
        total_usd = sum(p.totals.total_usd for p in purchases)
        total_bs = sum(p.totals.total_bs for p in purchases)
        return total_usd / total_fine_oz, total_bs / total_fine_oz

    def inventory_fine_oz(self, category: str, as_of: Optional[date] = None) -> float:
        purchases = self.list_purchases(category)
        sales = self.list_sales(category)
        if as_of is not None:
            purchases = [p for p in purchases if p.purchase_date <= as_of]
            sales = [s for s in sales if s.sale_date <= as_of]
        bought = sum(p.totals.fine_oz for p in purchases)
        sold = sum(s.fine_oz_sold for s in sales)
        return bought - sold

    def sale_profit(self, sale: Sale) -> CycleProfit:
        avg_usd, avg_bs = self.average_cost(sale.category, as_of=sale.sale_date)
        cost_basis_usd = sale.fine_oz_sold * avg_usd
        cost_basis_bs = sale.fine_oz_sold * avg_bs
        return compute_cycle_profit(
            sale.totals, cost_basis_usd, cost_basis_bs, sale.exchange_rate_bs_per_usd
        )


def _validate_category(category: str) -> None:
    if category not in CATEGORIES:
        raise ValueError(f"category must be one of {CATEGORIES}, got {category!r}")


def _row_to_purchase(row) -> Purchase:
    return Purchase(
        id=row[0],
        purchase_date=date.fromisoformat(row[1]),
        category=row[2],
        weight_g=row[3],
        purity_pct=row[4],
        price_usd_per_oz=row[5],
        exchange_rate_bs_per_usd=row[6],
        notes=row[7] or "",
        created_at=datetime.fromisoformat(row[8]),
    )


def _row_to_sale(row) -> Sale:
    return Sale(
        id=row[0],
        sale_date=date.fromisoformat(row[1]),
        category=row[2],
        fine_oz_sold=row[3],
        sale_price_usd_per_oz=row[4],
        royalty_pct=row[5],
        commission_pct=row[6],
        exchange_rate_bs_per_usd=row[7],
        notes=row[8] or "",
        created_at=datetime.fromisoformat(row[9]),
    )
