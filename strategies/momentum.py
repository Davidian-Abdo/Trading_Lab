"""
strategies/momentum.py  -- TIME-SERIES MOMENTUM

"Has the price gone up over the last N bars?" If yes, be long; if it has
gone down, be flat. The simplest possible momentum rule, useful as a
baseline to see whether more complex strategies actually add value.
"""

from core.strategy import Strategy
from core.feed import MarketData


class Momentum(Strategy):
    name = "ts_momentum"
    timeframe = "1h"
    lookback = 60
    interval = 300

    def __init__(self, lookback_bars=24):
        self.lb = lookback_bars

    def decide(self, md: MarketData, position: float) -> str:
        c = md.closes
        if len(c) < self.lb + 1:
            return "hold"
        past = c[-(self.lb + 1)]
        if past <= 0:
            return "hold"
        ret = (c[-1] - past) / past
        if ret > 0:
            return "buy"
        return "sell"
