"""Small deterministic OHLC fixtures; never used as production data."""

from datetime import datetime, timedelta
from decimal import Decimal

from nifty_api.engine import IST
from nifty_api.models import Bar, PatternConfig

CONFIG = PatternConfig(band_edges_pct=["0.25", "0.5", "1", "2"], close_tolerance_points="0")


def bars(day="2026-01-05"):
    start = datetime.fromisoformat(day + "T09:15:00").replace(tzinfo=IST)
    rows = [Bar(timestamp_ist=start + timedelta(minutes=i), open=100, high=100, low=100, close=100) for i in range(375)]
    updates = {
        0: (101, 99, 100), 5: (103, 100, 100), 15: (100, 97, 100),
        60: (104, 100, 100), 120: (100, 96, 100),
        180: (105, 95, 100), 240: (100, 94, 100), 250: (106, 100, 100),
        370: (107, 100, 100), 374: (103, 100, 103),
    }
    for i, (high, low, close) in updates.items():
        rows[i] = rows[i].model_copy(update={"high": Decimal(high), "low": Decimal(low), "close": Decimal(close)})
    return rows
