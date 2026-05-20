"""
core/feeds/stocks_feed.py   (IBKR)

Interactive Brokers PAPER stocks feed. Replaces the previous Alpaca
implementation. Data only — the lab never places real orders; fills are
simulated per-combo on isolated PaperBrokers.

Contract: Stock(SYMBOL, "SMART", "USD"), TRADES bars, regular trading
hours only (useRTH=True) so the close-only series matches what a
US-equities strategy would actually see.

Requires a reachable IB Gateway / TWS logged into a paper account with
the API enabled. See core/feeds/ib_base.py for the connection and
pacing notes.
"""

from core.feeds.ib_base import IBFeed


class StocksFeed(IBFeed):
    what_to_show = "TRADES"
    use_rth = True

    def __init__(self, host: str, port: int, client_id: int,
                 account: str = "", market_data_type: int = 3):
        super().__init__(host, port, client_id, account, market_data_type)
        self.name = "stocks:ibkr-paper"

    def _contract(self, symbol: str):
        from ib_async import Stock
        return Stock(symbol.upper(), "SMART", "USD")