#!/usr/bin/env python3
"""One-time history download: NSE bhavcopy + MTO from data.history_start (2010) to today, Yahoo/FRED/liquid-fund
history, current Nifty 500 list. Roughly 4,300 trading days x 2 files; expect 1-3 hours (requests are paced).
Re-run safely at any time; it only fetches what is missing.

  python scripts/bootstrap_data.py
  python scripts/bootstrap_data.py --import-raw /path/to/old/bhav   # optional: seed from an existing archive first
"""
import shutil
from pathlib import Path

from _common import Lock, load_cfg, log, parser

from t1 import pipeline

ap = parser(__doc__)
ap.add_argument("--import-raw", default=None, help="directory with normalised YYYYMMDD.csv bhavcopy files to copy first")
ap.add_argument("--import-mto", default=None, help="directory with normalised YYYYMMDD.csv MTO files to copy first")
a = ap.parse_args()
cfg = load_cfg(a)
with Lock(cfg):
    for src, sub in ((a.import_raw, "bhav"), (a.import_mto, "mto")):
        if src:
            dst = cfg.data_dir / "raw" / sub
            dst.mkdir(parents=True, exist_ok=True)
            n = 0
            for p in Path(src).glob("2*.csv"):
                if p.stem >= cfg["data"]["history_start"].replace("-", "") and not (dst / p.name).exists():
                    shutil.copy2(p, dst / p.name)
                    n += 1
            log(f"imported {n} {sub} files from {src}")
    st = pipeline.refresh(cfg, log, bootstrap=True)
    log(f"Nifty 500 list: {st['n500_list']} | yahoo: {st['yahoo']} | fred: {st['fred']} | liquid NAV: {st['liquid_nav']}")
    D, DP, members = pipeline.load_inputs(cfg, log)
    log(f"panel ready: {D.C.shape[0]} sessions {D.C.index[0].date()}..{D.C.index[-1].date()}, {D.C.shape[1]} symbols")
