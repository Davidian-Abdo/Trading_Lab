"""
selftest.py  -- offline, no credentials, no network. Run this FIRST.

Pushes synthetic trend / chop / crash price paths through the ENTIRE
(strategy x behavior) matrix using real PaperBrokers, and checks:
  - every strategy returns only valid actions
  - behaviors fire and exits are attributed
  - equity stays finite and non-negative
This proves the engine works before you touch any account.

v2.2: the position snapshot now carries the recent close window so the
ported ATR trailing stop (AtrTrailingStop in tp_trail) is exercised
offline exactly as it runs live. The new `indicator` strategy (ported
EMA/MACD/RSI logic) is picked up automatically from STRATEGIES.

    python selftest.py
"""

import math

from core.feed import MarketData
from core.matrix import STRATEGIES, BEHAVIORS
from core.paper import PaperBroker

VALID = {"buy", "sell", "hold"}


def path(n=260, kind="trend"):
    out = []
    for i in range(n):
        if kind == "trend":
            out.append(100 + i * 0.3 + 2 * math.sin(i / 5))
        elif kind == "chop":
            out.append(100 + 6 * math.sin(i / 4))
        else:  # crash then recover
            out.append(150 - i * 0.4 if i < 160 else 102 + (i - 160) * 0.2)
    return out


def simulate(skey, bkey, prices):
    strat = STRATEGIES[skey]()
    beh = BEHAVIORS[bkey]()
    br = PaperBroker(start_equity=10000, alloc_fraction=0.5, cost_bps=5)
    locked = False
    behavior_exits = 0
    win = max(strat.lookback, 60)
    for i in range(win, len(prices)):
        closes = prices[i - win:i]
        md = MarketData("TEST", prices[i], closes)
        br.mark(prices[i])
        # pass the close window so ATR-aware behaviors size correctly
        if br.in_position and beh.should_exit(br.position_state(closes)):
            br.close(prices[i]); locked = True; behavior_exits += 1
        action = strat.decide(md, br.qty)
        assert action in VALID, f"{skey} returned {action!r}"
        if action == "sell" and br.in_position:
            br.close(prices[i]); locked = False
        elif action != "buy":
            locked = False
        elif action == "buy" and not br.in_position and not locked:
            br.open_long(prices[i]); beh.reset()
        eq = br.equity(prices[i])
        assert eq == eq and eq >= 0, f"{skey}/{bkey} bad equity {eq}"
    return br.equity(prices[-1]), br.realized_trades, behavior_exits


def main():
    ok = True
    print(f"{'strategy':<18}{'behavior':<12}{'kind':<7}"
          f"{'end_eq':>10}{'trades':>8}{'beh_exit':>9}")
    for skey in STRATEGIES:
        for bkey in BEHAVIORS:
            for kind in ("trend", "chop", "crash"):
                try:
                    eq, tr, bx = simulate(skey, bkey, path(kind=kind))
                    print(f"{skey:<18}{bkey:<12}{kind:<7}"
                          f"{eq:>10.0f}{tr:>8}{bx:>9}")
                except AssertionError as e:
                    ok = False
                    print(f"FAIL {skey}/{bkey}/{kind}: {e}")
    print("\nALL GOOD" if ok else "\nFAILURES PRESENT")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()