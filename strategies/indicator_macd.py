"""
strategies/indicator_macd.py  -- EMA / MACD CONFIRMATION

Ported from the previous MetaTrader5 bot's `check_signals()`. The
original bot combined EMA + MACD + RSI signals in one decision; the lab
splits that into two stand-alone strategies (this one and
strategies/indicator_rsi.py) so the matrix can credit each indicator
family independently.

The original lived in a live MT5 bot that pulled OHLC via `mt5.copy_rates`
and computed indicators with the `ta` library. The lab's feed contract
is close-only (see core/feed.MarketData), and lab strategies are pure,
stateless decision functions derived solely from `md.closes` (no hidden
globals, no `ta` dependency, offline-testable). So the indicators are
re-implemented here in plain Python, keeping the SAME decision logic:

  - EMA fast (15) vs EMA slow (44)            -> regime / trend filter
  - MACD(12,26,9) line vs signal, last 3 bars -> momentum cross logic

The condition sets are a faithful translation of the previous bot's
`buy_conditions` / `sell_conditions` lists.

NOTE ON TIMEFRAME / MULTI-TIMEFRAME:
The previous bot ran this on M3 and additionally required a 2-of-N
agreement across M3/M5/M6/M10/M12. The lab gives each strategy ONE
timeframe through a single shared feed (so crypto/forex/stocks stay
comparable, and not every feed supports a 3m granularity). The
multi-timeframe vote is therefore out of scope for this single-feed lab
and is intentionally dropped; the single-timeframe signal logic is
preserved 1:1. Timeframe is set to "5m" because it is the one value all
three lab feeds (Kraken spot / IBKR paper forex / IBKR paper stocks)
map cleanly.
"""

from core.strategy import Strategy
from core.feed import MarketData


# ---- pure-python indicator helpers (close-only, lookahead-free) ----------
def _ema(values, window):
    """EMA aligned 1:1 with `values`; None until `window` samples seen.
    Seeded with the SMA of the first `window` values for stability."""
    n = len(values)
    if n < window:
        return [None] * n
    out = [None] * (window - 1)
    sma = sum(values[:window]) / window
    out.append(sma)
    k = 2.0 / (window + 1.0)
    prev = sma
    for v in values[window:]:
        prev = v * k + prev * (1.0 - k)
        out.append(prev)
    return out


def _macd(values, fast=12, slow=26, signal=9):
    """Return (macd_line, signal_line) each aligned 1:1 with `values`,
    with None padding where not yet computable."""
    ef = _ema(values, fast)
    es = _ema(values, slow)
    macd = [
        (a - b) if (a is not None and b is not None) else None
        for a, b in zip(ef, es)
    ]
    valid = [m for m in macd if m is not None]
    sig_valid = _ema(valid, signal)
    sig = [None] * len(macd)
    j = 0
    for i, m in enumerate(macd):
        if m is not None:
            sig[i] = sig_valid[j]
            j += 1
    return macd, sig


class IndicatorMacd(Strategy):
    name = "indicator_macd"
    timeframe = "5m"
    lookback = 160          # >= slow_ema(44) + macd_slow(26) + signal(9) + buffer
    interval = 60

    def __init__(self, fast_ema=15, slow_ema=44,
                 macd_fast=12, macd_slow=26, macd_signal=9,
                 ):
        self.fast_ema = fast_ema
        self.slow_ema = slow_ema
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal

    def decide(self, md: MarketData, position: float) -> str:
        c = md.closes
        need = max(self.slow_ema, self.macd_slow) + self.macd_signal + 5
        if len(c) < need:
            return "hold"

        ef = _ema(c, self.fast_ema)
        es = _ema(c, self.slow_ema)
        macd, sig = _macd(c, self.macd_fast, self.macd_slow,
                          self.macd_signal)

        # Need the last 3 MACD / signal 
        needed = (ef[-1], es[-1],
                  macd[-1], macd[-2], macd[-3],
                  sig[-1], sig[-2], sig[-3],)
        if any(v is None for v in needed):
            return "hold"

        ema_fast, ema_slow = ef[-1], es[-1]
        X1, X2, X3 = macd[-1], macd[-2], macd[-3]      # macd line
        Y1, Y2, Y3 = sig[-1], sig[-2], sig[-3]         # macd signal

        # ---- ported 1:1 from the MT5 bot's check_signals() ------------
        buy_conditions = [
            ema_fast > ema_slow and X1 > Y1 and X2 < Y2
            and X1 > X2 and X2 > X3,

            ema_fast > ema_slow and X1 > Y1 and X2 > Y2 and X3 < Y3
            and X1 > X2 and X2 > X3,

        ]

        sell_conditions = [
            ema_fast < ema_slow and X1 < Y1 and X2 > Y2
            and X1 < X2 and X2 < X3,

            ema_fast < ema_slow and X1 < Y1 and X2 < Y2 and X3 > Y3
            and X1 < X2 and X2 < X3,

        ]

        if any(buy_conditions):
            return "buy"
        if any(sell_conditions):
            return "sell"
        return "hold"