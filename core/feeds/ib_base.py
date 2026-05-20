"""
core/feeds/ib_base.py

Interactive Brokers data feed base, shared by the stocks and forex
feeds. Connects to a running IB Gateway / TWS (PAPER account) via the
`ib_async` library and returns the lab's standard MarketData contract:
COMPLETE bars only in `closes`, plus a SEPARATE live `price`.

Why a shared base: stocks and forex both pull from the SAME IB Gateway;
only the IB *contract* differs (Stock vs Forex) along with whatToShow /
useRTH and how the live price is derived. Each asset worker runs in its
own thread and MUST use a distinct clientId, otherwise IB rejects the
second connection.

Operational notes (read these — IBKR is not a REST API):
  - You must run IB Gateway or TWS somewhere reachable from the workers,
    logged into a PAPER account, with the API enabled and the worker's
    clientId trusted. For headless/24-7 use, drive it with IBC.
  - IB throttles historical-data requests (~60 / 10 min per connection).
    This feed caches each (symbol, timeframe, lookback) result for
    IB_CACHE_TTL seconds so repeated polls don't trip pacing. For a
    paper *comparison* lab, identical reused data across a short window
    is fine (often desirable for cross-combo comparability). Still,
    prefer a higher POLL_SECONDS for IB assets.
"""

import asyncio
import logging
import math
import os
import threading
import time

from core.feed import DataFeed, MarketData, FeedUnavailable

log = logging.getLogger("feed.ib")

# strategy timeframe -> IB barSizeSetting
_BAR = {
    "1m": "1 min", "5m": "5 mins", "15m": "15 mins",
    "30m": "30 mins", "1h": "1 hour", "4h": "4 hours", "1d": "1 day",
}
# strategy timeframe -> IB durationStr (generous; we slice last `lookback`)
_DUR = {
    "1m": "2 D", "5m": "5 D", "15m": "10 D",
    "30m": "20 D", "1h": "30 D", "4h": "60 D", "1d": "1 Y",
}

_CACHE_TTL = float(os.environ.get("IB_CACHE_TTL", "120"))


def _ensure_event_loop():
    """`ib_async` needs an asyncio loop bound to the CURRENT thread. Each
    asset worker runs in its own thread, so create + set one if missing."""
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


class IBFeed(DataFeed):
    name = "ib:abstract"
    what_to_show = "TRADES"      # subclasses override (forex => MIDPOINT)
    use_rth = True               # subclasses override (forex => False)

    def __init__(self, host: str, port: int, client_id: int,
                 account: str = "", market_data_type: int = 3):
        try:
            from ib_async import IB  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "Interactive Brokers feed needs the 'ib_async' package "
                "(pip install ib_async / see requirements.txt)."
            ) from e
        self._IB = IB
        self.host = host
        self.port = int(port)
        self.client_id = int(client_id)
        self.account = account
        self.market_data_type = int(market_data_type)
        self.ib = None
        self._lock = threading.Lock()
        self._cache: dict[tuple, tuple[float, MarketData]] = {}

    # ---- contract factory: subclasses implement -----------------------
    def _contract(self, symbol: str):
        raise NotImplementedError

    # ---- connection ---------------------------------------------------
    def _connect(self):
        _ensure_event_loop()
        if self.ib is not None and self.ib.isConnected():
            return
        ib = self._IB()
        # readonly=True: we NEVER place orders from the lab; data only.
        ib.connect(self.host, self.port, clientId=self.client_id,
                   timeout=15, readonly=True)
        # 3 = delayed, 4 = delayed-frozen. Lets paper accounts without a
        # live market-data subscription still return prices.
        ib.reqMarketDataType(self.market_data_type)
        self.ib = ib
        log.info("[ib] connected %s:%d clientId=%d (%s)",
                 self.host, self.port, self.client_id, self.name)

    def check(self) -> None:
        try:
            self._connect()
            log.info("[ib] %s connection OK", self.name)
        except Exception as e:
            # Gateway may be down or market closed at boot; warn, don't
            # kill the worker — the per-tick path raises FeedUnavailable
            # and the worker skips ticks until it recovers.
            log.warning("[ib] startup check failed (gateway down or "
                        "market closed?): %s", e)

    # ---- data ---------------------------------------------------------
    def get_market_data(self, symbol, timeframe, lookback) -> MarketData:
        key = (symbol, timeframe, lookback)
        now = time.time()
        with self._lock:
            cached = self._cache.get(key)
            if cached and (now - cached[0]) < _CACHE_TTL:
                return cached[1]

            try:
                self._connect()
            except Exception as e:
                raise FeedUnavailable(f"ib connect failed: {e}") from e

            contract = self._contract(symbol)
            try:
                self.ib.qualifyContracts(contract)
            except Exception as e:
                raise FeedUnavailable(
                    f"ib {symbol}: cannot qualify contract: {e}") from e

            bar_size = _BAR.get(timeframe, "5 mins")
            duration = _DUR.get(timeframe, "5 D")
            try:
                bars = self.ib.reqHistoricalData(
                    contract, endDateTime="", durationStr=duration,
                    barSizeSetting=bar_size,
                    whatToShow=self.what_to_show,
                    useRTH=self.use_rth, formatDate=2)
            except Exception as e:
                raise FeedUnavailable(
                    f"ib {symbol}: historical data error: {e}") from e

            if not bars or len(bars) < 3:
                raise FeedUnavailable(
                    f"ib {symbol}: no/insufficient bars (market closed?)")

            # Drop the last (possibly still-forming) bar — mirrors the
            # crypto feed's intrabar-lookahead discipline so crypto and
            # IB stay comparable.
            complete = bars[:-1]
            closes = [float(b.close) for b in complete][-lookback:]
            if len(closes) < 2:
                raise FeedUnavailable(
                    f"ib {symbol}: not enough complete bars")

            price = self._live_price(contract, closes[-1])
            if price <= 0:
                raise FeedUnavailable(f"ib {symbol}: bad price")

            md = MarketData(symbol=symbol, price=float(price),
                            closes=closes)
            self._cache[key] = (now, md)
            return md

    # ---- live price ---------------------------------------------------
    def _live_price(self, contract, fallback_close: float) -> float:
        """Snapshot live price; fall back to the last complete close if
        the snapshot has no usable field (common on delayed paper data
        outside market hours)."""
        try:
            tickers = self.ib.reqTickers(contract)
        except Exception:
            return fallback_close
        if not tickers:
            return fallback_close
        v = self._snapshot_price(tickers[0])
        if v and v > 0 and not math.isnan(v):
            return v
        return fallback_close

    def _snapshot_price(self, t) -> float:
        for attr in ("last", "close", "marketPrice"):
            val = getattr(t, attr, None)
            if callable(val):
                try:
                    val = val()
                except Exception:
                    val = None
            if val and not math.isnan(val) and val > 0:
                return float(val)
        return 0.0