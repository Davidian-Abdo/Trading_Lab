"""
core/strategy.py

A strategy is a pure decision function. It gets recent market data plus
whether we are currently in a position, and returns one of:

    "buy"  -> we want to be LONG  (enter if flat, otherwise stay)
    "sell" -> we want to be FLAT  (exit if long, otherwise do nothing)
    "hold" -> no change

Strategies must be SELF-CONTAINED and STATELESS where possible: they should
derive everything from the `closes` they are given, not from hidden globals.
This makes them easy to reason about and impossible to accidentally feed
future data (a classic lookahead bug).
"""

from abc import ABC, abstractmethod
from core.feed import MarketData


class Strategy(ABC):
    #: shown in logs / dashboard, must be unique per bot
    name: str = "abstract"
    #: how many recent candles this strategy needs to make a decision
    lookback: int = 50
    #: candle size, e.g. "1m", "5m", "15m", "1h" (adapter maps it)
    timeframe: str = "5m"
    #: seconds between decisions (poll interval)
    interval: int = 60

    @abstractmethod
    def decide(self, md: MarketData, position: float) -> str:
        """Return 'buy', 'sell', or 'hold'. position>0 means currently long."""
