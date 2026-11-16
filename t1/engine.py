"""T1 portfolio engine: one code path for backtests and the paper book (live execution mirrors its decisions).

Daily loop (port of lgb_paper/engine_paper.py, T1 branch = lgb_events engine_ev T1 = base(B=100)):
  1. Idle cash earns the liquid-fund NAV return of the session (taxed at slab at FY end).
  2. At the OPEN: pending exits sell at the open - slippage; pending entries buy at the open + slippage, skipped if the
     open is at/below the entry's initial stop. Size = floor(equity x 9.9% / price), capped by cash.
  3. Stops on daily bars: open at/below the stop -> sell at the open ("stop_gap"); else low touches the stop -> sell at
     the stop ("stop"); both minus slippage. Checked every session, including the entry session.
  4. FY end: capital-gains tax settled from cash.
  5. Friday (last session of the week) CLOSE: every holding's trailing stop is raised to
     max(stop, highest weekly close since entry - 3 x ATR10w). On rebalance weeks (every 2nd week from the anchor):
     holdings ranked > 100 (or no longer eligible) exit at the next open; free slots are filled from the top of the
     ranking, each with initial stop = weekly close - 2 x ATR10w.
"""
from __future__ import annotations

import math
from collections import defaultdict

import numpy as np
import pandas as pd

from t1 import fees as F
from t1.tax import Ledger, fy_of


def simulate(D, start, end, score: pd.DataFrame, cfg: dict, capital: float = 1e6, liquidate: bool = True, wset=None,
             tax_rates: str = "historical", initial_state: dict | None = None) -> dict:
    s_cfg, f_cfg = cfg["strategy"], cfg["fees"]
    card = F.card(f_cfg["card"])
    intraday = bool(f_cfg.get("intraday_reclass", False))
    slip = s_cfg["slippage"]
    K = s_cfg["top_k"]
    w_each = s_cfg["invested_fraction"] / K
    B = s_cfg["rank_exit_above"]
    reb_n = s_cfg["rebalance_every_weeks"]
    anchor = pd.Timestamp(s_cfg["rebalance_anchor"])
    stop_k, trail_k, buf = s_cfg["initial_stop_atr"], s_cfg["trailing_stop_atr"], s_cfg["cash_buffer"]
    earn = cfg["cash"]["earn_liquid_fund_interest"]
    tx = cfg["tax"]
    C, O, H, L = D.C, D.O, D.H, D.L
    days = C.loc[start:end].index
    wset = set(D.WEND[(D.WEND >= days[0]) & (D.WEND <= days[-1])]) if wset is None else set(w for w in wset if days[0] <= w <= days[-1])
    widx = {w: k for k, w in enumerate(D.WEND)}
    if anchor not in widx:
        raise ValueError(f"rebalance anchor {anchor.date()} is not a week end in the panel")
    fills, trades, eq, expo, decisions = [], [], [], [], []
    cash = float(capital)
    pos: dict = {}
    fees_tot = tax_tot = 0.0
    pend_entry: list = []
    pend_exit: dict = {}
    lastpx: dict = {}
    lastvalid: dict = {}
    led = Ledger(slab=tx["slab_rate"], cess=tx["cess"], rates=tax_rates, ltcg_exemption=tx.get("ltcg_exemption") if tax_rates == "current" else None)
    skipped = defaultdict(int)
    prev_d = None
    apply_tax = 1.0 if tx.get("apply", True) else 0.0

    def sell(s, px, d, why):
        nonlocal cash, fees_tot
        p = pos.pop(s)
        val = p["q"] * px
        if intraday and d == p["d0"]:
            # same-session buy->sell: intraday re-class (delivery buy STT/stamp refunded, intraday charges both legs)
            buy_val = p["q"] * p["e"]
            buy_chg = F.intraday_charges(buy_val, "BUY", card)
            sell_chg = F.intraday_charges(val, "SELL", card)
            refund = p["cost"] - buy_val - buy_chg
            cash += val - sell_chg + refund
            fees_tot += sell_chg - refund
            led.spec += val - sell_chg - (buy_val + buy_chg)
            f, stt = sell_chg, val * card.intra_stt_sell
            pnl = val - sell_chg - (buy_val + buy_chg)
        else:
            f, stt = F.delivery_charges(val, "SELL", card)
            cash += val - f
            fees_tot += f
            led.realise(p["d0"], d, p["basis"], val - f + stt)       # STT is not deductible
            pnl = val - f - p["cost"]
        fills.append(dict(date=d, side="SELL", sym=s, q=p["q"], price=px, value=val, charges=f, stt=stt, why=why))
        trades.append(dict(sym=s, entry_date=p["d0"], exit_date=d, entry=p["e"], exit=px, q=p["q"], cost=p["cost"], pnl=pnl,
                           ret=pnl / p["cost"], why=why, weeks=p["w"], days=(d - p["d0"]).days))

    if initial_state:
        cash = float(initial_state["cash"])
        for s, p in initial_state["positions"].items():
            pos[s] = dict(p)
            lastpx[s] = p.get("last", p["e"])
            lastvalid[s] = 0

    for i, d in enumerate(days):
        if prev_d is not None and earn:
            r = D.liq.loc[d] / D.liq.loc[prev_d] - 1
            it = cash * r
            cash += it
            led.interest += it
        o, h, l, c = O.loc[d], H.loc[d], L.loc[d], C.loc[d]
        # 1) open: discretionary exits, then entries
        for s, why in list(pend_exit.items()):
            if s in pos:
                px = o[s] if o[s] == o[s] else lastpx.get(s, pos[s]["e"])
                sell(s, px * (1 - slip), d, why)
        pend_exit = {}
        if pend_entry:
            mv = sum(p["q"] * lastpx.get(x, p["e"]) for x, p in pos.items())
            E = cash + mv
            for s, stop_px, w in pend_entry:
                if len(pos) >= K or s in pos:
                    continue
                if not (o[s] == o[s]):
                    skipped["no_open"] += 1
                    continue
                if o[s] <= stop_px:
                    skipped["gap_below_stop"] += 1
                    continue
                e = o[s] * (1 + slip)
                q = math.floor(E * w / e)
                q = min(q, math.floor(E * 0.15 / e), math.floor((cash - buf * E) / (e * 1.0013)))
                if q <= 0:
                    skipped["zero_shares_or_cash"] += 1
                    continue
                f, stt = F.delivery_charges(q * e, "BUY", card)
                cash -= q * e + f
                fees_tot += f
                pos[s] = dict(q=q, e=e, s0=stop_px, stop=stop_px, cost=q * e + f, basis=q * e + f - stt, d0=d, w=0, hc=e)
                fills.append(dict(date=d, side="BUY", sym=s, q=q, price=e, value=q * e, charges=f, stt=stt, why="rank_entry"))
                lastpx[s] = o[s]
                lastvalid[s] = i
            pend_entry = []
        # 2) stops on daily bars
        for s in list(pos):
            p = pos[s]
            if not (o[s] == o[s]):
                if i - lastvalid.get(s, i) > 5:
                    sell(s, lastpx[s] * (1 - slip), d, "no_data")
                continue
            if o[s] <= p["stop"]:
                sell(s, o[s] * (1 - slip), d, "stop_gap")
                continue
            if l[s] <= p["stop"]:
                sell(s, p["stop"] * (1 - slip), d, "stop")
                continue
        for s in pos:
            if c[s] == c[s]:
                lastpx[s] = c[s]
                lastvalid[s] = i
        # 3) FY-end tax
        nxt = days[i + 1] if i + 1 < len(days) else None
        if nxt is not None and fy_of(nxt) != fy_of(d):
            t = led.settle(fy_of(d)) * apply_tax
            cash -= t
            tax_tot += t
        mv = sum(p["q"] * lastpx.get(x, p["e"]) for x, p in pos.items())
        Eq = cash + mv
        eq.append((d, Eq))
        expo.append(mv / Eq if Eq > 0 else 0)
        prev_d = d
        # 4) weekly decision at the close
        if d in wset:
            T = d
            for s, p in pos.items():
                p["w"] += 1
                wc = D.WC.at[T, s]
                if wc != wc:
                    continue
                p["hc"] = max(p["hc"], wc)
                a = D.ATR.at[T, s]
                if a == a:
                    p["stop"] = max(p["stop"], p["hc"] - trail_k * a)
            reb = (widx[T] - widx[anchor]) % reb_n == 0
            decisions.append(dict(week=T, rebalance=bool(reb)))
            if not reb:
                continue
            u, _missing = D.universe(T)
            sc = score.loc[T].reindex(u).dropna() if T in score.index else pd.Series(dtype=float)
            if len(sc) == 0:
                decisions[-1]["note"] = "no scores"
                continue
            rk = sc.rank(ascending=False, method="first")
            for s_ in list(pos):
                if s_ in pend_exit or rk.get(s_, 1e9) <= B:
                    continue
                pend_exit[s_] = f"rank>{B}" if s_ in sc.index else "left_universe"
            free = K - len(pos) + len(pend_exit)
            for x in sc.sort_values(ascending=False).index:
                if free <= 0:
                    break
                if x in pos:
                    continue
                a = D.ATR.at[T, x]
                wc = D.WC.at[T, x]
                if not (a == a and wc == wc) or a <= 0:
                    continue
                pend_entry.append((x, wc - stop_k * a, w_each))
                free -= 1
    eqs = pd.Series(dict(eq))

    def base():
        return dict(eq=eqs, trades=pd.DataFrame(trades), fills=pd.DataFrame(fills), fees=fees_tot, decisions=decisions,
                    skipped=dict(skipped), expo=float(np.mean(expo)) if expo else 0.0)
    if not liquidate:
        return dict(base(), tax=tax_tot, cash=cash, pos=pos, lastpx=lastpx, pend_entry=pend_entry, pend_exit=pend_exit,
                    ledger_gains=dict(led.g), ledger_interest=led.interest, taxlog=pd.DataFrame(led.log))
    dlast = days[-1]
    for s in list(pos):
        sell(s, lastpx[s] * (1 - slip), dlast, "end")
    t = led.settle(fy_of(dlast)) * apply_tax
    cash -= t
    tax_tot += t
    return dict(base(), tax=tax_tot, terminal_after_tax=cash, taxlog=pd.DataFrame(led.log))
