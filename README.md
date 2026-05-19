What is
each bot to run all four strategies across all three asset classes separately, so they can compare how each strategy performs within crypto, within forex, and within stocks individually. It's not about spreading strategies across different bots, but rather testing the full strategy suite against each market type to identify which approaches work best for each.
And beyond just the strategies themselves, execution behaviors like trailing stops versus fixed stops are another configurable layer that should also vary by asset class, since what works as a risk management approach in one market might not suit another. The practical constraint: running 4 strategies × 3 behaviors × 3 asset classes means 36 combos that all need to share venue connections (crypto via Binance Spot Testnet, forex via OANDA, stocks via Alpaca) without their equity tracks colliding inside a single demo account.

1. Strategy × Behavior × Asset
A system that runs every strategy, under every execution behavior, on
every asset class at the same time on simulated (paper) money, then
shows you a pivot of what works for which kind of market — including
whether behaviors like a trailing stop help or hurt per asset.

Written for someone who knows Python but is new to trading. Demo only.
Not investment advice.

Not "4 bots = 4 strategies, one asset each."
a matrix. Each strategy is tested on crypto AND forex
AND stocks, so you can conclude "trend works on crypto but not forex."
Plus a dimension for behaviors. A strategy decides direction; a
behavior (the take-profit / stop-loss rule) is a separate execution
rule that can force an exit. Every behavior in the matrix sets a
take-profit; the stop-loss axis is what varies, because "a trailing
stop helps momentum on crypto but hurts it on forex" is exactly the
kind of thing you want to discover.

So the experiment is the cartesian product:

   STRATEGIES   ×   BEHAVIORS        ×   ASSET CLASSES
   (sma,             (tp,                 (crypto,
    rsi,              tp_sl,               forex,
    breakout,         tp_trail)            stocks)
    momentum)
   = 4            ×   3                ×   3   = 36 combinations TOTAL
   = 12 per asset (each scans a configurable list of symbols)

The three behaviors:
  - tp        : take-profit only, no stop-loss. Lets winners run all
                the way to TP; an adverse move can ride to zero.
  - tp_sl     : take-profit + FIXED stop-loss at entry. Symmetric
                risk/reward bracket — classic "set and forget".
  - tp_trail  : take-profit + TRAILING stop-loss that starts below
                entry and ratchets UP with the high-water mark. If
                price runs toward TP and reverses without touching
                it, you still exit on the trail with locked-in
                profit instead of giving the move back.

The key architectural decision
If 36 combos all sent real orders to one demo account, they would fight
over the same position (the exchange shows one net position per symbol)
and per-combo profit attribution would be impossible.

So every combination has its own internal paper account. The system
pulls real live market data from each venue — including the official
Binance Spot Testnet demo account for crypto — but simulates fills
internally per combo. This means:

Perfect, isolated P&L attribution per (strategy × behavior × asset).
Unlimited combinations with no extra accounts and no collisions.
The whole lab is just 3 worker processes (one per asset class) +
a dashboard — it runs on a $5 VPS.
Trade-off (stated honestly): this is a forward simulation, not the
demo matching engine. For comparing strategies against each other on
identical data that is the methodologically cleaner choice. When you
later have a winner, you promote just that one to real demo orders and
then tiny real capital — reusing the deployment patterns from the earlier
guides. This lab's single job is the clean comparison.

1b. The combo and the symbol axis (important)

Each combo is (asset × strategy × behavior). Within each asset class,
the SAME combo continuously SCANS every configured symbol for entry
signals — it is not split into one-combo-per-symbol. So adding symbols
makes each combo smarter (more opportunities to find a setup) but does
NOT inflate the combo count or fragment P&L attribution.

Position model — SINGLE position per combo:
  - Flat:    each tick, the combo asks its strategy about every symbol
             in order; the first symbol that says "buy" gets the trade.
  - Long:    only the currently-held symbol is consulted (mark, behavior
             exit, strategy exit). Other symbols are ignored until the
             combo goes flat again.

That keeps the PaperBroker simple (one cash + one position) and the
equity attribution honest (one combo = one equity curve). If you ever
want concurrent positions across symbols within one combo (true
portfolio), that's a separate, bigger change.

2. Architecture
            ┌──────────────── ONE cheap Linux VPS ─────────────────┐
            │                                                       │
 live data  │  worker-crypto ─ Binance TESTNET (BTC,ETH,SOL) ┐      │
 (real      │     └ 12 (strat×beh) combos, each SCANS all    │      │
  prices)   │       3 symbols, holds 1 position max          │      │
            │  worker-forex  ─ fetch EUR_USD,GBP_USD,…    ── ┼──▶ results.db
            │     └ 12 combos, each scans all forex syms     │   (SQLite)
            │  worker-stocks ─ fetch SPY,QQQ,AAPL each ──── ─┘      │
            │     └ 12 combos, each scans all stocks syms     ▼     │
            │  each combo = its own PaperBroker        dashboard:8501│
            │  3 processes total, restart:always     (per-asset pivot│
            │                                         + live position│
            │                                          table)        │
            └───────────────────────────────────────────────────────┘
File	Role
core/feed.py + core/feeds/*	Data per asset (crypto=Binance Spot Testnet via ccxt, forex=OANDA, stocks=Alpaca)
core/strategy.py, strategies/*	Direction logic (want long / flat) — symbol-agnostic
core/behavior.py	The behavior axis: TakeProfit, HardStop, TrailingStop, Composite, AllOf
core/paper.py	One isolated simulated account per combo, with per-asset cost in bps
core/matrix.py	The experiment: edit STRATEGIES, BEHAVIORS, ASSETS (symbols + cost)
core/worker.py	One per asset: fetch all symbols once → each combo scans → log tagged
core/results.py	Shared SQLite, every row tagged (asset, symbol, strategy, behavior)
dashboard.py	Per-asset pivot + live positions table + "does the stop-loss help?"
selftest.py	Offline matrix test — run first
3. Accounts you need
Sign-up screens change; these are the concepts. All three asset
classes run on official DEMO/PAPER environments — no real money is
ever at risk. Create keys with no withdrawal permission. Check each
provider's current docs if a screen differs.

Crypto (Binance Spot Testnet — DEMO account):
  - Go to https://testnet.binance.vision and log in with GitHub
    (the only auth method the testnet supports).
  - Click "Generate HMAC_SHA256 Key", give it a label, and copy the
    API Key and Secret Key — the Secret is shown ONCE.
  - The testnet seeds your account with virtual balances of BTC,
    USDT, BNB, etc. You can place real (demo) orders against the
    live testnet matching engine. The lab uses these credentials
    for live market data; fills stay simulated per-combo for clean
    P&L attribution.
Forex (OANDA practice): register a free demo/practice account,
then in account settings → API generate a personal access token, and
note your Account ID (looks like 101-001-1234567-001). These are
used for data only.
Stocks (Alpaca paper): sign up free, switch to Paper Trading,
generate paper API key + secret (used for data only).
Optional — Telegram alerts: message @BotFather, /newbot, copy the
token; message your bot, then open
https://api.telegram.org/bot<TOKEN>/getUpdates to read your chat id.
Put the Binance Testnet / OANDA / Alpaca / Telegram values into
.env.shared. ALL THREE asset feeds now require credentials.

4. Run locally first (do not skip)

The lab ships with a master AVAILABLE list of symbols per asset (see
ASSETS in core/matrix.py). The dashboard shows that master list as
checkboxes per asset; you pick the subset each worker actually fetches
and scans. Defaults (all available, included):
  crypto -> BTC/USDT, ETH/USDT, SOL/USDT, BNB/USDT, XRP/USDT,
            ADA/USDT, DOGE/USDT, LTC/USDT     (Binance testnet pairs)
  forex  -> EUR_USD, GBP_USD, USD_JPY, AUD_USD, USD_CAD, USD_CHF,
            NZD_USD, EUR_GBP
  stocks -> SPY, QQQ, AAPL, MSFT, GOOGL, AMZN, NVDA, TSLA
Combo count per asset = 4 strategies x 3 behaviors = 12. The symbols
list is NOT a new dimension — each combo continuously scans every
selected symbol for the next entry (see section 1b). Add new tickers
to ASSETS[asset]["available_symbols"] in core/matrix.py and they show
up as a new checkbox next dashboard load. For crypto, confirm any new
ticker exists on testnet.binance.vision first — testnet has a smaller
symbol universe than production Binance.

cd trading-lab
python -m venv .venv
# Linux/Mac:   source .venv/bin/activate
# Windows:     .venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 4.1 Offline matrix test — no accounts, no network
python selftest.py            # must end with "ALL GOOD"
#   You will SEE the point of the lab in its output: e.g. sma+tp loses
#   in 'chop' but sma+tp_trail cuts the loss a lot — behavior matters
#   per regime. That is exactly what you set out to measure.

# 4.2 Real credentials
cp .env.shared.example .env.shared   # edit: Binance testnet + OANDA +
                                     # Alpaca (+ optional Telegram)
#   (.env.shared is gitignored. NEVER commit real keys.)

# 4.3 Start the lab (single command, no separate worker terminals)
streamlit run dashboard.py     # http://localhost:8501
# The dashboard auto-spawns one worker thread per asset (crypto, forex,
# stocks). Use the "Worker Control" panel in the UI to start/stop each
# worker. Pick which symbols each one fetches in the "Symbols per
# asset" panel. There is NO `python -m bots.worker` step in the normal
# flow.

# 4.4 (Optional) Dry-run without real credentials
DRY_RUN=1 streamlit run dashboard.py    # uses SyntheticFeed for all 3

# 4.5 (Advanced) Run ONE worker standalone, bypassing the dashboard
#     Only use this if you need to scale workers across hosts.
#     Set EMBED_WORKERS=0 on the dashboard to disable its in-process
#     workers, otherwise both will write to the same DB.
export ASSET=crypto                                 # crypto | forex | stocks
export SYMBOLS="BTC/USDT,ETH/USDT,SOL/USDT"        # optional pin
export DB_PATH=data/results.db
python -m bots.worker
The dashboard renders the full experiment grid IMMEDIATELY — even before
any worker has logged a single tick — so you can see the shape of the
matrix on day zero. Cells fill in as data arrives, with two clear stages:
  - "provisional" (data exists but < MIN_TRADES closed trades) — shown
    in the pivot but flagged as not yet trustworthy
  - "rankable" (>= MIN_TRADES) — eligible for the per-asset leader and
    the "does the behavior help?" rollup

The header strip shows: configured combos / with data / rankable / last
tick age, so it is obvious at a glance whether the workers are alive.

A "Current positions per combo" table shows which symbol each combo is
currently long on (or — for flat), so you can see the symbol scanner
in action.

Worker Control: Run all three / Stop all, or toggle each asset
individually. The status table has an `applied` column that says
`pending` right after you click — it flips to `applied` on the worker's
next heartbeat (within POLL_SECONDS, default 60s). "Stop" means the
worker thread stays alive but pauses fetching and trading until you
toggle it back on. The toggle state is persisted in SQLite, so it
survives dashboard refreshes AND restarts.

Symbols per asset: a checkbox grid right under Worker Control lists the
master AVAILABLE symbols for each asset (from ASSETS in core/matrix.py).
Check the ones you want the worker to fetch and scan. Changes apply on
the worker's next tick. If you uncheck a symbol a combo is currently
long on, it is closed at the last marked price (reason
`symbol_excluded`) so equity doesn't drift on data we no longer fetch.
Unchecking everything for an asset keeps its worker idle but alive.

5. Deploy 24/7 on a cheap VPS (Hetzner / DigitalOcean / etc.)

The lab is designed to run on the smallest VPS class your provider sells:
  - 1 vCPU, 2 GB RAM is plenty (3 workers + dashboard, all idle most of
    the second). On Hetzner that is CPX11 (~$5/mo). On DO, the
    "Basic / Regular" $6/mo droplet.
  - Ubuntu 22.04 or 24.04 LTS.

# 5.1 Provision the VPS
Create an Ubuntu VPS in your provider's UI. Add your SSH public key. Note
the IPv4 address. Then on your LAPTOP:

  ssh root@VPS_IP

# 5.2 Bootstrap the box (one time)
  # create a non-root user and lock down SSH a little
  adduser --disabled-password --gecos "" lab
  usermod -aG sudo lab
  rsync --archive --chown=lab:lab ~/.ssh /home/lab/

  # firewall: only SSH from the internet
  apt-get update && apt-get install -y ufw git curl sqlite3
  ufw allow OpenSSH
  ufw --force enable

  # Docker (official one-liner)
  curl -fsSL https://get.docker.com | sh
  usermod -aG docker lab

  # log back in as the new user
  exit
  ssh lab@VPS_IP

# 5.3 Put the project on GitHub
On your laptop, create a private GitHub repository. This folder currently
does not need secrets or local data in git; `.gitignore` already excludes
`.env.shared`, `data/`, local virtualenvs, and SQLite files.

  git init
  git add .
  git commit -m "Initial trading lab deploy"
  git branch -M main
  git remote add origin git@github.com:YOUR_USER/trading-lab.git
  git push -u origin main

If you prefer the GitHub web UI, create the empty private repo there first,
then use the `git remote add` / `git push` commands above.

# 5.4 Clone from GitHub on the VPS
Create a read-only deploy key on the VPS:

  ssh lab@VPS_IP
  ssh-keygen -t ed25519 -C "trading-lab-vps" -f ~/.ssh/trading_lab_deploy
  cat ~/.ssh/trading_lab_deploy.pub
  printf "Host github.com\n  IdentityFile ~/.ssh/trading_lab_deploy\n  IdentitiesOnly yes\n" >> ~/.ssh/config
  chmod 600 ~/.ssh/config

In GitHub: repository Settings -> Deploy keys -> Add deploy key. Paste the
public key, leave "Allow write access" unchecked.

Then on the VPS:

  git clone git@github.com:YOUR_USER/trading-lab.git /home/lab/trading-lab

Fallback without GitHub, from your laptop:

  rsync -av \
    --exclude .git --exclude .venv --exclude Lab_venv --exclude __pycache__ \
    --exclude data --exclude .env.shared \
    ./ lab@VPS_IP:/home/lab/trading-lab/

# 5.5 Configure secrets on the SERVER (not in git)
  ssh lab@VPS_IP
  cd /home/lab/trading-lab
  cp .env.shared.example .env.shared
  nano .env.shared           # paste OANDA / Alpaca / Telegram / heartbeat
                             # set DASHBOARD_PASSWORD to something strong

# 5.6 Bring the lab up
  docker compose up -d --build
  docker compose ps          # single `lab` service should be "healthy"
  docker compose logs -f lab # streams dashboard + every worker thread

  # restart: always brings the lab back after crash or reboot. The
  # compose file also rotates logs (10 MB x 5) so a wedged worker
  # thread can never fill the disk. The lab is ONE container that runs
  # streamlit + 3 worker threads in-process.

# 5.7 Reach the dashboard SAFELY
The compose file binds Streamlit to 127.0.0.1 ONLY on the VPS — it is
NOT reachable from the public internet. From your laptop:

  ssh -L 8501:localhost:8501 lab@VPS_IP
  # then open http://localhost:8501 in your browser

If you really want public access (e.g. team review), do all THREE of:
  1. set DASHBOARD_PASSWORD in .env.shared (already wired through),
  2. change the dashboard ports line in docker-compose.yml from
     "127.0.0.1:8501:8501" to "8501:8501", and
  3. ufw allow from YOUR_OFFICE_IP to any port 8501
     (never `ufw allow 8501` unrestricted — that is the public internet).

# 5.8 Operate
  docker compose ps                          # lab service health
  docker compose logs -f lab                 # tail dashboard + workers
  docker compose restart lab                 # restart the whole thing
  docker compose down                        # stop
  docker compose up -d --build               # rebuild after code changes
  docker system prune -f                     # reclaim old images

To deploy a new GitHub commit:

  cd /home/lab/trading-lab
  git pull --ff-only
  docker compose up -d --build

For the day-to-day operating switch, leave Docker Compose running and use
the dashboard toggles. The single `lab` container stays supervised by
Docker; SQLite stores the desired run/pause state and the per-asset
symbol selections so they survive dashboard refreshes and container
restarts.

# 5.9 Backups (tiny but worth doing)
SQLite is the only stateful thing. Cron-snapshot it nightly:
  echo "0 3 * * * sqlite3 /home/lab/trading-lab/data/results.db \".backup /home/lab/results-\$(date +\\%F).db\" && find /home/lab -name 'results-*.db' -mtime +14 -delete" | crontab -

6. Reading the results — answering "what works for which kind"
Open the dashboard. It is built around your question:

Per-asset pivot table. For CRYPTO, FOREX, STOCKS separately: rows =
strategy, columns = behavior, cell = risk-adjusted score (Sharpe by
default). You read straight off it: on crypto, which strategy+behavior
is best; on forex, is it different?
"Best rankable combo per asset" is printed for each kind.
"Does the stop-loss help?" table: average effect of tp_sl /
tp_trail vs. tp (take-profit only), per asset class. This is the
direct answer to "does adding a stop-loss help on crypto but hurt on
forex — and if it helps, is fixed or trailing better?"
Discipline the dashboard enforces (don't fight it):

Combos with < 20 trades are excluded from ranking — too small a
sample to mean anything.
Compare Sharpe, not raw return, and compare within an asset
class (crypto volatility dwarfs forex/stocks; raw P&L across them is
meaningless).
Per-asset cost assumptions (core/matrix.py → ASSETS) materially
change who wins. They are explicit on purpose — tune them honestly;
optimistic costs manufacture fake winners.
The honest caveat (unchanged and important): running a big matrix and
picking the top cell is the textbook multiple-testing trap — with 36
combos, some will look great by luck. A leader here is a hypothesis.
Before it becomes "my final strategy": check it leads across sub-periods,
beats the others by more than noise, and then re-confirm it over a fresh
out-of-sample period (and eventually tiny real capital). Finding that a
demo leader fails re-confirmation is the system working — it caught a
false positive for free.

7. Customising the experiment
Everything is in core/matrix.py — three dictionaries:

STRATEGIES: add yours (subclass Strategy, implement decide()).
BEHAVIORS: tune the TP/SL percentages, swap TrailingStop for AllOf,
or add a new Behavior subclass — your "bot behavior" axis is
first-class here. Every entry in the dict becomes a new column in the
per-asset pivot.
ASSETS: extend the `available_symbols` list for a kind (e.g. add
"DOGE/USDT" to crypto), tune `cost_bps`, or add another asset class by
adding a feed. New tickers appear as checkboxes in the dashboard's
"Symbols per asset" panel; you pick which ones the worker fetches —
without editing docker-compose.yml or env vars.
Then python selftest.py, rsync up, docker compose up -d --build.
The workers rebuild the full matrix automatically.

8. Maintenance quick reference
docker compose ps                          # lab service health
docker compose logs -f lab                 # tail dashboard + workers
docker compose restart lab                 # restart everything
docker compose down                        # stop
docker compose up -d --build               # (re)start after code changes
Watch the dashboard "Worker Control" panel: desired=paused is a manual
pause from the UI; STALE/CHECK means a worker thread went silent (check
the "Embedded worker threads" expander for the last error). With
Telegram set you get a daily per-worker heartbeat — silence is the
warning sign, not error messages.

9. File map
trading-lab/
├── core/
│   ├── feed.py            # data interface + MarketData
│   ├── feeds/             # crypto (Binance testnet via ccxt) / forex (oanda) / stocks (alpaca)
│   ├── strategy.py        # strategy base class
│   ├── strategies? -> ../strategies/  (4 strategies)
│   ├── behavior.py        # THE behavior axis (TakeProfit, HardStop, TrailingStop, Composite)
│   ├── paper.py           # isolated paper account per combo
│   ├── matrix.py          # the experiment definition (edit me)
│   ├── worker.py          # one worker per asset = whole matrix for it
│   ├── results.py         # shared tagged SQLite
│   ├── config.py          # per-worker env config
│   └── alerts.py          # telegram + heartbeat
├── strategies/            # sma / rsi / breakout / momentum
├── bots/worker.py         # legacy single-asset entrypoint (advanced)
├── core/worker_runner.py  # in-dashboard supervisor (default path)
├── dashboard.py           # per-asset pivot cockpit
├── selftest.py            # offline matrix test (run first)
├── Dockerfile
├── docker-compose.yml     # single `lab` service (dashboard + workers)
├── requirements.txt
└── .env.shared.example
Demo simulation only. Build the comparison discipline before you ever
risk a real cent.
