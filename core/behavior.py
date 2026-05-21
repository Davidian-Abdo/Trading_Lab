"""
core/behavior.py

Behavior primitives composed into the matrix behaviors in
core/matrix.py:

    tp        = TakeProfit
    tp_sl     = Composite(HardStop, TakeProfit)
    tp_trail  = Composite(AtrTrailingStop, TakeProfit)   # <-- ported

TrailingStop notes (the original percentage trail, kept available):
With the default activate_pct=0.0 the trail's high-water mark starts at
the entry price, so a `pct` drop from entry triggers it BEFORE the trade
is ever in profit. Set activate_pct>0 to arm the trail only once price
is that far above entry (a pure profit-trailer with no initial stop).

AtrTrailingStop (NEW — ported from the previous MT5 bot):
The previous bot trailed the stop by an ATR-scaled distance
(`trailing_distance = ATR * 1.2`), only ever moved the stop in the
favourable direction, and additionally refused to tighten it unless it
still gave "reasonable protection". This behavior reproduces that:
distance = ATR * atr_mult, ratchets up with the high-water mark, never
loosens, with an optional activate_pct mirroring the protection guard.
The lab feed carries closes only (no OHLC), so ATR is estimated from the
close-to-close path — the same close-only simplification the strategies
already make, so it stays consistent and offline-testable.

Also adds AllOf: Composite is "exit if ANY child"; AllOf is "exit only
if ALL children".
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List


@dataclass
class PositionState:
    entry_price: float
    last_price: float
    high_water: float
    low_water: float
    bars_held: int
    # Optional recent close series for the held symbol. Volatility-aware
    # behaviors (AtrTrailingStop) read this; all others ignore it. Kept
    # last with a default so every existing PositionState(...) call and
    # every existing Behavior keeps working unchanged.
    closes: List[float] = field(default_factory=list)


class Behavior(ABC):
    name: str = "abstract"

    def reset(self) -> None:
        """Called when a NEW position opens. Override if stateful."""

    @abstractmethod
    def should_exit(self, ps: PositionState) -> bool:
        ...

    def exit_price(self, ps: PositionState) -> float | None:
        """Realistic fill price if this behavior triggered an exit on the
        current tick.

        Returning None means "fill at last_price" (the default — what the
        worker did before this method existed). Returning a number means
        "a real resting order at this level would have filled here". This
        removes the bias where a tick that GAPPED above a take-profit
        level filled at the spiked price (capturing free overshoot a real
        limit order can't get), while a tick that gapped below a hard /
        trailing stop also filled at the spiked-down price (paying the
        worst of the gap). Both now fill at their resting-order level,
        symmetrically.
        """
        return None


class NoBehavior(Behavior):
    name = "none"

    def should_exit(self, ps: PositionState) -> bool:
        return False


class TrailingStop(Behavior):
    """
    Percentage trailing stop (the lab's original trail — kept available).

    Exit if price falls `pct`% below its high-water mark since entry.
    With activate_pct=0.0 (default) it behaves like a hard stop at `pct`
    below entry until the trade is in profit. Set activate_pct>0 for a
    pure profit-trailing stop with no initial floor.
    """

    def __init__(self, pct: float = 2.0, activate_pct: float = 0.0):
        self.pct = pct
        self.activate_pct = activate_pct
        self.name = (f"trail{pct:g}" if activate_pct == 0
                     else f"trail{pct:g}a{activate_pct:g}")

    def _stop_level(self, ps: PositionState) -> float:
        return ps.high_water * (1.0 - self.pct / 100.0)

    def should_exit(self, ps: PositionState) -> bool:
        if ps.entry_price <= 0 or ps.high_water <= 0:
            return False
        gain_to_high = (ps.high_water - ps.entry_price) / ps.entry_price * 100.0
        if gain_to_high < self.activate_pct:
            return False                      # trail not armed yet
        return ps.last_price <= self._stop_level(ps)

    def exit_price(self, ps: PositionState) -> float | None:
        if ps.entry_price <= 0 or ps.high_water <= 0:
            return None
        # Fill at the stop level, not the gapped-down tick price.
        return self._stop_level(ps)


def _atr_from_closes(closes: List[float], period: int) -> float:
    """Close-to-close ATR proxy.

    True Wilder ATR needs per-bar high/low/close; the lab feed only
    carries closes (and the strategies already work close-only), so we
    approximate true range with |close[i] - close[i-1]| and average the
    last `period` of them. This keeps the ported trailing logic faithful
    in spirit (ATR-scaled distance) without changing the feed contract.
    """
    n = len(closes)
    if n < period + 1:
        return 0.0
    diffs = [abs(closes[i] - closes[i - 1])
             for i in range(n - period, n)]
    return sum(diffs) / period


class AtrTrailingStop(Behavior):
    """
    ATR-based trailing stop, ported from the previous MT5 bot's
    apply_trailing_stop().

    Behaviour reproduced:
      - trailing distance = ATR * atr_mult   (previous bot used 1.2)
      - the stop only ever moves UP (ratchets with the high-water mark),
        never loosens — exactly like the MT5 `new_sl > pos.sl` guard.
      - optional activate_pct: the stop stays disarmed until the trade
        is at least activate_pct% in profit, mirroring the previous
        bot's "(current_price - new_sl) > (...)*0.5" protection guard
        (kept off by default so tp_trail still floors losing trades).

    Stateful: keeps its own ratcheted stop level, cleared on reset()
    when a new position opens.
    """

    def __init__(self, period: int = 24, atr_mult: float = 1.2,
                 activate_pct: float = 0.0):
        self.period = period
        self.atr_mult = atr_mult
        self.activate_pct = activate_pct
        self.name = (f"atrtrail{atr_mult:g}" if activate_pct == 0
                     else f"atrtrail{atr_mult:g}a{activate_pct:g}")
        self._stop = None

    def reset(self) -> None:
        self._stop = None

    def should_exit(self, ps: PositionState) -> bool:
        closes = ps.closes or []
        if ps.entry_price <= 0 or ps.high_water <= 0:
            return False
        if len(closes) < self.period + 1:
            return False
        atr = _atr_from_closes(closes, self.period)
        if atr <= 0:
            return False

        gain_to_high = (ps.high_water - ps.entry_price) / ps.entry_price * 100.0
        if gain_to_high < self.activate_pct:
            return False                      # trail not armed yet

        candidate = ps.high_water - atr * self.atr_mult
        if self._stop is None or candidate > self._stop:
            self._stop = candidate            # ratchet up only
        return ps.last_price <= self._stop

    def exit_price(self, ps: PositionState) -> float | None:
        # Fill at the (ratcheted) stop level set in should_exit, not at
        # the gapped-down tick. Symmetric with TakeProfit.
        return self._stop


class HardStop(Behavior):
    def __init__(self, pct: float = 3.0):
        self.pct = pct
        self.name = f"stop{pct:g}"

    def _stop_level(self, ps: PositionState) -> float:
        return ps.entry_price * (1.0 - self.pct / 100.0)

    def should_exit(self, ps: PositionState) -> bool:
        if ps.entry_price <= 0:
            return False
        return ps.last_price <= self._stop_level(ps)

    def exit_price(self, ps: PositionState) -> float | None:
        if ps.entry_price <= 0:
            return None
        return self._stop_level(ps)


class TakeProfit(Behavior):
    def __init__(self, pct: float = 5.0):
        self.pct = pct
        self.name = f"tp{pct:g}"

    def _tp_level(self, ps: PositionState) -> float:
        return ps.entry_price * (1.0 + self.pct / 100.0)

    def should_exit(self, ps: PositionState) -> bool:
        if ps.entry_price <= 0:
            return False
        return ps.last_price >= self._tp_level(ps)

    def exit_price(self, ps: PositionState) -> float | None:
        if ps.entry_price <= 0:
            return None
        # Fill at TP, not the spiked-up tick. A real resting limit order
        # at TP would have filled exactly here.
        return self._tp_level(ps)


class Composite(Behavior):
    """Exit if ANY child says exit (OR). Alias kept for compatibility."""

    def __init__(self, name: str, *children: Behavior):
        self.name = name
        self.children = children

    def reset(self) -> None:
        for c in self.children:
            c.reset()

    def should_exit(self, ps: PositionState) -> bool:
        return any(c.should_exit(ps) for c in self.children)

    def exit_price(self, ps: PositionState) -> float | None:
        """Pick the price of whichever child actually triggered. If more
        than one fired (e.g. a tick that gapped past both TP and SL),
        choose the WORSE outcome — that matches what a stop+limit pair
        on a real exchange would do: the stop would arm first on the
        adverse spike. With our same-tick model we approximate by taking
        the lower (more conservative) fill for a long position."""
        triggered = [c for c in self.children if c.should_exit(ps)]
        if not triggered:
            return None
        prices = [c.exit_price(ps) for c in triggered]
        prices = [p for p in prices if p is not None]
        if not prices:
            return None
        return min(prices)


class AllOf(Behavior):
    """Exit only if ALL children say exit (AND)."""

    def __init__(self, name: str, *children: Behavior):
        self.name = name
        self.children = children

    def reset(self) -> None:
        for c in self.children:
            c.reset()

    def should_exit(self, ps: PositionState) -> bool:
        return all(c.should_exit(ps) for c in self.children)

    def exit_price(self, ps: PositionState) -> float | None:
        prices = [c.exit_price(ps) for c in self.children]
        prices = [p for p in prices if p is not None]
        if not prices:
            return None
        return min(prices)