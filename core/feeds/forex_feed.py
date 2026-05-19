"""
core/feeds/forex_feed.py   (v2.1)

Already complete-bar-only; now standardized to the shared contract and
raises FeedUnavailable instead of returning bad data (review #4/#5).
"""

import logging

from oandapyV20 import API
import oandapyV20.endpoints.instruments as instruments
import oandapyV20.endpoints.pricing as pricing

from core.feed import DataFeed, MarketData, FeedUnavailable

log = logging.getLogger("feed.forex")

_TF = {"1m": "M1", "5m": "M5", "15m": "M15", "30m": "M30",
       "1h": "H1", "4h": "H4", "1d": "D"}


class ForexFeed(DataFeed):
    def __init__(self, token: str, account: str):
        if not token or not account:
            raise RuntimeError("Forex feed needs OANDA_TOKEN and OANDA_ACCOUNT")
        self.name = "forex:oanda-practice"
        self.acct = account
        self.api = API(access_token=token, environment="practice")

    def check(self) -> None:
        try:
            self.get_market_data("EUR_USD", "5m", 5)
            log.info("[forex] OANDA practice OK")
        except FeedUnavailable as e:
            log.warning("[forex] startup check: market data unavailable "
                        "(may be weekend/closed): %s", e)

    def get_market_data(self, symbol, timeframe, lookback) -> MarketData:
        gran = _TF.get(timeframe, "M5")
        r = instruments.InstrumentsCandles(
            instrument=symbol,
            params={"count": lookback + 1, "granularity": gran, "price": "M"},
        )
        self.api.request(r)
        candles = [c for c in r.response["candles"] if c.get("complete")]
        closes = [float(c["mid"]["c"]) for c in candles][-lookback:]
        if len(closes) < 2:
            raise FeedUnavailable(f"forex {symbol}: no complete candles")

        p = pricing.PricingInfo(self.acct, params={"instruments": symbol})
        self.api.request(p)
        prices = p.response.get("prices") or []
        if not prices:
            raise FeedUnavailable(f"forex {symbol}: no pricing")
        px = prices[0]
        price = (float(px["closeoutBid"]) + float(px["closeoutAsk"])) / 2.0
        if price <= 0:
            raise FeedUnavailable(f"forex {symbol}: bad price")
        return MarketData(symbol=symbol, price=price, closes=closes)