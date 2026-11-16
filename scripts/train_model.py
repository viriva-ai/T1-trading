#!/usr/bin/env python3
"""Train M4 model blocks (5 LightGBM fits each, ~2-6 min per block on 4 cores).

  python scripts/train_model.py                    # the block for the latest completed week (if missing)
  python scripts/train_model.py --refit 2026-12-18 # a specific refit week
  python scripts/train_model.py --bootstrap-hn     # one-time: seed-7 walk-forward of H0..H4 for the HN choice (~250 fits, hours)
  python scripts/train_model.py --all              # every block since 2015 (needed for a full walk-forward backtest)
"""
from _common import Lock, load_cfg, log, parser

import pandas as pd

from t1 import pipeline
from t1.model import M4Store, refit_week_for, schedule

ap = parser(__doc__)
ap.add_argument("--refit", default=None)
ap.add_argument("--bootstrap-hn", action="store_true")
ap.add_argument("--all", action="store_true")
ap.add_argument("--force", action="store_true", help="retrain even if the block exists")
a = ap.parse_args()
cfg = load_cfg(a)
with Lock(cfg):
    D, DP, _ = pipeline.load_inputs(cfg, log)
    F = pipeline.build_features(cfg, D, DP, log)
    X = F["X"]
    weeks, refits = schedule(X, cfg["model"]["score_start"], cfg["model"]["refit_every_weeks"])
    st = M4Store(cfg.models_dir)
    if a.bootstrap_hn:
        st.bootstrap_hn(X, weeks, refits, cfg["model"], log=log)
    if a.all:
        targets = refits
    elif a.refit:
        targets = [pd.Timestamp(a.refit)]
    else:
        targets = [refit_week_for(pipeline.completed_weeks(D)[-1], refits)]
    for R in targets:
        if R not in refits:
            raise SystemExit(f"{R.date()} is not a refit week; refit weeks: {[str(r.date()) for r in refits[-4:]]} ...")
        if st.has(R) and not a.force:
            log(f"block {R.date()} exists")
            continue
        meta = st.train_block(X, R, weeks, refits, cfg["model"], log)
        log(f"block {R.date()} trained: HP {meta['hp']} ({meta['hp_reason']}); next refit due {meta['next_refit_due']}")
