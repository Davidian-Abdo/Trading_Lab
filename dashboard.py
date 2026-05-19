"""
dashboard.py

Local control room for the asset x strategy x behavior lab.

Run:
    streamlit run dashboard.py

Each combo is (asset, strategy, behavior). At runtime that combo
continuously SCANS every configured symbol for entry signals and holds
at most one position at a time. The dashboard therefore renders ONE
pivot per asset (rows = strategy, columns = behavior) and a separate
"current positions" table showing which symbol each combo is long on,
if any.

The dashboard owns a small SQLite control plane. Docker/systemd keeps
the three worker processes alive in the background; the UI tells each
worker whether it should actively trade its matrix or pause cleanly.

Early-data behavior: this dashboard SHOWS something from the very first
tick. Combos with too little history get their cell rendered with the
raw P&L number we DO have (or "—" while still empty) and an explicit
"provisional / rankable" marker. Streamlit no longer st.stop()'s on an
empty equity table — it renders the matrix shell with zeros so you can
see the shape of the experiment immediately.
"""

import hmac
import os
import sqlite3
import time

import numpy as np
import pandas as pd
import streamlit as st

# Load .env.shared BEFORE the worker runner reads credentials so the
# dashboard can spawn workers without the user setting env vars manually.
try:
    from dotenv import load_dotenv
    load_dotenv(".env.shared")
except Exception:
    pass

from core.matrix import (ASSETS, STRATEGIES, BEHAVIORS, asset_symbols,
                         available_symbols)
from core.results import ResultsDB
from core.worker_runner import ensure_workers_started, runner_status


DB = os.environ.get("DB_PATH", "data/results.db")
MIN_TRADES = int(os.environ.get("MIN_TRADES", "20"))
DASH_DAYS = float(os.environ.get("DASH_DAYS", "45"))
STALE_AFTER_SECONDS = int(os.environ.get("WORKER_STALE_SECONDS", "600"))
DASH_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")

ASSET_ORDER = tuple(ASSETS.keys())
STRATEGY_ORDER = tuple(STRATEGIES.keys())
BEHAVIOR_ORDER = tuple(BEHAVIORS.keys())
PERIODS_PER_YEAR = {
    "crypto": 24 * 365,
    "forex": 24 * 365,
    "stocks": 6.5 * 252,
}


st.set_page_config(page_title="Strategy x Behavior x Asset Lab",
                   layout="wide")


# --- optional password gate (no plaintext compare; constant-time) -----
def _password_gate() -> bool:
    if not DASH_PASSWORD:
        return True
    if st.session_state.get("auth_ok"):
        return True
    st.title("Strategy x Behavior x Asset Lab")
    pwd = st.text_input("Dashboard password", type="password")
    if pwd and hmac.compare_digest(pwd, DASH_PASSWORD):
        st.session_state["auth_ok"] = True
        st.rerun()
    elif pwd:
        st.error("Wrong password.")
    st.stop()
    return False


_password_gate()


st.title("Strategy x Behavior x Asset - Comparison Lab")
st.caption(
    "Local paper-simulation control room. Each combo continuously scans "
    "every configured symbol of its asset class for entry signals and "
    "holds one position at a time. Use the controls below to run all "
    "three asset workers or pause/resume each one."
)


# ---- Data access -----------------------------------------------------
@st.cache_data(ttl=5)
def load_tables():
    cutoff = time.time() - DASH_DAYS * 86400
    con = sqlite3.connect(DB, timeout=30)
    con.execute("PRAGMA busy_timeout=30000;")
    eq = pd.read_sql_query(
        "SELECT * FROM equity WHERE ts >= ? ORDER BY ts", con,
        params=(cutoff,))
    tr = pd.read_sql_query("SELECT * FROM trades", con)
    hb = pd.read_sql_query("SELECT * FROM heartbeat", con)
    ctrl = pd.read_sql_query("SELECT * FROM worker_control", con)
    state = pd.read_sql_query("SELECT combo, ts, state FROM combo_state",
                              con)
    con.close()
    # symbol column may be NULL for very old rows (pre-multi-symbol DB)
    if "symbol" in eq.columns:
        eq["symbol"] = eq["symbol"].fillna("")
    else:
        eq["symbol"] = ""
    if "symbol" in tr.columns:
        tr["symbol"] = tr["symbol"].fillna("")
    else:
        tr["symbol"] = ""
    return eq, tr, hb, ctrl, state


def score(df: pd.DataFrame, asset: str) -> dict:
    """Risk-adjusted score from an equity series.

    Never returns empty: too-short series fall back to raw return so the
    dashboard can show SOMETHING from tick one. `hours` and `samples`
    tell you how seriously to take the value.
    """
    s = df.sort_values("ts").copy()
    if s.empty:
        return {"ret_pct": 0.0, "sharpe": 0.0, "mdd_pct": 0.0,
                "hours": 0, "samples": 0}
    s["dt"] = pd.to_datetime(s["ts"], unit="s")
    ser = s.set_index("dt")["equity"].resample("1h").last().dropna()
    samples = int(len(s))
    if len(ser) < 2:
        e0 = float(s["equity"].iloc[0])
        e1 = float(s["equity"].iloc[-1])
        ret_pct = (e1 / e0 - 1) * 100 if e0 else 0.0
        return {"ret_pct": ret_pct, "sharpe": 0.0, "mdd_pct": 0.0,
                "hours": int(len(ser)), "samples": samples}
    e = ser.to_numpy(dtype=float)
    r = np.diff(e) / e[:-1]
    r = r[np.isfinite(r)]
    if len(r) < 3 or r.std() == 0:
        peak = np.maximum.accumulate(e)
        mdd = ((e - peak) / peak).min() if len(e) > 1 else 0.0
        return {"ret_pct": (e[-1] / e[0] - 1) * 100,
                "sharpe": 0.0, "mdd_pct": float(mdd) * 100,
                "hours": int(len(e)), "samples": samples}
    ppy = PERIODS_PER_YEAR.get(asset, 24 * 365)
    sharpe = r.mean() / r.std() * np.sqrt(ppy)
    peak = np.maximum.accumulate(e)
    mdd = ((e - peak) / peak).min()
    return {"ret_pct": (e[-1] / e[0] - 1) * 100, "sharpe": float(sharpe),
            "mdd_pct": float(mdd) * 100, "hours": int(len(e)),
            "samples": samples}


control_db = ResultsDB(DB)
for asset_name in ASSET_ORDER:
    control_db.ensure_worker_control(asset_name, default_run=True)
    # seed the inclusion table with the master list (idempotent) so the
    # checkbox UI shows the full available set on first load, before any
    # worker has run.
    for sym in available_symbols(asset_name):
        control_db.ensure_symbol_inclusion(asset_name, sym,
                                           default_included=True)

# Auto-spawn one worker thread per asset (no terminals needed). The
# pause/resume toggle below still gates whether each worker actually
# trades. Set EMBED_WORKERS=0 to disable in-process workers, e.g. when
# running the legacy multi-container docker-compose layout.
_runner_status = ensure_workers_started()

try:
    eq, tr, hb, ctrl, state = load_tables()
except Exception as e:
    st.error(f"Cannot read {DB}: {e}")
    st.stop()


def desired_for(asset_name: str) -> bool:
    row = ctrl[ctrl.asset == asset_name]
    if row.empty:
        return True
    return bool(int(row.iloc[0].desired_run))


def heartbeat_for(asset_name: str):
    worker_name = f"worker-{asset_name}"
    row = hb[hb.worker == worker_name]
    if row.empty:
        return None
    return row.sort_values("ts").iloc[-1]


def current_position_for(asset_name: str, skey: str, bkey: str):
    """Look up the live position symbol for one combo from combo_state."""
    cid = f"{asset_name}|{skey}|{bkey}"
    row = state[state.combo == cid]
    if row.empty:
        return None, None
    try:
        import json
        st_dict = json.loads(row.iloc[0]["state"])
    except Exception:
        return None, None
    sym = st_dict.get("sym") or None
    qty = float(st_dict.get("qty", 0.0))
    return sym, qty


# ---- Worker control --- callback-based so the DB is the source of ----
# ---- truth and the toggle UI stays consistent across reruns ---------
def _on_toggle_change(asset_name: str):
    """Fires when the user flips an asset toggle. Streamlit writes the
    new value into st.session_state BEFORE this runs, so we just push it
    to the DB and invalidate caches."""
    new_value = bool(st.session_state[f"toggle_{asset_name}"])
    control_db.set_worker_desired(
        asset_name, new_value,
        note=f"{asset_name} toggled from dashboard")
    load_tables.clear()


def _run_all(desired_run: bool, note: str):
    for asset_name in ASSET_ORDER:
        control_db.set_worker_desired(asset_name, desired_run, note=note)
        # Keep toggle widget state in sync with the DB write so the UI
        # doesn't snap back to its old value on rerun.
        st.session_state[f"toggle_{asset_name}"] = desired_run
    load_tables.clear()


st.subheader("Worker Control")
left, mid, right = st.columns([1, 1, 3])
if left.button("Run all three", type="primary", width="stretch"):
    _run_all(True, "run all from dashboard")
    st.rerun()
if mid.button("Stop all", width="stretch"):
    _run_all(False, "stop all from dashboard")
    st.rerun()

now = time.time()
status_rows = []
for asset_name in ASSET_ORDER:
    desired = desired_for(asset_name)
    beat = heartbeat_for(asset_name)
    if beat is None:
        process_status = "not seen"
        last_seen_s = None
        note = ""
    else:
        last_seen_s = int(now - float(beat.ts))
        process_status = (
            "live" if last_seen_s < STALE_AFTER_SECONDS else "STALE/CHECK"
        )
        note = beat.note
    # mark pending if the worker's last heartbeat note disagrees with
    # the desired state (worker hasn't polled the new value yet)
    pending = False
    if beat is not None and last_seen_s is not None:
        observed_paused = "paused" in (note or "").lower()
        if desired and observed_paused:
            pending = True
        if (not desired) and (not observed_paused):
            pending = True
    status_rows.append({
        "asset": asset_name,
        "desired": "running" if desired else "paused",
        "process": process_status,
        "applied": "pending" if pending else "applied",
        "last_seen_s": last_seen_s,
        "note": note,
    })

st.dataframe(pd.DataFrame(status_rows), width="stretch",
             hide_index=True)
st.caption(
    "`applied=pending` means you just toggled this asset and the worker "
    "hasn't polled the new state yet. Workers poll once per tick "
    "(POLL_SECONDS, default 60s); the row will flip to `applied` on "
    "their next heartbeat. Workers run inside this dashboard process — "
    "no separate terminal needed. Set EMBED_WORKERS=0 only if you want "
    "to run them as standalone processes instead."
)

# Show that the embedded workers are alive (one supervising thread per
# asset). A dead row means run_worker() raised a fatal exception — see
# the dashboard's stdout/log for the traceback.
runner_rows = []
for asset_name in ASSET_ORDER:
    info = runner_status().get(asset_name, {})
    runner_rows.append({
        "asset": asset_name,
        "thread": "alive" if info.get("alive") else "DEAD",
        "started_at": (time.strftime("%Y-%m-%d %H:%M:%S",
                                     time.localtime(info["started_at"]))
                       if info.get("started_at") else "—"),
        "last_error": info.get("last_error", "") or "—",
    })
with st.expander("Embedded worker threads (advanced)", expanded=False):
    st.dataframe(pd.DataFrame(runner_rows), width="stretch",
                 hide_index=True)
    st.caption(
        "These are the supervising threads spawned by the dashboard. "
        "Each one runs the same loop as `python -m bots.worker`. If a "
        "row is DEAD, the worker hit a fatal error (bad credentials, "
        "feed broken). Fix the issue and restart the dashboard."
    )

# Per-asset toggles. Stable keys + on_change keep the widget consistent
# with the DB even when other actions (Run all / Stop all) change state.
toggle_cols = st.columns(len(ASSET_ORDER))
for col, asset_name in zip(toggle_cols, ASSET_ORDER):
    key = f"toggle_{asset_name}"
    current = desired_for(asset_name)
    # Sync session_state from the DB on every render BEFORE the widget
    # is created. Programmatic writes to session_state do NOT trigger
    # on_change, so this is safe; user clicks DO trigger it.
    st.session_state[key] = current
    col.toggle(
        f"{asset_name.upper()} worker",
        key=key,
        on_change=_on_toggle_change,
        args=(asset_name,),
        help=(f"On = the {asset_name} worker actively scans symbols and "
              "trades the matrix. Off = the worker stays alive but "
              "pauses fetching and trading. Takes up to one POLL_SECONDS "
              "interval to apply."),
    )

st.divider()


# ---- Per-asset symbol selection ---------------------------------------
def _on_symbol_change(asset_name: str, symbol: str):
    """User flipped a symbol checkbox. Persist the new value to the DB
    so the worker picks it up on its next tick."""
    key = f"sym_{asset_name}_{symbol}"
    included = bool(st.session_state[key])
    control_db.set_symbol_included(asset_name, symbol, included)
    load_tables.clear()


st.subheader("Symbols per asset")
st.caption(
    "Check the symbols you want each asset's worker to fetch and scan. "
    "Changes apply on the worker's next tick (POLL_SECONDS, default "
    "60s). If a combo is currently long on a symbol you uncheck, it is "
    "closed at the last marked price with reason `symbol_excluded`. "
    "Uncheck everything for an asset to keep its worker idle without "
    "stopping it. Add new tickers to the master list by editing "
    "`ASSETS[asset]['available_symbols']` in `core/matrix.py`."
)

symbol_cols = st.columns(len(ASSET_ORDER))
for col, asset_name in zip(symbol_cols, ASSET_ORDER):
    master = available_symbols(asset_name)
    inclusion = control_db.get_inclusion_map(asset_name)
    n_on = sum(1 for s in master if inclusion.get(s, True))
    col.markdown(f"**{asset_name.upper()}** &nbsp; "
                 f"<span style='color:#888'>{n_on}/{len(master)} "
                 f"included</span>", unsafe_allow_html=True)
    for symbol in master:
        key = f"sym_{asset_name}_{symbol}"
        # sync widget state from DB before render so external changes
        # (e.g. another browser tab) win, and so programmatic writes
        # here don't fire on_change
        st.session_state[key] = inclusion.get(symbol, True)
        col.checkbox(
            symbol, key=key,
            on_change=_on_symbol_change,
            args=(asset_name, symbol),
        )

st.divider()

# ---- Performance --- always renders, even with no/sparse data --------
metric = st.selectbox("Rank by", ["sharpe", "ret_pct"], index=0)

recs = []
have = set()
if not eq.empty:
    for (a, s, b), g in eq.groupby(["asset", "strategy", "behavior"]):
        sc = score(g, a)
        n_closed = int(((tr.asset == a) & (tr.strategy == s) &
                        (tr.behavior == b) & (tr.side == "sell")).sum())
        recs.append({
            "asset": a, "strategy": s, "behavior": b,
            "trades": n_closed, **sc,
            "rankable": n_closed >= MIN_TRADES,
        })
        have.add((a, s, b))

# placeholder rows for every (asset, strategy, behavior) we KNOW is
# configured but haven't logged anything for yet — so the pivot shape
# is visible from the very first render
for asset_name in ASSET_ORDER:
    for s in STRATEGY_ORDER:
        for b in BEHAVIOR_ORDER:
            if (asset_name, s, b) in have:
                continue
            recs.append({
                "asset": asset_name, "strategy": s, "behavior": b,
                "trades": 0, "ret_pct": 0.0, "sharpe": 0.0,
                "mdd_pct": 0.0, "hours": 0, "samples": 0,
                "rankable": False,
            })

df = pd.DataFrame(recs)

last_tick = float(eq["ts"].max()) if not eq.empty else None
total_combos = len(df)
combos_with_data = int((df["samples"] > 0).sum())
rankable = int(df["rankable"].sum())
header_cols = st.columns(4)
header_cols[0].metric("Configured combos", total_combos)
header_cols[1].metric("With data", combos_with_data)
header_cols[2].metric(f"Rankable (>={MIN_TRADES} trades)", rankable)
header_cols[3].metric(
    "Last tick",
    "never" if last_tick is None
    else f"{int(now - last_tick)}s ago")

if combos_with_data == 0:
    st.info(
        "No ticks have been logged yet. The grid below shows the shape "
        "of the experiment with placeholder zeros — values will start "
        "filling in within a few ticks of the first running worker."
    )

st.subheader("Per-asset pivot (rows = strategy, columns = behavior)")
st.caption(
    f"Cells = {metric}. Sharpe is annualized and comparable within an "
    f"asset class. Combos with < {MIN_TRADES} closed trades are shown "
    "but flagged as provisional — treat them as a preview of behavior, "
    "not as a leaderboard."
)

for asset_name in ASSET_ORDER:
    sub = df[df.asset == asset_name]
    if sub.empty:
        continue
    syms = control_db.get_included_symbols(asset_name)
    syms_label = (", ".join(syms) if syms
                  else "(none selected — uncheck/check above)")
    n_with_data = int((sub["samples"] > 0).sum())
    n_rank = int(sub["rankable"].sum())
    st.markdown(
        f"### {asset_name.upper()}  &nbsp; "
        f"<span style='color:#888;font-weight:normal'>scanning "
        f"{len(syms)} symbols: {syms_label} &nbsp;|&nbsp; "
        f"{n_with_data}/{len(sub)} combos with data &nbsp;|&nbsp; "
        f"{n_rank} rankable</span>",
        unsafe_allow_html=True,
    )
    pivot = sub.pivot_table(
        index="strategy", columns="behavior",
        values=metric, aggfunc="first"
    ).reindex(index=STRATEGY_ORDER, columns=BEHAVIOR_ORDER)
    st.dataframe(pivot.style.format("{:.2f}", na_rep="—"),
                 width="stretch")
    rk = sub[sub.rankable]
    if not rk.empty:
        best = rk.sort_values(metric, ascending=False).iloc[0]
        st.success(
            f"Best rankable combo on {asset_name}: "
            f"`{best.strategy}` + `{best.behavior}` "
            f"({metric}={best[metric]:.2f}, trades={best.trades})"
        )
    elif n_with_data > 0:
        prov = sub[sub["samples"] > 0].sort_values(
            metric, ascending=False).iloc[0]
        st.info(
            f"No rankable combo yet on {asset_name}. Provisional "
            f"leader: `{prov.strategy}` + `{prov.behavior}` "
            f"({metric}={prov[metric]:.2f}, trades={prov.trades}). "
            f"Need {MIN_TRADES} closed trades to be trustworthy."
        )

st.divider()
st.subheader("Current positions per combo")
st.caption("Which symbol each combo is currently long on (— = flat).")
pos_rows = []
for asset_name in ASSET_ORDER:
    for s in STRATEGY_ORDER:
        for b in BEHAVIOR_ORDER:
            sym, qty = current_position_for(asset_name, s, b)
            pos_rows.append({
                "asset": asset_name,
                "strategy": s,
                "behavior": b,
                "symbol": sym if sym else "—",
                "qty": round(qty, 6) if qty else 0.0,
            })
st.dataframe(pd.DataFrame(pos_rows), width="stretch",
             hide_index=True)

# Baseline = `tp` (take-profit only, no stop-loss). The behavior axis
# now asks "does ADDING a stop-loss help — fixed or trailing?" so the
# rollup compares tp_sl and tp_trail to tp.
BASELINE_BEHAVIOR = "tp"
st.subheader(
    f"Does the stop-loss help? (avg vs. {BASELINE_BEHAVIOR}, per asset)"
)
hr = []
for asset_name in ASSET_ORDER:
    sub = df[(df.asset == asset_name) & (df["samples"] > 0)]
    if sub.empty:
        for behavior in BEHAVIOR_ORDER:
            if behavior == BASELINE_BEHAVIOR:
                continue
            hr.append({
                "asset": asset_name,
                "behavior": behavior,
                f"avg_{metric}_vs_{BASELINE_BEHAVIOR}": None,
            })
        continue
    base_rows = sub[sub.behavior == BASELINE_BEHAVIOR]
    base = base_rows[metric].mean() if not base_rows.empty else 0.0
    for behavior in BEHAVIOR_ORDER:
        if behavior == BASELINE_BEHAVIOR:
            continue
        beh_rows = sub[sub.behavior == behavior]
        avg = (beh_rows[metric].mean() - base) if not beh_rows.empty else None
        hr.append({
            "asset": asset_name,
            "behavior": behavior,
            f"avg_{metric}_vs_{BASELINE_BEHAVIOR}": (
                round(avg, 3) if avg is not None else None),
        })
st.dataframe(pd.DataFrame(hr), width="stretch", hide_index=True)

st.warning(
    "Compare within an asset class, use annualized Sharpe over raw return, "
    "ignore non-rankable rows for any real decision, and treat every "
    "winner as a hypothesis to re-confirm out of sample."
)
