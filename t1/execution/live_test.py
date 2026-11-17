"""Guarded ONE-SHARE live order test (separate from the T1 book; config live_trading_enabled stays false).

Purpose: prove the real Zerodha order path from the registered static IP with the smallest possible order.
Every guard below must pass before place_order is reachable; the default is a dry run that never sends anything.

Hard limits (constants, not config, so they cannot be raised by a YAML edit):
  qty 1, NSE EQ series, product CNC, LIMIT only, limit price within 0.5% of LTP, order value <= Rs 500,
  at most 1 BUY and 1 SELL per IST day (state file, written BEFORE sending), kill files, market hours on an
  NSE trading day (official holiday list), machine public IP == the IP registered with Kite,
  and an exact confirm string  <SIDE>-<SYMBOL>-1-<YYYYMMDD>  (today's IST date).
"""
from __future__ import annotations

import fcntl
import json
import math
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, time as dtime
from pathlib import Path

from t1.data.kite import READ_ONLY
from t1.fees import card, delivery_charges, intraday_charges
from t1.timeutil import IST, now_ist

STATIC_IP = "13.233.202.137"
MAX_QTY = 1
MAX_VALUE = 500.0
MAX_LIMIT_DEV = 0.005          # limit price within 0.5% of LTP
LIMIT_OFFSET = 0.003           # buy at LTP+0.3% / sell at LTP-0.3% (marketable, still inside the 0.5% band)
OPEN_T, CLOSE_T = dtime(9, 15), dtime(15, 30)
LAST_ENTRY_T = dtime(15, 20)   # no new BUY after 15:20 so the same-day SELL has time
TAG = "t1oneshare"
HOLIDAYS_FILE = Path(__file__).resolve().parent.parent / "resources" / "nse_holidays.json"
# the only methods this path may use; place_order / cancel_order are reachable only via LiveTester.send()
LIVE_ALLOWED = READ_ONLY | {"place_order", "cancel_order"}
SECRET_KEYS = ("token", "secret", "api_key", "password", "checksum", "enctoken")


class GuardError(RuntimeError):
    pass


class LimitedKite:
    """Wraps KiteConnect; blocks everything except read methods + place_order/cancel_order (no modify, no GTT)."""

    def __init__(self, raw, allow_orders: bool):
        self._k, self._allow = raw, allow_orders

    def __getattr__(self, name):
        if name not in (LIVE_ALLOWED if self._allow else READ_ONLY):
            raise PermissionError(f"one-share test client: '{name}' is blocked")
        return getattr(self._k, name)


def redact(x):
    if isinstance(x, dict):
        return {k: ("<redacted>" if any(s in str(k).lower() for s in SECRET_KEYS) else redact(v)) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [redact(v) for v in x]
    return x


def holidays() -> dict[str, set[str]]:
    d = json.loads(HOLIDAYS_FILE.read_text())
    return {y: set(v) for y, v in d.items() if y.isdigit()}


def trading_day_reason(now: datetime) -> str | None:
    hol = holidays()
    if str(now.year) not in hol:
        return f"no NSE holiday list for {now.year} in {HOLIDAYS_FILE.name} (fail closed)"
    if now.weekday() >= 5:
        return f"weekend ({now:%a})"
    if now.strftime("%Y-%m-%d") in hol[str(now.year)]:
        return f"NSE holiday {now:%Y-%m-%d}"
    return None


def tick_round(p: float, tick: float, side: str) -> float:
    n = p / tick
    n = math.floor(n + 1e-9) if side == "BUY" else math.ceil(n - 1e-9)
    return round(n * tick, 2)


def public_ip(urls=("https://checkip.amazonaws.com", "https://api.ipify.org")) -> str:
    seen = set()
    for u in urls:
        try:
            with urllib.request.urlopen(u, timeout=8) as r:
                seen.add(r.read().decode().strip())
        except Exception:
            seen.add("unreachable")
    return seen.pop() if len(seen) == 1 else "mismatch:" + ",".join(sorted(seen))


@dataclass
class Plan:
    side: str
    symbol: str
    qty: int
    ltp: float | None = None
    limit_price: float | None = None
    tick: float | None = None
    value: float | None = None
    checks: dict = field(default_factory=dict)      # name -> "PASS" | "FAIL: why" | "SKIP: why"
    fees: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(v == "PASS" for v in self.checks.values())

    def order_kwargs(self) -> dict:
        return dict(variety="regular", exchange="NSE", tradingsymbol=self.symbol, transaction_type=self.side,
                    quantity=self.qty, product="CNC", order_type="LIMIT", price=self.limit_price, validity="DAY", tag=TAG)


def fee_table(price: float, qty: int = 1, fee_card: str = "zerodha_2026") -> dict:
    """Round-trip charges for a 1-share test: same-day sell (Zerodha re-classes to intraday) vs sell on a later day."""
    c, v = card(fee_card), price * qty
    b_del, _ = delivery_charges(v, "BUY", c)
    s_del, _ = delivery_charges(v, "SELL", c)
    b_int, s_int = intraday_charges(v, "BUY", c), intraday_charges(v, "SELL", c)
    return dict(value=round(v, 2), buy_only_delivery=round(b_del, 4), same_day_round_trip=round(b_int + s_int, 4),
                next_day_round_trip=round(b_del + s_del, 4), dp_charge_next_day=c.dp_per_sell)


class LiveTester:
    def __init__(self, kite, state_dir: Path, log_dir: Path, kill_files: list[Path], now=None, ip_fn=public_ip,
                 sleep=time.sleep):
        self.k, self.state_dir, self.log_dir = kite, Path(state_dir), Path(log_dir)
        self.kill_files, self.ip_fn, self.sleep = kill_files, ip_fn, sleep
        self.now = now or now_ist()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.state_dir / f"oneshare_{self.now:%Y%m%d}.json"
        self.log_file = self.log_dir / f"oneshare_{self.now:%Y%m%d}.jsonl"

    # ---------- logging / state ----------
    def log(self, event: str, payload=None):
        rec = dict(ts_ist=now_ist().isoformat(timespec="seconds"), event=event, data=redact(payload))
        with open(self.log_file, "a") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")

    def state(self) -> dict:
        return json.loads(self.state_file.read_text()) if self.state_file.exists() else {}

    def _call(self, name, *a, **kw):
        try:
            r = getattr(self.k, name)(*a, **kw)
        except PermissionError:
            raise
        except Exception as e:  # never echo exception text verbatim into stdout beyond its type + message
            self.log(f"{name}:error", dict(type=type(e).__name__, msg=str(e)[:300]))
            raise
        self.log(name, r)
        return r

    # ---------- guards ----------
    def static_checks(self, side: str, symbol: str, qty: int, confirm: str | None, live: bool) -> dict:
        ch = {}
        exp = f"{side}-{symbol}-{qty}-{self.now:%Y%m%d}"
        ch["confirm_string"] = "PASS" if (confirm == exp) else (f"FAIL: need --confirm-live {exp}" if live else "SKIP: dry run")
        ch["side"] = "PASS" if side in ("BUY", "SELL") else "FAIL: side must be BUY or SELL"
        ch["qty_is_1"] = "PASS" if qty == MAX_QTY else f"FAIL: qty {qty} != {MAX_QTY}"
        ch["symbol_eq_series"] = "PASS" if (symbol.isascii() and symbol.replace("&", "").replace("-", "").isalnum()
                                            and "-" not in symbol) else f"FAIL: '{symbol}' is not a plain NSE EQ symbol"
        why = trading_day_reason(self.now)
        t = self.now.time()
        last = LAST_ENTRY_T if side == "BUY" else CLOSE_T
        if why:
            ch["market_open"] = f"FAIL: {why}"
        elif not (OPEN_T <= t <= last):
            ch["market_open"] = f"FAIL: {self.now:%H:%M} IST outside 09:15-{last:%H:%M}"
        else:
            ch["market_open"] = "PASS"
        kills = [str(p) for p in self.kill_files if Path(p).exists()]
        ch["kill_switch"] = "PASS" if not kills else f"FAIL: kill file present {kills}"
        st = self.state()
        ch["once_per_day"] = "PASS" if side not in st else f"FAIL: a {side} was already sent today ({st[side].get('order_id')})"
        if side == "SELL":
            b = st.get("BUY", {})
            ch["sell_after_filled_buy"] = "PASS" if b.get("status") == "COMPLETE" else \
                "FAIL: no COMPLETE one-share BUY by this script today (next-day sells are not allowed here: they need CDSL TPIN and cost Rs15.34 DP)"
            if b and b.get("symbol") != symbol:
                ch["sell_after_filled_buy"] = f"FAIL: today's BUY was {b.get('symbol')}, not {symbol}"
        ip = self.ip_fn()
        ch["static_ip"] = "PASS" if ip == STATIC_IP else f"FAIL: public IP {ip} != {STATIC_IP}"
        return ch

    def build_plan(self, side: str, symbol: str, qty: int = 1, confirm: str | None = None, live: bool = False) -> Plan:
        side, symbol = side.upper(), symbol.upper()
        p = Plan(side, symbol, qty)
        p.checks = self.static_checks(side, symbol, qty, confirm, live)
        if self.k is None:
            p.checks["kite_session"] = "FAIL: no valid Kite token (log in first)"
            return p
        try:
            prof = self._call("profile")
            p.checks["kite_session"] = "PASS"
            ex = prof.get("exchanges") or []
            pr = prof.get("products") or []
            p.checks["account_nse_cnc"] = "PASS" if ("NSE" in ex and "CNC" in pr) else f"FAIL: exchanges={ex} products={pr}"
        except Exception as e:
            p.checks["kite_session"] = f"FAIL: profile() {type(e).__name__}"
            return p
        inst = [i for i in self._call_instruments() if i.get("tradingsymbol") == symbol]
        if len(inst) != 1 or inst[0].get("segment") != "NSE" or inst[0].get("instrument_type") != "EQ":
            p.checks["instrument_tradable"] = f"FAIL: {symbol} not a single NSE EQ instrument ({len(inst)} found)"
            return p
        p.tick = float(inst[0].get("tick_size") or 0.05)
        p.checks["instrument_tradable"] = "PASS"
        key = f"NSE:{symbol}"
        q = self._call("quote", [key]).get(key)
        if not q:
            p.checks["quote"] = "FAIL: no quote"
            return p
        p.ltp = float(q["last_price"])
        lo, hi = float(q.get("lower_circuit_limit") or 0), float(q.get("upper_circuit_limit") or 0)
        raw = p.ltp * (1 + LIMIT_OFFSET) if side == "BUY" else p.ltp * (1 - LIMIT_OFFSET)
        p.limit_price = tick_round(raw, p.tick, side)
        p.value = round(p.limit_price * qty, 2)
        dev = abs(p.limit_price / p.ltp - 1)
        p.checks["limit_within_0.5pct"] = "PASS" if dev <= MAX_LIMIT_DEV + 1e-12 else f"FAIL: {dev:.3%} from LTP"
        p.checks["value_le_500"] = "PASS" if p.value <= MAX_VALUE else f"FAIL: Rs{p.value:.2f} > Rs{MAX_VALUE:.0f}"
        band_ok = lo > 0 and hi > 0 and lo < p.limit_price < hi
        at_circuit = (side == "BUY" and p.ltp >= hi) or (side == "SELL" and p.ltp <= lo)
        depth = (q.get("depth") or {}).get("sell" if side == "BUY" else "buy") or []
        opp = sum(int(d.get("quantity") or 0) for d in depth)
        p.checks["not_in_circuit"] = "PASS" if (band_ok and not at_circuit and opp > 0) else \
            f"FAIL: band {lo}-{hi}, ltp {p.ltp}, opposite-side depth {opp}"
        if side == "BUY":
            m = self._call("margins", "equity")
            cash = float(((m or {}).get("available") or {}).get("live_balance", (m or {}).get("net", 0)) or 0)
            need = p.value + fee_table(p.limit_price)["buy_only_delivery"] + 1
            p.checks["cash_available"] = "PASS" if cash >= need else f"FAIL: cash Rs{cash:.2f} < Rs{need:.2f}"
        else:
            pos = self._call("positions") or {}
            held = sum(int(x.get("quantity") or 0) for x in pos.get("net", [])
                       if x.get("tradingsymbol") == symbol and x.get("exchange") == "NSE" and x.get("product") == "CNC")
            p.checks["position_held"] = "PASS" if held >= qty else f"FAIL: today's CNC position in {symbol} is {held}"
        p.fees = fee_table(p.limit_price)
        return p

    def _call_instruments(self):
        cache = self.state_dir / f"instruments_NSE_{self.now:%Y%m%d}.json"
        if cache.exists():
            return json.loads(cache.read_text())
        rows = self.k.instruments("NSE")
        slim = [dict(tradingsymbol=r.get("tradingsymbol"), segment=r.get("segment"), instrument_type=r.get("instrument_type"),
                     tick_size=r.get("tick_size"), lot_size=r.get("lot_size")) for r in rows]
        cache.write_text(json.dumps(slim))
        self.log("instruments", dict(rows=len(slim)))
        return slim

    # ---------- the only path to a real order ----------
    def send(self, p: Plan, live: bool, poll_s: int = 60, cancel_unfilled: bool = True) -> dict:
        self.log("plan", asdict(p) | dict(order=p.order_kwargs(), live=live))
        if not live:
            return dict(sent=False, reason="dry run", order=p.order_kwargs())
        if not p.ok:
            raise GuardError("refusing: " + "; ".join(f"{k}={v}" for k, v in p.checks.items() if v != "PASS"))
        lockf = open(self.state_dir / ".oneshare.lock", "w")
        try:
            fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise GuardError("another one-share run holds the lock")
        try:
            st = self.state()
            if p.side in st:                                    # re-check under the lock
                raise GuardError(f"a {p.side} was already sent today")
            if any(Path(f).exists() for f in self.kill_files):
                raise GuardError("kill file appeared")
            kw = p.order_kwargs()
            if kw["quantity"] != 1 or kw["product"] != "CNC" or kw["order_type"] != "LIMIT" or kw["exchange"] != "NSE" \
                    or kw["price"] * kw["quantity"] > MAX_VALUE:
                raise GuardError("hard-limit re-check failed")
            st[p.side] = dict(symbol=p.symbol, price=p.limit_price, status="SENDING", ts=now_ist().isoformat())
            self.state_file.write_text(json.dumps(st))          # recorded BEFORE sending: a crash still counts
            oid = str(self._call("place_order", **kw))
            st[p.side]["order_id"] = oid
            self.state_file.write_text(json.dumps(st))
            status, hist = "OPEN", []
            for _ in range(max(1, poll_s // 2)):
                self.sleep(2)
                hist = self._call("order_history", oid)
                status = (hist[-1] if hist else {}).get("status", "UNKNOWN")
                if status in ("COMPLETE", "REJECTED", "CANCELLED"):
                    break
            if status not in ("COMPLETE", "REJECTED", "CANCELLED") and cancel_unfilled:
                self._call("cancel_order", variety="regular", order_id=oid)
                hist = self._call("order_history", oid)
                status = (hist[-1] if hist else {}).get("status", "UNKNOWN")
            last = hist[-1] if hist else {}
            st[p.side].update(status=status, avg_price=last.get("average_price"), filled=last.get("filled_quantity"),
                              status_message=last.get("status_message"))
            self.state_file.write_text(json.dumps(st))
            if (Path(self.state_dir) / "ONESHARE_TRADES.csv").exists() is False:
                (Path(self.state_dir) / "ONESHARE_TRADES.csv").write_text("date,side,symbol,order_id,status,avg_price\n")
            with open(Path(self.state_dir) / "ONESHARE_TRADES.csv", "a") as fh:
                fh.write(f"{self.now:%Y-%m-%d},{p.side},{p.symbol},{oid},{status},{last.get('average_price')}\n")
            return dict(sent=True, order_id=oid, status=status, avg_price=last.get("average_price"),
                        status_message=last.get("status_message"))
        finally:
            fcntl.flock(lockf, fcntl.LOCK_UN)
