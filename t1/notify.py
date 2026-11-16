"""Optional notifications. If TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set, messages also go to Telegram;
otherwise they are only printed (cron captures stdout into the log)."""
from __future__ import annotations

import os

import requests


def send(text: str) -> None:
    print(text, flush=True)
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if tok and chat:
        try:
            requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", data=dict(chat_id=chat, text=text[:4000]), timeout=20)
        except requests.RequestException:
            print("(telegram notification failed)")
