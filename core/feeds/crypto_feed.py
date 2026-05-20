"""
core/feeds/crypto_feed.py   (Kraken)

Crypto data feed backed by Kraken spot, via CCXT.

Why no testnet: Kraken has no public spot *testnet/sandbox* (only a
separate Futures demo). Since this lab only needs DATA — every combo
simulates fills on its own isolated PaperBroker for clean P&L
attribution — we use Kraken's LIVE public market data. OHLCV and ticker
are public endpoints, so API key/secret are OPTIONAL here and only
serve to raise private rate limits. Leave them blank for data-only use.
No real orders are ever sent.

Bar discipline (preserved): CCXT's last OHLCV row is the STILL-FORMING
bar; using its close as closes[-1] is intrabar lookahead. We drop it
and take a separate ticker call for the live mark. Raises
FeedUnavailable when data is insufficient so the worker skips the tick
instead of marking equity at a bad price.
"""

import logging

import ccxt

from core.feed import DataFeed, MarketData, FeedUnavailable

log = logging.getLogger("feed.crypto")


class CryptoFeed(DataFeed):
    """Kraken spot feed via CCXT (public data; keys optional)."""

    def __init__(self, api_key: str = "", api_secret: str = "",
                 exchange_id: str = "kraken"):
        if exchange_id != "kraken":
            raise RuntimeError(
                f"Crypto feed is now hardwired to Kraken; got "
                f"exchange_id={exchange_id!r}. Set CRYPTO_EXCHANGE=kraken "
                f"or remove it from your env."
            )
        self.name = "crypto:kraken"
        opts = {"enableRateLimit": True}
        if api_key and api_secret:
            opts["apiKey"] = api_key
            opts["secret"] = api_secret
        self.ex = ccxt.kraken(opts)

    def check(self) -> None:
        self.ex.load_markets()
        log.info("[crypto] %s connected, %d markets",
                 self.name, len(self.ex.markets))

    def get_market_data(self, symbol, timeframe, lookback) -> MarketData:
        try:
            ohlcv = self.ex.fetch_ohlcv(symbol, timeframe=timeframe,
                                        limit=lookback + 1)
        except Exception as e:
            raise FeedUnavailable(
                f"crypto {symbol}: ohlcv error: {e}") from e
        if not ohlcv or len(ohlcv) < 2:
            raise FeedUnavailable(f"crypto {symbol}: insufficient OHLCV")
        complete = ohlcv[:-1]                      # drop forming bar
        closes = [row[4] for row in complete]
        try:
            ticker = self.ex.fetch_ticker(symbol)
            price = ticker.get("last")
        except Exception as e:
            raise FeedUnavailable(
                f"crypto {symbol}: ticker error: {e}") from e
        if not price or price <= 0:
            raise FeedUnavailable(f"crypto {symbol}: no live price")
        return MarketData(symbol=symbol, price=float(price),
                          closes=closes)