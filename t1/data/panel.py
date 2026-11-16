"""Daily OHLCV panel built from the normalised NSE bhavcopy files, split/bonus-adjusted with the same price-jump
heuristic the original pipeline used, plus delivery %, the Nifty 500 index and the liquid-fund NAV.

Layout under <data_dir>/panel/:
  long.pkl      long table of raw rows (date, SYMBOL, O, H, L, C, QTY), appended incrementally
  prices.pkl    dict(C, O, H, L, V) of date x symbol DataFrames (adjusted), plus 'splits' (detected factors)
  delivery.pkl  date x symbol delivery fraction (MTO DELIV / QTY)
Symbols kept: every name that was ever in the Nifty 500 (bundled membership) plus the current list.
Known limit: the heuristic misses demergers/rights and odd ratios; the sealed t1_core research used NSE's
official corporate-action ledger instead (see docs/STRATEGY.md).
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

from t1.data import external as X
from t1.data.membership import Membership

STD = [1 / 2, 1 / 3, 1 / 4, 1 / 5, 1 / 10, 2 / 3, 2 / 5, 1 / 20, 1 / 6, 1 / 8, 2, 3, 4, 5, 10]
SER_PRI = {"EQ": 0, "BE": 1, "BZ": 2}


def _read_bhav(f: Path, keep: set[str]) -> pd.DataFrame:
    d = pd.read_csv(f, usecols=["SYMBOL", "SERIES", "O", "H", "L", "C", "QTY"])
    d = d[d.SYMBOL.isin(keep)]
    d["pri"] = d.SERIES.map(SER_PRI)
    d = d.sort_values(["SYMBOL", "pri"]).drop_duplicates("SYMBOL")
    for c in ["O", "H", "L", "C", "QTY"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d[(d.C > 0) & (d.O > 0)]
    d["date"] = pd.Timestamp(f.stem)
    return d[["date", "SYMBOL", "O", "H", "L", "C", "QTY"]]


def update_long(data_dir: Path, symbols: set[str], start, log=print) -> pd.DataFrame:
    pdir = data_dir / "panel"
    pdir.mkdir(parents=True, exist_ok=True)
    f = pdir / "long.pkl"
    files = sorted((data_dir / "raw" / "bhav").glob("2*.csv"))
    files = [p for p in files if pd.Timestamp(p.stem) >= pd.Timestamp(start)]
    if f.exists():
        L = pd.read_pickle(f)
        have_days = set(L.date.unique())
        have_syms = set(L.attrs.get("symbols", []))
    else:
        L, have_days, have_syms = None, set(), set()
    new_syms = symbols - have_syms
    todo = [p for p in files if pd.Timestamp(p.stem) not in have_days]
    parts = []
    if new_syms and L is not None:   # newly added symbols need their full history from already-read days
        log(f"  panel: adding history for {len(new_syms)} new symbols")
        parts += [_read_bhav(p, new_syms) for p in files if pd.Timestamp(p.stem) in have_days]
    if todo:
        log(f"  panel: reading {len(todo)} new bhavcopy days")
        parts += [_read_bhav(p, symbols | have_syms) for p in todo]
    if parts:
        L = pd.concat(([L] if L is not None else []) + parts, ignore_index=True)
        L = L.drop_duplicates(["date", "SYMBOL"], keep="last").sort_values(["date", "SYMBOL"]).reset_index(drop=True)
    L.attrs["symbols"] = sorted(symbols | have_syms)
    tmp = f.with_suffix(".tmp")
    L.to_pickle(tmp)
    os.replace(tmp, f)
    return L


def ancestors(changes: pd.DataFrame, sym: str) -> list[tuple[str, pd.Timestamp]]:
    """[(old_symbol, effective_date), ...] walking back through ticker renames (latest first)."""
    out, cur, before = [], sym, pd.Timestamp("2200-01-01")
    for _ in range(10):
        c = changes[(changes.new == cur) & (changes.date <= before)]
        if c.empty:
            break
        r = c.iloc[-1]
        out.append((r.old, r.date))
        cur, before = r.old, r.date
    return out


def stitch_renames(L: pd.DataFrame, changes: pd.DataFrame, wanted: set[str]) -> pd.DataFrame:
    """Copy each renamed stock's pre-rename rows under its current ticker (the old ticker's rows are kept too, so
    historical index membership under the old name still resolves)."""
    add = []
    for s in wanted:
        for old, d in ancestors(changes, s):
            rows = L[(L.SYMBOL == old) & (L.date < d)]
            if len(rows):
                add.append(rows.assign(SYMBOL=s))
    if not add:
        return L
    out = pd.concat([L] + add, ignore_index=True)
    return out.drop_duplicates(["date", "SYMBOL"], keep="first")


def split_adjust(P: dict) -> tuple[dict, list]:
    """Back-adjust OHLC (and volume inversely) for split/bonus jumps detected from close/open vs previous close."""
    C, O = P["C"], P["O"]
    prev = C.ffill().shift(1)
    r, ro = C / prev, O / prev
    cand = ((r < 0.72) | (r > 1.9)) & r.notna()
    F = pd.DataFrame(1.0, index=C.index, columns=C.columns)
    found = []
    for d, s in zip(*np.where(cand.values)):
        dd, ss = C.index[d], C.columns[s]
        rv, rov = r.iat[d, s], ro.iat[d, s]
        m = [x for x in STD if abs(rov / x - 1) < 0.07 and abs(rv / x - 1) < 0.25]
        if m:
            F.iat[d, s] = m[0]
            found.append(dict(sym=ss, date=str(dd.date()), factor=m[0]))
    cf = F.iloc[::-1].cumprod().iloc[::-1].shift(-1).fillna(1.0)
    out = {k: P[k] * cf for k in "OHLC"}
    out["V"] = P["V"] / cf
    return out, found


def build_prices(data_dir: Path, members: Membership, start, log=print) -> dict:
    from t1.data.nse import load_symbol_changes
    syms = members.ever_members() | members.live_symbols()
    changes = load_symbol_changes(data_dir / "nse")
    olds = {o for s in syms for o, _ in ancestors(changes, s)}
    L = update_long(data_dir, syms | olds, start, log)
    L = stitch_renames(L, changes, syms)
    P = {k: L.pivot(index="date", columns="SYMBOL", values=c).sort_index() for k, c in zip("COHLV", ["C", "O", "H", "L", "QTY"])}
    for k in P:
        P[k].index = pd.DatetimeIndex(P[k].index)
        P[k] = P[k].astype(float)
    P, splits = split_adjust(P)
    P["splits"] = splits
    pd.to_pickle(P, data_dir / "panel" / "prices.pkl")
    log(f"  panel: {P['C'].shape[0]} days x {P['C'].shape[1]} symbols, last {P['C'].index[-1].date()}, {len(splits)} split/bonus steps")
    return P


def build_delivery(data_dir: Path, index, columns, mto_start) -> pd.DataFrame:
    f = data_dir / "panel" / "delivery_long.pkl"
    files = [p for p in sorted((data_dir / "raw" / "mto").glob("2*.csv")) if pd.Timestamp(p.stem) >= pd.Timestamp(mto_start)]
    M = pd.read_pickle(f) if f.exists() else None
    have = set(M.date.unique()) if M is not None else set()
    parts = []
    for p in files:
        d = pd.Timestamp(p.stem)
        if d in have:
            continue
        x = pd.read_csv(p)
        x = x[x.SERIES == "EQ"].copy()
        x["date"] = d
        parts.append(x[["date", "SYMBOL", "QTY", "DELIV"]])
    if parts:
        M = pd.concat(([M] if M is not None else []) + parts, ignore_index=True)
        M.to_pickle(f)
    if M is None:
        return pd.DataFrame(index=index, columns=columns, dtype=float)
    M = M.copy()
    M["QTY"] = pd.to_numeric(M.QTY, errors="coerce")
    M["DELIV"] = pd.to_numeric(M.DELIV, errors="coerce")
    M = M[M.QTY > 0].drop_duplicates(["date", "SYMBOL"])
    M["dp"] = (M.DELIV / M.QTY).clip(0, 1)
    return M.pivot(index="date", columns="SYMBOL", values="dp").reindex(index=index, columns=columns)


def build_aux(data_dir: Path, index: pd.DatetimeIndex, dividend_yield: float) -> dict:
    ext = data_dir / "ext"
    px = X.yahoo_close(ext, "yf_IDX_CRSLDX.csv")
    px = px[~px.index.duplicated()].sort_index()
    gaps = px.index.to_series().diff().dt.days.fillna(0).values
    tri = px * np.cumprod(1 + dividend_yield * gaps / 365.0)   # approximate total-return index
    nav = X.load_liquid_nav(ext)
    liq = nav if len(nav) else pd.Series(1.0, index=index)
    return dict(px=px, tri=tri, liq=liq)


def load_prices(data_dir: Path) -> dict:
    return pd.read_pickle(data_dir / "panel" / "prices.pkl")
