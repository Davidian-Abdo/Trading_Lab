"""
core/paper.py   (v2.1)

Adds to_state()/load_state() so a worker restart RESUMES each combo
exactly where it left off (review #3). Previously a restart silently
rebuilt every broker at start_equity, putting a fake cliff in every
equity curve and corrupting Sharpe / drawdown / "best per kind".

v2.2: position_state() now optionally carries the recent close series
so volatility-aware behaviors (AtrTrailingStop, ported from the MT5
bot) can size their trailing distance off ATR. Defaulted, so existing
callers keep working.
"""

from core.behavior import PositionState


class PaperBroker:
    def __init__(self, start_equity: float, alloc_fraction: float,
                 cost_bps: float):
        self.start_equity = start_equity
        self.cash = start_equity
        self.alloc = alloc_fraction
        self.cost = cost_bps / 10000.0

        self.qty = 0.0
        self.entry = 0.0
        self.high = 0.0
        self.low = 0.0
        self.bars = 0
        self.last = 0.0
        self.realized_trades = 0

    # ---- persistence (review #3) ---------------------------------------
    def to_state(self) -> dict:
        return {"cash": self.cash, "qty": self.qty, "entry": self.entry,
                "high": self.high, "low": self.low, "bars": self.bars,
                "last": self.last, "rt": self.realized_trades}

    def load_state(self, s: dict) -> None:
        self.cash = s["cash"]; self.qty = s["qty"]; self.entry = s["entry"]
        self.high = s["high"]; self.low = s["low"]; self.bars = s["bars"]
        self.last = s["last"]; self.realized_trades = s["rt"]

    # ---- state ---------------------------------------------------------
    @property
    def in_position(self) -> bool:
        return self.qty > 0.0

    def equity(self, price: float) -> float:
        return self.cash + self.qty * price

    def mark(self, price: float) -> None:
        self.last = price
        if self.in_position:
            self.high = max(self.high, price)
            self.low = min(self.low, price)
            self.bars += 1

    def position_state(self, closes=None) -> PositionState:
        """Snapshot for behaviors. `closes` (recent close series of the
        held symbol) is optional and only consumed by volatility-aware
        behaviors such as AtrTrailingStop; all others ignore it."""
        return PositionState(self.entry, self.last, self.high,
                             self.low, self.bars,
                             list(closes) if closes else [])

    # ---- execution -----------------------------------------------------
    def open_long(self, price: float) -> None:
        if self.in_position or price <= 0:
            return
        spend = self.cash * self.alloc
        qty = spend / price
        fee = spend * self.cost
        self.cash -= (spend + fee)
        self.qty = qty
        self.entry = price
        self.high = price
        self.low = price
        self.bars = 0

    def close(self, price: float) -> None:
        if not self.in_position or price <= 0:
            return
        proceeds = self.qty * price
        fee = proceeds * self.cost
        self.cash += (proceeds - fee)
        self.qty = 0.0
        self.entry = 0.0
        self.high = 0.0
        self.low = 0.0
        self.bars = 0
        self.realized_trades += 1
