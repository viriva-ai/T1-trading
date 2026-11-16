"""Deterministic synthetic market used by the tests (no real data in the repo)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

SECTORS = ["Banks", "IT", "Pharma", "Autos"]


def panel(n_sym: int = 40, start="2013-01-01", end="2015-06-30", seed: int = 7) -> dict:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, end)
    syms = [f"S{i:02d}" for i in range(n_sym)]
    drift = rng.normal(0.0004, 0.0004, n_sym)
    vol = rng.uniform(0.01, 0.03, n_sym)
    r = rng.normal(drift, vol, (len(idx), n_sym))
    C = pd.DataFrame(100 * np.exp(np.cumsum(r, axis=0)) * rng.uniform(0.5, 5, n_sym), index=idx, columns=syms)
    O = C.shift(1).fillna(C.iloc[0]) * (1 + rng.normal(0, 0.004, C.shape))
    H = np.maximum(O, C) * (1 + np.abs(rng.normal(0, 0.006, C.shape)))
    L = np.minimum(O, C) * (1 - np.abs(rng.normal(0, 0.006, C.shape)))
    V = pd.DataFrame(rng.integers(200_000, 2_000_000, C.shape).astype(float), index=idx, columns=syms)
    C.iloc[100:110, 3] = np.nan          # a data gap
    for k in (O, H, L, V):
        k.iloc[100:110, 3] = np.nan
    DP = pd.DataFrame(rng.uniform(0.2, 0.8, C.shape), index=idx, columns=syms)
    px = pd.Series(10000 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, len(idx)))), index=idx)
    liq = pd.Series(np.cumprod(np.full(len(idx), 1 + 0.065 / 252)), index=idx)
    sec = {s: SECTORS[i % 4] for i, s in enumerate(syms[:-1])}     # last symbol: UNKNOWN sector
    return dict(C=C, O=O, H=H, L=L, V=V, DP=DP, px=px, liq=liq, tri=px * 1.0, sec=sec, syms=syms)


def write_ext(ext: Path, idx: pd.DatetimeIndex, seed: int = 11) -> None:
    rng = np.random.default_rng(seed)
    ext.mkdir(parents=True, exist_ok=True)
    days = pd.date_range(idx[0] - pd.Timedelta(days=400), idx[-1], freq="D")
    for name, lvl in [("yf_IDX_INDIAVIX", 15), ("yf_INR_X", 60), ("yf_IDX_GSPC", 2000), ("yf_GC_F", 1300)]:
        v = lvl * np.exp(np.cumsum(rng.normal(0, 0.01, len(days))))
        pd.DataFrame({"Date": days, "Close": v}).to_csv(ext / f"{name}.csv", index=False)
    for sid, lvl in [("DCOILBRENTEU", 80), ("DGS10", 2.5)]:
        v = lvl * np.exp(np.cumsum(rng.normal(0, 0.01, len(days))))
        pd.DataFrame({"observation_date": days[::1], sid: v}).to_csv(ext / f"fred_{sid}.csv", index=False)
