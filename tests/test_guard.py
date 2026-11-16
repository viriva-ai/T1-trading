"""Live trading must be impossible unless every guard passes; the read-only client must block order calls."""
from datetime import datetime

import pandas as pd
import pytest

from t1 import config
from t1.data.kite import ReadOnlyKite, request_token_from
from t1.execution import guard, planner
from t1.execution.kite_orders import PaperExecutor, make_executor
from t1.timeutil import IST

OPEN = datetime(2026, 11, 23, 9, 16, tzinfo=IST)


@pytest.fixture
def cfg(tmp_path):
    return config.load(None, overrides=dict(paths=dict(state_dir=str(tmp_path / "st"), token_file=str(tmp_path / "tok.json"))))


def test_defaults_are_paper(cfg):
    assert cfg["mode"] == "paper" and cfg["live_trading_enabled"] is False
    ok, why = guard.check(cfg, cli_live=True, confirm=guard.CONFIRM_PHRASE, now=OPEN)
    assert not ok and any("live_trading_enabled" in w for w in why)


def test_all_checks_needed(cfg):
    cfg["mode"], cfg["live_trading_enabled"] = "live", True
    assert guard.check(cfg, True, guard.CONFIRM_PHRASE, now=OPEN)[0]
    assert not guard.check(cfg, False, guard.CONFIRM_PHRASE, now=OPEN)[0]        # no --live
    assert not guard.check(cfg, True, "yes", now=OPEN)[0]                         # wrong phrase
    assert not guard.check(cfg, True, guard.CONFIRM_PHRASE, now=OPEN.replace(hour=16))[0]
    (cfg.state_dir / "KILL").touch()
    assert not guard.check(cfg, True, guard.CONFIRM_PHRASE, now=OPEN)[0]


def test_make_executor_falls_back_to_paper(cfg):
    ex = make_executor(cfg, cli_live=True, confirm=None, token_file=cfg.token_file, log=lambda *_: None)
    assert isinstance(ex, PaperExecutor) and ex.live is False


def test_read_only_client_blocks_writes():
    class Fake:
        def place_order(self, **k):
            raise AssertionError("must never be reached")

        def quote(self, x):
            return {"ok": 1}

    k = ReadOnlyKite(Fake())
    assert k.quote(["NSE:INFY"]) == {"ok": 1}
    for name in ("place_order", "modify_order", "cancel_order", "place_gtt", "convert_position"):
        with pytest.raises(PermissionError):
            getattr(k, name)


def test_request_token_parsing():
    assert request_token_from("http://127.0.0.1:5000/callback?request_token=abc123&action=login&status=success") == "abc123"
    assert request_token_from("abc123") == "abc123"


def test_planner_follows_paper_rules(cfg):
    pend = pd.DataFrame([dict(action="BUY", sym="AAA", stop=95.0, weight=0.099, note=""),
                         dict(action="BUY", sym="BBB", stop=95.0, weight=0.099, note=""),
                         dict(action="SELL", sym="CCC", stop=None, weight=None, note="exit at next OPEN: rank>100")])
    orders, notes = planner.plan_open(pend, {"CCC": 10}, opens={"AAA": 100.0, "BBB": 94.0}, ltps={"AAA": 100.0, "BBB": 94.0, "CCC": 50.0},
                                      equity=1_000_000, cash=500_000, cfg=cfg)
    sides = {(o.side, o.sym) for o in orders}
    assert ("SELL", "CCC") in sides and ("BUY", "AAA") in sides and ("BUY", "BBB") not in sides   # BBB opened below stop
    buy = [o for o in orders if o.sym == "AAA"][0]
    assert buy.qty * buy.price <= cfg["execution"]["max_order_value"]
    stops = planner.plan_stops({"AAA": 95.03}, {"AAA": 100})
    assert stops[0].order_type == "SL-M" and stops[0].trigger == 95.05
