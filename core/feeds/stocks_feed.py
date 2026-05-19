"""
core/feeds/stocks_feed.py   (v2.1)

Fixes review #4: previously returned price=0.0 when no bars came back
(market closed), and the worker logged equity at price 0 -> every metric
skewed. Now it raises FeedUnavailable and the worker skips the tick.
check() no longer passes vacuously.
"""

import logging

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestTradeRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from core.feed import DataFeed, MarketData, FeedUnavailable

log = logging.getLogger("feed.stocks")


def _tf(s: str) -> TimeFrame:
    table = {
        "1m": TimeFrame(1, TimeFrameUnit.Minute),
        "5m": TimeFrame(5, TimeFrameUnit.Minute),
        "15m": TimeFrame(15, TimeFrameUnit.Minute),
        "1h": TimeFrame(1, TimeFrameUnit.Hour),
        "1d": TimeFrame(1, TimeFrameUnit.Day),
    }
    return table.get(s, TimeFrame(5, TimeFrameUnit.Minute))


class StocksFeed(DataFeed):
    def __init__(self, key: str, secret: str):
        if not key or not secret:
            raise RuntimeError("Stocks feed needs ALPACA_KEY and ALPACA_SECRET")
        self.name = "stocks:alpaca"
        self.data = StockHistoricalDataClient(key, secret)

    def check(self) -> None:
        try:
            md = self.get_market_data("SPY", "5m", 5)
            log.info("[stocks] Alpaca data OK (price=%.2f)", md.price)
        except FeedUnavailable as e:
            log.warning("[stocks] startup check: market likely CLOSED "
                        "(no bars). This is normal outside US RTH: %s", e)

    def get_market_data(self, symbol, timeframe, lookback) -> MarketData:
        req = StockBarsRequest(symbol_or_symbols=symbol,
                               timeframe=_tf(timeframe), limit=lookback)
        bars = self.data.get_stock_bars(req).data.get(symbol, [])
        closes = [float(b.close) for b in bars]
        if len(closes) < 2:
            raise FeedUnavailable(f"stocks {symbol}: no bars (market closed?)")
        try:
            lt = self.data.get_stock_latest_trade(
                StockLatestTradeRequest(symbol_or_symbols=symbol))
            price = float(lt[symbol].price)
        except Exception:
            price = closes[-1]                     # fallback: last close
        if price <= 0:
            raise FeedUnavailable(f"stocks {symbol}: bad price")
        return MarketData(symbol=symbol, price=price, closes=closes)