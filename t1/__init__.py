"""T1: weekly NSE Nifty-500 momentum/quality ranker (M4 LightGBM) with ATR stops.

Paper trading is the default. Real orders need ALL of: live_trading_enabled: true in the
config file, --live on the command line, and the typed confirmation phrase (see
t1/execution/guard.py). Nothing in this package places an order on import.
"""
__version__ = "1.0.0"

# Hard default. The config file can only turn live trading on together with the CLI flag
# and the confirmation phrase; this constant is never flipped in code.
LIVE_TRADING_ENABLED = False
