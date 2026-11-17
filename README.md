# T1 — weekly Nifty 500 ranker with ATR stops (NSE, Zerodha Kite)

**Paper trading is the default.** Real orders need four separate switches (see [docs/LIVE_TRADING.md](docs/LIVE_TRADING.md)).
Past results don't guarantee future returns. This is research code, not investment advice.

## What T1 does

Every Friday after the close, a model scores every stock in the point-in-time **Nifty 500** and buys the **top 10 at 9.9% each**:

- **Model (M4):** five LightGBM models (different random seeds) predict each stock's **4-week forward-return rank**. The score is the average of their percentile ranks.
- **Inputs (37):** 23 cross-sectional ranks (1–52-week momentum, distance from the 52-week high, volatility, ATR, volume, liquidity, gaps, RSI, moving-average distance, delivery %, sector strength), one 52-week breakout flag, and 13 market readings (Nifty 500 trend, India VIX, breadth, USD/INR, Brent, S&P 500, US 10-year yield, gold).
- **Retraining:** every 13 weeks on an expanding window from 2011. The newest 5 weeks are left out of training, so the label can't leak.
- **Rebalancing:** every 2 weeks. A holding is sold at the next open if its rank falls below 100 or it leaves the index. Free slots are filled from the top of the ranking.
- **Stops:** the initial stop is 2 × ATR(10 weeks) below the entry week's close. The trailing stop is the highest weekly close minus 3 × ATR, raised every Friday. Stops are checked on every session's low. If a stock opens below its stop, it is sold at the open.
- **Costs:** Zerodha CNC charges (STT, exchange, SEBI, IPFT, GST, stamp, DP) plus 0.1% slippage per side, about 0.44% per round trip. STCG and LTCG are worked out FIFO and settled at the end of each financial year. Idle cash earns liquid-fund returns, taxed at slab.

It is **not intraday**: it uses end-of-day NSE data plus a daily stop.

## Results (sealed research reports; ₹10 lakh; after all fees and tax)

| Period | After-tax XIRR | Max drawdown | Nifty 500 TRI after tax |
|---|---|---|---|
| Development 2015-01-01 → 2024-10-22 | **19.56%** | −31.25% | 13.53% |
| **Holdout (unseen) 2024-10-23 → 2026-10-22** | **11.37%** | **−18.58%** | −1.60% |
| Holdout re-run on Zerodha Kite prices | **11.37%** (11.365%) | **−18.58%** | — |
| 4-year descriptive run, 15 Nov 2022 → 13 Nov 2026 (Zerodha fees + liquid-fund interest) | 23.95% CAGR | −23.24% (−24.13% after accrued tax) | 10.93% CAGR |

Notes:
- Development and holdout use the sealed conservative cost envelope: Zerodha charges plus 0.1% brokerage capped at ₹20, and no interest on cash. Dividends are credited.
- In the Kite re-run, 349 of the 351 holdout fills are identical. Kite has no data for 146 delisted or BE-series names, so those still came from NSE files.
- In the 4-year run, the worst week was −6.58% (14 Feb 2025). The worst rolling 13 weeks lost 15.0%.
- Source reports are in the private research workspace (t1_core `RERUN_REPORT.md`, `kite_xcheck_20261116/REPORT.md` and `backtest_4y_20261115/REPORT.md`). Each was pre-registered and sha256-sealed before it ran.

### Honest risks

- **Gap-downs can blow through stops.** A stock that opens far below its stop is sold at the open. The worst real stop exit in the 4-year run lost 22%, against a typical −8% to −15%.
- **The edge is not statistically proven.** T1 fails the deflated-Sharpe gate (DSR 0.0034, needing ≥ 0.95) and the block bootstrap (p = 0.0695). After about 12,015 strategy trials in this research, part of the result may be luck.
- **The holdout was weaker than development** (11.4% against 19.6%). In the 4-year run, 2025 returned only +7.5%, against +45.7% in 2023. Expect years that trail the index.
- **Small accounts do worse.** Fixed DP charges and whole-share rounding hurt: at ₹1 lakh, development XIRR was 17.1% instead of 19.6%.
- **The data in this repo is simpler than the sealed research data.** The repo builds prices from raw NSE bhavcopy and detects splits and bonuses from price jumps. It does not use the official corporate-action ledger, does not credit dividends, and has no price-band rules. Re-running the holdout this way gave **9.28% after tax and a −17.22% max drawdown**, lower than the sealed 11.37%. The development window went the other way: 27.0% against the sealed 19.6%. That is too high, mainly because the repo engine lacks the upper-circuit rule. Treat the repo backtest as a check that the machinery works, not as evidence. See [docs/STRATEGY.md](docs/STRATEGY.md#reproducing-the-numbers).
- **Operational risk:** missed logins, API changes, NSE file delays, VM outages, and fills worse than the 0.1% slippage assumed.

## Quick start (paper)

```bash
git clone https://github.com/viriva-ai/T1-trading.git && cd T1-trading
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp config/config.example.yaml config/config.yaml           # paper mode, live off
.venv/bin/python -m pytest -q                               # 22 tests
.venv/bin/python scripts/bootstrap_data.py                  # one-time NSE history download (1-3 h)
.venv/bin/python scripts/train_model.py                     # current M4 block (~1 min)
.venv/bin/python scripts/daily_run.py                       # paper book + next-session orders
.venv/bin/python scripts/weekly_signal.py --no-refresh      # this week's ranking
```

Full VM setup, covering Oracle Cloud in Mumbai or Hyderabad or AWS Lightsail Mumbai with a static IP, the Kite login and cron: **[docs/SETUP.md](docs/SETUP.md)**.

## Setup guide (summary; every step in full in [docs/SETUP.md](docs/SETUP.md))

1. **VM.** Oracle Cloud Always Free, home region **Mumbai or Hyderabad**, Ubuntu 24.04 on `VM.Standard.A1.Flex` (2–4 OCPU, 12–24 GB). Don't use the 1 GB micro shape.
2. **Static IP.** Networking → IP Management → *Reserve public IP*. On the instance's primary VNIC, set the public IP to *None*, then attach the reserved one. Open only port 22. Check with `curl ifconfig.me` (on dual-stack VMs, prefer IPv4 first: SETUP §3).
3. **SSH and the OS.**
   - `ssh -i key ubuntu@<IP>`
   - `sudo timedatectl set-timezone Asia/Kolkata`
   - `sudo apt install python3-venv git build-essential libgomp1`
4. **Clone, venv, install.** Use the quick-start commands above. `pytest` should say *22 passed*.
5. **Secrets.**
   - `mkdir -p ~/.config/t1 && cp .env.example ~/.config/t1/env && chmod 600 ~/.config/t1/env`
   - Fill in `KITE_API_KEY` and `KITE_API_SECRET`. They never go in the repo.
6. **Kite console** (developers.kite.trade → your app):
   - Set the redirect URL to `http://127.0.0.1:5000/callback`.
   - **Register the reserved static IP.** From **1 April 2026**, SEBI's algo rules make Zerodha reject API orders from unregistered IPs. Market data works from any IP.
7. **Daily login.** Tokens expire at about **06:00 IST** every day.
   - Run `scripts/kite_login.py` and open the printed URL.
   - After 2FA the browser goes to `127.0.0.1:5000/callback?request_token=…`. That page fails to load, which is expected. Paste the full address back.
   - Or use `ssh -L 5000:127.0.0.1:5000` with `--listen` to capture it automatically.
   - Paper mode needs no login.
8. **Cron in IST.** Paste `deploy/crontab.example` (`CRON_TZ=Asia/Kolkata`):
   - 08:50 login check
   - 09:16 open orders
   - 09:25 stop orders
   - 19:45 and 21:30 daily run
   - Friday 20:30 ranking
   - Sunday backup
9. **Backtest.** `scripts/backtest.py --start 2024-10-23 --end 2026-10-22` (walk-forward; output in `~/t1-state/backtests/`).
10. **Paper.** This is the default. `config.yaml` starts the book on **20 Nov 2026** with Zerodha fees and liquid-fund interest on idle cash. Check `~/t1-state/summary.json` and `positions.csv` daily.
11. **Going live.** Wait first: the strategy failed its significance gates, and paper should run for weeks before any real money. Live needs all four switches: `mode: live`, `live_trading_enabled: true`, `--live`, and the confirmation phrase. It also needs the registered IP and a small `max_order_value`. See [docs/LIVE_TRADING.md](docs/LIVE_TRADING.md).
12. **Monitoring.**
    - Logs are in `~/t1-logs/<job>_YYYYMMDD.log`. Watch for FAILED, PROVISIONAL and REFIT DUE.
    - Telegram alerts are optional (`TELEGRAM_*` in the env file).
    - `touch ~/t1-state/KILL` blocks all live orders.
    - More in [docs/OPERATIONS.md](docs/OPERATIONS.md).

## Layout

```
t1/                      the package
  config.py              YAML + env config (secrets only from env)
  data/nse.py            NSE bhavcopy (UDiFF + legacy), MTO delivery, Nifty 500 list, symbol changes
  data/external.py       Yahoo (index, VIX, FX, S&P, gold), FRED (Brent, US10y), mfapi (liquid fund NAV)
  data/panel.py          adjusted OHLCV panel, delivery %, index/TRI, liquid NAV
  data/membership.py     point-in-time Nifty 500 (bundled history + live snapshots)
  data/kite.py           Kite login helper + read-only client
  features.py            the 37 M4 inputs + 4-week label
  model.py               M4 walk-forward training, HN hyper-parameter choice, scoring
  engine.py              portfolio rules: entries, rank exits, stops, fees, tax (backtest == paper)
  fees.py / tax.py       Zerodha charges, Indian capital-gains ledger
  paper.py / pipeline.py paper book + orchestration
  execution/             guard (live switches), planner (orders from engine), executors (paper / Kite)
  resources/             Nifty 500 membership history, sector map, HN history (small, public data)
scripts/                 bootstrap_data, daily_run, weekly_signal, train_model, backtest,
                         execute_orders, kite_login, live_mark
config/config.example.yaml
deploy/                  run.sh (cron wrapper), crontab.example
docs/                    SETUP, STRATEGY, OPERATIONS, LIVE_TRADING
tests/                   fees, tax, stops, feature parity, guard, secret scan
```

Data, models, state, logs and tokens live **outside** the repo (`~/t1-data`, `~/t1-models`, `~/t1-state`, `~/t1-logs`, `~/.config/t1/`).

## Security

- Never commit `config/config.yaml`, `.env`, or token files. `.gitignore` covers them.
- `KITE_API_KEY` and `KITE_API_SECRET` are read from the environment only. The daily access token is stored with chmod 600 and never printed.
- The read-only Kite client blocks every order method. Order code is in `t1/execution/` and is only reachable through the guard.
- If your API secret was ever pasted into a chat, an email or a commit, regenerate it in the Kite developer console.
