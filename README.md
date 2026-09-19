# decipher-trader

Self-hosted crypto trading system built on NautilusTrader and Hyperliquid. Runs strategies against Hyperliquid testnet by default; requires an explicit override plus a per-strategy human promotion before it will touch mainnet.

## Prerequisites

- Docker Engine 24+ with Compose v2.
- A Hyperliquid **API wallet** (not your main wallet). Generate at
  https://app.hyperliquid-testnet.xyz/API (testnet) or
  https://app.hyperliquid.xyz/API (mainnet): log in with your funded wallet,
  click "Generate", copy the API-wallet private key, then sign the on-chain
  approval. The API wallet is trade-only — it cannot withdraw — so exposing
  its key in `.env` is safe.
- A `.env` file (copy from `.env.example`) with:
  - `OPERATOR_TOKEN` — long random string. Every mutating REST call needs it.
  - `DASHBOARD_SESSION_SECRET` — 32+ char random string.
  - `DASHBOARD_OPERATOR_USERNAME` / `DASHBOARD_OPERATOR_PASSWORD_BCRYPT` — dashboard login. Generate the bcrypt hash locally with any bcrypt tool.
  - `HYPERLIQUID_TESTNET_PRIVATE_KEY` — API wallet private key (0x-prefixed 64 hex chars).
  - `HYPERLIQUID_TESTNET_ACCOUNT_ID` — Nautilus-side account label in `HYPERLIQUID-<tag>` format (e.g. `HYPERLIQUID-TESTNET-001`). This is a local identifier, **not** your wallet address — the wallet identity comes from the private key.
  - `HYPERLIQUID_MAINNET_PRIVATE_KEY`, `HYPERLIQUID_MAINNET_ACCOUNT_ID` — same shape for live mode.

## Run in paper mode (default)

```bash
docker compose -f docker-compose.yml -f docker-compose.paper.yml up --build
```

Visit `http://localhost:3000`, log in, and register a strategy. Nautilus-runner will only see strategies with `status=paper` and will trade them against Hyperliquid testnet.

## Promote to live mode

Live mode requires that at least one strategy has already been promoted through the dashboard (`Promote to live` on its detail page). The promote button is gated in the backend: it will refuse until the strategy has been in paper mode for at least 14 days with no snapshot showing drawdown greater than its `max_daily_loss`.

Only after promotion, stop the paper stack and start the live stack:

```bash
docker compose -f docker-compose.yml -f docker-compose.paper.yml down
docker compose -f docker-compose.yml -f docker-compose.live.yml up --build
```

The runner refuses to start in live mode if any `status=live` row lacks `promoted_at` / `promoted_by` metadata.

## Kill switch

- Global: dashboard → Settings → KILL ALL LIVE STRATEGIES.
- Per-strategy: dashboard → strategy detail → Demote to paper.
- Programmatic: `POST /kill_all` on control-plane with `Authorization: Bearer $OPERATOR_TOKEN`.

Both paths demote the row(s) and broadcast a `kill_all` event over the websocket, which nautilus-runner honours by stopping the node.

## Backup and portability

State is in two named docker volumes: `decipher-db` (SQLite) and `decipher-cache` (backtest data). Back up:

```bash
docker run --rm -v decipher-trader_decipher-db:/data -v $PWD:/backup alpine tar czf /backup/decipher-db.tgz -C /data .
```

Restore on any host by untar-ing into the same volume before `docker compose up`.

## Chaos tests

Two shallow chaos scenarios that test runner and control-plane failure paths against real Hyperliquid testnet. No production code changes are needed — these tests verify existing behavior.

**Scenario A (`kill_runner`)**: sends `docker kill` (SIGKILL) to the runner container, asserts it exits with code 137, confirms the control-plane is still healthy, and checks that no audit rows were lost.

**Scenario B (`kill_control_plane`)**: stops the control-plane container and asserts the runner self-terminates cleanly (exit code 0) within ~10 seconds via the heartbeat-miss path. The chaos overlay sets `HEARTBEAT_INTERVAL_SECS=2` and `HEARTBEAT_MISS_LIMIT=2` so the runner fires `on_miss` (→ `node.stop()`) after ~4-6 s of control-plane absence instead of the default 60+ s.

### Prerequisites (same as smoke)

- `.env` populated with at minimum:
  - `OPERATOR_TOKEN` — long random string.
  - `HYPERLIQUID_TESTNET_PRIVATE_KEY` — real Hyperliquid testnet private key.
  - `HYPERLIQUID_TESTNET_ACCOUNT_ID` — your testnet account ID.
- `httpx` installed in the Python environment running `chaos.py`.

These tests connect to real Hyperliquid testnet and are local-only, single-user development tests — they are not run in CI.

The bind-mount source honours `SMOKE_DATA_DIR` (default `/tmp/decipher-e2e`). On **macOS Docker Desktop**, set `SMOKE_DATA_DIR=$HOME/.decipher-e2e` — same reason as smoke.py above.

### How to run chaos.py

```bash
export SMOKE_DATA_DIR=$HOME/.decipher-e2e   # macOS only; skip on Linux
mkdir -p "$SMOKE_DATA_DIR"

# Scenario A
docker compose -f docker-compose.yml -f docker-compose.paper.yml -f e2e/docker-compose.chaos.yml up -d
OPERATOR_TOKEN=$(grep -E '^OPERATOR_TOKEN=' .env | cut -d= -f2) python e2e/chaos.py A
docker compose -f docker-compose.yml -f docker-compose.paper.yml -f e2e/docker-compose.chaos.yml down

# Scenario B (fresh stack)
docker compose -f docker-compose.yml -f docker-compose.paper.yml -f e2e/docker-compose.chaos.yml up -d
OPERATOR_TOKEN=$(grep -E '^OPERATOR_TOKEN=' .env | cut -d= -f2) python e2e/chaos.py B
docker compose -f docker-compose.yml -f docker-compose.paper.yml -f e2e/docker-compose.chaos.yml down
```

On success the script prints `CHAOS A OK`, `CHAOS B OK`, and (when run together) `CHAOS OK`.

## End-to-end smoke test

See `e2e/smoke.py`. Requires Docker; drives the full paper→live promotion flow with the operator token, then kills. The smoke now also boots `nautilus-runner`, verifies it is running before the kill event, and asserts it exits within 60 seconds of `POST /kill_all` with an accepted shutdown code (`0`, `133`, `137`, or `143` — see [known quirks](#known-quirks-nautilus-rc5) below).

**This smoke connects to real Hyperliquid testnet.** Accept network flake and retry on transient failures.

### Prerequisites

- `.env` populated with at minimum:
  - `OPERATOR_TOKEN` — long random string.
  - `HYPERLIQUID_TESTNET_PRIVATE_KEY` — real Hyperliquid testnet private key (runner will fail to boot without it).
  - `HYPERLIQUID_TESTNET_ACCOUNT_ID` — your testnet account ID.

### How to run smoke.py

`smoke.py` talks to control-plane on `localhost:8000` and back-dates `paper_started_at` by opening the SQLite file directly on disk. The `e2e/docker-compose.smoke.yml` overlay publishes the port, bind-mounts the SQLite file, disables SQLite WAL (cross-OS WAL/SHM locking breaks host-side reads on macOS Docker Desktop), and overrides the runner's `restart: on-failure:3` to `restart: "no"` so a clean exit stays visible to `docker compose ps`.

The bind-mount source and the host-side SQLite path are both controlled by the `SMOKE_DATA_DIR` environment variable (default `/tmp/decipher-e2e`). On **macOS Docker Desktop**, `/tmp` lives inside the Docker VM and is invisible to host Python, so set `SMOKE_DATA_DIR=$HOME/.decipher-e2e` (under `/Users`, which Docker Desktop shares by default). On Linux the default is fine.

```bash
cp .env.example .env
# Edit .env: set OPERATOR_TOKEN, HYPERLIQUID_TESTNET_PRIVATE_KEY, HYPERLIQUID_TESTNET_ACCOUNT_ID
export SMOKE_DATA_DIR=$HOME/.decipher-e2e   # macOS only; skip on Linux
mkdir -p "$SMOKE_DATA_DIR"
docker compose -f docker-compose.yml -f docker-compose.paper.yml -f e2e/docker-compose.smoke.yml up -d
python3 -m venv /tmp/decipher-e2e-venv
/tmp/decipher-e2e-venv/bin/pip install httpx
OPERATOR_TOKEN=$(grep -E '^OPERATOR_TOKEN=' .env | cut -d= -f2) /tmp/decipher-e2e-venv/bin/python e2e/smoke.py
docker compose -f docker-compose.yml -f docker-compose.paper.yml -f e2e/docker-compose.smoke.yml down
```

On success the script prints `SMOKE OK (with runner)`.

## Known quirks (Nautilus rc5)

- **Runner exit code `133` on `kill_all` / heartbeat-loss shutdown.** `nautilus-trader==2.0.0rc5`'s Rust runtime raises `SIGTRAP` (exit code `133`) when `node.stop()` is invoked from a non-main thread — which is exactly how both `kill_listener_loop` and `heartbeat_loop.on_miss` invoke it. The process still terminates and stops trading as intended; the exit code is a shutdown-path quirk rather than a behaviour defect. The smoke and chaos scripts accept `{0, 133, 137, 143}` for this reason.

  Fix paths for a future release:
  - Rewire shutdown so the main thread performs `node.stop()` in response to a threading event set by the listener/heartbeat.
  - Upgrade to a Nautilus release with cleaner cross-thread shutdown once one is available.

- **macOS Docker Desktop bind-mount visibility.** `/tmp` inside the Docker VM is not the same directory as `/tmp` on the macOS host, and Docker Desktop does not follow host-side symlinks when resolving compose bind-mount sources. The smoke and chaos overlays honour `SMOKE_DATA_DIR` (default `/tmp/decipher-e2e`); set `SMOKE_DATA_DIR=$HOME/.decipher-e2e` on macOS so both host Python and the container see the same file.
