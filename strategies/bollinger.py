"""
strategies/bollinger.py  -- VOLATILITY MEAN REVERSION

Buy when price closes below the lower Bollinger Band (a volatility-scaled
distance from the moving average), exit when it returns to the middle
band. A volatility-aware cousin of the RSI mean-reversion strategy.
"""

from statistics import mean, pstdev
from core.strategy import Strategy
from core.feed import MarketData


class Bollinger(Strategy):
    name = "bollinger_revert"
    timeframe = "5m"
    lookback = 60
    interval = 60

    def __init__(self, period=20, num_std=2.0):
        self.period = period
        self.num_std = num_std

    def decide(self, md: MarketData, position: float) -> str:
        c = md.closes
        if len(c) < self.period:
            return "hold"
        window = c[-self.period:]
        mid = mean(window)
        sd = pstdev(window)
        lower = mid - self.num_std * sd
        if md.price < lower:
            return "buy"
        if md.price >= mid:
            return "sell"
        return "hold"
