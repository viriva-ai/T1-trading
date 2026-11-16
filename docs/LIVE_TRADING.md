# Live trading: read this before you switch on real money

## Why wait

1. **No proven edge.** T1 fails the deflated-Sharpe and bootstrap gates (README, Honest risks).
2. **Live fills differ from the model.** Paper assumes the open price + 0.1%. Real opening auctions, wide spreads on mid-caps and circuit limits can be worse.
3. **The data path is simpler than the sealed research.** It has no dividends and no official corporate-action ledger (see STRATEGY.md). Corporate actions on holdings need a manual check.
4. **Operations:** a missed login means no stop orders that day.

Recommended path:
1. Run paper for at least 4–8 weeks on the VM and compare it with the research paper book.
2. Then start with **₹1–2 lakh**, keeping `max_order_value` small.
3. Scale only after a quarter of clean operation.

## What live mode does

| Paper rule | Live implementation (`scripts/execute_orders.py`) |
|---|---|
| Sell pending exits at the open | 09:16: LIMIT sell at LTP × (1 − 0.5%) for the full holding (or MARKET with `market_protection`) |
| Buy at the open if open > stop | 09:16: reads the session open from Kite. If open ≤ stop, skip. Otherwise LIMIT buy at LTP × (1 + 0.5%). Qty = floor(equity × 9.9% ÷ price), capped by cash and `max_order_value`. |
| Stop when the low touches the stop | 09:25: one **SL-M** CNC sell per holding at its current stop (rounded up to the tick), with `market_protection: -1`. These are DAY orders, so they are re-placed every morning by cron. |
| Trailing stops raised on Fridays | `daily_run` recomputes them, and the next morning's SL-M uses the new level. |

The order sizes come from the **real account**: Kite holdings, positions and margins. The paper book keeps running alongside, so you can compare the two.

Limits of the live mode:
- Partial fills and rejected orders are logged, not retried. Check the Kite order book each morning.
- Stocks with a 0.01 tick: the planner rounds to 0.05. Adjust `tick_round` if Kite rejects a price.
- Holdings that the engine doesn't know about are never touched, except when a stop or exit names them.

## How to enable (all four are required)

1. Your static IP is registered in the Kite console, and `curl ifconfig.me` on the VM shows the same IP.
2. In `config/config.yaml`, set:
   ```yaml
   mode: live
   live_trading_enabled: true
   execution:
     max_order_value: 25000     # start small
   ```
3. In the crontab, add `--live` to the two `execute_orders.py` lines.
4. Put the confirmation phrase in `~/.config/t1/env` (chmod 600):
   ```
   T1_LIVE_CONFIRM=I ACCEPT REAL MONEY ORDERS
   ```

The guard (`t1/execution/guard.py`) also refuses outside 09:15–15:25 IST on weekdays, and whenever `~/t1-state/KILL` exists.

**Emergency stop:** `touch ~/t1-state/KILL`, then cancel any open orders in the Kite app. Remove the file to resume.

## Dry run before going live

Keep `mode: paper` and run the following on a trading morning after logging in:
```bash
.venv/bin/python scripts/execute_orders.py --phase open     # uses real Kite quotes, records orders only
.venv/bin/python scripts/execute_orders.py --phase stops
```
Compare `~/t1-state/paper_orders_YYYYMMDD.csv` with what you would expect from `pending_orders.csv`.
