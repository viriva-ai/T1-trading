"""The repo's feature code must reproduce the original lgb_paper/features_paper.py on a synthetic market.
tests/fixtures/features_expected.csv was produced by the original code (see tests/fixtures/README.md)."""
from pathlib import Path

import numpy as np
import pandas as pd

import synth
from t1 import features as FE

FIX = Path(__file__).parent / "fixtures"


def test_features_match_original(tmp_path):
    P = synth.panel()
    synth.write_ext(tmp_path / "ext", P["C"].index)
    D = FE.Data(P["C"], P["O"], P["H"], P["L"], P["V"], P["px"], P["liq"], P["tri"], lambda T: P["syms"])
    X = FE.build(D, P["sec"], P["DP"], tmp_path / "ext")["X"]
    exp = pd.read_csv(FIX / "features_expected.csv", parse_dates=["T"])
    got = X[X["T"].isin(exp["T"].unique())][["T", "sym"] + FE.FEATURES].reset_index(drop=True)
    assert list(got.columns) == list(exp.columns)
    assert len(got) == len(exp) and (got.sym.values == exp.sym.values).all()
    a = got[FE.FEATURES].to_numpy(dtype=float)
    b = exp[FE.FEATURES].to_numpy(dtype=float)
    assert np.array_equal(np.isnan(a), np.isnan(b))
    np.testing.assert_allclose(a[~np.isnan(a)], b[~np.isnan(b)], rtol=2e-6, atol=1e-6)


def test_feature_list_is_the_37_m4_inputs():
    assert len(FE.FEATURES) == 37 and len(FE.RANKED) == 23 and len(FE.MARKET) == 13
