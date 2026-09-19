# decipher-trader

Self-hosted crypto trading system built on NautilusTrader and Hyperliquid. Runs strategies against Hyperliquid testnet by default; requires an explicit override plus a per-strategy human promotion before it will touch mainnet.

## Prerequisites

- Docker Engine 24+ with Compose v2.
- A `.env` file (copy from `.env.example`) with:
  - `OPERATOR_TOKEN` — long random string. Every mutating REST call needs it.
  - `DASHBOARD_SESSION_SECRET` — 32+ char random string.
  - `DASHBOARD_OPERATOR_USERNAME` / `DASHBOARD_OPERATOR_PASSWORD_BCRYPT` — dashboard login. Generate the bcrypt hash locally with any bcrypt tool.
  - `HYPERLIQUID_TESTNET_PRIVATE_KEY`, `HYPERLIQUID_TESTNET_ACCOUNT_ID` for paper mode.
  - `HYPERLIQUID_MAINNET_PRIVATE_KEY`, `HYPERLIQUID_MAINNET_ACCOUNT_ID` for live mode.

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

## End-to-end smoke test

See `e2e/smoke.py`. Requires Docker; drives the full paper→live promotion flow with the operator token, then kills. The smoke now also boots `nautilus-runner`, verifies it is running before the kill event, and asserts it exits cleanly (exit code 0) within 60 seconds of `POST /kill_all`.

**This smoke connects to real Hyperliquid testnet.** Accept network flake and retry on transient failures.

### Prerequisites

- `.env` populated with at minimum:
  - `OPERATOR_TOKEN` — long random string.
  - `HYPERLIQUID_TESTNET_PRIVATE_KEY` — real Hyperliquid testnet private key (runner will fail to boot without it).
  - `HYPERLIQUID_TESTNET_ACCOUNT_ID` — your testnet account ID.

### How to run smoke.py

`smoke.py` talks to control-plane on `localhost:8000` and back-dates `paper_started_at` by opening the SQLite file at `/tmp/decipher-e2e/decipher.sqlite3`. The `e2e/docker-compose.smoke.yml` overlay publishes the port, bind-mounts the SQLite file to that host path, disables SQLite WAL (cross-OS WAL/SHM locking breaks host-side reads on macOS Docker Desktop), and overrides the runner's `restart: on-failure:3` to `restart: "no"` so a clean exit stays visible to `docker compose ps`.

```bash
cp .env.example .env
# Edit .env: set OPERATOR_TOKEN, HYPERLIQUID_TESTNET_PRIVATE_KEY, HYPERLIQUID_TESTNET_ACCOUNT_ID
mkdir -p /tmp/decipher-e2e   # on macOS Docker Desktop, use $HOME/.decipher-e2e and symlink
docker compose -f docker-compose.yml -f docker-compose.paper.yml -f e2e/docker-compose.smoke.yml up -d
python3 -m venv /tmp/decipher-e2e-venv
/tmp/decipher-e2e-venv/bin/pip install httpx
OPERATOR_TOKEN=$(grep -E '^OPERATOR_TOKEN=' .env | cut -d= -f2) /tmp/decipher-e2e-venv/bin/python e2e/smoke.py
docker compose -f docker-compose.yml -f docker-compose.paper.yml -f e2e/docker-compose.smoke.yml down
```

On success the script prints `SMOKE OK (with runner)`.
