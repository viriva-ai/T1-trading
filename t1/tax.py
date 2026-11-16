"""Indian capital-gains ledger for a resident individual (listed equity, STT paid), FIFO lots, settled at FY end.

- STCG (held <= 12 months): 15% before 23 Jul 2024, 20% from 23 Jul 2024 (by sale date).
- LTCG (held > 12 months): 10% (from FY2018-19) before 23 Jul 2024, 12.5% after; exemption Rs1 lakh/FY,
  Rs1.25 lakh from FY2024-25.
- Losses: current-year LT losses only against LT gains; ST losses against ST then LT gains; unabsorbed losses carried
  forward 8 FYs (LT carry only against LT gains).
- Liquid-fund interest on idle cash and intraday (speculative) income are taxed at the slab rate.
- Health & education cess 4% on top. Surcharge, rebates and the basic exemption are ignored (assumed used elsewhere).
`rates="current"` applies today's 20% / 12.5% / Rs1.25 lakh to every year (the paper engine's convention).
STT is not deductible: the caller passes the basis and consideration net of STT.
This is a model for backtesting, not tax advice.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

import pandas as pd

BUDGET_2024 = date(2024, 7, 23)


def fy_of(d) -> int:
    d = pd.Timestamp(d)
    return d.year if d.month >= 4 else d.year - 1


def cg_rates(sold, mode: str = "historical") -> tuple[float, float]:
    """(stcg_rate, ltcg_rate) for a sale date."""
    if mode == "current":
        return 0.20, 0.125
    d = pd.Timestamp(sold).date()
    if d < date(2018, 4, 1):
        return 0.15, 0.0
    return (0.15, 0.10) if d < BUDGET_2024 else (0.20, 0.125)


class Ledger:
    def __init__(self, slab: float = 0.30, cess: float = 0.04, rates: str = "historical", ltcg_exemption: float | None = None):
        self.slab, self.cess, self.rates, self.exempt_override = slab, cess, rates, ltcg_exemption
        self.g = defaultdict(float)      # (head, rate) -> net gain this FY ; head in ST / LT
        self.interest = 0.0
        self.spec = 0.0
        self.cf: list[list] = []          # [fy, amount, 'ST'|'LT']
        self.spec_cf: list[list] = []     # [fy, amount]
        self.log: list[dict] = []

    def realise(self, buy_d, sell_d, basis: float, consideration: float) -> None:
        held = (pd.Timestamp(sell_d) - pd.Timestamp(buy_d)).days
        st, lt = cg_rates(sell_d, self.rates)
        if held > 365:
            self.g[("LT", lt)] += consideration - basis
        else:
            self.g[("ST", st)] += consideration - basis

    def _exempt(self, fy: int) -> float:
        if self.exempt_override is not None:
            return self.exempt_override
        if self.rates == "current":
            return 125000.0
        return 125000.0 if fy >= 2024 else 100000.0

    def settle(self, fy: int) -> float:
        gains = {k: max(v, 0.0) for k, v in self.g.items()}
        loss = {h: -sum(min(v, 0.0) for k, v in self.g.items() if k[0] == h) for h in ("ST", "LT")}

        def absorb(amount: float, heads) -> float:
            for head in heads:
                keys = [k for k in gains if k[0] == head]
                tot = sum(gains[k] for k in keys)
                take = min(amount, tot)
                if tot > 0:
                    for k in keys:
                        gains[k] *= 1 - take / tot
                amount -= take
            return amount

        loss["LT"] = absorb(loss["LT"], ["LT"])
        loss["ST"] = absorb(loss["ST"], ["ST", "LT"])
        self.cf = [c for c in self.cf if fy - c[0] <= 8]
        for c in self.cf:
            c[1] = absorb(c[1], ["LT"] if c[2] == "LT" else ["ST", "LT"])
        self.cf = [c for c in self.cf if c[1] > 1e-9]
        for h in ("ST", "LT"):
            if loss[h] > 1e-9:
                self.cf.append([fy, loss[h], h])
        lt_taxable = sum(v for k, v in gains.items() if k[0] == "LT" and k[1] > 0)
        factor = max(0.0, lt_taxable - self._exempt(fy)) / lt_taxable if lt_taxable > 0 else 0.0
        cg = sum(v * k[1] * (factor if k[0] == "LT" else 1.0) for k, v in gains.items())
        # speculative (intraday re-class) income: losses only against speculative gains, 4-FY carry
        spec = self.spec
        self.spec_cf = [c for c in self.spec_cf if fy - c[0] <= 4]
        if spec > 0:
            for c in self.spec_cf:
                u = min(c[1], spec)
                c[1] -= u
                spec -= u
        elif spec < 0:
            self.spec_cf.append([fy, -spec])
            spec = 0.0
        tax = (cg + self.slab * (self.interest + spec)) * (1 + self.cess)
        self.log.append(dict(fy=fy, **{f"{h}_{r}": round(v, 2) for (h, r), v in self.g.items()}, interest=round(self.interest, 2),
                             speculative=round(self.spec, 2), tax=round(tax, 2), carry_forward=round(sum(c[1] for c in self.cf), 2)))
        self.g = defaultdict(float)
        self.interest = 0.0
        self.spec = 0.0
        return tax
