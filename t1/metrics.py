"""Performance metrics (after-tax XIRR on a lump sum, drawdown, Sharpe, deflated Sharpe)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def xirr_lump(v0, v1, d0, d1) -> float:
    return 100 * ((v1 / v0) ** (365.25 / (pd.Timestamp(d1) - pd.Timestamp(d0)).days) - 1)


def maxdd(e: pd.Series) -> float:
    return 100 * float((e / e.cummax() - 1).min())


def sharpe(e: pd.Series) -> float:
    r = e.pct_change().dropna()
    return float(r.mean() / r.std() * np.sqrt(248)) if r.std() > 0 else 0.0


def index_fund(D, start, end, capital, ter=0.002, ltcg=0.125, stcg=0.20, exempt=125000.0, cess=0.04):
    t = D.tri.loc[start:end].dropna()
    yrs = (t.index - t.index[0]).days / 365.25
    v = capital * t / t.iloc[0] * (1 - ter) ** yrs
    gain = v.iloc[-1] - capital
    held = (t.index[-1] - t.index[0]).days
    tax = (ltcg * max(0, gain - exempt) if held > 365 else stcg * max(0, gain)) * (1 + cess)
    return v, v.iloc[-1] - tax


def dsr(monthly_excess, n_trials: int):
    """Deflated Sharpe ratio (Bailey & Lopez de Prado) of monthly excess returns given n_trials tried strategies."""
    from scipy.stats import kurtosis, norm, skew
    x = np.asarray(monthly_excess, float)
    T = len(x)
    sr = x.mean() / x.std(ddof=1)
    g = 0.5772156649
    sr0 = np.sqrt(1.0 / (T - 1)) * ((1 - g) * norm.ppf(1 - 1.0 / n_trials) + g * norm.ppf(1 - 1.0 / (n_trials * np.e)))
    den = np.sqrt(1 - skew(x) * sr + (kurtosis(x, fisher=False) - 1) / 4 * sr * sr)
    return float(norm.cdf((sr - sr0) * np.sqrt(T - 1) / den)), float(sr), float(sr0)


def summarize(D, res, start, end, capital, n_trials: int | None = None) -> dict:
    e = res["eq"]
    d0, d1 = e.index[0], e.index[-1]
    fv, fnet = index_fund(D, start, end, capital)
    tr = res["trades"]
    out = dict(start=str(d0.date()), end=str(d1.date()), capital=capital,
               xirr_after_tax=round(xirr_lump(capital, res["terminal_after_tax"], d0, d1), 3),
               terminal_after_tax=round(res["terminal_after_tax"], 2),
               maxdd=round(maxdd(e), 2), sharpe=round(sharpe(e), 3), exposure_pct=round(100 * res["expo"], 1),
               trades=int(len(tr)), win_pct=round(100 * float((tr.pnl > 0).mean()), 1) if len(tr) else None,
               fees=round(res["fees"], 0), tax_paid=round(res["tax"], 0),
               index_fund_xirr_after_tax=round(xirr_lump(capital, fnet, d0, d1), 3),
               exits=tr.why.value_counts().to_dict() if len(tr) else {})
    if n_trials:
        ms = e.resample("ME").last().pct_change().dropna()
        mb = D.tri.loc[start:end].resample("ME").last().pct_change().dropna()
        ex = (ms - mb.reindex(ms.index)).dropna()
        if len(ex) > 12:
            out["dsr"], out["ir_monthly"], out["sr0_monthly"] = (round(v, 4) for v in dsr(ex.values, n_trials))
    return out
