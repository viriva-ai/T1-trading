"""Zerodha Kite Connect: login helper + READ-ONLY client.

Credentials come only from the environment (KITE_API_KEY, KITE_API_SECRET; see .env.example). The daily access
token is written to a chmod-600 JSON file outside the repo (paths.token_file, default ~/.config/t1/kite_token.json).
Nothing here prints the secret or the token.

Zerodha expires every access token at about 06:00 IST, so a fresh login is needed each trading day.
The read-only wrapper allows market-data and account-read methods only; order methods live in
t1/execution/kite_orders.py behind the live-trading guard.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

READ_ONLY = {"profile", "quote", "ltp", "ohlc", "historical_data", "instruments", "holdings", "positions", "margins",
             "orders", "order_history", "trades", "login_url"}


class TokenExpired(RuntimeError):
    pass


def api_key() -> str:
    k = os.environ.get("KITE_API_KEY", "").strip()
    if not k:
        raise SystemExit("KITE_API_KEY is not set (see .env.example)")
    return k


def api_secret() -> str:
    s = os.environ.get("KITE_API_SECRET", "").strip()
    if not s:
        raise SystemExit("KITE_API_SECRET is not set (see .env.example)")
    return s


def login_url() -> str:
    return f"https://kite.zerodha.com/connect/login?api_key={api_key()}&v=3"


def request_token_from(text: str) -> str:
    """Accept either the bare request_token or the full redirect URL Kite sends you to after login."""
    text = text.strip()
    if "request_token=" in text:
        q = parse_qs(urlparse(text).query)
        if q.get("status", ["success"])[0] != "success":
            raise SystemExit("Kite login did not succeed (status != success)")
        return q["request_token"][0]
    return text


def save_token(path: Path, access_token: str) -> None:
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, stat.S_IRWXU)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(dict(access_token=access_token, created_at=datetime.now().astimezone().isoformat(timespec="seconds")), fh)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def load_token(path: Path) -> str:
    path = Path(path).expanduser()
    if not path.exists():
        raise TokenExpired(f"no token file at {path}; run scripts/kite_login.py")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise SystemExit(f"{path} is readable by other users (mode {oct(mode)}); run: chmod 600 {path}")
    return json.loads(path.read_text())["access_token"]


def exchange_request_token(request_token: str, token_file: Path) -> dict:
    """Swap the one-time request_token for today's access token and store it. Returns non-secret profile info."""
    from kiteconnect import KiteConnect
    k = KiteConnect(api_key=api_key())
    sess = k.generate_session(request_token, api_secret=api_secret())
    save_token(token_file, sess["access_token"])
    return dict(user_name=sess.get("user_name"), login_time=str(sess.get("login_time")))


class ReadOnlyKite:
    """Wraps KiteConnect and refuses every method outside READ_ONLY (no orders, no GTT, no writes)."""

    def __init__(self, raw):
        self._k = raw

    def __getattr__(self, name):
        if name not in READ_ONLY:
            raise PermissionError(f"read-only Kite client: '{name}' is blocked")
        return getattr(self._k, name)


def raw_client(token_file: Path):
    from kiteconnect import KiteConnect
    k = KiteConnect(api_key=api_key())
    k.set_access_token(load_token(token_file))
    return k


def connect_read_only(token_file: Path) -> ReadOnlyKite:
    k = ReadOnlyKite(raw_client(token_file))
    try:
        k.profile()
    except Exception as e:  # do not print the exception text: it can echo request details
        if type(e).__name__ in {"TokenException", "PermissionException", "InputException"}:
            raise TokenExpired("Kite session expired or invalid; log in again (scripts/kite_login.py)") from None
        raise RuntimeError(f"Kite unavailable ({type(e).__name__})") from None
    return k


def token_status(token_file: Path) -> str:
    try:
        connect_read_only(token_file)
        return "valid"
    except TokenExpired as e:
        return f"expired: {e}"
    except SystemExit as e:
        return f"not configured: {e}"
    except Exception as e:
        return f"error: {type(e).__name__}"


if __name__ == "__main__":  # pragma: no cover
    print(login_url() if len(sys.argv) < 2 else "usage: python -m t1.data.kite")
