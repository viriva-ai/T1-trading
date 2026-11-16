"""NSE public archives: daily bhavcopy (prices), MTO (delivery %), Nifty 500 constituents.

Files are normalised and stored under <data_dir>/raw/:
  bhav/YYYYMMDD.csv  columns SYMBOL,SERIES,O,H,L,C,P,QTY,VAL,ISIN   (EQ/BE/BZ series only)
  mto/YYYYMMDD.csv   columns SYMBOL,SERIES,QTY,DELIV
  YYYYMMDD.none      marker: no session that day (holiday), written only for dates > 10 days old
No login is needed. NSE rate-limits aggressive clients, so requests are paced (data.request_pause_s).
"""
from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
      "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"}
UDIFF_FROM = pd.Timestamp("2024-07-08")
SERIES_KEEP = ("EQ", "BE", "BZ")
N500_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"

_session = None


def session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(UA)
    return _session


def _get(url: str, timeout: int = 40) -> tuple[int, bytes]:
    for attempt in range(3):
        try:
            r = session().get(url, timeout=timeout)
            return r.status_code, r.content
        except requests.RequestException:
            time.sleep(2 + 3 * attempt)
    return 0, b""


def bhav_urls(d: pd.Timestamp) -> list[str]:
    new = f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
    mon = d.strftime("%b").upper()
    old = f"https://nsearchives.nseindia.com/content/historical/EQUITIES/{d:%Y}/{mon}/cm{d:%d}{mon}{d:%Y}bhav.csv.zip"
    return [new, old] if d >= UDIFF_FROM else [old, new]


def parse_bhav_zip(body: bytes) -> pd.DataFrame:
    z = zipfile.ZipFile(io.BytesIO(body))
    df = pd.read_csv(z.open(z.namelist()[0]))
    df.columns = [c.strip() for c in df.columns]
    if "TckrSymb" in df.columns:   # UDiFF format (from 8 Jul 2024)
        df = df[df.SctySrs.isin(SERIES_KEEP)]
        return pd.DataFrame(dict(SYMBOL=df.TckrSymb, SERIES=df.SctySrs, O=df.OpnPric, H=df.HghPric, L=df.LwPric, C=df.ClsPric,
                                 P=df.PrvsClsgPric, QTY=df.TtlTradgVol, VAL=df.TtlTrfVal, ISIN=df.ISIN))
    df["SERIES"] = df.SERIES.astype(str).str.strip()
    df = df[df.SERIES.isin(SERIES_KEEP)]
    return pd.DataFrame(dict(SYMBOL=df.SYMBOL.str.strip(), SERIES=df.SERIES, O=df.OPEN, H=df.HIGH, L=df.LOW, C=df.CLOSE,
                             P=df.PREVCLOSE, QTY=df.TOTTRDQTY, VAL=df.TOTTRDVAL, ISIN=df.get("ISIN")))


def parse_mto(body: bytes) -> pd.DataFrame:
    rows = [line.split(",") for line in body.decode("latin-1").splitlines() if line.startswith("20,")]
    return pd.DataFrame([dict(SYMBOL=x[2].strip(), SERIES=x[3].strip(), QTY=x[4].strip(), DELIV=x[5].strip()) for x in rows if len(x) >= 6])


def _write_atomic(df: pd.DataFrame, f: Path) -> None:
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    df.to_csv(tmp, index=False)
    tmp.replace(f)


def fetch_bhav(d: pd.Timestamp, raw: Path, today: pd.Timestamp) -> str:
    f = raw / "bhav" / f"{d:%Y%m%d}.csv"
    none = raw / "bhav" / f"{d:%Y%m%d}.none"
    if f.exists() or none.exists():
        return "have"
    code = 0
    for url in bhav_urls(d):
        code, body = _get(url)
        if code == 200 and body[:2] == b"PK":
            try:
                _write_atomic(parse_bhav_zip(body), f)
                return "new"
            except (zipfile.BadZipFile, KeyError, ValueError):
                continue
    if (today - d).days > 10 and code in (404, 403):
        none.parent.mkdir(parents=True, exist_ok=True)
        none.write_text(f"http {code}\n")
    return f"missing(http {code})"


def fetch_mto(d: pd.Timestamp, raw: Path, today: pd.Timestamp) -> str:
    f = raw / "mto" / f"{d:%Y%m%d}.csv"
    none = raw / "mto" / f"{d:%Y%m%d}.none"
    if f.exists() or none.exists():
        return "have"
    code, body = _get(f"https://nsearchives.nseindia.com/archives/equities/mto/MTO_{d:%d%m%Y}.DAT")
    if code == 200:
        df = parse_mto(body)
        if len(df):
            _write_atomic(df, f)
            return "new"
    if (today - d).days > 10 and code in (404, 403):
        none.parent.mkdir(parents=True, exist_ok=True)
        none.write_text(f"http {code}\n")
    return f"missing(http {code})"


def fetch_range(start, end, raw: Path, mto_start, pause: float = 0.4, log=print) -> dict:
    """Download every weekday in [start, end] that is not on disk yet. Idempotent."""
    from t1.timeutil import today_ist
    today = today_ist()
    out = {}
    days = pd.bdate_range(pd.Timestamp(start), pd.Timestamp(end))
    for i, d in enumerate(days):
        b = fetch_bhav(d, raw, today)
        m = fetch_mto(d, raw, today) if d >= pd.Timestamp(mto_start) else "skip"
        out[str(d.date())] = dict(bhav=b, mto=m)
        if b == "new" or m == "new":
            time.sleep(pause)
        if log and (i % 250 == 0 or i == len(days) - 1):
            log(f"  NSE {d.date()} ({i + 1}/{len(days)}): bhav {b}, mto {m}")
    return out


def fetch_n500_list(nse_dir: Path, today: pd.Timestamp) -> str:
    """Current Nifty 500 list -> nse/n500_live.csv, plus a dated snapshot (forward point-in-time membership)."""
    code, body = _get(N500_URL)
    nse_dir.mkdir(parents=True, exist_ok=True)
    if code == 200 and body.startswith(b"Company Name"):
        (nse_dir / "n500_live.csv").write_bytes(body)
        snap = nse_dir / "n500_snapshots"
        snap.mkdir(exist_ok=True)
        (snap / f"{today:%Y%m%d}.csv").write_bytes(body)
        return f"refreshed ({len(body.splitlines()) - 1} names)"
    return f"FAILED http {code}; using cached copy" if (nse_dir / "n500_live.csv").exists() else f"FAILED http {code}; no cached copy"


SYMCHG_URL = "https://nsearchives.nseindia.com/content/equities/symbolchange.csv"


def fetch_symbol_changes(nse_dir: Path) -> str:
    """NSE's list of ticker renames (old -> new, effective date), used to stitch price history across renames."""
    code, body = _get(SYMCHG_URL)
    nse_dir.mkdir(parents=True, exist_ok=True)
    if code == 200 and b"OLD SYMBOL" in body[:200].upper():
        (nse_dir / "symbolchange.csv").write_bytes(body)
        return "refreshed"
    return f"FAILED http {code}; using cached copy" if (nse_dir / "symbolchange.csv").exists() else f"FAILED http {code}"


def load_symbol_changes(nse_dir: Path) -> pd.DataFrame:
    f = Path(nse_dir) / "symbolchange.csv"
    if not f.exists():
        return pd.DataFrame(columns=["old", "new", "date"])
    x = pd.read_csv(f, encoding="latin-1")
    x.columns = [c.strip().upper() for c in x.columns]
    out = pd.DataFrame(dict(old=x["OLD SYMBOL"].astype(str).str.strip(), new=x["NEW SYMBOL"].astype(str).str.strip(),
                            date=pd.to_datetime(x["EFFECTIVE DATE"].astype(str).str.strip(), format="%d-%b-%Y", errors="coerce")))
    return out.dropna().sort_values("date").reset_index(drop=True)


def trading_days(raw: Path) -> list[pd.Timestamp]:
    return sorted(pd.Timestamp(p.stem) for p in (raw / "bhav").glob("2*.csv"))
