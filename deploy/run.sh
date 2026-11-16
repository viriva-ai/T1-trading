#!/usr/bin/env bash
# Cron wrapper: loads secrets from ~/.config/t1/env (chmod 600), activates the venv, runs one script, logs to ~/t1-logs.
# Usage: deploy/run.sh daily_run.py [args...]
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${T1_ENV_FILE:-$HOME/.config/t1/env}"
if [ -f "$ENV_FILE" ]; then set -a; . "$ENV_FILE"; set +a; fi
export TZ=Asia/Kolkata
LOG_DIR="${T1_LOG_DIR:-$HOME/t1-logs}"; mkdir -p "$LOG_DIR"
NAME="$(basename "$1" .py)"
LOG="$LOG_DIR/${NAME}_$(date +%Y%m%d).log"
cd "$REPO"
SCRIPT="$1"; shift
rc=0
"$REPO/.venv/bin/python" "scripts/$SCRIPT" "$@" >> "$LOG" 2>&1 || rc=$?
if [ "$rc" -ne 0 ]; then echo "[$(date '+%F %T') IST] $NAME FAILED (exit $rc)" >> "$LOG"; fi
exit "$rc"
