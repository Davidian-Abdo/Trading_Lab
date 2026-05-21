"""
core/results.py

One shared SQLite DB. Every row is TAGGED with its dimensions
(asset, symbol, strategy, behavior) so the dashboard can pivot:
"for each (asset, symbol), which strategy x behavior won?"

WAL + busy timeout so the 3 workers can write concurrently.

Review fixes:
  - #8: indexes on (asset,strategy,behavior,ts); prune() for retention so
    the equity table doesn't grow unbounded and the dashboard stays fast.
  - #3: combo_state table + save_state/load_state for restart persistence.
  - medium: one reused connection per worker (not a new connect() per
    write); commit batched per tick via flush().
  - multi-symbol: equity/trades carry an explicit symbol column.
    _migrate() adds it to pre-existing tables on startup.
"""

import json
import os
import sqlite3
import threading
import time

# Tables only. Indexes are created AFTER _migrate() runs, because the new
# indexes reference the `symbol` column which may not yet exist on an old
# database from a prior version.
TABLES_SCHEMA = """
CREATE TABLE IF NOT EXISTS equity (
    combo TEXT, asset TEXT, symbol TEXT, strategy TEXT, behavior TEXT,
    ts REAL, equity REAL, price REAL
);
CREATE TABLE IF NOT EXISTS trades (
    combo TEXT, asset TEXT, symbol TEXT, strategy TEXT, behavior TEXT,
    ts REAL, side TEXT, qty REAL, price REAL, reason TEXT
);
CREATE TABLE IF NOT EXISTS heartbeat (
    worker TEXT PRIMARY KEY, ts REAL, note TEXT
);
CREATE TABLE IF NOT EXISTS worker_control (
    asset TEXT PRIMARY KEY,
    desired_run INTEGER NOT NULL,
    updated_ts REAL NOT NULL,
    updated_by TEXT,
    note TEXT
);
CREATE TABLE IF NOT EXISTS combo_state (
    combo TEXT PRIMARY KEY, ts REAL, state TEXT
);
CREATE TABLE IF NOT EXISTS symbol_inclusion (
    asset TEXT NOT NULL,
    symbol TEXT NOT NULL,
    included INTEGER NOT NULL,
    updated_ts REAL NOT NULL,
    PRIMARY KEY (asset, symbol)
);
CREATE TABLE IF NOT EXISTS periods (
    combo TEXT NOT NULL,
    asset TEXT NOT NULL,
    strategy TEXT NOT NULL,
    behavior TEXT NOT NULL,
    idx INTEGER NOT NULL,
    start_ts REAL NOT NULL,
    end_ts REAL NOT NULL,
    start_equity REAL NOT NULL,
    settled_equity REAL,
    settled_ts REAL,
    status TEXT NOT NULL,
    PRIMARY KEY (combo, idx)
);
"""

INDEXES_SCHEMA = """
CREATE INDEX IF NOT EXISTS ix_equity_dim
    ON equity (asset, symbol, strategy, behavior, ts);
CREATE INDEX IF NOT EXISTS ix_equity_ts ON equity (ts);
CREATE INDEX IF NOT EXISTS ix_trades_dim
    ON trades (asset, symbol, strategy, behavior, ts);
"""

CONTROL_TABLES = {
    "heartbeat": {
        "create": (
            "CREATE TABLE {name} ("
            "worker TEXT PRIMARY KEY, ts REAL, note TEXT)"
        ),
        "columns": ("worker", "ts", "note"),
        "defaults": {"worker": "''", "ts": "0", "note": "''"},
        "pk": ("worker",),
    },
    "worker_control": {
        "create": (
            "CREATE TABLE {name} ("
            "asset TEXT PRIMARY KEY, desired_run INTEGER NOT NULL, "
            "updated_ts REAL NOT NULL, updated_by TEXT, note TEXT)"
        ),
        "columns": ("asset", "desired_run", "updated_ts",
                    "updated_by", "note"),
        "defaults": {
            "asset": "''",
            "desired_run": "1",
            "updated_ts": "0",
            "updated_by": "''",
            "note": "''",
        },
        "pk": ("asset",),
    },
    "combo_state": {
        "create": (
            "CREATE TABLE {name} ("
            "combo TEXT PRIMARY KEY, ts REAL, state TEXT)"
        ),
        "columns": ("combo", "ts", "state"),
        "defaults": {"combo": "''", "ts": "0", "state": "'{}'"},
        "pk": ("combo",),
    },
    "symbol_inclusion": {
        "create": (
            "CREATE TABLE {name} ("
            "asset TEXT NOT NULL, symbol TEXT NOT NULL, "
            "included INTEGER NOT NULL, updated_ts REAL NOT NULL, "
            "PRIMARY KEY (asset, symbol))"
        ),
        "columns": ("asset", "symbol", "included", "updated_ts"),
        "defaults": {
            "asset": "''",
            "symbol": "''",
            "included": "1",
            "updated_ts": "0",
        },
        "pk": ("asset", "symbol"),
    },
    "periods": {
        "create": (
            "CREATE TABLE {name} ("
            "combo TEXT NOT NULL, asset TEXT NOT NULL, "
            "strategy TEXT NOT NULL, behavior TEXT NOT NULL, "
            "idx INTEGER NOT NULL, start_ts REAL NOT NULL, "
            "end_ts REAL NOT NULL, start_equity REAL NOT NULL, "
            "settled_equity REAL, settled_ts REAL, "
            "status TEXT NOT NULL, PRIMARY KEY (combo, idx))"
        ),
        "columns": ("combo", "asset", "strategy", "behavior", "idx",
                    "start_ts", "end_ts", "start_equity",
                    "settled_equity", "settled_ts", "status"),
        "defaults": {
            "combo": "''", "asset": "''", "strategy": "''",
            "behavior": "''", "idx": "0", "start_ts": "0",
            "end_ts": "0", "start_equity": "0",
            "settled_equity": "NULL", "settled_ts": "NULL",
            "status": "'open'",
        },
        "pk": ("combo", "idx"),
    },
}


def _has_column(conn: sqlite3.Connection, table: str, col: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == col for row in cur.fetchall())


def _table_info(conn: sqlite3.Connection, table: str) -> list:
    return list(conn.execute(f"PRAGMA table_info({table})"))


def _pk_columns(conn: sqlite3.Connection, table: str) -> tuple:
    info = _table_info(conn, table)
    pk_rows = sorted(
        ((row[5], row[1]) for row in info if row[5]),
        key=lambda item: item[0],
    )
    return tuple(name for _, name in pk_rows)


def _rebuild_control_table(conn: sqlite3.Connection, table: str,
                           spec: dict) -> None:
    """Recreate small control tables when an old schema lacks constraints.

    SQLite cannot add a PRIMARY KEY with ALTER TABLE. These tables are
    small metadata/control-plane tables, so rebuilding them is simpler
    and safer than allowing later ON CONFLICT writes to fail.
    """
    tmp = f"__{table}_migrate"
    conn.execute(f"DROP TABLE IF EXISTS {tmp}")
    conn.execute(spec["create"].format(name=tmp))
    existing = {row[1] for row in _table_info(conn, table)}
    select_exprs = [
        col if col in existing else spec["defaults"][col]
        for col in spec["columns"]
    ]
    where = " AND ".join(
        f"{col} IS NOT NULL AND {col} != ''"
        for col in spec["pk"] if col in existing
    )
    sql = (
        f"INSERT OR REPLACE INTO {tmp} "
        f"({', '.join(spec['columns'])}) "
        f"SELECT {', '.join(select_exprs)} FROM {table}"
    )
    if where:
        sql += f" WHERE {where}"
    conn.execute(sql)
    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {tmp} RENAME TO {table}")


def _ensure_control_tables(conn: sqlite3.Connection) -> None:
    for table, spec in CONTROL_TABLES.items():
        existing = {row[1] for row in _table_info(conn, table)}
        missing = set(spec["columns"]) - existing
        if missing or _pk_columns(conn, table) != spec["pk"]:
            _rebuild_control_table(conn, table, spec)


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive migrations for upgrades from older schemas. Idempotent."""
    for table in ("equity", "trades"):
        if not _has_column(conn, table, "symbol"):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN symbol TEXT")
    _ensure_control_tables(conn)
    conn.commit()


class ResultsDB:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        # ONE reused connection. WAL so a separate read-only connection
        # (e.g. the dashboard's @st.cache_data loader) can read while we
        # write. check_same_thread=False is required because Streamlit
        # fires widget on_change callbacks on a thread other than the
        # one that originally created this connection at module import.
        # We serialize all access through self._lock to make that safe.
        self.conn = sqlite3.connect(path, timeout=30,
                                    check_same_thread=False)
        self._lock = threading.Lock()
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA busy_timeout=30000;")
        self.conn.executescript(TABLES_SCHEMA)
        _migrate(self.conn)
        self.conn.executescript(INDEXES_SCHEMA)
        self.conn.commit()

    # ---- writes (batched; call flush() once per tick) ------------------
    def log_equity(self, combo, asset, symbol, strat, beh, equity, price):
        with self._lock:
            self.conn.execute(
                "INSERT INTO equity "
                "(combo, asset, symbol, strategy, behavior, ts, equity, "
                "price) VALUES (?,?,?,?,?,?,?,?)",
                (combo, asset, symbol, strat, beh, time.time(),
                 equity, price))

    def log_trade(self, combo, asset, symbol, strat, beh,
                  side, qty, price, reason):
        with self._lock:
            self.conn.execute(
                "INSERT INTO trades "
                "(combo, asset, symbol, strategy, behavior, ts, side, "
                "qty, price, reason) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (combo, asset, symbol, strat, beh, time.time(),
                 side, qty, price, reason))

    def save_state(self, combo, state: dict):
        with self._lock:
            self.conn.execute(
                "INSERT INTO combo_state (combo, ts, state) VALUES (?,?,?) "
                "ON CONFLICT(combo) DO UPDATE SET ts=excluded.ts, "
                "state=excluded.state",
                (combo, time.time(), json.dumps(state)))

    def load_state(self, combo):
        with self._lock:
            cur = self.conn.execute(
                "SELECT state FROM combo_state WHERE combo=?", (combo,))
            row = cur.fetchone()
        return json.loads(row[0]) if row else None

    # ---- dashboard control plane --------------------------------------
    def ensure_worker_control(self, asset: str, default_run: bool = True):
        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO worker_control "
                "(asset, desired_run, updated_ts, updated_by, note) "
                "VALUES (?,?,?,?,?)",
                (asset, int(default_run), time.time(), "system",
                 "initial default"))
            self.conn.commit()

    def set_worker_desired(self, asset: str, desired_run: bool,
                           updated_by: str = "dashboard", note: str = ""):
        with self._lock:
            self.conn.execute(
                "INSERT INTO worker_control "
                "(asset, desired_run, updated_ts, updated_by, note) "
                "VALUES (?,?,?,?,?) "
                "ON CONFLICT(asset) DO UPDATE SET "
                "desired_run=excluded.desired_run, "
                "updated_ts=excluded.updated_ts, "
                "updated_by=excluded.updated_by, note=excluded.note",
                (asset, int(desired_run), time.time(), updated_by, note))
            self.conn.commit()

    def worker_desired(self, asset: str, default_run: bool = True) -> bool:
        with self._lock:
            cur = self.conn.execute(
                "SELECT desired_run FROM worker_control WHERE asset=?",
                (asset,))
            row = cur.fetchone()
        return bool(row[0]) if row else default_run

    # ---- per-asset symbol inclusion (dashboard-editable) -------------
    def ensure_symbol_inclusion(self, asset: str, symbol: str,
                                default_included: bool = True) -> None:
        """Insert a row for (asset, symbol) if missing. Used at startup so
        the dashboard sees every symbol from the master list with a
        sensible default."""
        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO symbol_inclusion "
                "(asset, symbol, included, updated_ts) VALUES (?,?,?,?)",
                (asset, symbol, int(default_included), time.time()))
            self.conn.commit()

    def set_symbol_included(self, asset: str, symbol: str,
                            included: bool) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO symbol_inclusion "
                "(asset, symbol, included, updated_ts) VALUES (?,?,?,?) "
                "ON CONFLICT(asset, symbol) DO UPDATE SET "
                "included=excluded.included, "
                "updated_ts=excluded.updated_ts",
                (asset, symbol, int(included), time.time()))
            self.conn.commit()

    def get_included_symbols(self, asset: str) -> list:
        """Return the list of symbols flagged as included for this asset,
        sorted by symbol for stable ordering."""
        with self._lock:
            cur = self.conn.execute(
                "SELECT symbol FROM symbol_inclusion "
                "WHERE asset=? AND included=1 ORDER BY symbol",
                (asset,))
            return [row[0] for row in cur.fetchall()]

    def get_inclusion_map(self, asset: str) -> dict:
        """Return {symbol: bool included} for every row this asset has."""
        with self._lock:
            cur = self.conn.execute(
                "SELECT symbol, included FROM symbol_inclusion "
                "WHERE asset=?", (asset,))
            return {row[0]: bool(row[1]) for row in cur.fetchall()}

    def beat(self, worker, note=""):
        with self._lock:
            self.conn.execute(
                "INSERT INTO heartbeat (worker, ts, note) VALUES (?,?,?) "
                "ON CONFLICT(worker) DO UPDATE SET ts=excluded.ts, "
                "note=excluded.note",
                (worker, time.time(), note))

    def flush(self):
        with self._lock:
            self.conn.commit()

    # ---- test-schedule windows ----------------------------------------
    def open_period(self, combo, asset, strategy, behavior, idx,
                    start_ts, end_ts, start_equity):
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO periods "
                "(combo, asset, strategy, behavior, idx, start_ts, end_ts, "
                "start_equity, settled_equity, settled_ts, status) "
                "VALUES (?,?,?,?,?,?,?,?,NULL,NULL,'open')",
                (combo, asset, strategy, behavior, idx,
                 start_ts, end_ts, start_equity))
            self.conn.commit()

    def settle_period(self, combo, idx, settled_equity, settled_ts):
        with self._lock:
            self.conn.execute(
                "UPDATE periods SET settled_equity=?, settled_ts=?, "
                "status='settled' WHERE combo=? AND idx=?",
                (settled_equity, settled_ts, combo, idx))
            self.conn.commit()

    def latest_period(self, combo):
        """Return (idx, start_ts, end_ts, start_equity, status) or None."""
        with self._lock:
            cur = self.conn.execute(
                "SELECT idx, start_ts, end_ts, start_equity, status "
                "FROM periods WHERE combo=? ORDER BY idx DESC LIMIT 1",
                (combo,))
            row = cur.fetchone()
        return row

    # ---- retention (review #8) -----------------------------------------
    def prune(self, keep_days: float = 45.0):
        """Delete raw equity rows older than keep_days. Trades are kept
        (they're sparse and needed for trade counts)."""
        cutoff = time.time() - keep_days * 86400
        with self._lock:
            self.conn.execute("DELETE FROM equity WHERE ts < ?", (cutoff,))
            self.conn.commit()
