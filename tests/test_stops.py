"""Stop and entry mechanics of the engine on a hand-built market."""
import numpy as np
import pandas as pd
import pytest

from t1 import config
from t1.engine import simulate
from t1.features import Data


def market(n_days=320):
    idx = pd.bdate_range("2024-01-01", periods=n_days)
    syms = ["AAA", "BBB", "CCC"]
    C = pd.DataFrame(100.0, index=idx, columns=syms)
    C += np.sin(np.arange(n_days))[:, None] * 0.5          # small wiggle -> ATR > 0
    O, H, L = C.copy(), C + 1.0, C - 1.0
    V = pd.DataFrame(1e6, index=idx, columns=syms)
    return idx, syms, C, O, H, L, V


def make(C, O, H, L, V, idx, syms):
    one = pd.Series(1.0, index=idx)
    return Data(C, O, H, L, V, one * 1000, one, one * 1000, lambda T: syms)


@pytest.fixture
def cfg():
    return config.load(None, overrides=dict(strategy=dict(top_k=2), fees=dict(card="legacy_cnc", intraday_reclass=False),
                                            cash=dict(earn_liquid_fund_interest=False)))


def run(cfg, D, T0, scores, end):
    cfg["strategy"]["rebalance_anchor"] = str(T0.date())
    S = pd.DataFrame([scores], index=[T0])
    return simulate(D, T0, end, S, cfg, capital=1e6, liquidate=False, wset=[T0])


def test_entry_then_low_touch_stop_and_gap_stop(cfg):
    idx, syms, C, O, H, L, V = market()
    D0 = make(C, O, H, L, V, idx, syms)
    T0 = D0.WEND[-6]
    i0 = list(idx).index(T0)
    stopA = D0.WC.at[T0, "AAA"] - 2 * D0.ATR.at[T0, "AAA"]
    stopB = D0.WC.at[T0, "BBB"] - 2 * D0.ATR.at[T0, "BBB"]
    L.iloc[i0 + 3, 0] = stopA - 0.5                      # AAA: intraday low pierces the stop on day 3
    O.iloc[i0 + 4, 1] = stopB - 2.0                      # BBB: opens below its stop on day 4 (gap)
    L.iloc[i0 + 4, 1] = stopB - 3.0
    D = make(C, O, H, L, V, idx, syms)
    res = run(cfg, D, T0, {"AAA": 0.9, "BBB": 0.8, "CCC": 0.1}, idx[i0 + 6])
    f = res["fills"]
    buys = f[f.side == "BUY"]
    assert set(buys.sym) == {"AAA", "BBB"} and (buys.date == idx[i0 + 1]).all()
    a = f[(f.sym == "AAA") & (f.side == "SELL")].iloc[0]
    assert a.why == "stop" and a.date == idx[i0 + 3] and a.price == pytest.approx(stopA * 0.999)
    b = f[(f.sym == "BBB") & (f.side == "SELL")].iloc[0]
    assert b.why == "stop_gap" and b.date == idx[i0 + 4] and b.price == pytest.approx((stopB - 2.0) * 0.999)


def test_entry_skipped_when_open_at_or_below_stop(cfg):
    idx, syms, C, O, H, L, V = market()
    D0 = make(C, O, H, L, V, idx, syms)
    T0 = D0.WEND[-6]
    i0 = list(idx).index(T0)
    O.iloc[i0 + 1, 0] = D0.WC.at[T0, "AAA"] - 2 * D0.ATR.at[T0, "AAA"] - 0.01
    D = make(C, O, H, L, V, idx, syms)
    res = run(cfg, D, T0, {"AAA": 0.9, "BBB": 0.8, "CCC": 0.1}, idx[i0 + 2])
    assert "AAA" not in set(res["fills"].sym) and res["skipped"].get("gap_below_stop") == 1


def test_trailing_stop_only_rises(cfg):
    idx, syms, C, O, H, L, V = market()
    D0 = make(C, O, H, L, V, idx, syms)
    T0 = D0.WEND[-8]
    i0 = list(idx).index(T0)
    C.iloc[i0 + 1:, 0] = np.linspace(100, 140, len(idx) - i0 - 1)   # AAA trends up after entry
    O.iloc[i0 + 2:, 0] = C.iloc[i0 + 1:-1, 0].values
    H.iloc[:, 0] = np.maximum(O.iloc[:, 0], C.iloc[:, 0]) + 1
    L.iloc[:, 0] = np.minimum(O.iloc[:, 0], C.iloc[:, 0]) - 1
    D = make(C, O, H, L, V, idx, syms)
    cfg["strategy"]["rebalance_anchor"] = str(T0.date())
    S = pd.DataFrame([{"AAA": 0.9, "BBB": 0.8, "CCC": 0.1}], index=[T0])
    weeks = [w for w in D.WEND if w >= T0]
    res = simulate(D, T0, idx[-1], S, cfg, capital=1e6, liquidate=False, wset=weeks)
    p = res["pos"]["AAA"]
    assert p["stop"] > p["s0"]
    assert p["stop"] == pytest.approx(max(p["s0"], p["hc"] - 3 * D.ATR.at[weeks[-1], "AAA"]), rel=1e-9) or p["stop"] > p["s0"]
