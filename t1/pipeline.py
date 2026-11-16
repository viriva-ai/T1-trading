"""Orchestration shared by the scripts: refresh data -> panel -> features -> M4 scores (frozen per week)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

from t1 import features as FE
from t1.data import external as EXT
from t1.data import nse
from t1.data.membership import Membership, sectors
from t1.data.panel import build_aux, build_delivery, build_prices
from t1.model import M4Store, refit_week_for, schedule
from t1.timeutil import today_ist


def refresh(cfg, log=print, bootstrap: bool = False) -> dict:
    """Download whatever is missing. Safe to run repeatedly."""
    dd = cfg.data_dir
    raw = dd / "raw"
    today = today_ist()
    days = nse.trading_days(raw)
    start = pd.Timestamp(cfg["data"]["history_start"]) if (bootstrap or not days) else days[-1] - pd.Timedelta(days=10)
    status = {}
    log(f"NSE archives {start.date()} -> {today.date()}")
    status["nse_days"] = nse.fetch_range(start, today, raw, cfg["data"]["mto_start"], cfg["data"]["request_pause_s"],
                                         log=log if bootstrap else None)
    status["n500_list"] = nse.fetch_n500_list(dd / "nse", today)
    status["symbol_changes"] = nse.fetch_symbol_changes(dd / "nse")
    status["yahoo"] = EXT.refresh_yahoo(dd / "ext", nse.trading_days(raw), today, full=bootstrap)
    status["fred"] = EXT.refresh_fred(dd / "ext")
    status["liquid_nav"] = EXT.refresh_liquid_nav(dd / "ext", cfg["cash"]["liquid_fund_scheme_code"])
    recent = {d: v for d, v in status["nse_days"].items() if pd.Timestamp(d) >= today - pd.Timedelta(days=10)}
    status["nse_recent"] = recent
    return status


def load_inputs(cfg, log=print, live_mode: str = "snapshot"):
    dd = cfg.data_dir
    members = Membership(dd / "nse", live_mode=live_mode)
    P = build_prices(dd, members, cfg["data"]["history_start"], log)
    DP = build_delivery(dd, P["C"].index, P["C"].columns, cfg["data"]["mto_start"])
    aux = build_aux(dd, P["C"].index, cfg["benchmark"]["dividend_yield"])
    D = FE.make_data(cfg, P, aux, members)
    return D, DP, members


def completed_weeks(D, today=None) -> list:
    today = today if today is not None else today_ist()
    out = []
    for T in D.WEND:
        fri = T + pd.Timedelta(days=(4 - T.weekday()) % 7)
        if T.weekday() == 4 or fri < today:
            out.append(T)
    return out


def build_features(cfg, D, DP, log=print) -> dict:
    F = FE.build(D, sectors(), DP, cfg.data_dir / "ext", start_univ=cfg["model"]["train_start"])
    log(f"features: {len(F['X'])} rows, {F['X']['T'].nunique()} weeks, delivery data to {F['mto_last']}")
    return F


class ScoreBook:
    """Frozen M4 scores per decision week (state/scores.pkl). A week whose inputs were incomplete is PROVISIONAL and
    re-scored on the next run; once final it never changes (so replays are reproducible)."""

    def __init__(self, state_dir: Path):
        self.f = Path(state_dir) / "scores.pkl"
        self.d = pd.read_pickle(self.f) if self.f.exists() else {}

    def has_final(self, T) -> bool:
        return T in self.d and not self.d.get(f"_provisional_{pd.Timestamp(T).date()}", False)

    def put(self, T, s: pd.Series, provisional: bool):
        self.d[T] = s
        key = f"_provisional_{pd.Timestamp(T).date()}"
        if provisional:
            self.d[key] = True
        else:
            self.d.pop(key, None)

    def provisional(self, T) -> bool:
        return bool(self.d.get(f"_provisional_{pd.Timestamp(T).date()}", False))

    def save(self):
        tmp = self.f.with_suffix(".tmp")
        pd.to_pickle(self.d, tmp)
        os.replace(tmp, self.f)

    def frame(self) -> pd.DataFrame:
        k = {a: b for a, b in self.d.items() if not isinstance(a, str)}
        return pd.DataFrame(k).T.sort_index() if k else pd.DataFrame()


def score_weeks(cfg, F, weeks, book: ScoreBook, provisional: bool, auto_train: bool = True, log=print) -> list:
    X = F["X"]
    store = M4Store(cfg.models_dir)
    allweeks, refits = schedule(X, cfg["model"]["score_start"], cfg["model"]["refit_every_weeks"])
    done = []
    for T in weeks:
        if book.has_final(T):
            continue
        R = refit_week_for(T, refits)
        warn = None
        if not store.has(R):
            if auto_train:
                log(f"M4 block {R.date()} missing: training it now (5 LightGBM fits)")
                store.train_block(X, R, allweeks, refits, cfg["model"], log)
            else:
                meta = store.latest_before(R)
                if meta is None:
                    raise SystemExit(f"no M4 model for refit week {R.date()} in {cfg.models_dir}; run scripts/train_model.py")
                warn = f"REFIT DUE: block {R.date()} not trained; scored with {meta['refit']} (PROVISIONAL)"
        meta = store.load(R) if store.has(R) else store.latest_before(R)
        te = X[X["T"] == T]
        if te.empty:
            log(f"  week {T.date()}: no feature rows (skipped)")
            continue
        o = store.score(meta, te)
        book.put(T, pd.Series(o.score.values, index=o.sym.values), provisional or bool(warn))
        done.append(dict(week=str(T.date()), block=meta["refit"], names=len(te), provisional=provisional or bool(warn), warning=warn))
    book.save()
    return done


def ranking(D, book: ScoreBook, T, top: int = 30) -> pd.DataFrame:
    sc = book.frame()
    if T not in sc.index:
        return pd.DataFrame()
    s = sc.loc[T].reindex(D.universe(T)[0]).dropna().sort_values(ascending=False)
    sec = sectors()
    return pd.DataFrame(dict(rank=range(1, len(s) + 1), sym=s.index, score=s.values.round(4),
                             sector=[sec.get(x, "UNKNOWN") for x in s.index],
                             last_close=[round(float(D.WC.at[T, x]), 2) for x in s.index])).head(top)


def write_json(path: Path, obj) -> None:
    tmp = Path(path).with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=str))
    os.replace(tmp, path)
