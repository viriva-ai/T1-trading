#!/usr/bin/env python3
"""Send the next-session orders (PAPER by default).

Phases (cron, Mon-Fri IST):
  --phase open   09:16  sells for pending exits, then buys for pending entries whose OPEN is above their stop
  --phase stops  09:25  one SL-M stop-loss sell per holding at its current stop (day order, re-placed every morning)
Paper mode prints the plan and records it in <state_dir>/paper_orders_YYYYMMDD.csv. It reads Kite quotes when a valid
token exists (read-only), otherwise uses the engine's expected prices.
Live mode needs config mode: live + live_trading_enabled: true + --live + --confirm "<phrase>" (see t1/execution/guard.py).
"""
from _common import Lock, load_cfg, log, parser

import pandas as pd

from t1.data.kite import TokenExpired, connect_read_only
from t1.execution import planner
from t1.execution.kite_orders import make_executor

ap = parser(__doc__)
ap.add_argument("--phase", choices=["open", "stops"], required=True)
grp = ap.add_mutually_exclusive_group()
grp.add_argument("--paper", action="store_true", default=True, help="(default) record intents only")
grp.add_argument("--live", action="store_true", help="send real orders if every guard check passes")
ap.add_argument("--confirm", default=None)
a = ap.parse_args()
cfg = load_cfg(a)
with Lock(cfg, "exec"):
    st = cfg.state_dir
    pend = pd.read_csv(st / "pending_orders.csv") if (st / "pending_orders.csv").exists() else pd.DataFrame(columns=["action"])
    posf = pd.read_csv(st / "positions.csv") if (st / "positions.csv").exists() and (st / "positions.csv").stat().st_size > 1 else pd.DataFrame(columns=["sym", "qty", "stop", "last_close"])
    ex = make_executor(cfg, a.live, a.confirm, cfg.token_file, log)
    k = None
    try:
        k = connect_read_only(cfg.token_file)
    except (TokenExpired, SystemExit, RuntimeError) as e:
        if ex.live:
            raise SystemExit(f"live mode needs a valid Kite session: {e}")
        log(f"no Kite session ({e}); planning on the engine's expected prices")
    if ex.live:
        hold = {h["tradingsymbol"]: int(h["quantity"]) + int(h.get("t1_quantity", 0)) for h in k.holdings()}
        hold.update({p["tradingsymbol"]: hold.get(p["tradingsymbol"], 0) + int(p["quantity"]) for p in k.positions()["net"] if p["product"] == "CNC"})
        m = k.margins("equity")
        cash = float(m["available"]["live_balance"])
    else:
        hold = dict(zip(posf.sym, posf.qty))
        cash = None
    syms = sorted(set(pend.get("sym", pd.Series(dtype=str)).dropna()) | set(hold))
    q = k.quote([f"NSE:{s}" for s in syms]) if (k is not None and syms) else {}
    ltp = {s: q[f"NSE:{s}"]["last_price"] for s in syms if f"NSE:{s}" in q}
    opn = {s: q[f"NSE:{s}"]["ohlc"]["open"] for s in syms if f"NSE:{s}" in q}
    if not q:   # paper fallback: expected prices from the engine
        ltp = dict(zip(pend.sym, pend.expected_px))
        ltp.update({s: float(c) for s, c in zip(posf.sym, posf.last_close)})
        opn = dict(ltp)
    equity = (cash if cash is not None else 0.0) + sum(hold.get(s, 0) * ltp.get(s, 0) for s in hold)
    if cash is None:   # paper: take the engine's cash from summary.json
        import json
        cash = float(json.loads((st / "summary.json").read_text())["cash"])
        equity += cash
    if a.phase == "open":
        orders, notes = planner.plan_open(pend, hold, opn, ltp, equity, cash, cfg)
    else:
        stops = dict(zip(posf.sym, posf.stop))
        stops.update({r.sym: r.stop for r in pend[pend.action == "BUY"].itertuples()})   # fresh entries: initial stop
        orders, notes = planner.plan_stops(stops, hold), []
    for n in notes:
        log("  " + n)
    log(f"{'LIVE' if ex.live else 'PAPER'} {a.phase}: {len(orders)} orders (equity ~Rs{equity:,.0f}, cash ~Rs{cash:,.0f})")
    for it in orders:
        ref = ex.place(it)
        log(f"  {it.side} {it.qty} {it.sym} {it.order_type} price={it.price} trigger={it.trigger} -> {ref}  ({it.reason})")
