"""
core/worker_runner.py

In-process worker supervisor. The dashboard imports this and calls
ensure_workers_started() at startup so the user does NOT have to run
`python -m bots.worker` in a separate terminal per asset. One daemon
thread per asset (crypto / forex / stocks). Whether each worker actually
fetches data is still controlled by the dashboard's `worker_control`
toggle — this module just keeps a supervising thread alive that polls
that toggle and runs the matrix when desired.

Why threads, not subprocesses:
  - One Streamlit process owns the lifecycle; threads vanish with the
    parent and there are no orphaned PIDs to track.
  - SQLite WAL + ResultsDB._lock already allow safe concurrent writes
    from the same process.
  - Workers are I/O-bound (HTTP fetches + SQLite writes), so the GIL is
    not the bottleneck.

Why idempotent:
  - Streamlit re-runs dashboard.py on every widget interaction. Calling
    ensure_workers_started() many times must spawn exactly one thread
    per asset for the lifetime of the Python process. A module-level
    lock + dict of (asset -> Thread) enforces that.

Crash policy:
  - Each thread wraps run_worker() in try/except. A FATAL exception from
    run_worker() (re-raised after alert) means the thread dies. We log
    + alert + record the death; the dashboard's heartbeat table will
    show the asset as STALE/CHECK so the user notices. We do NOT
    auto-restart inside the dashboard process — repeated re-raises of
    the same bug would just spin. Docker/systemd restarts the dashboard
    container if it as a whole dies.

Opt-out:
  - Set EMBED_WORKERS=0 in the environment to disable in-process
    workers entirely. Useful when running the legacy multi-container
    docker-compose layout where each asset has its own worker container.
"""

import logging
import os
import threading
import time
from typing import Dict, Optional

from core.config import load_worker_config_for
from core.worker import run_worker

log = logging.getLogger("worker_runner")

_ASSETS = ("crypto", "forex", "stocks")
_lock = threading.Lock()
_threads: Dict[str, threading.Thread] = {}
_started_at: Dict[str, float] = {}
_last_error: Dict[str, str] = {}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _runner(asset: str) -> None:
    try:
        cfg = load_worker_config_for(asset)
        log.info("[runner:%s] starting in-process worker", asset)
        run_worker(cfg)
    except Exception as e:
        # run_worker() only re-raises on FATAL. transient errors are
        # handled inside its own loop. So if we land here, the worker
        # is genuinely dead and won't recover without code/config change.
        _last_error[asset] = repr(e)
        log.exception("[runner:%s] worker died: %s", asset, e)


def ensure_workers_started() -> Dict[str, str]:
    """Spawn the supervising thread for each asset if not already alive.

    Idempotent. Returns a dict of asset -> status ("running" |
    "skipped: disabled" | "died: <error>"). The dashboard renders this
    so the user can see at a glance that the in-process workers are up.
    """
    status: Dict[str, str] = {}

    if not _env_bool("EMBED_WORKERS", True):
        for asset in _ASSETS:
            status[asset] = "skipped: EMBED_WORKERS=0"
        return status

    with _lock:
        for asset in _ASSETS:
            t = _threads.get(asset)
            if t is not None and t.is_alive():
                status[asset] = "running"
                continue
            if t is not None and not t.is_alive():
                err = _last_error.get(asset, "thread exited")
                status[asset] = f"died: {err}"
                # do NOT auto-restart — see crash policy in module docstring
                continue
            new_t = threading.Thread(
                target=_runner, args=(asset,),
                name=f"worker-{asset}", daemon=True)
            new_t.start()
            _threads[asset] = new_t
            _started_at[asset] = time.time()
            status[asset] = "running"
    return status


def runner_status() -> Dict[str, dict]:
    """Snapshot for the dashboard's diagnostic panel."""
    out: Dict[str, dict] = {}
    with _lock:
        for asset in _ASSETS:
            t = _threads.get(asset)
            alive = bool(t and t.is_alive())
            out[asset] = {
                "alive": alive,
                "started_at": _started_at.get(asset),
                "last_error": _last_error.get(asset, ""),
            }
    return out
