"""
core/matrix.py

The experiment definition. The lab runs the CARTESIAN PRODUCT of:

    STRATEGIES  x  BEHAVIORS  x  ASSETS

Each combo is one (asset, strategy, behavior) triple with its own
isolated PaperBroker. At runtime, that combo CONTINUOUSLY scans every
configured symbol for its asset class and trades whichever one signals
first. The combo holds AT MOST one position at a time — see worker.py
for the scan/enter/exit logic.

So the combo count per asset is STRATEGIES x BEHAVIORS = 5 x 3 = 15
(not multiplied by symbols). Adding symbols deepens the search a combo
performs each tick; it does NOT split P&L attribution across symbols.

CHANGES vs. the original lab (porting from the previous MT5 bot):
  - The `breakout` strategy has been REPLACED by two split-out indicator
    strategies — `indicator_MACD` (strategies/indicator_macd.py, EMA +
    MACD) and `indicator_RSI` (strategies/indicator_rsi.py, EMA + RSI).
    Both are 1:1 ports of the previous bot's `check_signals()`, separated
    so the matrix can credit each indicator family independently.
  - The `tp_trail` behavior now uses AtrTrailingStop (ATR-scaled
    trailing distance, ratchets up, never loosens) instead of the
    fixed-percentage TrailingStop — the previous bot's
    apply_trailing_stop() logic. The percentage TrailingStop class is
    still available in core/behavior.py if you want to A/B it.

Edit the dimensions below to grow/shrink the experiment.
"""

from typing import Iterable, List

from strategies.sma_crossover import SmaCrossover
from strategies.rsi_meanrev import RsiMeanRev
from strategies.indicator_macd import IndicatorMacd 
from strategies.indicator_rsi import IndicatorRsi
from strategies.momentum import Momentum

from core.behavior import (AtrTrailingStop, TrailingStop, HardStop,
                           TakeProfit, Composite)


# ---- factories so each combo gets a FRESH instance -----------------------
# Five strategies — one trend (sma), one mean-reversion (rsi), one
# momentum (momentum), and TWO ported multi-indicator confirmation
# strategies (indicator_MACD: EMA + MACD, indicator_RSI: EMA + RSI) that
# replaced the old breakout.
STRATEGIES = {
    "sma":       lambda: SmaCrossover(fast=10, slow=30),
    "rsi":       lambda: RsiMeanRev(period=14, oversold=30, exit_level=50),
    "indicator_MACD": lambda: IndicatorMacd(fast_ema=15, slow_ema=44,
                                          macd_fast=12, macd_slow=26,
                                          macd_signal=9),
    "indicator_RSI": lambda: IndicatorRsi(fast_ema=15, slow_ema=44,
                                          big_fast_ema=50, big_slow_ema=180,
                                           rsi_period=14),
    "momentum":  lambda: Momentum(lookback_bars=24),
}

# Three behaviors — every trade has a take-profit; the SL axis is what
# varies, so the dashboard can directly answer "does a stop-loss help on
# this asset, and if so, fixed or (ATR-)trailing?"
#
#   tp        : take-profit ONLY, no stop-loss
#   tp_sl     : take-profit + FIXED stop-loss at entry
#   tp_trail  : take-profit + ATR TRAILING stop (ported from the MT5
#               bot): distance = ATR * 1.2, ratchets up with the
#               high-water mark, never loosens. Also floors losing
#               trades (activate_pct=0) so it doubles as an initial stop.
BEHAVIORS = {
    "tp":       lambda: TakeProfit(pct=5.0),
    "tp_sl":    lambda: Composite("tp_sl",
                                  HardStop(pct=3.0), TakeProfit(pct=5.0)),
    "tp_trail": lambda: Composite("tp_trail",
                                  AtrTrailingStop(period=24,
                                                  atr_mult=1.2),
                                  TakeProfit(pct=5.0)),
}

# Asset class -> master AVAILABLE symbols list + a realistic round-trip
# cost assumption in basis points (fee + spread + slippage). These differ
# a lot by market and DELIBERATELY affect who wins; tune them honestly.
ASSETS = {
    "crypto": {
        # Kraken spot pairs (verify any new ticker on kraken.com first).
        "available_symbols": ["BTC/USDT", "ETH/USDT", "SOL/USDT",
                              "DOT/USDT", "XRP/USDT", "ADA/USDT",
                              "DOGE/USDT", "LTC/USDT"],
        "cost_bps": 16.0,   # Kraken taker fees/spread run a bit higher
    },
   
    "forex": {
        "available_symbols": ["EUR_USD", "GBP_USD", "USD_JPY",
                              "AUD_USD", "USD_CAD", "USD_CHF",
                              "NZD_USD", "EUR_GBP"],
        "cost_bps": 2.0,
    },
    "stocks": {
        "available_symbols": ["SPY", "QQQ", "AAPL", "MSFT", "GOOGL",
                              "AMZN", "NVDA", "TSLA"],
        "cost_bps": 3.0,
    },
}


def available_symbols(asset: str) -> List[str]:
    """The master list of symbols the dashboard offers as checkboxes for
    this asset. Editing this list is the only place new tickers enter the
    UI; the user then picks the subset they actually want to fetch."""
    meta = ASSETS[asset]
    if "available_symbols" in meta and meta["available_symbols"]:
        return list(meta["available_symbols"])
    # back-compat with older configs
    if "symbols" in meta and meta["symbols"]:
        return list(meta["symbols"])
    if "symbol" in meta and meta["symbol"]:
        return [meta["symbol"]]
    raise RuntimeError(f"No available_symbols configured for asset {asset}")


def asset_symbols(asset: str, override: Iterable[str] = ()) -> List[str]:
    """Legacy resolver, kept for the standalone `python -m bots.worker`
    entrypoint that doesn't have access to the dashboard inclusion table.

    Order of precedence: explicit `override` (from SYMBOLS / SYMBOL env
    vars) > the master available list. The runtime path (dashboard +
    in-process workers) instead calls ResultsDB.get_included_symbols().
    """
    syms = [s.strip() for s in override if s and s.strip()]
    if syms:
        return syms
    return available_symbols(asset)


def build_combos():
    """All (strategy_key, behavior_key) pairs. Symbol is scanned at runtime
    by the worker, NOT folded into combo identity."""
    for s in STRATEGIES:
        for b in BEHAVIORS:
            yield s, b


def combo_id(asset: str, strat: str, beh: str) -> str:
    return f"{asset}|{strat}|{beh}"