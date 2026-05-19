"""
core/worker.py

ONE worker per asset class. Per tick it fetches market data ONCE per
unique (symbol, timeframe, lookback) across ALL configured symbols, then
pushes the cache through every (strategy x behavior) combination — and
each combo CONTINUOUSLY SCANS every symbol for entry signals.

Combo identity is (asset, strategy, behavior). One PaperBroker per
combo. SINGLE-POSITION MODEL: a combo is either flat or long on exactly
one symbol.

v2.2: when a combo is in a position, the held symbol's recent close
series is passed into the behavior's position_state so ATR-aware
behaviors (AtrTrailingStop, ported from the previous MT5 bot) can size
their trailing distance. Behaviors that don't need it simply ignore it.

Decision flow per combo per tick:

  IF in position on symbol S:
    1. broker.mark(price_S)
    2. behavior says exit?  -> close (reason="behavior"), LOCK re-entry
    3. strategy says sell?  -> close (reason="strategy"), unlock
    4. strategy says buy/hold while long  -> stay
    5. log equity tagged with S

  IF flat:
    1. for each symbol in the asset's symbols list (in order):
         ask strategy: want long?
         if "buy" and (not locked OR cooldown_ok): open long on this
             symbol, set current_symbol=S, log trade tagged with S, STOP
         if not "buy": treat as a re-arm signal for the lock
    2. log equity tagged with the FIRST available symbol as the
       observation, so the dashboard has a continuous series even when
       the combo is flat

State persisted across restarts:
  - PaperBroker state (cash, qty, entry, high, low, bars, last,
    realized_trades) — review #3
  - current_symbol (which symbol we're long on) — needed so a restart
    doesn't lose the link between the open position and its symbol.

Per tick (other operational items, unchanged):
  - if a feed is unavailable for a key this tick (market closed, etc.),
    SKIP combos that need it — no marking equity at price 0 (#4).
  - drift-free schedule, batched DB commit, transient-streak alerting,
    daily retention prune.
"""

import logging
import os
import sys
import time

from core.config import WorkerConfig
from core.results import ResultsDB
from core.feed import FeedUnavailable
from core.matrix import (STRATEGIES, BEHAVIORS, ASSETS, asset_symbols,
                         available_symbols, build_combos, combo_id)
from core.paper import PaperBroker
from core.alerts import alert, heartbeat

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("worker")

# review #9: 0 = lock re-entry until the strategy goes flat (default,
# preserves cross-run comparability). >0 = also re-arm after this many
# ticks even if the strategy is still long (mitigates "one trailing-stop
# touch kills a multi-day trend"). Tunable, documented in the README.
REENTRY_COOLDOWN = int(os.environ.get("REENTRY_COOLDOWN_BARS", "0"))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def build_feed(cfg: WorkerConfig):
    if os.environ.get("DRY_RUN", "") in ("1", "true", "yes"):
        from core.feeds.synthetic_feed import SyntheticFeed
        return SyntheticFeed()
    if cfg.asset == "crypto":
        from core.feeds.crypto_feed import CryptoFeed
        return CryptoFeed(cfg.binance_testnet_key,
                          cfg.binance_testnet_secret,
                          cfg.crypto_exchange)
    if cfg.asset == "forex":
        from core.feeds.forex_feed import ForexFeed
        return ForexFeed(cfg.oanda_token, cfg.oanda_account)
    from core.feeds.stocks_feed import StocksFeed
    return StocksFeed(cfg.alpaca_key, cfg.alpaca_secret)


class Combo:
    """One (asset, strategy, behavior) experiment with a single broker
    that may hold a single long position on one of the configured
    symbols at a time."""

    def __init__(self, asset, skey, bkey, cfg, cost_bps, db: ResultsDB):
        self.asset = asset
        self.skey = skey
        self.bkey = bkey
        self.id = combo_id(asset, skey, bkey)
        self.strategy = STRATEGIES[skey]()
        self.behavior = BEHAVIORS[bkey]()
        self.broker = PaperBroker(cfg.start_equity, cfg.alloc_fraction,
                                  cost_bps)
        self.locked = False
        self.lock_age = 0
        self.current_symbol: str | None = None
        # cache-key components for this combo (tf, lb are per-strategy)
        self.tf = self.strategy.timeframe
        self.lb = self.strategy.lookback

        # review #3: resume prior state if present
        prev = db.load_state(self.id)
        if prev:
            self.broker.load_state(prev)
            self.current_symbol = prev.get("sym") or None
            self.locked = bool(prev.get("locked", False))
            self.lock_age = int(prev.get("lock_age", 0))
            log.info("[resume] %s cash=%.2f qty=%.6f sym=%s",
                     self.id, self.broker.cash, self.broker.qty,
                     self.current_symbol)

    def to_full_state(self) -> dict:
        s = self.broker.to_state()
        s["sym"] = self.current_symbol or ""
        s["locked"] = bool(self.locked)
        s["lock_age"] = int(self.lock_age)
        return s


def _resolve_symbols(cfg: WorkerConfig, db: ResultsDB) -> list:
    """Resolve which symbols the worker should fetch THIS tick.

    Priority:
      1. explicit env override (cfg.symbols, from SYMBOLS / SYMBOL) — only
         honored if the caller actually set one. Lets advanced users pin
         a worker to a fixed list and bypass the dashboard.
      2. the dashboard's `symbol_inclusion` table — the normal path.
      3. the master `available_symbols` list — fallback if the inclusion
         table is empty for this asset (e.g. fresh DB).

    The fallback also seeds the inclusion table with defaults so the
    dashboard shows the full master list on first render.
    """
    if cfg.symbols:
        return list(cfg.symbols)
    included = db.get_included_symbols(cfg.asset)
    if included:
        return included
    master = available_symbols(cfg.asset)
    for sym in master:
        db.ensure_symbol_inclusion(cfg.asset, sym, default_included=True)
    return list(master)


def run_worker(cfg: WorkerConfig):
    meta = ASSETS[cfg.asset]
    cost_bps = meta["cost_bps"]

    db = ResultsDB(cfg.db_path)
    default_run = _env_bool("WORKER_AUTOSTART", True)
    db.ensure_worker_control(cfg.asset, default_run=default_run)
    # seed inclusion table with the master list so the dashboard renders
    # a complete checkbox set on first load, even before the first tick.
    for sym in available_symbols(cfg.asset):
        db.ensure_symbol_inclusion(cfg.asset, sym, default_included=True)

    feed = None
    combos: list[Combo] = []
    fetch_keys: list[tuple[str, str, int]] = []
    active_symbols: list[str] = []

    def ensure_runtime_started():
        nonlocal feed, combos
        if feed is not None:
            return
        feed = build_feed(cfg)
        try:
            feed.check()
        except Exception as e:
            alert(f"[worker:{cfg.asset}] feed startup FAILED: {e}",
                  cfg.tg_token, cfg.tg_chat)
            raise
        combos = [Combo(cfg.asset, s, b, cfg, cost_bps, db)
                  for s, b in build_combos()]
        log.info("[worker:%s] %s combos=%d cost=%.1fbps",
                 cfg.asset, feed.name, len(combos), cost_bps)
        alert(f"[worker:{cfg.asset}] online: {len(combos)} combos",
              cfg.tg_token, cfg.tg_chat)

    def refresh_symbol_set():
        """Recompute active_symbols / fetch_keys when the dashboard's
        inclusion list changes. Cheap; we only log on actual changes."""
        nonlocal active_symbols, fetch_keys
        new_syms = _resolve_symbols(cfg, db)
        if new_syms == active_symbols:
            return
        log.info("[worker:%s] symbol set changed: %s -> %s",
                 cfg.asset, active_symbols, new_syms)
        active_symbols = new_syms
        fetch_keys = sorted({(sym, c.tf, c.lb)
                             for sym in active_symbols for c in combos})

    period = cfg.poll_seconds
    last_daily = 0.0
    last_prune = 0.0
    transient_streak = 0
    last_desired = None

    while True:
        tick_started = time.time()
        try:
            desired = db.worker_desired(cfg.asset, default_run=default_run)
            if not desired:
                if last_desired is not False:
                    log.info("[worker:%s] paused by dashboard", cfg.asset)
                    alert(f"[worker:{cfg.asset}] paused by dashboard",
                          cfg.tg_token, cfg.tg_chat)
                db.beat(f"worker-{cfg.asset}", "paused by dashboard")
                db.flush()
                heartbeat(cfg.heartbeat_url)
                transient_streak = 0
                last_desired = False
                time.sleep(max(1.0, period - (time.time() - tick_started)))
                continue

            if last_desired is False:
                log.info("[worker:%s] resumed by dashboard", cfg.asset)
                alert(f"[worker:{cfg.asset}] resumed by dashboard",
                      cfg.tg_token, cfg.tg_chat)
            last_desired = True
            ensure_runtime_started()
            refresh_symbol_set()

            if not active_symbols:
                # user unchecked every symbol for this asset — stay idle
                # but heartbeating so the dashboard shows the worker alive
                db.beat(f"worker-{cfg.asset}",
                        "idle: no symbols selected in dashboard")
                db.flush()
                heartbeat(cfg.heartbeat_url)
                time.sleep(max(1.0, period - (time.time() - tick_started)))
                continue

            # ---- fetch once per unique (symbol, tf, lb) -----------------
            cache: dict[tuple[str, str, int], object] = {}
            for sym, tf, lb in fetch_keys:
                try:
                    cache[(sym, tf, lb)] = feed.get_market_data(sym, tf, lb)
                except FeedUnavailable as e:
                    cache[(sym, tf, lb)] = None
                    log.info("[worker:%s] data unavailable %s/%s/%s: %s",
                             cfg.asset, sym, tf, lb, e)

            for cm in combos:
                if cm.locked:
                    cm.lock_age += 1

                # if the position's symbol is no longer included by the
                # dashboard, close the position at the last marked price
                # (reason="symbol_excluded"). Holding a position whose
                # symbol we no longer fetch would let equity drift on a
                # stale price.
                if (cm.current_symbol and
                        cm.current_symbol not in active_symbols):
                    _force_close_excluded(cm, db, cfg.asset)

                if cm.current_symbol:
                    _step_in_position(cm, cache, db)
                else:
                    _step_flat(cm, active_symbols, cache, db)

                db.save_state(cm.id, cm.to_full_state())

            db.beat(f"worker-{cfg.asset}",
                    f"{len(combos)} combos / {len(active_symbols)} syms ok")
            db.flush()                              # one commit per tick
            heartbeat(cfg.heartbeat_url)
            transient_streak = 0

            now = time.time()
            if now - last_daily > 86400:
                alert(f"[worker:{cfg.asset}] daily heartbeat, "
                      f"{len(combos)} combos alive", cfg.tg_token,
                      cfg.tg_chat)
                last_daily = now
            if now - last_prune > 86400:
                db.prune(keep_days=45.0)            # review #8 retention
                last_prune = now

        except FeedUnavailable as e:
            log.info("[worker:%s] feed unavailable, skipping tick: %s",
                     cfg.asset, e)
        except Exception as e:
            transient = any(w in repr(e).lower() for w in
                            ("timeout", "network", "connection",
                             "temporarily", "rate limit", "502", "503",
                             "504", "ssl", "remote end"))
            if transient:
                transient_streak += 1
                log.warning("[worker:%s] transient #%d: %s",
                            cfg.asset, transient_streak, e)
                # review #10: a long blip should not look like silent death
                if transient_streak in (5, 20, 60):
                    alert(f"[worker:{cfg.asset}] {transient_streak} "
                          f"consecutive transient errors: {e}",
                          cfg.tg_token, cfg.tg_chat)
            else:
                alert(f"[worker:{cfg.asset}] FATAL: {e}",
                      cfg.tg_token, cfg.tg_chat)
                raise

        time.sleep(max(1.0, period - (time.time() - tick_started)))


def _force_close_excluded(cm: Combo, db: ResultsDB, asset: str) -> None:
    """The dashboard removed the symbol this combo is holding. Close at
    the last marked price so equity stops drifting on data we no longer
    fetch. If the broker has no last price (shouldn't happen), just drop
    the position pointer."""
    held = cm.current_symbol
    br = cm.broker
    price = br.last
    if br.in_position and price > 0:
        q = br.qty
        br.close(price)
        db.log_trade(cm.id, asset, held, cm.skey, cm.bkey,
                     "sell", q, price, "symbol_excluded")
        db.log_equity(cm.id, asset, held, cm.skey, cm.bkey,
                      br.equity(price), price)
    cm.locked = False
    cm.lock_age = 0
    cm.current_symbol = None
    log.info("[worker:%s] %s closed forced: %s no longer included",
             asset, cm.id, held)


def _step_in_position(cm: Combo, cache: dict, db: ResultsDB) -> None:
    """Combo is long on cm.current_symbol; only consult that symbol."""
    md = cache.get((cm.current_symbol, cm.tf, cm.lb))
    if md is None:
        return  # skip tick for this combo
    br = cm.broker
    price = md.price
    br.mark(price)

    held_symbol = cm.current_symbol  # remember for trade tagging

    # behavior-forced exit. Pass the held symbol's recent closes so
    # ATR-aware behaviors (AtrTrailingStop, ported from the MT5 bot)
    # can size their trailing distance; all other behaviors ignore it.
    if br.in_position and cm.behavior.should_exit(
            br.position_state(md.closes)):
        q = br.qty
        br.close(price)
        cm.locked = True
        cm.lock_age = 0
        cm.current_symbol = None
        db.log_trade(cm.id, cm.asset, held_symbol, cm.skey, cm.bkey,
                     "sell", q, price, "behavior")
        db.log_equity(cm.id, cm.asset, held_symbol, cm.skey, cm.bkey,
                      br.equity(price), price)
        return

    # strategy-driven exit (or stay)
    want = cm.strategy.decide(md, br.qty)
    if want == "sell" and br.in_position:
        q = br.qty
        br.close(price)
        cm.locked = False
        cm.current_symbol = None
        db.log_trade(cm.id, cm.asset, held_symbol, cm.skey, cm.bkey,
                     "sell", q, price, "strategy")
    elif want != "buy":
        cm.locked = False  # strategy went flat -> re-arm

    db.log_equity(cm.id, cm.asset, held_symbol, cm.skey, cm.bkey,
                  br.equity(price), price)


def _step_flat(cm: Combo, symbols: list, cache: dict,
               db: ResultsDB) -> None:
    """Combo is flat; scan all symbols, open on the first buy signal."""
    br = cm.broker
    observation_symbol = None
    observation_price = 0.0
    saw_non_buy = False

    for sym in symbols:
        md = cache.get((sym, cm.tf, cm.lb))
        if md is None:
            continue
        if observation_symbol is None:
            observation_symbol = sym
            observation_price = md.price

        want = cm.strategy.decide(md, 0.0)
        if want != "buy":
            saw_non_buy = True
            continue

        cooldown_ok = (REENTRY_COOLDOWN > 0 and
                       cm.lock_age >= REENTRY_COOLDOWN)
        if cm.locked and not cooldown_ok:
            # strategy still bullish on this symbol but we're locked;
            # keep scanning in case another symbol also signals, but
            # don't open here
            continue

        br.open_long(md.price)
        cm.behavior.reset()
        cm.current_symbol = sym
        cm.locked = False
        cm.lock_age = 0
        db.log_trade(cm.id, cm.asset, sym, cm.skey, cm.bkey,
                     "buy", br.qty, md.price, "strategy")
        # log equity at the entry price so the curve has a clean step
        db.log_equity(cm.id, cm.asset, sym, cm.skey, cm.bkey,
                      br.equity(md.price), md.price)
        return

    # didn't enter — re-arm lock if at least one symbol went flat
    if saw_non_buy:
        cm.locked = False

    # log a continuous equity row (cash, since we're flat) so the
    # dashboard sees the combo as alive even with no trades yet
    if observation_symbol is not None:
        br.mark(observation_price)
        db.log_equity(cm.id, cm.asset, "", cm.skey, cm.bkey,
                      br.equity(observation_price), observation_price)