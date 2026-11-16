"""The live-trading guard. Real orders are possible only when EVERY check passes:

  1. config: mode: live  AND  live_trading_enabled: true
  2. command line: --live (the scripts default to --paper)
  3. confirmation: --confirm "<phrase>" or env T1_LIVE_CONFIRM equal to CONFIRM_PHRASE
  4. no kill file: <state_dir>/KILL does not exist (touch it to stop all live orders immediately)
  5. market hours: Monday-Friday 09:15-15:25 IST
Anything else falls back to paper. The reason is always printed.
"""
from __future__ import annotations

import os
from datetime import time

from t1.timeutil import now_ist

CONFIRM_PHRASE = "I ACCEPT REAL MONEY ORDERS"


def check(cfg, cli_live: bool, confirm: str | None = None, now=None) -> tuple[bool, list[str]]:
    reasons = []
    if cfg.get("mode") != "live":
        reasons.append("config mode is not 'live'")
    if cfg.get("live_trading_enabled") is not True:
        reasons.append("config live_trading_enabled is not true")
    if not cli_live:
        reasons.append("--live flag not given (paper is the default)")
    phrase = confirm if confirm is not None else os.environ.get("T1_LIVE_CONFIRM")
    if phrase != CONFIRM_PHRASE:
        reasons.append("confirmation phrase missing or wrong")
    if (cfg.state_dir / "KILL").exists():
        reasons.append(f"kill file present: {cfg.state_dir / 'KILL'}")
    now = now or now_ist()
    if now.weekday() >= 5 or not (time(9, 15) <= now.time() <= time(15, 25)):
        reasons.append(f"outside market hours ({now:%a %H:%M} IST)")
    return (not reasons), reasons
