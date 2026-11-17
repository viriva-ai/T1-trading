# T1 strategy reference

## Rules (sealed, used identically in backtest, paper and live)

| Item | Rule |
|---|---|
| Universe | Point-in-time Nifty 500 member. Also needs ≥ 260 sessions of history, a 20-day median traded value ≥ ₹5 crore, and a price ≥ ₹20. |
| Decision time | The last session of each week (normally Friday) after the close. A week is decided only once it is complete. |
| Model | M4: 5 LightGBM regressors on `y4_rk`, the within-week rank of (close 4 weeks later ÷ next open − 1). |
| Inputs | 37 (see `t1/features.py`): 23 cross-sectional ranks, `w3_signal`, 13 market features. |
| Training | Expanding window from 2011. Refit every 13 scored weeks from the first week of 2015. Training cutoff = refit week − 5 weeks. |
| Hyper-parameters | HN. At each refit, choose H0–H4 by the best mean weekly rank-IC over earlier out-of-sample weeks. H1 (`num_leaves=15, min_child_samples=500`) has been chosen at every refit since mid-2018. |
| Ensemble | The chosen H with seed 7, plus seeds 11, 23, 42 and 101. Score = mean of the 5 per-week percentile ranks. |
| Portfolio | Top 10 by score at 9.9% each (99% invested). |
| Rebalance | Every 2 weeks, counted from week 2024-10-25. Rebalance closes: … 6 Nov, **20 Nov**, 4 Dec, 18 Dec 2026 … |
| Exit | On a rebalance close, a holding ranked > 100 among eligible names, or no longer eligible, is sold at the next open. |
| Entry | At the next open + 0.1%. Skipped if the open is at or below the initial stop. Quantity = floor(equity × 9.9% ÷ price), capped by cash. |
| Initial stop | Decision-week close − 2 × ATR(10 weeks), using a weekly true-range average. |
| Trailing stop | max(stop, highest weekly close since entry − 3 × ATR(10w)), raised every Friday, including non-rebalance weeks. |
| Stop check | Every session, including the entry session. If the open is ≤ stop, sell at the open; else if the low is ≤ stop, sell at the stop. Both less 0.1% slippage. |
| Costs | Zerodha CNC card (`t1/fees.py`). If a stop hits on the entry day, the trade is re-classed as intraday, with speculative income taxed at slab. |
| Tax | FIFO STCG/LTCG at the rates in force on the sale date, the ₹1.25 lakh LTCG exemption, 8-year loss carry-forward and 4% cess, settled at FY end. Liquid-fund interest is taxed at slab (30%). |

## Data sources

| Data | Source | Notes |
|---|---|---|
| Daily OHLCV | NSE bhavcopy (`nsearchives.nseindia.com`), EQ/BE/BZ series | UDiFF format from 8 Jul 2024, legacy `cmDDMONYYYYbhav.csv` before that. Splits and bonuses are back-adjusted with the price-jump heuristic (standard ratios). Ticker renames are stitched using NSE `symbolchange.csv`. |
| Delivery % | NSE MTO files (from 2013) | |
| Membership | Bundled history to 22 Oct 2026, then NSE `ind_nifty500list.csv` snapshotted on each refresh | Forward point-in-time. |
| Index, VIX, FX, S&P 500, gold | Yahoo Finance | `^CRSLDX` is the Nifty 500 index. |
| Brent, US 10y | FRED | Published with a 1–3 day lag, which the features handle with as-of-before-T values. |
| Liquid fund | mfapi.in scheme 119568 (ABSL Liquid Fund Direct Growth) | |
| Live prices / orders | Zerodha Kite Connect | Kite daily candles are back-adjusted, including some dividends. NSE files stay the primary record. |

## Reproducing the numbers

The README's sealed figures (development 19.56%, holdout 11.37%) come from the research engine, which runs on an **official data bundle**. That bundle includes:
- raw NSE bars with NSE's own corporate-action ledger (exact split, bonus, rights and demerger factors)
- cash dividends credited at ex-date + 30 days
- official price bands (no buy at the upper circuit, sells deferred while locked at the lower circuit)
- amalgamation handling

Building that bundle needs NSE endpoints that block automated clients, so it is not part of this repo.

This repo's engine is the paper engine (`lgb_paper/engine_paper.py`, T1 branch), ported verbatim. It runs on a simpler panel built from raw bhavcopy with a split heuristic, and it **does not credit dividends**, which costs roughly 1% a year. Verification done before the first commit (16 Nov 2026):

| Check | Result |
|---|---|
| Feature code vs original `features_paper.py` (synthetic fixture, 37 inputs × 6 weeks) | identical (`tests/test_features_parity.py`) |
| Scores for week 2026-11-13 on the live paper pipeline's panel | **identical to the paper book: all 469 names, max \|Δ\| = 0.0**. Rank 1 CASTROLIND 0.9996 … rank 10 AJANTPHARM 0.9693. |
| Retraining block 2026-09-18 with `t1/model.py` | **bit-identical boosters** (max \|Δ prediction\| 0.0 on all 5 members, 239,966 training rows, cutoff 2026-08-14) |
| Engine vs `engine_paper.simulate` (holdout, same scores and panel) | **354/354 fills identical, equity Δ ₹0.00**, after-tax XIRR 13.657% on that panel |
| Scores for 2026-11-13 from a panel rebuilt from raw bhavcopy (this repo's own data path) | **same top-10 names** (order differs slightly), Spearman 0.988 across 469 names. 21 names differ by > 0.05 (e.g. CUB, TRENT, VEDL, where Yahoo and raw-bhav adjustments differ). |
| Fresh VM (Lightsail, 17 Nov 2026): raw-bhavcopy panel, block 2026-09-18 **retrained on that panel** (H1, 253,181 rows vs 239,966) | 13 Nov top-10 shares **6 of 10** names with the paper book (Spearman 0.91). The sealed boosters on the same VM features give **9 of 10** (Spearman 0.983; ABSLAMC in for AJANTPHARM). Most of the gap comes from retraining on the different panel, not from the feature code. HN on that panel re-picks H1 for every block since 2018-12 (40/48 blocks agree with `hn_history.json`, all mismatches 2015–2018). |
| Holdout backtest end-to-end on the repo's raw-bhavcopy data, walk-forward M4 | **9.28% after tax, max DD −17.22%**, 220 trades. The sealed figure is 11.37% / −18.58%. The gap comes from the data (dividends, corporate actions, bands), not the rules. |
| Development backtest end-to-end (2015-01-01 → 2024-10-22), same data path | **27.0% after tax, max DD −29.96%**, 793 trades, DSR 0.065. The sealed figure is 19.56% / −31.25%. **This is higher than the sealed figure and should not be trusted.** |

### Why the repo's backtest can be off in either direction

- The repo engine has **no price-band rule**. It buys at the open even when a stock opens locked at its upper circuit, which you can't do in practice. Momentum names often do this, so this inflates returns, especially in 2015–2021.
  - With the sealed holdout M4 scores on the paper pipeline's Yahoo-adjusted panel, the paper engine earned 13.66%, against 11.37% for the sealed research engine.
- **No dividends** are credited, which lowers returns by about 1% a year.
- **Splits and bonuses are detected from price jumps.** A missed split looks like a crash and a stop exit. A genuine crash that resembles a split ratio can be adjusted away by mistake.
- The models are retrained on this panel, so the scores differ slightly from the sealed ones.
- The repo may overstate returns, as in development, or understate them, as in the holdout. **Use the sealed figures in the README for decisions.** Use the repo backtest to check that the machinery works, not as fresh evidence.

Weeks 30 Oct and 6 Nov 2026 scored again today differ from the paper book's frozen scores (max |Δ| 0.08–0.12). That is expected: the paper froze them with the inputs available on decision day (for example, Brent was last published on 27 Oct). This is why scores are frozen per week and never recomputed once final.

## Statistical caveats

- About 12,015 strategy variants were tried in this research programme. At that count T1's deflated Sharpe ratio is 0.0034, against a 0.95 bar, and the block-bootstrap p-value is 0.0695 against 0.05. **T1 failed both significance gates.** The sealed development and holdout results are positive, but they don't establish an edge that will persist.
- The holdout (Oct 2024 – Oct 2026) earned 11.4% after tax while the Nifty 500 TRI lost 1.6%. That is one two-year sample.
- Two alternatives came close: Q16 (T1 plus 16 Qlib features), and volatility-adjusted momentum, which was weaker on the holdout. Neither replaced T1.
