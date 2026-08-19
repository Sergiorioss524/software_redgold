from datetime import date, datetime, timezone

from redgold.adjustment import compute_daily_adjustment
from redgold.storage import PriceHistory


def test_first_ever_quote_has_neutral_adjustment(tmp_path):
    history = PriceHistory(tmp_path / "test.db")
    history.save_quote(date(2026, 8, 1), "bcb", 4000.0, datetime.now(timezone.utc), "raw")
    quote = history.get_quote(date(2026, 8, 1), "bcb")

    adjustment = compute_daily_adjustment(history, quote)

    assert adjustment.adjustment_factor == 1.0
    assert adjustment.previous_price_usd_per_oz is None


def test_adjustment_reflects_day_over_day_change(tmp_path):
    history = PriceHistory(tmp_path / "test.db")
    history.save_quote(date(2026, 8, 1), "bcb", 4000.0, datetime.now(timezone.utc), "raw")
    history.save_quote(date(2026, 8, 2), "bcb", 4080.0, datetime.now(timezone.utc), "raw")
    quote = history.get_quote(date(2026, 8, 2), "bcb")

    adjustment = compute_daily_adjustment(history, quote)

    assert adjustment.previous_price_usd_per_oz == 4000.0
    assert adjustment.change_usd == 80.0
    assert round(adjustment.change_pct, 4) == 0.02
    assert round(adjustment.adjustment_factor, 4) == 1.02
