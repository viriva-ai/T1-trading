#!/usr/bin/env python3
"""Print the M4 ranking for a decision week (default: the latest completed week). Read-only apart from freezing
the week's scores in state/scores.pkl. Cron: Friday ~20:15 IST (after daily_run)."""
from _common import Lock, load_cfg, log, parser

import pandas as pd

from t1 import pipeline

ap = parser(__doc__)
ap.add_argument("--week", default=None, help="decision week (Friday) YYYY-MM-DD")
ap.add_argument("--top", type=int, default=30)
ap.add_argument("--no-refresh", action="store_true")
ap.add_argument("--live-membership", choices=["snapshot", "latest"], default="snapshot")
a = ap.parse_args()
cfg = load_cfg(a)
with Lock(cfg):
    if not a.no_refresh:
        pipeline.refresh(cfg, log)
    D, DP, _ = pipeline.load_inputs(cfg, log, live_mode=a.live_membership)
    F = pipeline.build_features(cfg, D, DP, log)
    weeks = pipeline.completed_weeks(D)
    T = pd.Timestamp(a.week) if a.week else weeks[-1]
    book = pipeline.ScoreBook(cfg.state_dir)
    pipeline.score_weeks(cfg, F, [T], book, provisional=False, log=log)
    r = pipeline.ranking(D, book, T, a.top)
    anchor = pd.Timestamp(cfg["strategy"]["rebalance_anchor"])
    wl = list(D.WEND)
    reb = (wl.index(T) - wl.index(anchor)) % cfg["strategy"]["rebalance_every_weeks"] == 0
    log(f"M4 ranking for week {T.date()} ({'REBALANCE week' if reb else 'not a rebalance week: stops only'}):")
    print(r.to_string(index=False))
    r.assign(week=str(T.date())).to_csv(cfg.state_dir / f"signal_{T:%Y%m%d}.csv", index=False)
