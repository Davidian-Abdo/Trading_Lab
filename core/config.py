"""
core/config.py

v2 runs ONE worker process/thread per asset class. Each worker runs the
whole (symbol x strategy x behavior) matrix for its asset against a
single shared data feed.

Broker switch:
  - crypto  -> Kraken spot (CCXT, public data; keys optional)
  - forex   -> Interactive Brokers PAPER (via IB Gateway/TWS)
  - stocks  -> Interactive Brokers PAPER (via IB Gateway/TWS)

IMPORTANT: stocks and forex share ONE IB Gateway but run as separate
workers, so they MUST use different IB clientIds. The per-asset client
id is resolved here from IB_CLIENT_ID_STOCKS / IB_CLIENT_ID_FOREX.

Config is per-worker, from environment variables.
"""

import os
from dataclasses import dataclass, field
from typing import List


def _req(k: str) -> str:
    v = os.environ.get(k)
    if not v:
        raise RuntimeError(f"Missing required env var: {k}")
    return v


def _parse_symbols(raw: str) -> List[str]:
    """Split a comma- or whitespace-separated symbol list, trimmed +
    deduped while preserving order."""
    if not raw:
        return []
    parts = [p.strip() for chunk in raw.split(",") for p in chunk.split()]
    seen = set()
    out = []
    for p in parts:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


@dataclass
class WorkerConfig:
    asset: str               # "crypto" | "forex" | "stocks"
    symbols: List[str] = field(default_factory=list)  # [] -> matrix default
    start_equity: float = 10000.0  # same virtual start for every combo
    alloc_fraction: float = 0.5
    poll_seconds: int = 60

    # ---- crypto: Kraken (public data; keys OPTIONAL) ----------------
    crypto_exchange: str = "kraken"           # hardwired to Kraken
    kraken_key: str = ""                       # optional (raises rate limits)
    kraken_secret: str = ""                   # optional

    # ---- forex + stocks: Interactive Brokers PAPER ------------------
    # Connect to a running IB Gateway / TWS. clientId is resolved
    # PER ASSET (stocks vs forex) so the two workers don't collide.
    ib_host: str = "127.0.0.1"
    ib_port: int = 4002                       # 4002=Gateway paper,
                                              # 7497=TWS paper
    ib_client_id: int = 11                    # set per-asset below
    ib_account: str = ""                      # optional paper acct id
    ib_market_data_type: int = 3              # 3=delayed,4=delayed-frozen,
                                              # 1=live (needs subscription)

    db_path: str = "data/results.db"
    tg_token: str = ""
    tg_chat: str = ""
    heartbeat_url: str = ""


def _ib_client_id_for(asset: str) -> int:
    """Distinct IB clientId per asset so stocks + forex workers can both
    connect to the same IB Gateway concurrently."""
    cid_stocks = int(os.environ.get("IB_CLIENT_ID_STOCKS", "11"))
    cid_forex = int(os.environ.get("IB_CLIENT_ID_FOREX", "12"))
    return cid_forex if asset == "forex" else cid_stocks


def load_worker_config_for(asset: str) -> WorkerConfig:
    """Build a WorkerConfig for a SPECIFIC asset, ignoring the ASSET env
    var. Used by the in-dashboard worker runner that spawns one thread
    per asset from a single process."""
    asset = asset.strip().lower()
    if asset not in ("crypto", "forex", "stocks"):
        raise RuntimeError("asset must be crypto|forex|stocks")
    return WorkerConfig(
        asset=asset,
        symbols=[],  # dashboard-driven; see core/worker._resolve_symbols
        start_equity=float(os.environ.get("START_EQUITY", "10000")),
        alloc_fraction=float(os.environ.get("ALLOC_FRACTION", "0.5")),
        poll_seconds=int(os.environ.get("POLL_SECONDS", "60")),
        crypto_exchange=os.environ.get("CRYPTO_EXCHANGE", "kraken"),
        kraken_key=os.environ.get("KRAKEN_KEY", ""),
        kraken_secret=os.environ.get("KRAKEN_SECRET", ""),
        ib_host=os.environ.get("IB_HOST", "127.0.0.1"),
        ib_port=int(os.environ.get("IB_PORT", "4002")),
        ib_client_id=_ib_client_id_for(asset),
        ib_account=os.environ.get("IB_ACCOUNT", ""),
        ib_market_data_type=int(os.environ.get("IB_MARKET_DATA_TYPE",
                                               "3")),
        db_path=os.environ.get("DB_PATH", "data/results.db"),
        tg_token=os.environ.get("TG_TOKEN", ""),
        tg_chat=os.environ.get("TG_CHAT", ""),
        heartbeat_url=os.environ.get("HEARTBEAT_URL", ""),
    )


def load_worker_config() -> WorkerConfig:
    asset = _req("ASSET").strip().lower()
    if asset not in ("crypto", "forex", "stocks"):
        raise RuntimeError("ASSET must be crypto|forex|stocks")

    # SYMBOLS (plural) wins. SYMBOL (singular, legacy) is also accepted.
    symbols = _parse_symbols(os.environ.get("SYMBOLS", ""))
    if not symbols:
        symbols = _parse_symbols(os.environ.get("SYMBOL", ""))

    cfg = load_worker_config_for(asset)
    cfg.symbols = symbols
    return cfg