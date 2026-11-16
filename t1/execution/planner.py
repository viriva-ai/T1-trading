"""Turn the engine's next-session instructions (pending_orders.csv) into concrete orders for a real account.

Mirrors the paper rules:
  - SELL every pending exit at the open (full holding).
  - BUY each pending entry only if the session OPEN is above its initial stop; quantity = floor(equity x weight / price),
    capped by available cash (keeping the 1% buffer).
  - Protect every holding with a stop-loss sell at its current stop (SL-M, re-placed each morning, mode 'slm_daily').
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import pandas as pd


@dataclass
class OrderIntent:
    sym: str
    side: str            # BUY | SELL
    qty: int
    order_type: str      # MARKET | LIMIT | SL-M
    price: float | None = None
    trigger: float | None = None
    reason: str = ""

    def as_dict(self):
        return asdict(self)


def tick_round(px: float, tick: float = 0.05, mode: str = "nearest") -> float:
    n = px / tick
    n = math.ceil(n - 1e-9) if mode == "up" else math.floor(n + 1e-9) if mode == "down" else round(n)
    return round(n * tick, 2)


def plan_open(pending: pd.DataFrame, holdings: dict, opens: dict, ltps: dict, equity: float, cash: float, cfg,
              ticks: dict | None = None) -> tuple[list[OrderIntent], list[str]]:
    ex = cfg["execution"]
    buf = cfg["strategy"]["cash_buffer"]
    ticks = ticks or {}
    out, notes = [], []
    for r in pending[pending.action == "SELL"].itertuples():
        q = int(holdings.get(r.sym, 0))
        if q <= 0:
            notes.append(f"SELL {r.sym}: not held in the account, skipped")
            continue
        if ex["entry_order_type"] == "LIMIT" and ltps.get(r.sym):
            px = tick_round(ltps[r.sym] * (1 - ex["limit_buffer"]), ticks.get(r.sym, 0.05), "down")
            out.append(OrderIntent(r.sym, "SELL", q, "LIMIT", price=px, reason=r.note))
        else:
            out.append(OrderIntent(r.sym, "SELL", q, "MARKET", reason=r.note))
    avail = cash - buf * equity + sum(i.qty * (ltps.get(i.sym) or 0) for i in out)   # sale proceeds become usable
    for r in pending[pending.action == "BUY"].itertuples():
        o, ltp = opens.get(r.sym), ltps.get(r.sym)
        if not o or not ltp:
            notes.append(f"BUY {r.sym}: no open/LTP quote, skipped")
            continue
        if o <= float(r.stop):
            notes.append(f"BUY {r.sym}: open {o} at/below stop {r.stop}, skipped (paper rule)")
            continue
        px = tick_round(ltp * (1 + ex["limit_buffer"]), ticks.get(r.sym, 0.05), "up") if ex["entry_order_type"] == "LIMIT" else ltp
        q = min(math.floor(equity * float(r.weight) / px), math.floor(equity * 0.15 / px), math.floor(max(avail, 0) / (px * 1.0013)))
        q = min(q, math.floor(ex["max_order_value"] / px))
        if q <= 0:
            notes.append(f"BUY {r.sym}: zero quantity (cash or max_order_value cap)")
            continue
        avail -= q * px * 1.0013
        out.append(OrderIntent(r.sym, "BUY", q, ex["entry_order_type"], price=px if ex["entry_order_type"] == "LIMIT" else None,
                               reason=f"rank entry, initial stop {r.stop}"))
    return out[: ex["max_orders_per_run"]], notes


def plan_stops(stops: dict, holdings: dict, ticks: dict | None = None) -> list[OrderIntent]:
    """stops: sym -> stop price (from positions.csv / pending BUY stops). One SL-M sell per held name."""
    ticks = ticks or {}
    out = []
    for s, stop in stops.items():
        q = int(holdings.get(s, 0))
        if q > 0 and stop and stop > 0:
            out.append(OrderIntent(s, "SELL", q, "SL-M", trigger=tick_round(float(stop), ticks.get(s, 0.05), "up"), reason="T1 stop"))
    return out
