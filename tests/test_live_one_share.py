"""The one-share live test must refuse unless every guard passes; place_order is reached only on the all-PASS path."""
from datetime import datetime

import pytest

from t1.execution import live_test as LT
from t1.timeutil import IST

OPEN = datetime(2026, 11, 18, 10, 0, tzinfo=IST)      # Wednesday, NSE trading day
OK_IP = lambda: LT.STATIC_IP                           # noqa: E731


class FakeKite:
    def __init__(self, ltp=150.0, cash=10_000.0, lo=135.0, hi=165.0, held=0, fill="COMPLETE"):
        self.ltp, self.cash, self.lo, self.hi, self.held, self.fill = ltp, cash, lo, hi, held, fill
        self.placed, self.cancelled = [], []

    def profile(self):
        return dict(user_id="AB1234", exchanges=["NSE", "BSE"], products=["CNC", "MIS"])

    def instruments(self, exch):
        return [dict(tradingsymbol="IOC", segment="NSE", instrument_type="EQ", tick_size=0.01, lot_size=1),
                dict(tradingsymbol="MRF", segment="NSE", instrument_type="EQ", tick_size=0.05, lot_size=1)]

    def quote(self, keys):
        return {k: dict(last_price=self.ltp, lower_circuit_limit=self.lo, upper_circuit_limit=self.hi,
                        depth=dict(buy=[dict(quantity=100)], sell=[dict(quantity=100)])) for k in keys}

    def margins(self, seg):
        return dict(net=self.cash, available=dict(live_balance=self.cash))

    def positions(self):
        return dict(net=[dict(tradingsymbol="IOC", exchange="NSE", product="CNC", quantity=self.held)])

    def place_order(self, **kw):
        self.placed.append(kw)
        return "250001"

    def order_history(self, oid):
        return [dict(status="OPEN"), dict(status=self.fill, average_price=self.ltp, filled_quantity=1)]

    def cancel_order(self, **kw):
        self.cancelled.append(kw)


def make_tester(tmp_path, kite=None, now=OPEN, ip=OK_IP):
    kite = kite or FakeKite()
    return LT.LiveTester(LT.LimitedKite(kite, allow_orders=True), tmp_path / "st", tmp_path / "logs",
                         [tmp_path / "st" / "KILL"], now=now, ip_fn=ip, sleep=lambda s: None), kite


def confirm(side="BUY", sym="IOC", d=OPEN):
    return f"{side}-{sym}-1-{d:%Y%m%d}"


def test_happy_path_sends_exactly_one_limit_cnc(tmp_path):
    t, k = make_tester(tmp_path)
    p = t.build_plan("BUY", "IOC", 1, confirm(), live=True)
    assert p.ok, p.checks
    r = t.send(p, live=True)
    assert r["status"] == "COMPLETE" and len(k.placed) == 1
    o = k.placed[0]
    assert (o["quantity"], o["product"], o["order_type"], o["exchange"]) == (1, "CNC", "LIMIT", "NSE")
    assert abs(o["price"] / 150 - 1) <= 0.005 and o["price"] <= 500


def test_dry_run_never_sends(tmp_path):
    t, k = make_tester(tmp_path)
    p = t.build_plan("BUY", "IOC", 1, None, live=False)
    assert t.send(p, live=False)["sent"] is False and k.placed == []


def _refused(t, k, p):
    assert not p.ok
    with pytest.raises(LT.GuardError):
        t.send(p, live=True)
    assert k.placed == []


def test_wrong_confirm_string(tmp_path):
    t, k = make_tester(tmp_path)
    for bad in (None, "BUY-IOC-1-20261117", "BUY-IOC-2-20261118", "SELL-IOC-1-20261118", "yes"):
        _refused(t, k, t.build_plan("BUY", "IOC", 1, bad, live=True))


def test_outside_market_hours_weekend_and_holiday(tmp_path):
    for now in (OPEN.replace(hour=9, minute=10), OPEN.replace(hour=15, minute=25), datetime(2026, 11, 21, 10, 0, tzinfo=IST),
                datetime(2026, 11, 24, 10, 0, tzinfo=IST), datetime(2027, 1, 5, 10, 0, tzinfo=IST)):
        t, k = make_tester(tmp_path, now=now)
        p = t.build_plan("BUY", "IOC", 1, confirm(d=now), live=True)
        assert p.checks["market_open"].startswith("FAIL")
        _refused(t, k, p)


def test_qty_above_one(tmp_path):
    t, k = make_tester(tmp_path)
    p = t.build_plan("BUY", "IOC", 2, "BUY-IOC-2-20261118", live=True)
    assert p.checks["qty_is_1"].startswith("FAIL")
    _refused(t, k, p)


def test_value_above_500(tmp_path):
    t, k = make_tester(tmp_path, kite=FakeKite(ltp=900.0, lo=800, hi=1000))
    p = t.build_plan("BUY", "IOC", 1, confirm(), live=True)
    assert p.checks["value_le_500"].startswith("FAIL")
    _refused(t, k, p)


def test_wrong_ip(tmp_path):
    t, k = make_tester(tmp_path, ip=lambda: "1.2.3.4")
    p = t.build_plan("BUY", "IOC", 1, confirm(), live=True)
    assert p.checks["static_ip"].startswith("FAIL")
    _refused(t, k, p)


def test_kill_switch(tmp_path):
    t, k = make_tester(tmp_path)
    (tmp_path / "st" / "KILL").touch()
    _refused(t, k, t.build_plan("BUY", "IOC", 1, confirm(), live=True))


def test_second_order_same_day(tmp_path):
    t, k = make_tester(tmp_path)
    t.send(t.build_plan("BUY", "IOC", 1, confirm(), live=True), live=True)
    t2, _ = make_tester(tmp_path, kite=k)
    p = t2.build_plan("BUY", "IOC", 1, confirm(), live=True)
    assert p.checks["once_per_day"].startswith("FAIL")
    with pytest.raises(LT.GuardError):
        t2.send(p, live=True)
    assert len(k.placed) == 1


def test_circuit_cash_and_instrument_checks(tmp_path):
    t, k = make_tester(tmp_path, kite=FakeKite(ltp=165.0))                 # at upper circuit
    _refused(t, k, t.build_plan("BUY", "IOC", 1, confirm(), live=True))
    t, k = make_tester(tmp_path, kite=FakeKite(cash=50.0))
    _refused(t, k, t.build_plan("BUY", "IOC", 1, confirm(), live=True))
    t, k = make_tester(tmp_path)
    _refused(t, k, t.build_plan("BUY", "IOC-BE", 1, "BUY-IOC-BE-1-20261118", live=True))
    _refused(t, k, t.build_plan("BUY", "NOSUCH", 1, "BUY-NOSUCH-1-20261118", live=True))


def test_sell_needs_filled_buy_and_unfilled_buy_is_cancelled(tmp_path):
    t, k = make_tester(tmp_path, kite=FakeKite(held=1))
    _refused(t, k, t.build_plan("SELL", "IOC", 1, confirm("SELL"), live=True))      # no BUY today
    t, k = make_tester(tmp_path, kite=FakeKite(held=0, fill="OPEN"))
    r = t.send(t.build_plan("BUY", "IOC", 1, confirm(), live=True), live=True, poll_s=4)
    assert k.cancelled and r["status"] != "COMPLETE" and len(k.placed) == 1
    p = t.build_plan("SELL", "IOC", 1, confirm("SELL"), live=True)                  # BUY not COMPLETE
    assert p.checks["sell_after_filled_buy"].startswith("FAIL")
    with pytest.raises(LT.GuardError):
        t.send(p, live=True)
    assert len(k.placed) == 1


def test_buy_then_sell_ok(tmp_path):
    t, k = make_tester(tmp_path, kite=FakeKite(held=1))
    t.send(t.build_plan("BUY", "IOC", 1, confirm(), live=True), live=True)
    p = t.build_plan("SELL", "IOC", 1, confirm("SELL"), live=True)
    assert p.ok, p.checks
    t.send(p, live=True)
    assert [o["transaction_type"] for o in k.placed] == ["BUY", "SELL"] and k.placed[1]["price"] < 150


def test_client_blocks_modify_and_gtt():
    lk = LT.LimitedKite(FakeKite(), allow_orders=True)
    for name in ("modify_order", "place_gtt", "modify_gtt", "convert_position", "exit_order"):
        with pytest.raises(PermissionError):
            getattr(lk, name)
    ro = LT.LimitedKite(FakeKite(), allow_orders=False)
    with pytest.raises(PermissionError):
        ro.place_order


def test_logs_redact_secrets(tmp_path):
    t, _ = make_tester(tmp_path)
    t.log("x", dict(access_token="SHOULD_NOT_APPEAR", api_secret="NOPE", ok=1))
    txt = t.log_file.read_text()
    assert "SHOULD_NOT_APPEAR" not in txt and "NOPE" not in txt and '"ok": 1' in txt


def test_fee_table_same_day_cheaper():
    f = LT.fee_table(150.0)
    assert f["same_day_round_trip"] < 1 and f["next_day_round_trip"] > 15
