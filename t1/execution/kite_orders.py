"""Executors. PaperExecutor records intents only. KiteExecutor sends real CNC orders and can be created ONLY through
make_executor() after guard.check() passed. Kite requires market_protection on MARKET and SL-M API orders, and orders
must come from the static IP registered on the Kite developer console (SEBI rule from 1 Apr 2026)."""
from __future__ import annotations

import csv
import time
from pathlib import Path

from t1.execution import guard
from t1.execution.planner import OrderIntent
from t1.timeutil import now_ist


class PaperExecutor:
    live = False

    def __init__(self, state_dir: Path):
        self.f = Path(state_dir) / f"paper_orders_{now_ist():%Y%m%d}.csv"

    def place(self, it: OrderIntent) -> str:
        new = not self.f.exists()
        with open(self.f, "a", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["ts_ist", *it.as_dict().keys()])
            if new:
                w.writeheader()
            w.writerow(dict(ts_ist=now_ist().isoformat(timespec="seconds"), **it.as_dict()))
        return "PAPER"


class KiteExecutor:
    live = True

    def __init__(self, raw_kite, cfg):
        self.k, self.cfg = raw_kite, cfg

    def place(self, it: OrderIntent) -> str:
        ex = self.cfg["execution"]
        if it.qty <= 0:
            raise ValueError("quantity must be positive")
        if it.price and it.qty * it.price > ex["max_order_value"] * 1.01:
            raise ValueError(f"{it.sym}: order value above max_order_value")
        if (self.cfg.state_dir / "KILL").exists():
            raise RuntimeError("kill file present; refusing to send")
        kw = dict(variety="regular", exchange="NSE", tradingsymbol=it.sym, transaction_type=it.side, quantity=int(it.qty),
                  product="CNC", order_type=it.order_type, validity="DAY", tag=str(ex.get("tag", "t1"))[:20])
        if it.order_type == "LIMIT":
            kw["price"] = it.price
        if it.order_type in ("MARKET", "SL-M"):
            kw["market_protection"] = ex["market_protection"]
        if it.order_type == "SL-M":
            kw["trigger_price"] = it.trigger
        oid = self.k.place_order(**kw)
        time.sleep(0.25)          # stay far below Kite's 10 orders/second limit
        return str(oid)


def make_executor(cfg, cli_live: bool, confirm: str | None, token_file: Path, log=print):
    ok, reasons = guard.check(cfg, cli_live, confirm)
    if not ok:
        if cli_live:
            log("LIVE REFUSED -> paper: " + "; ".join(reasons))
        return PaperExecutor(cfg.state_dir)
    from t1.data.kite import raw_client
    log("LIVE TRADING: real orders will be sent to Zerodha")
    return KiteExecutor(raw_client(token_file), cfg)
