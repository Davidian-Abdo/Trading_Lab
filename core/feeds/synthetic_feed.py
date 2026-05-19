"""
core/feeds/synthetic_feed.py   (v2.1, review suggestion: --dry-run)

Deterministic-ish synthetic price feed so you can smoke-test a worker
locally with NO network and NO credentials:

    DRY_RUN=1 ASSET=crypto python -m bots.worker

It blends a slow trend with noise and an occasional regime flip so all
strategy/behavior combos actually do something.
"""

import math
import random
import time

from core.feed import DataFeed, MarketData


class SyntheticFeed(DataFeed):
    def __init__(self, seed: int = 42):
        self.name = "synthetic:dry-run"
        self.t = 0
        self.rng = random.Random(seed)
        self.base = 100.0

    def check(self) -> None:
        pass

    def get_market_data(self, symbol, timeframe, lookback) -> MarketData:
        closes = []
        for i in range(lookback):
            k = self.t + i
            trend = 0.05 * k
            cycle = 8 * math.sin(k / 17.0)
            noise = self.rng.uniform(-1.5, 1.5)
            regime = 15 if (k // 200) % 2 else 0
            closes.append(self.base + trend + cycle + noise + regime)
        self.t += 1
        price = closes[-1] + self.rng.uniform(-0.5, 0.5)
        time.sleep(0)  # no real wait in dry-run
        return MarketData(symbol=symbol, price=price, closes=closes)