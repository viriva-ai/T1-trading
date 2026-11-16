import pytest

from t1.tax import Ledger, cg_rates, fy_of


def test_rates_change_on_budget_2024():
    assert cg_rates("2024-07-22") == (0.15, 0.10)
    assert cg_rates("2024-07-23") == (0.20, 0.125)
    assert cg_rates("2016-05-01") == (0.15, 0.0)


def test_fy():
    assert fy_of("2026-03-31") == 2025 and fy_of("2026-04-01") == 2026


def test_stcg_ltcg_exemption_and_cess():
    L = Ledger()
    L.realise("2025-01-01", "2025-06-01", 100_000, 150_000)    # ST +50k @20%
    L.realise("2023-01-01", "2025-06-01", 100_000, 300_000)    # LT +200k, 75k taxable @12.5%
    assert L.settle(2025) == pytest.approx((50_000 * 0.20 + 75_000 * 0.125) * 1.04)


def test_st_loss_offsets_lt_gain_and_carries_forward():
    L = Ledger()
    L.realise("2025-05-01", "2025-06-01", 100_000, 60_000)     # ST -40k
    L.realise("2023-01-01", "2025-06-01", 100_000, 130_000)    # LT +30k (below exemption anyway)
    assert L.settle(2025) == 0.0
    assert sum(c[1] for c in L.cf) == pytest.approx(10_000)   # 40k - 30k carried
    L.realise("2026-05-01", "2026-06-01", 100_000, 120_000)    # ST +20k next FY, 10k left after carry
    assert L.settle(2026) == pytest.approx(10_000 * 0.20 * 1.04)


def test_interest_taxed_at_slab():
    L = Ledger(slab=0.30)
    L.interest = 10_000
    assert L.settle(2025) == pytest.approx(3_000 * 1.04)
