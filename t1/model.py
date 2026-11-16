"""M4: the T1 ranking model.

- Five LightGBM regressors on the 4-week forward-return rank (y4_rk), 37 inputs (t1.features.FEATURES).
- Walk-forward: refit weeks are every 13th scored week counted from the first week >= 2015-01-01; a block trained at
  refit week R uses rows with T <= the week 5 weeks before R (4-week label + 1-week purge), expanding window from 2011.
- HN hyper-parameter choice per block: among H0..H4 pick the set whose seed-7 walk-forward scores had the best mean
  weekly rank-IC (vs y4) over earlier out-of-sample weeks whose label had closed by the cutoff (H0 if < 13 weeks).
- The block = the chosen H with seed 7 + the same H with seeds 11, 23, 42, 101. Score = mean of the 5 per-week pct-ranks.
Boosters are saved under <models_dir>/M4_<YYYYMMDD>_*.txt with a JSON manifest; they are not committed to git.
"""
from __future__ import annotations

import json
import os
from importlib import resources
from pathlib import Path

import numpy as np
import pandas as pd

from t1.features import FEATURES

HP = {"H0": {}, "H1": dict(num_leaves=15, min_child_samples=500), "H2": dict(num_leaves=63, min_child_samples=100),
      "H3": dict(n_estimators=600, learning_rate=0.025), "H4": dict(colsample_bytree=0.5, reg_lambda=5.0)}


def params(h: str, seed: int, n_jobs: int = 4) -> dict:
    p = dict(n_estimators=300, learning_rate=0.05, num_leaves=31, min_child_samples=200, subsample=0.8, subsample_freq=1,
             colsample_bytree=0.8, reg_lambda=1.0, n_jobs=n_jobs, random_state=seed, verbose=-1)
    p.update(HP[h])
    return p


def schedule(X: pd.DataFrame, score_start="2015-01-01", every=13):
    weeks = sorted(pd.DatetimeIndex(X["T"].unique()))
    scored = [w for w in weeks if w >= pd.Timestamp(score_start)]
    return weeks, scored[::every]


def refit_week_for(T, refits) -> pd.Timestamp:
    return max(r for r in refits if r <= pd.Timestamp(T))


def cutoff_for(R, weeks, purge=5) -> pd.Timestamp:
    wi = {w: i for i, w in enumerate(weeks)}
    return weeks[wi[pd.Timestamp(R)] - purge]


def fit_one(X, R, weeks, h, seed, purge=5, n_jobs=4):
    import lightgbm as lgb
    cut = cutoff_for(R, weeks, purge)
    tr = X[(X["T"] <= cut) & X["y4_rk"].notna()]
    m = lgb.LGBMRegressor(**params(h, seed, n_jobs))
    m.fit(tr[FEATURES], tr["y4_rk"])
    return m.booster_, cut, len(tr)


def weekly_ic(scores: pd.DataFrame, X: pd.DataFrame) -> pd.Series:
    z = scores.merge(X[["T", "sym", "y4"]], on=["T", "sym"]).dropna(subset=["score", "y4"])
    return z.groupby("T").apply(lambda g: g.score.rank().corr(g.y4.rank()) if len(g) > 20 else np.nan, include_groups=False).dropna()


class M4Store:
    def __init__(self, models_dir: Path):
        self.dir = Path(models_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def manifest(self, R) -> Path:
        return self.dir / f"M4_{pd.Timestamp(R):%Y%m%d}.json"

    def has(self, R) -> bool:
        return self.manifest(R).exists()

    def latest_before(self, R):
        have = sorted(self.dir.glob("M4_????????.json"))
        have = [p for p in have if pd.Timestamp(p.stem[3:]) <= pd.Timestamp(R)]
        return json.loads(have[-1].read_text()) if have else None

    def load(self, R) -> dict:
        return json.loads(self.manifest(R).read_text())

    def score(self, meta: dict, Xw: pd.DataFrame) -> pd.DataFrame:
        import lightgbm as lgb
        if meta["features"] != FEATURES:
            raise ValueError("model feature list differs from t1.features.FEATURES")
        out = Xw[["T", "sym"]].copy()
        cols = []
        for i, fn in enumerate(meta["files"]):
            b = lgb.Booster(model_file=str(self.dir / Path(fn).name))
            out[f"m{i}"] = b.predict(Xw[meta["features"]])
            out[f"m{i}"] = out.groupby("T")[f"m{i}"].rank(pct=True)
            cols.append(f"m{i}")
        out["score"] = out[cols].mean(axis=1)
        return out[["T", "sym", "score"]]

    # ---------------- HN choice ----------------
    def ic_cache(self) -> Path:
        return self.dir / "hn_walkforward_scores.pkl"

    def choose_hp(self, X, R, weeks, refits, cfg_model, log=print) -> tuple[str, str]:
        if cfg_model.get("hp_override"):
            return cfg_model["hp_override"], "config hp_override"
        cut = cutoff_for(R, weeks, cfg_model["purge_weeks"])
        if self.ic_cache().exists():
            S = pd.read_pickle(self.ic_cache())
            ics = {h: weekly_ic(S[h], X) for h in HP if h in S}
            if len(ics) == len(HP):
                past = {h: v[v.index + pd.Timedelta(weeks=4) <= cut] for h, v in ics.items()}
                if len(past["H0"]) < cfg_model["hn_min_weeks"]:
                    return "H0", "HN: fewer than 13 past IC weeks"
                best = max(HP, key=lambda h: past[h].mean())
                return best, "HN: " + ", ".join(f"{h} {past[h].mean():.4f}" for h in HP)
        with resources.files("t1.resources").joinpath("hn_history.json").open("r") as fh:
            hist = json.load(fh)["choices"]
        prior = [k for k in sorted(hist) if pd.Timestamp(k) <= pd.Timestamp(R)]
        h = hist[prior[-1]] if prior else "H0"
        log(f"  WARNING: no HN walk-forward cache ({self.ic_cache().name}); carrying the last recorded choice {h}. "
            "Run scripts/train_model.py --bootstrap-hn to compute it properly.")
        return h, f"carried from hn_history.json ({prior[-1] if prior else 'none'})"

    def bootstrap_hn(self, X, weeks, refits, cfg_model, upto=None, log=print):
        """Seed-7 walk-forward scores for every H and every refit block (heavy: 5 x ~50 fits). Resumable."""
        S = pd.read_pickle(self.ic_cache()) if self.ic_cache().exists() else {}
        upto = pd.Timestamp(upto) if upto else refits[-1]
        for h in HP:
            have = S.get(h)
            done = set(pd.DatetimeIndex(have.attrs.get("blocks", []))) if have is not None else set()
            parts = [have] if have is not None else []
            for k, R in enumerate(refits):
                if R > upto or R in done:
                    continue
                nxt = refits[k + 1] if k + 1 < len(refits) else pd.Timestamp("2100-01-01")
                b, cut, n = fit_one(X, R, weeks, h, 7, cfg_model["purge_weeks"], cfg_model["n_jobs"])
                te = X[(X["T"] >= R) & (X["T"] < nxt)]
                parts.append(te[["T", "sym"]].assign(score=b.predict(te[FEATURES])))
                done.add(R)
                cur = pd.concat(parts, ignore_index=True)
                cur.attrs["blocks"] = [str(d.date()) for d in sorted(done)]
                S[h] = cur
                tmp = self.ic_cache().with_suffix(".tmp")
                pd.to_pickle(S, tmp)
                os.replace(tmp, self.ic_cache())
                log(f"  HN bootstrap {h} block {R.date()} ({n} rows)")
        return S

    def train_block(self, X, R, weeks, refits, cfg_model, log=print) -> dict:
        R = pd.Timestamp(R)
        h, why = self.choose_hp(X, R, weeks, refits, cfg_model, log)
        files = []
        cut = None
        n = 0
        for seed in cfg_model["seeds"]:
            run = f"L5_{h}" if seed == 7 else f"SEED{seed}"
            b, cut, n = fit_one(X, R, weeks, h, seed, cfg_model["purge_weeks"], cfg_model["n_jobs"])
            fn = self.dir / f"M4_{R:%Y%m%d}_{run}.txt"
            b.save_model(str(fn))
            files.append(fn.name)
            log(f"  trained {run} ({h}, seed {seed}) on {n} rows, cutoff {pd.Timestamp(cut).date()}")
        meta = dict(refit=str(R.date()), cutoff=str(pd.Timestamp(cut).date()), ntrain=int(n), hp=h, hp_reason=why,
                    features=FEATURES, files=files, next_refit_due=str((R + pd.Timedelta(weeks=13)).date()))
        self.manifest(R).write_text(json.dumps(meta, indent=1))
        return meta
