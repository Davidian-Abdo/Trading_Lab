"""
core/feeds/crypto_feed.py

Crypto data feed backed by a BINANCE DEMO (testnet) account.

The lab no longer uses public Kraken/CCXT data for crypto — every
crypto worker connects to the official Binance Spot Testnet at
https://testnet.binance.vision via CCXT's sandbox mode. The combos
still execute fills internally on isolated PaperBrokers (so per-combo
P&L attribution stays clean), but the price stream comes from the same
exchange you would later promote a winner to.

Set up once: register at https://testnet.binance.vision, generate an
API key + secret, and put them in .env.shared as BINANCE_TESTNET_KEY /
BINANCE_TESTNET_SECRET. The keys are required — the lab refuses to
start the crypto worker without them, because the whole point of this
update is that crypto runs on a real demo account, not a guess about
public market data.

Bar discipline (preserved from the prior implementation): the last
OHLCV row returned by CCXT is the STILL-FORMING bar, so using its close
as closes[-1] is intrabar lookahead. We drop it and use a separate
ticker call for the live mark price. Raises FeedUnavailable if data
is insufficient.
"""

import logging

import ccxt

from core.feed import DataFeed, MarketData, FeedUnavailable

log = logging.getLogger("feed.crypto")


class CryptoFeed(DataFeed):
    """Binance Spot Testnet feed via CCXT sandbox mode."""

    def __init__(self, api_key: str, api_secret: str,
                 exchange_id: str = "binance"):
        if not api_key or not api_secret:
            raise RuntimeError(
                "CryptoFeed requires BINANCE_TESTNET_KEY and "
                "BINANCE_TESTNET_SECRET. Generate them at "
                "https://testnet.binance.vision and put them in "
                ".env.shared."
            )
        if exchange_id != "binance":
            raise RuntimeError(
                f"Crypto feed is hardwired to Binance testnet; got "
                f"exchange_id={exchange_id!r}. Remove CRYPTO_EXCHANGE "
                f"from your env or set it to 'binance'."
            )
        self.name = "crypto:binance-testnet"
        self.ex = ccxt.binance({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })
        # Route every endpoint (public + private) to testnet.binance.vision
        self.ex.set_sandbox_mode(True)

    def check(self) -> None:
        self.ex.load_markets()
        # fetch_balance hits a PRIVATE endpoint; if keys are wrong this
        # is where we fail fast at startup instead of mid-tick.
        self.ex.fetch_balance()
        log.info("[crypto] %s connected, %d markets, demo balance OK",
                 self.name, len(self.ex.markets))

    def get_market_data(self, symbol, timeframe, lookback) -> MarketData:
        # fetch one extra, then DROP the last (still-forming) bar
        ohlcv = self.ex.fetch_ohlcv(symbol, timeframe=timeframe,
                                    limit=lookback + 1)
        if not ohlcv or len(ohlcv) < 2:
            raise FeedUnavailable(f"crypto {symbol}: insufficient OHLCV")
        complete = ohlcv[:-1]                     # exclude forming bar
        closes = [row[4] for row in complete]
        ticker = self.ex.fetch_ticker(symbol)
        price = ticker.get("last")
        if not price or price <= 0:
            raise FeedUnavailable(f"crypto {symbol}: no live price")
        return MarketData(symbol=symbol, price=float(price), closes=closes)
