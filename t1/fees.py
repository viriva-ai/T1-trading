"""Zerodha equity charges (CNC delivery, plus the intraday re-class used when a stop exits on the entry day).

Card "zerodha_2026" = zerodha.com/charges as fetched 10 Nov 2026 (the card the paper book uses from 20 Nov 2026):
  brokerage 0 (delivery); STT 0.1% buy and sell; NSE transaction 0.00307%; SEBI Rs10/crore; IPFT Rs0.01/crore;
  GST 18% on (brokerage + transaction + SEBI + IPFT); stamp duty 0.015% on buys; DP Rs15.34 per scrip per sell day.
Card "legacy_cnc" = the same without IPFT (the card the backtests and the paper book used before 20 Nov 2026).
Intraday re-class: brokerage min(0.03%, Rs20) per order, STT 0.025% on the sell only, stamp 0.003% on the buy, no DP.
Charges can change: check zerodha.com/charges and update CARDS if they do.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FeeCard:
    stt: float = 0.001
    txn: float = 0.0000307
    sebi: float = 0.000001
    ipft: float = 0.000000001
    stamp_buy: float = 0.00015
    gst: float = 0.18
    dp_per_sell: float = 15.34
    # intraday re-class
    intra_stt_sell: float = 0.00025
    intra_stamp_buy: float = 0.00003
    intra_broker_pct: float = 0.0003
    intra_broker_cap: float = 20.0


CARDS = {
    "zerodha_2026": FeeCard(),
    "legacy_cnc": FeeCard(ipft=0.0),
}


def card(name: str) -> FeeCard:
    if name not in CARDS:
        raise KeyError(f"unknown fee card {name!r}; choose one of {sorted(CARDS)}")
    return CARDS[name]


def delivery_charges(value: float, side: str, c: FeeCard) -> tuple[float, float]:
    """Return (total charges incl. STT, STT part) for one CNC order of `value` rupees."""
    if value < 0 or side not in ("BUY", "SELL"):
        raise ValueError("value must be >= 0 and side BUY or SELL")
    stt = value * c.stt
    exch = value * (c.txn + c.sebi + c.ipft)
    stamp = value * c.stamp_buy if side == "BUY" else 0.0
    dp = c.dp_per_sell if side == "SELL" else 0.0
    return stt + stamp + exch * (1 + c.gst) + dp, stt


def intraday_charges(value: float, side: str, c: FeeCard) -> float:
    """Charges for one leg of a same-day buy->sell (intraday re-class), excluding nothing (STT included)."""
    brk = min(value * c.intra_broker_pct, c.intra_broker_cap)
    exch = value * (c.txn + c.sebi + c.ipft)
    stt = value * c.intra_stt_sell if side == "SELL" else 0.0
    stamp = value * c.intra_stamp_buy if side == "BUY" else 0.0
    return brk + exch + stt + stamp + (exch + brk) * c.gst


def round_trip_pct(value: float = 100_000.0, slippage: float = 0.001, c: FeeCard | None = None) -> float:
    c = c or CARDS["zerodha_2026"]
    b, _ = delivery_charges(value, "BUY", c)
    s, _ = delivery_charges(value, "SELL", c)
    return 100 * ((b + s) / value + 2 * slippage)
