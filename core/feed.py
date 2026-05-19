"""
core/feed.py

Data-only interface. v2.1 changes (review items #4, #5):
  - feeds raise FeedUnavailable when there is no usable data this tick
    (market closed, empty bars, price <= 0). The worker SKIPS that tick
    for the affected combos instead of marking equity at price 0.
  - contract is now explicit: `closes` are COMPLETE bars only (oldest
    first), and `price` is a SEPARATE live price. Every feed obeys this
    identically so crypto/forex/stocks stay comparable.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List


class FeedUnavailable(Exception):
    """No usable market data this tick (e.g. market closed). Not fatal."""


@dataclass
class MarketData:
    symbol: str
    price: float          # live price (NOT the last close)
    closes: List[float]   # COMPLETE bars only, oldest first, newest last


class DataFeed(ABC):
    name: str = "abstract"

    @abstractmethod
    def get_market_data(self, symbol: str, timeframe: str,
                        lookback: int) -> MarketData:
        """Return complete-bar closes + a live price, or raise FeedUnavailable."""

    @abstractmethod
    def check(self) -> None:
        """Startup connectivity check. May warn (not raise) if market closed."""