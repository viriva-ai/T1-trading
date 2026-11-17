#!/usr/bin/env python3
"""ONE-SHARE live order test: proves the real Zerodha order path from the registered static IP.

Separate from the T1 book: config live_trading_enabled stays false and is not read here. DRY RUN is the default.

  Suggest a stock (latest NSE bhavcopy; no token needed):
      deploy/run.sh live_oneshare.py --suggest            (or .venv/bin/python scripts/live_oneshare.py --suggest)
  Dry run (all pre-checks, prints the exact order, sends nothing):
      python scripts/live_oneshare.py --side BUY --symbol ITC
  Real BUY of 1 share (needs the exact confirm string for today, IST):
      python scripts/live_oneshare.py --side BUY --symbol ITC --live --confirm-live BUY-ITC-1-20261118
  Real same-day SELL of that share (only after the BUY is COMPLETE):
      python scripts/live_oneshare.py --side SELL --symbol ITC --live --confirm-live SELL-ITC-1-20261118
  Emergency stop: touch ~/t1-state/KILL  (or ~/t1-state/live_test/KILL_ONESHARE)

Hard limits are constants in t1/execution/live_test.py. Every Kite response is logged (secrets redacted) to
<repo>/logs/live_test/oneshare_YYYYMMDD.jsonl. The token is never printed.
"""
import json
from pathlib import Path

from _common import ROOT, load_cfg, log, parser

from t1.execution import live_test as LT

ap = parser(__doc__)
ap.add_argument("--side", choices=["BUY", "SELL"], default="BUY")
ap.add_argument("--symbol", default=None)
ap.add_argument("--qty", type=int, default=1)
ap.add_argument("--live", action="store_true", help="actually send (needs --confirm-live and every check PASS)")
ap.add_argument("--confirm-live", default=None, help="exactly <SIDE>-<SYMBOL>-1-<YYYYMMDD> (today, IST)")
ap.add_argument("--poll-seconds", type=int, default=60)
ap.add_argument("--suggest", action="store_true", help="list cheap liquid NSE EQ candidates from the latest bhavcopy")
a = ap.parse_args()
cfg = load_cfg(a)

if a.suggest:
    import pandas as pd
    files = sorted((cfg.data_dir / "raw" / "bhav").glob("2*.csv"))
    df = pd.read_csv(files[-1])
    df = df[(df.SERIES == "EQ") & df.C.between(50, 400)].copy()
    df["VAL_CR"] = df.VAL / 1e7
    df["RANGE_PCT"] = (df.H / df.L - 1) * 100
    top = df.sort_values("VAL", ascending=False).head(10)[["SYMBOL", "C", "QTY", "VAL_CR", "RANGE_PCT"]]
    log(f"cheap liquid NSE EQ names, bhavcopy {files[-1].stem} (close Rs50-400, by traded value):")
    print(top.round(2).to_string(index=False))
    raise SystemExit(0)

if not a.symbol:
    raise SystemExit("--symbol is required (or use --suggest)")
state_dir = cfg.state_dir / "live_test"
log_dir = ROOT / "logs" / "live_test"
kill = [cfg.state_dir / "KILL", state_dir / "KILL_ONESHARE"]

k = None
try:
    from t1.data.kite import load_token, api_key
    from kiteconnect import KiteConnect
    raw = KiteConnect(api_key=api_key())
    raw.set_access_token(load_token(cfg.token_file))
    k = LT.LimitedKite(raw, allow_orders=a.live)
except Exception as e:
    log(f"no usable Kite session ({type(e).__name__}); checks needing Kite will FAIL")

t = LT.LiveTester(k, state_dir, log_dir, kill)
p = t.build_plan(a.side, a.symbol, a.qty, a.confirm_live, a.live)
log(f"{'LIVE' if a.live else 'DRY RUN'} one-share test, {p.side} {p.qty} {p.symbol}")
for name, v in p.checks.items():
    print(f"  {name:24s} {v}")
print("  order:", json.dumps(p.order_kwargs()))
if p.fees:
    f = p.fees
    print(f"  ltp Rs{p.ltp}  tick {p.tick}  value Rs{f['value']}")
    print(f"  est. charges: same-day round trip Rs{f['same_day_round_trip']:.2f} (intraday re-class, no DP);"
          f" sell on a later day Rs{f['next_day_round_trip']:.2f} (incl. DP Rs{f['dp_charge_next_day']})")
if not a.live:
    t.send(p, live=False)
    log("dry run: nothing sent. ALL CHECKS PASS" if all(v in ("PASS", "SKIP: dry run") for v in p.checks.values())
        else "dry run: nothing sent. Some checks FAIL (see above)")
    raise SystemExit(0)
try:
    r = t.send(p, live=True, poll_s=a.poll_seconds)
except LT.GuardError as e:
    log(f"LIVE REFUSED: {e}")
    raise SystemExit(4)
log(f"SENT: {json.dumps(r, default=str)}")
