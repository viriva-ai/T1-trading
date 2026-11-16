#!/usr/bin/env python3
"""Read-only intraday mark of the paper holdings on Kite LTPs (quote calls only). Flags stops touched so far today."""
from _common import load_cfg, log, parser

import pandas as pd

from t1.data.kite import connect_read_only

ap = parser(__doc__)
a = ap.parse_args()
cfg = load_cfg(a)
pos = pd.read_csv(cfg.state_dir / "positions.csv")
k = connect_read_only(cfg.token_file)
q = k.quote([f"NSE:{s}" for s in pos.sym] + ["NSE:NIFTY 500"])
rows = []
for r in pos.itertuples():
    x = q.get(f"NSE:{r.sym}")
    if not x:
        continue
    ltp, low = float(x["last_price"]), float(x["ohlc"]["low"])
    rows.append(dict(sym=r.sym, qty=r.qty, ltp=ltp, chg_pct=round(100 * (ltp / r.last_close - 1), 2), stop=r.stop,
                     pct_above_stop=round(100 * (ltp / r.stop - 1), 2), stop_touched=low <= r.stop, unreal_pnl=round(r.qty * ltp - r.cost_incl_fees, 0)))
df = pd.DataFrame(rows)
log(f"Nifty 500 {q.get('NSE:NIFTY 500', {}).get('last_price')}")
print(df.to_string(index=False))
