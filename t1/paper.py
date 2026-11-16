"""Paper book: replays the T1 engine from the paper inception date on frozen weekly scores (idempotent: the same
inputs always give the same book), then writes the state files and the orders for the next session.

State files (<state_dir>/): positions.csv, pending_orders.csv, ledger.csv, closed_trades.csv, equity.csv,
weekly_picks.csv, summary.json, run_log.jsonl
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from t1 import metrics
from t1.data.membership import sectors
from t1.engine import simulate
from t1.pipeline import ScoreBook, ranking, write_json


def run(cfg, D, book: ScoreBook, decided_weeks: list, log=print) -> dict:
    st = cfg.state_dir
    cap = float(cfg["capital"])
    inc = pd.Timestamp(cfg["paper_inception"])
    days = D.C.index[D.C.index >= inc]
    if len(days) == 0:
        log(f"paper book starts {inc.date()}; no session on or after it yet")
        return dict(started=False)
    start, last_day = days[0], D.C.index[-1]
    wset = [T for T in decided_weeks if T >= start]
    score = book.frame()
    res = simulate(D, start, last_day, score, cfg, capital=cap, liquidate=False, wset=wset)
    liq = simulate(D, start, last_day, score, cfg, capital=cap, liquidate=True, wset=wset)
    eq = res["eq"]
    fv, fnet = metrics.index_fund(D, start, last_day, cap, cfg["benchmark"]["index_fund_ter"])
    pd.DataFrame(dict(strategy_equity=eq.round(2), index_fund_value=fv.reindex(eq.index).round(2))).to_csv(st / "equity.csv", index_label="date")
    fills = res["fills"] if len(res["fills"]) else pd.DataFrame(columns=["date", "side", "sym", "q", "price", "value", "charges", "stt", "why"])
    fills.to_csv(st / "ledger.csv", index=False)
    res["trades"].to_csv(st / "closed_trades.csv", index=False)
    sec = sectors()
    T_now = wset[-1] if wset else None
    rk_now = pd.Series(dtype=float)
    if T_now is not None and T_now in score.index:
        rk_now = score.loc[T_now].reindex(D.universe(T_now)[0]).dropna().rank(ascending=False, method="first")
    rows = []
    for s, p in res["pos"].items():
        lp = res["lastpx"].get(s, p["e"])
        a = D.ATR.at[T_now, s] if (T_now is not None and s in D.ATR.columns) else np.nan
        rows.append(dict(sym=s, sector=sec.get(s, "UNKNOWN"), entry_date=str(p["d0"].date()), qty=p["q"], entry_px_incl_slip=round(p["e"], 2),
                         cost_incl_fees=round(p["cost"], 2), last_close=lp, mkt_value=round(p["q"] * lp, 2), unreal_pnl=round(p["q"] * lp - p["cost"], 2),
                         stop=round(p["stop"], 2), stop_type="trailing (3xATR)" if p["stop"] > p["s0"] + 1e-9 else "initial (2xATR)",
                         initial_stop=round(p["s0"], 2), highest_weekly_close=round(p["hc"], 2), weekly_atr10=round(float(a), 2) if a == a else None,
                         pct_above_stop=round(100 * (lp / p["stop"] - 1), 2) if p["stop"] > 0 else None,
                         m4_rank=int(rk_now[s]) if s in rk_now.index else None, weeks_held=p["w"]))
    pd.DataFrame(rows).to_csv(st / "positions.csv", index=False)
    lastc = D.C.ffill(limit=5).loc[last_day]
    eq_now = float(eq.iloc[-1])
    slip = cfg["strategy"]["slippage"]
    pend = [dict(action="BUY", sym=s, expected_px=round(lastc[s] * (1 + slip), 2), expected_qty=int(eq_now * w // (lastc[s] * (1 + slip))),
                 stop=round(stop, 2), weight=w, note="buy at next OPEN; skip if the open is at/below the stop")
            for s, stop, w in res["pend_entry"]]
    pend += [dict(action="SELL", sym=s, expected_px=round(lastc.get(s, np.nan) * (1 - slip), 2), expected_qty=res["pos"][s]["q"] if s in res["pos"] else None,
                  stop=None, weight=None, note=f"exit at next OPEN: {why}") for s, why in res["pend_exit"].items()]
    pend += [dict(action="STOP", sym=s, expected_px=round(p["stop"], 2), expected_qty=p["q"], stop=round(p["stop"], 2), weight=None,
                  note="standing stop: exit if the session LOW touches it (at the open if it opens below)")
             for s, p in res["pos"].items() if s not in res["pend_exit"]]
    pd.DataFrame(pend, columns=["action", "sym", "expected_px", "expected_qty", "stop", "weight", "note"]).to_csv(st / "pending_orders.csv", index=False)
    picks = []
    for T in wset:
        r = ranking(D, book, T, 30)
        if len(r):
            r.insert(0, "week", str(T.date()))
            r["top10"] = r["rank"] <= cfg["strategy"]["top_k"]
            picks.append(r)
    if picks:
        pd.concat(picks).to_csv(st / "weekly_picks.csv", index=False)
    last_dec = res["decisions"][-1] if res["decisions"] else {}
    summ = dict(as_of=str(last_day.date()), inception=str(start.date()), latest_decision_week=str(T_now.date()) if T_now is not None else None,
                latest_week_was_rebalance=last_dec.get("rebalance"), scores_provisional=book.provisional(T_now) if T_now is not None else None,
                equity=round(eq_now, 0), cash=round(res["cash"], 0), open_positions=len(res["pos"]), pending_buys=len(res["pend_entry"]),
                pending_sells=len(res["pend_exit"]), fees_paid=round(res["fees"], 0), tax_paid=round(res["tax"], 0),
                after_tax_if_liquidated_now=round(liq["terminal_after_tax"], 0), index_fund_value=round(float(fv.iloc[-1]), 0),
                index_fund_after_tax_if_sold=round(float(fnet), 0), closed_trades=int(len(res["trades"])),
                stop_exits_today=[dict(sym=t.sym, exit_px=round(t.exit, 2), why=t.why, pnl=round(t.pnl, 0)) for t in res["trades"].itertuples()
                                  if pd.Timestamp(t.exit_date) == last_day and t.why in ("stop", "stop_gap")] if len(res["trades"]) else [],
                mode=cfg["mode"], live_trading_enabled=bool(cfg["live_trading_enabled"]))
    write_json(st / "summary.json", summ)
    with open(st / "run_log.jsonl", "a") as fh:
        fh.write(json.dumps(summ, default=str) + "\n")
    return dict(started=True, summary=summ, positions=rows, pending=pend, res=res)
