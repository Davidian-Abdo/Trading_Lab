"""
strategies/sma_crossover.py  -- TREND FOLLOWING

Classic moving-average crossover. Go long when the fast average is above
the slow average; exit when it crosses back below. Trend strategies make
money in trends and bleed in choppy markets, that's the whole point of
testing it against different asset classes.
"""

from core.strategy import Strategy
from core.feed import MarketData


def _sma(values, n):
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


class SmaCrossover(Strategy):
    name = "sma_crossover"
    timeframe = "5m"
    lookback = 60
    interval = 60

    def __init__(self, fast=10, slow=30):
        self.fast = fast
        self.slow = slow

    def decide(self, md: MarketData, position: float) -> str:
        fast = _sma(md.closes, self.fast)
        slow = _sma(md.closes, self.slow)
        if fast is None or slow is None:
            return "hold"
        if fast > slow:
            return "buy"
        return "sell"
