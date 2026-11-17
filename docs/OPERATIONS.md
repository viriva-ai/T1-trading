# Operations

## Daily cycle (IST)

| Time | What happens | You |
|---|---|---|
| ~06:00 | Zerodha expires yesterday's token | — |
| before 09:10 | | Log in (`scripts/kite_login.py`, see SETUP §7). Paper mode doesn't need it. |
| 09:16 | `execute_orders --phase open` | Paper: check `~/t1-state/paper_orders_*.csv` |
| 09:25 | `execute_orders --phase stops` | — |
| 18:00–19:30 | NSE publishes bhavcopy and MTO | — |
| 19:45 / 21:30 | `daily_run`: refresh, score completed weeks, replay paper book, write `pending_orders.csv` | Read the summary line in the log |
| Fri 20:30 | `weekly_signal` prints the ranking | On rebalance Fridays, `pending_orders.csv` lists Monday's buys and sells |

## Files

- `~/t1-state/summary.json`: equity, cash, positions, after-tax value if sold, index-fund comparison, stop exits today.
- `positions.csv`: each holding's qty, entry, stop, stop type, % above stop and current rank.
- `pending_orders.csv`: BUY, SELL and STOP rows for the next session.
- `ledger.csv`, `closed_trades.csv` and `equity.csv`: fills, trades, and daily equity vs the index fund.
- `scores.pkl`: frozen weekly scores. **Back it up.** It is what makes the paper book reproducible.
- `~/t1-models/M4_<refit>.json` + 5 booster `.txt` files per refit block.

## Model refits

- Refit weeks fall every 13 scored weeks: **2026-09-18, 2026-12-18, 2027-03-19**, and so on.
- On the first completed week of a new block, `daily_run` trains the block automatically. That is 5 fits, about a minute.
- The HN choice uses `~/t1-models/hn_walkforward_scores.pkl`, built once by `train_model.py --bootstrap-hn`. Without it, the last recorded choice (H1) is carried forward and a warning is printed.
- If you used `--no-auto-train`, the week is scored with the previous block, marked PROVISIONAL, and re-scored once the block exists.

### Running the paper book's sealed boosters (VM setup since 17 Nov 2026)

To match the research paper book, the VM uses the paper book's own block 2026-09-18 boosters instead of ones retrained on this repo's panel:

```bash
cp -a ~/t1-models ~/t1-models-vmtrained-YYYYMMDD           # keep the retrained block
cp <sealed>/M4_20260918* ~/t1-models/ && chmod 444 ~/t1-models/M4_20260918*
mv ~/t1-state/scores.pkl ~/t1-models-vmtrained-YYYYMMDD/   # only before paper_inception: frozen weeks are otherwise never re-scored
.venv/bin/python scripts/daily_run.py --no-refresh         # re-scores the latest week with the swapped block
```

- Nothing overwrites an existing block: `daily_run`, `backtest.py` and `train_model.py` train only missing blocks. Only `train_model.py --force` would, and the read-only files make it fail loudly.
- The **next refit (2026-12-18) is still trained automatically on the VM's own panel** the first time `daily_run` sees that week (Fri 18 Dec, 19:45). It writes new `M4_20261218*` files and leaves the sealed 09-18 block alone, but from then on the VM's scores come from a VM-trained model again. To stay on the paper book's models, copy its `M4_20261218*` block into `~/t1-models` (chmod 444) before that run.
- With the sealed boosters on the VM panel, 13 Nov 2026 gives 9/10 of the paper book's top 10 (Spearman 0.983). The remaining gap is from inputs: the paper book's Yahoo prices are dividend-adjusted and raw bhavcopy is not (e.g. AJANTPHARM ex-dividend Nov 2025 / Aug 2026, ABSLAMC Jul 2025 / Jul 2026).

## Holidays and special sessions

- No bhavcopy means no session. The calendar comes from the files themselves.
- Weekend or Muhurat special sessions show up as extra days. A week's decision is still taken on its last session.
- NSE holidays: check <https://www.nseindia.com/resources/exchange-communication-holidays>. On a holiday Monday, orders go out on the next session, and the 09:16 job does nothing because there is no open.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `missing(http 403)` for NSE days | NSE throttling. Wait, then re-run (downloads resume). Avoid running bootstrap from several machines. |
| `TOKEN_EXPIRED` / `Kite session expired` (`kite_login.py --status` exits 3) | Log in again. Tokens last until about 06:00 IST the next day. |
| `REFIT DUE` | Run `scripts/train_model.py`. |
| `PROVISIONAL` week | An input source failed or MTO is missing for the decision day. The next run re-scores it. |
| Yahoo FAILED | Cached files are used. If it persists more than 2 days, `pip install -U yfinance`. |
| Order rejected: IP not allowed | The VM's egress IP (`curl ifconfig.me`) must equal the IP registered in the Kite console. If it prints an IPv6 address, prefer IPv4 (SETUP §3). |
| Order rejected: market protection | Keep `execution.market_protection: -1` (required by Kite for API MARKET and SL-M orders). |

## Backups

The Sunday cron job tars `~/t1-state` and `~/t1-models` and keeps 8 weeks. Copy one off the VM now and then with `scp ubuntu@<ip>:t1-backup-*.tgz .`.

## Updating the code

```bash
cd ~/T1-trading && git pull && .venv/bin/pip install -r requirements.txt && .venv/bin/python -m pytest -q
```
Rule changes invalidate the sealed results. Change `strategy:` settings only through a new, pre-registered test.
