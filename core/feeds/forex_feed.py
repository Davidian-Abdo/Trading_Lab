"""
core/feeds/forex_feed.py   (IBKR)

Interactive Brokers PAPER forex feed. Replaces the previous OANDA
implementation. Data only — fills are simulated per-combo.

Contract: Forex("EURUSD") etc. The lab's symbol convention is
`EUR_USD`; we strip the separator to IB's `EURUSD`. FX uses MIDPOINT
bars with useRTH=False (FX trades ~24x5, no "regular session"), and the
live mark is the bid/ask midpoint to match the MIDPOINT bar series.

Requires a reachable IB Gateway / TWS on a paper account with the API
enabled. See core/feeds/ib_base.py for connection / pacing notes.
"""

import math

from core.feeds.ib_base import IBFeed


class ForexFeed(IBFeed):
    what_to_show = "MIDPOINT"
    use_rth = False

    def __init__(self, host: str, port: int, client_id: int,
                 account: str = "", market_data_type: int = 3):
        super().__init__(host, port, client_id, account, market_data_type)
        self.name = "forex:ibkr-paper"

    def _contract(self, symbol: str):
        from ib_async import Forex
        pair = symbol.replace("_", "").replace("/", "").upper()
        return Forex(pair)

    def _snapshot_price(self, t) -> float:
        # Prefer the true midpoint to match the MIDPOINT bar series.
        mid = getattr(t, "midpoint", None)
        if callable(mid):
            try:
                m = mid()
                if m and not math.isnan(m) and m > 0:
                    return float(m)
            except Exception:
                pass
        bid = getattr(t, "bid", None)
        ask = getattr(t, "ask", None)
        try:
            if (bid and ask and not math.isnan(bid)
                    and not math.isnan(ask) and bid > 0 and ask > 0):
                return (float(bid) + float(ask)) / 2.0
        except TypeError:
            pass
        return super()._snapshot_price(t)