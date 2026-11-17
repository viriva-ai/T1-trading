#!/usr/bin/env python3
"""Daily paper run (cron, Mon-Fri ~19:45 IST after NSE publishes the bhavcopy):
refresh data -> rebuild panel/features -> score any newly completed decision week (Friday) -> replay the paper book ->
write state files + next-session orders (pending_orders.csv). Never places an order.

  python scripts/daily_run.py                 # normal
  python scripts/daily_run.py --no-refresh    # reuse data on disk
  python scripts/daily_run.py --kite-check    # also compare the last close of held names with Kite (read-only)
"""
from _common import Lock, load_cfg, log, parser

import pandas as pd

from t1 import notify, paper, pipeline

ap = parser(__doc__)
ap.add_argument("--no-refresh", action="store_true")
ap.add_argument("--kite-check", action="store_true")
ap.add_argument("--no-auto-train", action="store_true", help="never train a missing M4 block here; score provisionally")
a = ap.parse_args()
cfg = load_cfg(a)
with Lock(cfg):
    st = {} if a.no_refresh else pipeline.refresh(cfg, log)
    if st:
        log(f"data: n500 {st['n500_list']}; recent NSE days {st['nse_recent']}")
    D, DP, _ = pipeline.load_inputs(cfg, log)
    F = pipeline.build_features(cfg, D, DP, log)
    weeks = pipeline.completed_weeks(D)
    inc = pd.Timestamp(cfg["paper_inception"])
    todo = [T for T in weeks if T >= inc - pd.Timedelta(days=6)]
    if weeks[-1] not in todo:
        todo.append(weeks[-1])          # always show the latest signal, even before the paper book starts
    failed = any("FAIL" in str(v) for v in st.values()) if st else False
    provisional = failed or (F["mto_last"] is not None and pd.Timestamp(F["mto_last"]) < weeks[-1])
    book = pipeline.ScoreBook(cfg.state_dir)
    done = pipeline.score_weeks(cfg, F, todo, book, provisional, auto_train=not a.no_auto_train, log=log)
    for x in done:
        log(f"scored week {x['week']} with block {x['block']} ({x['names']} names){' PROVISIONAL' if x['provisional'] else ''}"
            f"{' - ' + x['warning'] if x['warning'] else ''}")
    out = paper.run(cfg, D, book, weeks, log)
    if not out.get("started"):
        T = weeks[-1]
        r = pipeline.ranking(D, book, T, 10)
        log(f"signal for week {T.date()} (paper book not started):\n{r.to_string(index=False)}")
        raise SystemExit(0)
    s = out["summary"]
    msg = [f"T1 PAPER {s['as_of']}: equity Rs{s['equity']:,.0f} (cash Rs{s['cash']:,.0f}, {s['open_positions']} open) | "
           f"after-tax if sold Rs{s['after_tax_if_liquidated_now']:,.0f} | index fund Rs{s['index_fund_value']:,.0f}"]
    if s["stop_exits_today"]:
        msg.append(f"STOP EXITS TODAY: {s['stop_exits_today']}")
    nxt = [p for p in out["pending"] if p["action"] in ("BUY", "SELL")]
    if nxt:
        msg.append("Next open: " + ", ".join(f"{p['action']} {p['sym']} ~{p['expected_qty']}" for p in nxt))
    notify.send("\n".join(msg))
    if a.kite_check:
        from t1.data.kite import TokenExpired, connect_read_only
        try:
            k = connect_read_only(cfg.token_file)
            held = [p["sym"] for p in out["positions"]]
            q = k.ohlc([f"NSE:{x}" for x in held]) if held else {}
            last = D.C.index[-1]
            for x in held:
                kc = q.get(f"NSE:{x}", {}).get("ohlc", {}).get("close")
                nc = D.C.at[last, x]
                log(f"  kite-check {x}: NSE close {nc:.2f} vs Kite {kc}")
        except TokenExpired as e:
            log(f"kite-check skipped: TOKEN_EXPIRED ({e})")
        except RuntimeError as e:   # Kite down or network error: the paper book is already written, so don't fail the run
            log(f"kite-check skipped: {e}")
