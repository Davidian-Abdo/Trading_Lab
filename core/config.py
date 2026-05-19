"""
core/config.py

v2 runs ONE worker process per asset class. Each worker runs the whole
(symbol x strategy x behavior) matrix for its asset against a single
shared data feed. This keeps the whole lab to ~3 tiny processes, so a
$5 VPS handles dozens of combinations easily and there are no broker
rate-limit storms.

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
    """Split a comma- or whitespace-separated symbol list, trimmed + deduped
    while preserving order."""
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

    # feed credentials (only the ones for this asset are needed)
    crypto_exchange: str = "binance"          # hardwired to Binance testnet
    binance_testnet_key: str = ""             # required when asset=crypto
    binance_testnet_secret: str = ""          # required when asset=crypto
    oanda_token: str = ""
    oanda_account: str = ""
    alpaca_key: str = ""
    alpaca_secret: str = ""

    db_path: str = "data/results.db"
    tg_token: str = ""
    tg_chat: str = ""
    heartbeat_url: str = ""


def load_worker_config_for(asset: str) -> WorkerConfig:
    """Build a WorkerConfig for a SPECIFIC asset, ignoring the ASSET env
    var. Used by the in-dashboard worker runner that spawns one thread
    per asset from a single process. Env-var SYMBOLS is honored ONLY when
    a single asset is being run via the standalone entrypoint — when the
    dashboard runs everything, symbol selection is per-asset in the DB."""
    asset = asset.strip().lower()
    if asset not in ("crypto", "forex", "stocks"):
        raise RuntimeError("asset must be crypto|forex|stocks")
    return WorkerConfig(
        asset=asset,
        symbols=[],  # dashboard-driven; see core/worker._resolve_symbols
        start_equity=float(os.environ.get("START_EQUITY", "10000")),
        alloc_fraction=float(os.environ.get("ALLOC_FRACTION", "0.5")),
        poll_seconds=int(os.environ.get("POLL_SECONDS", "60")),
        crypto_exchange=os.environ.get("CRYPTO_EXCHANGE", "binance"),
        binance_testnet_key=os.environ.get("BINANCE_TESTNET_KEY", ""),
        binance_testnet_secret=os.environ.get("BINANCE_TESTNET_SECRET", ""),
        oanda_token=os.environ.get("OANDA_TOKEN", ""),
        oanda_account=os.environ.get("OANDA_ACCOUNT", ""),
        alpaca_key=os.environ.get("ALPACA_KEY", ""),
        alpaca_secret=os.environ.get("ALPACA_SECRET", ""),
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
    # When set, it pins this standalone worker to a fixed symbol list and
    # bypasses the dashboard's per-asset checkbox selection.
    symbols = _parse_symbols(os.environ.get("SYMBOLS", ""))
    if not symbols:
        symbols = _parse_symbols(os.environ.get("SYMBOL", ""))

    cfg = load_worker_config_for(asset)
    cfg.symbols = symbols
    return cfg
