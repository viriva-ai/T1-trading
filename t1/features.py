"""Weekly feature matrix for the M4 ranker (37 inputs) and the 4-week label.

Point-in-time rules: every feature for decision week T uses data up to the last session of week T only.
Inputs per stock (cross-sectionally pct-ranked each week, minus 0.5): r1w r4w r12w r26w r52w mom12_1 dist52h rvol20
atr_pct volz vr turnover amihud gap_abs20 gap_mean20 rsi14 ma50d ma200d deliv5 deliv_z sec_rs4 sec_rs12 stock_vs_sec4
(23 ranks) + w3_signal (52-week breakout with 1.5x volume above the 30-week SMA) + 13 market inputs (Nifty 500 1/4/12-week
return and 40-week trend, India VIX level and 4-week change, % of eligible names above their 200-day average, USD/INR,
Brent, S&P 500 1w/4w, US 10y change, gold).
Label for training: y4 = weekly close 4 weeks after T / next session open after T - 1, ranked within the week (y4_rk).
This is a verbatim port of lgb_paper/features_paper.py (itself allfactor/features.py) + lgb_max/labels.py.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from t1.data import external as EXT

W3 = dict(breakout_weeks=52, vol_mult=1.5, vol_avg_weeks=20, sma_weeks=30, regime_ma_weeks=40, atr_weeks=10)
MARKET = ["n500_r1w", "n500_r4w", "n500_r12w", "n500_trend", "vix", "vix_chg4", "breadth200", "usdinr_r4", "brent_r4",
          "spx_r1w", "spx_r4w", "us10y_chg4", "gold_r4"]
STOCK = ["r1w", "r4w", "r12w", "r26w", "r52w", "mom12_1", "dist52h", "rvol20", "atr_pct", "volz", "vr", "turnover", "amihud",
         "gap_abs20", "gap_mean20", "rsi14", "ma50d", "ma200d", "deliv5", "deliv_z"]
RANKED = [k + "_rk" for k in STOCK + ["sec_rs4", "sec_rs12", "stock_vs_sec4"]]
FEATURES = RANKED + ["w3_signal"] + MARKET          # the 37 M4 inputs, in model order


class Data:
    """Daily panel + weekly aggregates + the eligible universe (port of engine_paper.Data)."""

    def __init__(self, C, O, H, L, V, IDX, liq, tri, members_fn, min_hist=260, min_tv=5e7, min_px=20.0, atr_weeks=10):
        self.C = C
        self.O, self.H, self.L, self.V = (x.reindex_like(C) for x in (O, H, L, V))
        self.IDX = IDX.reindex(C.index).ffill()
        self.liq = liq.reindex(C.index).ffill().bfill()
        self.tri = tri.reindex(C.index).ffill()
        self.members_fn = members_fn
        self.min_hist, self.min_tv, self.min_px = min_hist, min_tv, min_px
        self.tv20 = (C * self.V).rolling(20).median()
        self.hist = C.notna().cumsum()
        wk = C.index.to_period("W-FRI")
        W = pd.DatetimeIndex(pd.Series(C.index, index=C.index).groupby(wk).max().values)
        self.WEND = W
        g = lambda df, f: df.groupby(wk).agg(f).set_axis(W)
        self.WC = C.ffill(limit=5).groupby(wk).last().set_axis(W).where(C.groupby(wk).count().set_axis(W) > 0)
        self.WH, self.WL, self.WV = g(self.H, "max"), g(self.L, "min"), g(self.V, "sum")
        self.WIDX = self.IDX.groupby(wk).last().set_axis(W)
        WC = self.WC
        wtr = pd.concat([self.WH - self.WL, (self.WH - WC.shift()).abs(), (self.WL - WC.shift()).abs()]).groupby(level=0).max().reindex(W)
        self.ATR = wtr.rolling(atr_weeks).mean()
        self.SMA = WC.rolling(W3["sma_weeks"]).mean()
        self.HI = WC.rolling(W3["breakout_weeks"]).max()
        self.VR = self.WV / self.WV.shift(1).rolling(W3["vol_avg_weeks"]).mean()
        self._u: dict = {}

    def universe(self, T):
        """Eligible names at week T: index member, >= 260 sessions of history, 20-day median traded value >= Rs5 cr, price >= 20."""
        if T in self._u:
            return self._u[T]
        mem = self.members_fn(T)
        h, t, c = self.hist.loc[T], self.tv20.loc[T], self.C.loc[T]
        out, missing = [], []
        for x in mem:
            if x not in self.C.columns:
                missing.append(x)
                continue
            if h[x] >= self.min_hist and t[x] >= self.min_tv and c[x] >= self.min_px:
                out.append(x)
        self._u[T] = (out, missing)
        return self._u[T]


def make_data(cfg, P: dict, aux: dict, members) -> Data:
    u = cfg["universe"]
    return Data(P["C"], P["O"], P["H"], P["L"], P["V"], aux["px"], aux["liq"], aux["tri"], members.members,
                min_hist=u["min_history_days"], min_tv=u["min_traded_value_20d"], min_px=u["min_price"],
                atr_weeks=cfg["strategy"]["atr_weeks"])


def build(D: Data, sec: dict, DP: pd.DataFrame, ext: Path, start_univ="2011-01-01", weeks=None, labels=True) -> dict:
    """Return dict(X=feature rows, FEATURES, ...). `weeks` limits which decision weeks get rows (rolling stats still use all history)."""
    C, O, V = D.C, D.O, D.V
    W, WC = D.WEND, D.WC

    def atW(df):
        return df.ffill(limit=5).reindex(W)

    f = {}
    for n in (1, 4, 12, 26, 52):
        f[f"r{n}w"] = WC / WC.shift(n) - 1
    f["mom12_1"] = WC.shift(4) / WC.shift(52) - 1
    f["dist52h"] = WC / WC.rolling(52, min_periods=40).max() - 1
    r = C.pct_change(fill_method=None)
    f["rvol20"] = atW(r.rolling(20, min_periods=15).std())
    f["atr_pct"] = D.ATR / WC
    pv = D.WV.shift(1).rolling(20, min_periods=15)
    f["volz"] = (D.WV - pv.mean()) / pv.std()
    f["vr"] = D.VR
    f["turnover"] = np.log(atW(D.tv20).clip(lower=1))
    f["amihud"] = atW((r.abs() / (C * V).replace(0, np.nan)).rolling(20, min_periods=15).mean())
    g = O / C.shift(1) - 1
    f["gap_abs20"] = atW(g.abs().rolling(20, min_periods=15).mean())
    f["gap_mean20"] = atW(g.rolling(20, min_periods=15).mean())
    dc = C.diff()
    up = dc.clip(lower=0).ewm(alpha=1 / 14, min_periods=14).mean()
    dn = (-dc.clip(upper=0)).ewm(alpha=1 / 14, min_periods=14).mean()
    f["rsi14"] = atW(100 - 100 / (1 + up / dn))
    f["ma50d"] = atW(C / C.rolling(50, min_periods=40).mean() - 1)
    above200 = C > C.rolling(200, min_periods=160).mean()
    f["ma200d"] = atW(C / C.rolling(200, min_periods=160).mean() - 1)
    w3 = ((WC >= D.HI - 1e-9) & (D.VR > W3["vol_mult"]) & (WC > D.SMA)).astype(float)
    DP = DP.reindex(index=C.index, columns=C.columns)
    f["deliv5"] = atW(DP.rolling(5, min_periods=3).mean())
    dw = f["deliv5"]
    f["deliv_z"] = (dw - dw.rolling(52, min_periods=26).mean()) / dw.rolling(52, min_periods=26).std()
    rows_weeks = [T for T in W if T >= pd.Timestamp(start_univ)]
    if weeks is not None:
        keep = set(pd.DatetimeIndex(weeks))
        rows_weeks = [T for T in rows_weeks if T in keep]
    univ = {}
    hist, tv, cl = D.hist.reindex(W), D.tv20.reindex(W), C.ffill(limit=5).reindex(W)
    for T in rows_weeks:
        if T >= pd.Timestamp("2014-01-01"):
            univ[T] = D.universe(T)[0]
        else:   # before 2014 (training only): liquidity filter on all panel names
            m = (hist.loc[T] >= D.min_hist) & (tv.loc[T] >= D.min_tv) & (cl.loc[T] >= D.min_px)
            univ[T] = list(m[m].index)
    secs = pd.Series({s: sec.get(s, "UNKNOWN") for s in C.columns})
    days = C.index
    pos = {d: i for i, d in enumerate(days)}
    nxt_open = pd.DataFrame({T: O.loc[days[pos[T] + 1]] if pos[T] + 1 < len(days) else pd.Series(np.nan, index=C.columns) for T in W}).T
    y1 = WC.shift(-1) / nxt_open - 1
    y4 = WC.shift(-4) / nxt_open - 1

    def asof_before(s, k=0):
        out = {}
        for T in W:
            x = s.loc[:T - pd.Timedelta(days=1 + 7 * k)]
            out[T] = x.iloc[-1] if len(x) else np.nan
        return pd.Series(out)

    idx = D.WIDX
    mk = pd.DataFrame(index=W)
    mk["n500_r1w"] = idx / idx.shift(1) - 1
    mk["n500_r4w"] = idx / idx.shift(4) - 1
    mk["n500_r12w"] = idx / idx.shift(12) - 1
    mk["n500_trend"] = idx / idx.rolling(40).mean() - 1
    vix = EXT.yahoo_close(ext, "yf_IDX_INDIAVIX.csv")
    vixW = vix.reindex(W, method="ffill")
    mk["vix"] = vixW
    mk["vix_chg4"] = vixW - vixW.shift(4)
    ab = atW(above200.astype(float).where(C.notna()))
    mk["breadth200"] = pd.Series({T: ab.loc[T, univ[T]].mean() if T in univ else np.nan for T in W})
    src_last = dict(vix=str(vix.index[-1].date()))
    spx = EXT.yahoo_close(ext, "yf_IDX_GSPC.csv")
    for nm, s, kind in [("usdinr_r4", EXT.yahoo_close(ext, "yf_INR_X.csv"), "r4"),
                        ("brent_r4", EXT.fred_series(ext, "fred_DCOILBRENTEU.csv", "DCOILBRENTEU"), "r4"),
                        ("spx_r1w", spx, "r1"), ("spx_r4w", spx, "r4"),
                        ("us10y_chg4", EXT.fred_series(ext, "fred_DGS10.csv", "DGS10"), "d4"),
                        ("gold_r4", EXT.yahoo_close(ext, "yf_GC_F.csv"), "r4")]:
        a = asof_before(s)
        b = asof_before(s, 4 if kind != "r1" else 1)
        mk[nm] = a - b if kind == "d4" else a / b - 1
        src_last[nm] = str(s.index[-1].date())
    rows = []
    for T, u in univ.items():
        if not u:
            continue
        d = pd.DataFrame({k: f[k].loc[T][u] for k in STOCK})   # row first: .loc[T, u] kept an 881 x n view alive per column (OOM on 4 GB)
        d["w3_signal"] = w3.loc[T][u]
        d["y"] = y1.loc[T][u]
        d["y4"] = y4.loc[T][u]
        d["sym"] = u
        d["T"] = T
        d["sector"] = secs[u].values
        g4 = d.groupby("sector").r4w.transform("mean")
        g12 = d.groupby("sector").r12w.transform("mean")
        d["sec_rs4"] = g4 - d.r4w.mean()
        d["sec_rs12"] = g12 - d.r12w.mean()
        d["stock_vs_sec4"] = d.r4w - g4
        d.loc[d.sector == "UNKNOWN", ["sec_rs4", "sec_rs12", "stock_vs_sec4"]] = np.nan
        rows.append(d)
    X = pd.concat(rows, ignore_index=True)
    for k in STOCK + ["sec_rs4", "sec_rs12", "stock_vs_sec4"]:
        X[k + "_rk"] = X.groupby("T")[k].rank(pct=True) - 0.5
    if labels:
        X["y4_rk"] = X.groupby("T").y4.rank(pct=True) - 0.5
    X = X.merge(mk, left_on="T", right_index=True, how="left")
    for c in X.columns:
        if X[c].dtype == np.float64 and c not in ("y4", "y4_rk"):   # labels stay float64 (as in lgb_max/labels.py)
            X[c] = X[c].astype(np.float32)
    # unknown-sector names get a neutral sector-relative rank (allfactor amendment A1)
    for c in ["sec_rs4_rk", "sec_rs12_rk", "stock_vs_sec4_rk"]:
        X.loc[X.sector == "UNKNOWN", c] = 0.0
    X = X.sort_values(["T", "sym"]).reset_index(drop=True)
    mto_last = DP.dropna(how="all").index.max() if DP.notna().any().any() else None
    return dict(X=X, FEATURES=FEATURES, mto_last=str(pd.Timestamp(mto_last).date()) if mto_last is not None else None, src_last=src_last)
