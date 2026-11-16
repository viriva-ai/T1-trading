"""Market-wide inputs (free, no login): Yahoo Finance daily bars, FRED series, AMFI/mfapi liquid-fund NAV.

Stored under <data_dir>/ext/ with the same file names the original pipeline used:
  yf_IDX_CRSLDX.csv (Nifty 500 index), yf_IDX_INDIAVIX.csv, yf_INR_X.csv, yf_IDX_GSPC.csv, yf_GC_F.csv,
  fred_DCOILBRENTEU.csv, fred_DGS10.csv, liquid_nav.json (mfapi scheme, default 119568 = ABSL Liquid Direct Growth)
"""
from __future__ import annotations

import io
import json
import warnings
from pathlib import Path

import pandas as pd
import requests

YAHOO = {"yf_IDX_INDIAVIX": "^INDIAVIX", "yf_INR_X": "INR=X", "yf_IDX_GSPC": "^GSPC", "yf_GC_F": "GC=F", "yf_IDX_CRSLDX": "^CRSLDX"}
FRED = ("DCOILBRENTEU", "DGS10")


def refresh_yahoo(ext: Path, nse_days, today: pd.Timestamp, full: bool = False) -> dict:
    import yfinance as yf
    ext.mkdir(parents=True, exist_ok=True)
    res = {}
    nse_days = set(pd.DatetimeIndex(nse_days))
    for fn, tk in YAHOO.items():
        path = ext / f"{fn}.csv"
        old = pd.read_csv(path, parse_dates=["Date"]).set_index("Date") if path.exists() else None
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                x = yf.download(tk, period="max" if (full or old is None) else "3mo", interval="1d", auto_adjust=False,
                                progress=False, threads=False)
            if isinstance(x.columns, pd.MultiIndex):
                x.columns = x.columns.get_level_values(0)
            x.index = pd.DatetimeIndex(x.index).tz_localize(None).normalize()
            x.index.name = "Date"
            if tk in ("^INDIAVIX", "^CRSLDX") and nse_days:
                # drop sessions whose NSE bhavcopy is not published yet (today's possibly-live bar)
                x = x[x.index <= max(nse_days)]
            else:
                x = x[x.index < today]                     # drop today's possibly-live bar
            x = x.dropna(subset=["Close"])
            if old is not None:
                old = old[~old.index.isin(x.index)]
                x = pd.concat([old, x[[c for c in x.columns if c in old.columns]]]).sort_index()
            x = x[~x.index.duplicated(keep="last")]
            x.to_csv(path)
            res[fn] = f"ok, last {x.Close.dropna().index[-1].date()}"
        except Exception as e:  # network / parsing: keep cached file
            res[fn] = f"FAILED {type(e).__name__}; using cached"
    return res


def refresh_fred(ext: Path) -> dict:
    ext.mkdir(parents=True, exist_ok=True)
    res = {}
    for sid in FRED:
        try:
            r = requests.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}", timeout=90)
            ok = r.status_code == 200 and r.content[:20].lower().startswith(b"observation_date")
        except requests.RequestException:
            ok, r = False, None
        if ok:
            (ext / f"fred_{sid}.csv").write_bytes(r.content)
            res[sid] = f"ok, last {pd.read_csv(io.BytesIO(r.content)).iloc[-1, 0]}"
        else:
            res[sid] = f"FAILED http {getattr(r, 'status_code', 0)}; using cached"
    return res


def refresh_liquid_nav(ext: Path, scheme: int) -> str:
    ext.mkdir(parents=True, exist_ok=True)
    try:
        r = requests.get(f"https://api.mfapi.in/mf/{scheme}", timeout=60)
        j = r.json()
        _ = j["data"][0]["nav"]
        (ext / "liquid_nav.json").write_bytes(r.content)
        return f"ok, last {j['data'][0]['date']}"
    except Exception as e:
        return f"FAILED {type(e).__name__}; using cached"


def load_liquid_nav(ext: Path) -> pd.Series:
    f = ext / "liquid_nav.json"
    if not f.exists():
        return pd.Series(dtype=float)
    j = json.loads(f.read_text())
    s = pd.Series({pd.to_datetime(x["date"], dayfirst=True): float(x["nav"]) for x in j["data"]})
    return s.sort_index()


def yahoo_close(ext: Path, name: str, col: str = "Close") -> pd.Series:
    x = pd.read_csv(Path(ext) / name, parse_dates=["Date"]).set_index("Date")[col]
    return pd.to_numeric(x, errors="coerce").dropna()


def fred_series(ext: Path, name: str, col: str) -> pd.Series:
    x = pd.read_csv(Path(ext) / name, parse_dates=["observation_date"]).set_index("observation_date")[col]
    return pd.to_numeric(x, errors="coerce").dropna()
