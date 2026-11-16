#!/usr/bin/env python3
"""Backtest T1 with the same engine the paper book uses.

  python scripts/backtest.py --start 2024-10-23 --end 2026-10-22              # walk-forward M4 scores (trains blocks as needed)
  python scripts/backtest.py --start ... --end ... --scores my_scores.pkl      # pre-computed scores (DataFrame week x symbol,
                                                                               #   or long DataFrame T,sym,score)
Results go to <state_dir>/backtests/<start>_<end>/ (stats.json, equity.csv, trades.csv, fills.csv).
Numbers will not equal the sealed research figures exactly: those used NSE's official corporate-action ledger, raw
bars and price-band rules (see docs/STRATEGY.md).
"""
from _common import Lock, load_cfg, log, parser

import json

import pandas as pd

from t1 import metrics, pipeline
from t1.engine import simulate
from t1.model import M4Store, refit_week_for, schedule

ap = parser(__doc__)
ap.add_argument("--start", required=True)
ap.add_argument("--end", required=True)
ap.add_argument("--scores", default=None)
ap.add_argument("--capital", type=float, default=None)
ap.add_argument("--tax-rates", choices=["historical", "current"], default="historical")
ap.add_argument("--n-trials", type=int, default=12015, help="trial count for the deflated Sharpe ratio")
a = ap.parse_args()
cfg = load_cfg(a)
cap = a.capital or float(cfg["capital"])
with Lock(cfg):
    D, DP, _ = pipeline.load_inputs(cfg, log)
    s0, s1 = pd.Timestamp(a.start), pd.Timestamp(a.end)
    if a.scores:
        S = pd.read_pickle(a.scores)
        if {"T", "sym", "score"} <= set(getattr(S, "columns", [])):
            S = S.pivot(index="T", columns="sym", values="score")
    else:
        F = pipeline.build_features(cfg, D, DP, log)
        X = F["X"]
        weeks, refits = schedule(X, cfg["model"]["score_start"], cfg["model"]["refit_every_weeks"])
        st = M4Store(cfg.models_dir)
        parts = []
        for R in sorted({refit_week_for(T, refits) for T in weeks if max(s0 - pd.Timedelta(days=7), refits[0]) <= T <= s1}):
            if not st.has(R):
                st.train_block(X, R, weeks, refits, cfg["model"], log)
            i = refits.index(R)
            nxt = refits[i + 1] if i + 1 < len(refits) else pd.Timestamp("2100-01-01")
            parts.append(st.score(st.load(R), X[(X["T"] >= R) & (X["T"] < nxt)]))
        S = pd.concat(parts).pivot(index="T", columns="sym", values="score")
    res = simulate(D, s0, s1, S, cfg, capital=cap, liquidate=True, tax_rates=a.tax_rates)
    out = metrics.summarize(D, res, s0, s1, cap, a.n_trials)
    d = cfg.state_dir / "backtests" / f"{s0:%Y%m%d}_{s1:%Y%m%d}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "stats.json").write_text(json.dumps(out, indent=1, default=str))
    res["eq"].rename("equity").to_csv(d / "equity.csv", index_label="date")
    res["trades"].to_csv(d / "trades.csv", index=False)
    res["fills"].to_csv(d / "fills.csv", index=False)
    log(json.dumps(out, indent=1, default=str))
