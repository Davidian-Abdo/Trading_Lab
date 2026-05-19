"""
strategies/breakout.py  -- BREAKOUT

Donchian-style channel breakout. Go long when price makes a new N-bar
high (something is breaking out), exit when it falls back to the N-bar
low. A different flavour of trend-capture than the SMA crossover.
"""

from core.strategy import Strategy
from core.feed import MarketData


class Breakout(Strategy):
    name = "donchian_breakout"
    timeframe = "15m"
    lookback = 60
    interval = 120

    def __init__(self, channel=20):
        self.channel = channel

    def decide(self, md: MarketData, position: float) -> str:
        c = md.closes
        if len(c) < self.channel + 1:
            return "hold"
        window = c[-(self.channel + 1):-1]  # exclude the current bar
        highest = max(window)
        lowest = min(window)
        if md.price > highest:
            return "buy"
        if md.price < lowest:
            return "sell"
        return "hold"
