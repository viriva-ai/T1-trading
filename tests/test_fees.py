import pytest

from t1 import fees as F


def test_zerodha_2026_delivery_buy_and_sell():
    c = F.card("zerodha_2026")
    buy, stt_b = F.delivery_charges(100_000, "BUY", c)
    sell, stt_s = F.delivery_charges(100_000, "SELL", c)
    # STT 100 + stamp 15 + (txn 3.07 + SEBI 0.10 + IPFT 0.0001) x 1.18
    assert stt_b == pytest.approx(100.0)
    assert buy == pytest.approx(100 + 15 + 3.1701 * 1.18, abs=1e-6)
    # STT 100 + exchange x 1.18 + DP 15.34, no stamp on sells
    assert sell == pytest.approx(100 + 3.1701 * 1.18 + 15.34, abs=1e-6)
    assert stt_s == pytest.approx(100.0)


def test_legacy_card_matches_engine_paper_formula():
    c = F.card("legacy_cnc")
    val = 123_456.78
    TXN, SEBI, STAMP, GST, DP = 0.0000307, 0.000001, 0.00015, 0.18, 15.34
    exp_buy = val * 0.001 + val * STAMP + val * (TXN + SEBI) + GST * val * (TXN + SEBI)
    exp_sell = val * 0.001 + val * (TXN + SEBI) + GST * val * (TXN + SEBI) + DP
    assert F.delivery_charges(val, "BUY", c)[0] == pytest.approx(exp_buy, rel=1e-12)
    assert F.delivery_charges(val, "SELL", c)[0] == pytest.approx(exp_sell, rel=1e-12)


def test_intraday_reclass_brokerage_cap_and_stt():
    c = F.card("zerodha_2026")
    big = F.intraday_charges(1_000_000, "SELL", c)        # brokerage capped at Rs20
    exch = 1_000_000 * (c.txn + c.sebi + c.ipft)
    assert big == pytest.approx(20 + 250 + exch + (exch + 20) * 0.18, abs=1e-6)   # GST on brokerage + exchange
    small = F.intraday_charges(10_000, "BUY", c)          # 0.03% = Rs3 < Rs20 cap; stamp 0.003%
    ex = 10_000 * (c.txn + c.sebi + c.ipft)
    assert small == pytest.approx(3 + 0.3 + ex + (ex + 3) * 0.18, abs=1e-6)


def test_round_trip_is_about_0_44_percent():
    assert 0.43 < F.round_trip_pct(100_000) < 0.46


def test_bad_side_rejected():
    with pytest.raises(ValueError):
        F.delivery_charges(100, "HOLD", F.card("zerodha_2026"))
