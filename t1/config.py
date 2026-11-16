"""Configuration: YAML file + environment variables. Secrets are never stored in the config.

Lookup order for the config file: --config argument, $T1_CONFIG, ./config/config.yaml,
./config/config.example.yaml (the example is safe: paper mode, live trading off).
"""
from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: dict = {
    "mode": "paper",
    "live_trading_enabled": False,
    "capital": 1_000_000,
    "paper_inception": "2026-11-20",
    "paths": {
        "data_dir": "~/t1-data",
        "state_dir": "~/t1-state",
        "models_dir": "~/t1-models",
        "log_dir": "~/t1-logs",
        "token_file": "~/.config/t1/kite_token.json",
    },
    "data": {
        "history_start": "2010-01-01",
        "mto_start": "2013-01-01",
        "request_pause_s": 0.4,
    },
    "universe": {"min_history_days": 260, "min_traded_value_20d": 5.0e7, "min_price": 20.0},
    "strategy": {
        "top_k": 10,
        "invested_fraction": 0.99,       # weight per name = invested_fraction / top_k = 9.9%
        "rebalance_every_weeks": 2,
        "rebalance_anchor": "2024-10-25",
        "rank_exit_above": 100,
        "atr_weeks": 10,
        "initial_stop_atr": 2.0,
        "trailing_stop_atr": 3.0,
        "slippage": 0.001,
        "cash_buffer": 0.01,
    },
    "model": {
        "train_start": "2011-01-01",
        "score_start": "2015-01-01",
        "refit_every_weeks": 13,
        "purge_weeks": 5,
        "hn_min_weeks": 13,
        "seeds": [7, 11, 23, 42, 101],
        "n_jobs": 4,
        "hp_override": None,            # e.g. "H1" to skip the HN choice
    },
    "fees": {"card": "zerodha_2026", "intraday_reclass": True},
    "cash": {"earn_liquid_fund_interest": True, "liquid_fund_scheme_code": 119568},
    "tax": {"slab_rate": 0.30, "cess": 0.04, "ltcg_exemption": 125000.0, "apply": True},
    "benchmark": {"dividend_yield": 0.013, "index_fund_ter": 0.002},
    "execution": {
        "entry_order_type": "LIMIT",     # LIMIT (price = open-check LTP * (1 + limit_buffer)) or MARKET
        "limit_buffer": 0.005,
        "market_protection": -1,         # required by Kite for MARKET / SL-M API orders
        "stop_mode": "slm_daily",        # slm_daily | eod_check
        "max_order_value": 200000,
        "max_orders_per_run": 25,
        "tag": "t1",
    },
}


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def find_config(path: str | None = None) -> Path | None:
    for cand in [path, os.environ.get("T1_CONFIG"), REPO_ROOT / "config/config.yaml", REPO_ROOT / "config/config.example.yaml"]:
        if cand and Path(cand).expanduser().exists():
            return Path(cand).expanduser()
    return None


class Config(dict):
    """dict with attribute-style path helpers."""

    def path(self, key: str) -> Path:
        p = Path(os.path.expandvars(self["paths"][key])).expanduser()
        if key != "token_file":
            p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def data_dir(self) -> Path:
        return self.path("data_dir")

    @property
    def state_dir(self) -> Path:
        return self.path("state_dir")

    @property
    def models_dir(self) -> Path:
        return self.path("models_dir")

    @property
    def log_dir(self) -> Path:
        return self.path("log_dir")

    @property
    def token_file(self) -> Path:
        return self.path("token_file")


def load(path: str | None = None, overrides: dict | None = None) -> Config:
    cfg = copy.deepcopy(DEFAULTS)
    f = find_config(path)
    if f is not None:
        cfg = _merge(cfg, yaml.safe_load(f.read_text()) or {})
        cfg["_config_file"] = str(f)
    for env, key in (("T1_DATA_DIR", "data_dir"), ("T1_STATE_DIR", "state_dir"), ("T1_MODELS_DIR", "models_dir"),
                     ("T1_LOG_DIR", "log_dir"), ("T1_TOKEN_FILE", "token_file")):
        if os.environ.get(env):
            cfg["paths"][key] = os.environ[env]
    if overrides:
        cfg = _merge(cfg, overrides)
    return Config(cfg)
