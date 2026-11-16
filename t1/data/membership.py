"""Point-in-time Nifty 500 membership.

History (to 22 Oct 2026) is bundled in t1/resources/nifty500_membership.csv.gz (reconstructed from NSE index
press releases). After that date the strategy uses NSE's published current list, snapshotted on every refresh under
<data_dir>/nse/n500_snapshots/ so later replays see the list as it was on each decision week.
"""
from __future__ import annotations

from importlib import resources
from pathlib import Path

import pandas as pd

BUNDLED_END = pd.Timestamp("2026-10-22")


class Membership:
    def __init__(self, nse_dir: Path | None = None, bundled_end=BUNDLED_END, live_mode: str = "snapshot"):
        with resources.files("t1.resources").joinpath("nifty500_membership.csv.gz").open("rb") as fh:
            self.hist = pd.read_csv(fh, compression="gzip", parse_dates=["valid_from", "valid_to"])
        self.end = pd.Timestamp(bundled_end)
        self.nse_dir = Path(nse_dir) if nse_dir else None
        self.live_mode = live_mode            # snapshot | latest (latest = re-apply today's list to all live weeks)
        self._cache: dict = {}
        self._snaps = None

    def ever_members(self) -> set[str]:
        return set(self.hist.symbol)

    def _live_frame(self) -> pd.DataFrame | None:
        if self.nse_dir and (self.nse_dir / "n500_live.csv").exists():
            return pd.read_csv(self.nse_dir / "n500_live.csv")
        return None

    def live_symbols(self) -> set[str]:
        f = self._live_frame()
        return set(f.Symbol) if f is not None else set()

    def live_sectors(self) -> dict:
        f = self._live_frame()
        return dict(zip(f.Symbol, f.Industry)) if f is not None else {}

    def _snapshot_for(self, T: pd.Timestamp) -> list[str] | None:
        if self._snaps is None:
            d = self.nse_dir / "n500_snapshots" if self.nse_dir else None
            self._snaps = sorted(d.glob("2*.csv")) if d and d.exists() else []
        best = None
        for p in self._snaps:      # last snapshot on or before T + 3 days (a Friday decision may use Monday's refresh)
            if pd.Timestamp(p.stem) <= T + pd.Timedelta(days=3):
                best = p
        if best is None and self._snaps:
            best = self._snaps[0]
        return list(dict.fromkeys(pd.read_csv(best).Symbol)) if best is not None else None

    def members(self, T) -> list[str]:
        T = pd.Timestamp(T)
        if T in self._cache:
            return self._cache[T]
        if T > self.end:
            syms = self._snapshot_for(T) if self.live_mode == "snapshot" else None
            if syms is None:
                syms = list(dict.fromkeys(self._live_frame().Symbol)) if self._live_frame() is not None else []
        else:
            m = self.hist[(self.hist.valid_from <= T) & (self.hist.valid_to.isna() | (self.hist.valid_to > T))]
            syms = list(dict.fromkeys(m.symbol))
        self._cache[T] = syms
        return syms


def sectors(nse_dir: Path | None = None) -> dict:
    with resources.files("t1.resources").joinpath("sectors.csv").open("r") as fh:
        s = pd.read_csv(fh)
    out = dict(zip(s.symbol, s.sector))
    return out
