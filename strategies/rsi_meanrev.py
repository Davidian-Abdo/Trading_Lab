"""
strategies/rsi_meanrev.py  -- MEAN REVERSION

Buy when RSI says "oversold", exit when it returns to neutral. Mean
reversion tends to do the OPPOSITE of trend strategies: it likes choppy,
range-bound markets and gets hurt in strong trends. Great contrast to
have in the lab.
"""

from core.strategy import Strategy
from core.feed import MarketData


def _rsi(values, n=14):
    if len(values) < n + 1:
        return None
    gains, losses = 0.0, 0.0
    for i in range(-n, 0):
        diff = values[i] - values[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    if losses == 0:
        return 100.0
    rs = (gains / n) / (losses / n)
    return 100.0 - (100.0 / (1.0 + rs))


class RsiMeanRev(Strategy):
    name = "rsi_meanrev"
    timeframe = "5m"
    lookback = 50
    interval = 60

    def __init__(self, period=14, oversold=30, exit_level=50):
        self.period = period
        self.oversold = oversold
        self.exit_level = exit_level

    def decide(self, md: MarketData, position: float) -> str:
        rsi = _rsi(md.closes, self.period)
        if rsi is None:
            return "hold"
        if rsi < self.oversold:
            return "buy"
        if rsi > self.exit_level:
            return "sell"
        return "hold"
