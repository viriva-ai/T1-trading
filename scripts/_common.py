"""Shared CLI plumbing: repo on sys.path, config loading, single-instance lock, IST log prefix."""
from __future__ import annotations

import argparse
import fcntl
import os
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")
os.environ.setdefault("OMP_NUM_THREADS", "4")

from t1 import config as C  # noqa: E402
from t1.timeutil import now_ist  # noqa: E402


def parser(desc: str) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=desc)
    ap.add_argument("--config", default=None, help="config YAML (default $T1_CONFIG or config/config.yaml)")
    return ap


def load_cfg(args):
    cfg = C.load(args.config)
    return cfg


def log(msg: str) -> None:
    print(f"[{now_ist():%Y-%m-%d %H:%M:%S} IST] {msg}", flush=True)


class Lock:
    """Non-blocking exclusive lock in the state dir so two cron jobs never overlap."""

    def __init__(self, cfg, name="t1"):
        self.f = open(cfg.state_dir / f".{name}.lock", "w")

    def __enter__(self):
        try:
            fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("another T1 job is running (lock held); exiting")
        return self

    def __exit__(self, *a):
        fcntl.flock(self.f, fcntl.LOCK_UN)
