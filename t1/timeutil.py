"""IST helpers. Everything in T1 is scheduled and dated in India time (Asia/Kolkata)."""
from __future__ import annotations

from datetime import datetime

import pandas as pd

try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except Exception:  # pragma: no cover
    IST = None


def now_ist() -> datetime:
    return datetime.now(IST)


def today_ist() -> pd.Timestamp:
    return pd.Timestamp(now_ist().date())


def week_end_friday(d) -> pd.Timestamp:
    d = pd.Timestamp(d)
    return d + pd.Timedelta(days=(4 - d.weekday()) % 7)
