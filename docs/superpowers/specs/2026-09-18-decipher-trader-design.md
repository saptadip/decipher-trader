# decipher-trader — Design Spec

- **Status:** Draft (awaiting user review)
- **Date:** 2026-09-18
- **Owner:** Saptadip Sarkar
- **Repository:** `decipher-trader/` (sibling to upstream `nautilus_trader/`, never modifies it)

---

## 1. Purpose and Scope

Build a portable, self-hosted crypto trading system that:

1. Runs one or more trading strategies against the Hyperliquid exchange.
2. Uses NautilusTrader as the execution engine (consumed as a pinned pip dependency, never forked).
3. Provides an LLM agent (Anthropic Claude) that authors new strategies and allocates capital across them.
4. Enforces a hard gate: no strategy touches real money on Hyperliquid mainnet until (a) it has forward-run on Hyperliquid testnet for at least 14 continuous days, and (b) the human operator explicitly promotes it from `paper` to `live` through the dashboard.
5. Runs identically on a laptop and on a rented VPS via a single `docker compose up`.

**Non-goals (v1):**

- Multi-user access, role-based auth beyond a single operator login.
- Venues other than Hyperliquid.
- Non-spot instruments (perpetuals, options) — deferred to a later spec.
- Cross-account portfolio management.
- Regulatory reporting.

## 2. Personas

- **Operator (single user):** owns the machine, holds the Hyperliquid credentials, is the only party that can promote a strategy to live or trigger the kill switch.
- **LLM agent (non-human):** proposes strategy code and capital weights. Never authorised to execute promotion or spend real capital directly.

## 3. High-Level Architecture

```
┌───────────────────────────────────────────────────────────────┐
│  docker compose (portable, single .env)                       │
│                                                               │
│  ┌────────────┐   ┌───────────────┐   ┌───────────────────┐   │
│  │ dashboard  │──▶│ control-plane │──▶│ nautilus-runner   │──▶ Hyperliquid
│  │ (Next.js)  │   │ (FastAPI)     │   │ (TradingNode)     │   (testnet or
│  └────────────┘   └───────────────┘   └───────────────────┘   mainnet)
│         │                │                       │            │
│         │                ▼                       ▼            │
│         │        ┌───────────────┐       ┌──────────────┐     │
│         └───────▶│  sqlite (WAL) │◀──────│  audit log   │     │
│                  │  registry +   │       │  (append)    │     │
│                  │  metrics      │       └──────────────┘     │
│                  └───────────────┘                            │
│                                                               │
│                  ┌──────────────────┐                         │
│                  │ agent-service    │  (Phase 2+)             │
│                  │ (Claude loop)    │                         │
│                  └──────────────────┘                         │
└───────────────────────────────────────────────────────────────┘
```

All services run in the same compose network. State lives in a single SQLite file mounted as a named docker volume `decipher-db`. No host-specific paths.

### 3.1 Why one control-plane in front of SQLite

SQLite writes are serialised. Rather than have both Python services and Next.js touch the file directly (locking risk, schema-drift risk), one Python service (`control-plane`, FastAPI) owns all SQL access. Every other service — dashboard, nautilus-runner, agent-service — talks to control-plane over HTTP on the compose network.

## 4. Components

### 4.1 `nautilus-runner` (Python)

- Boots a `nautilus_trader.live.TradingNode` with the Hyperliquid live data + exec client factories from `nautilus_trader.adapters.hyperliquid`.
- On start, calls `control-plane.GET /strategies?status=paper,live` to load the strategies it should run (`paper` in `TRADING_MODE=paper`, `live` in `TRADING_MODE=live`).
- Each strategy is a `nautilus_trader.trading.Strategy` subclass located in `decipher-trader/strategies/<name>/strategy.py`.
- Reads env var `TRADING_MODE ∈ {paper, live}`. `paper` points Hyperliquid client at testnet URL; `live` points at mainnet.
- Refuses to start in `live` mode if any strategy row marked `status='live'` lacks `promoted_at` and `promoted_by`.
- Subscribes to a `control-plane` websocket for hot events: `promote`, `demote`, `kill_all`, `reload_config`.

### 4.2 `control-plane` (Python, FastAPI)

- Owns SQLite. Exposes REST + websocket API on internal port 8000.
- Endpoints (v1):
  - `GET /strategies` — list with filters.
  - `POST /strategies` — register draft strategy (creates DB row + optional code file reference).
  - `POST /strategies/{id}/promote` — validates 14-day paper-forward requirement, records `promoted_by`, flips status.
  - `POST /strategies/{id}/demote` — reverse.
  - `POST /kill_all` — flips every `live` row to `paper`, broadcasts `kill_all` websocket event.
  - `GET /metrics/{strategy_id}` — pull rolling PnL / Sharpe / max-drawdown.
  - `GET /audit` — filterable list of audit entries.
  - `POST /audit` — append-only insert (used by other services).
  - `WS /events` — stream of control events.
- Auth: single operator token from `.env` (`OPERATOR_TOKEN`). Required for every mutating endpoint and every request from a browser-facing service. The dashboard container holds this token server-side and attaches it to outgoing calls; browsers never see it. The compose network is not exposed on the host, so internal service-to-service reads (nautilus-runner, agent-service) use the same token as their identity.

### 4.3 `agent-service` (Python, Anthropic Claude SDK)

- Phase 1: not deployed.
- Phase 2 role — **strategy author**:
  - Periodic loop (configurable cadence, default hourly).
  - Fetches recent news via **two or more** configurable news providers behind a `NewsProvider` adapter interface. v1 launches with two concrete adapters: **CryptoPanic API** (aggregator, sentiment-tagged) and **CoinDesk RSS** (editorial). Adding another later = one new class implementing `NewsProvider`, no core changes. Each provider carries its own env-var block (`CRYPTOPANIC_TOKEN`, etc.); a provider with no credentials is skipped, not fatal.
  - Corroboration rule: after fetch, items are deduped by URL host + title-similarity (Jaccard on tokenized titles, threshold configurable); each surviving item carries `sources: [provider_id, ...]`. The Claude prompt receives the corroboration count so it can down-weight solo-source news and up-weight items seen across multiple providers.
  - Fetches market summary from control-plane.
  - Prompts Claude to output a new `Strategy` subclass as Python source.
  - Runs output through: `ast.parse` → import allow-list check (only `nautilus_trader.*`, stdlib, `numpy`, `pandas` permitted) → static test in a scratch subprocess with 60 s timeout.
  - If it passes, `POST /strategies` with status `draft`. Operator (or later, an automated backtest job) advances it.
- Phase 3 role — **allocator**:
  - Slower loop (default daily).
  - Reads metrics + regime signals, proposes new `capital_weight` per active strategy.
  - Weights are proposals — operator either auto-applies (`allocator_autoapply=true` in `.env`) or reviews in dashboard.

### 4.4 `dashboard` (Next.js, TypeScript)

- Server-rendered pages calling `control-plane` REST.
- Pages:
  - `/` — list of strategies with status badge (`draft/backtest/paper/live/retired`), PnL sparkline, capital weight.
  - `/strategy/[id]` — detail: source, metrics chart, promotion history, per-strategy kill switch (calls `POST /strategies/{id}/demote` — a per-strategy demote from `live`→`paper`).
  - `/audit` — filterable audit log.
  - `/settings` — trading mode, agent cadence, kill switch (global).
- Auth: single operator login (username + password bcrypt-hashed, from `.env`). Login issues a server-side session (HTTP-only cookie); dashboard uses that session to call control-plane on the browser's behalf, attaching `OPERATOR_TOKEN` server-side.
- No build-time secrets baked in image.

### 4.5 `strategy-registry` (SQLite schema, owned by control-plane)

Tables (v1):

- `strategies(id, name, code_path, status, capital_weight, max_notional, max_daily_loss, max_position, created_at, backtest_metrics_json, paper_started_at, promoted_at, promoted_by, retired_at)`
- `metrics_snapshots(strategy_id, ts, pnl, sharpe, max_drawdown, n_trades)`
- `audit_log(id, ts, actor, action, payload_json)`  — append-only, indexed on `ts`.
- `alembic_version(...)` — schema migrations.

`status` enum: `draft`, `backtest`, `paper`, `live`, `retired`.

## 5. Data Flow — Strategy Lifecycle

```
                  operator or agent
                        │
                        ▼
                   status=draft   (source stored, no execution)
                        │
                        ▼  (nightly job or manual trigger)
                   status=backtest — Nautilus backtest on cached historical data
                        │  (auto-advances if Sharpe ≥ threshold, drawdown ≤ threshold)
                        ▼
                   status=paper   — runs live on Hyperliquid TESTNET
                                    metrics snapshots every 5 min
                        │
                        │  operator opens dashboard, sees ≥14 continuous days of
                        │  paper-forward with green metrics, clicks Promote
                        ▼
                   status=live    — runs on Hyperliquid MAINNET
                                    per-strategy risk caps enforced by Nautilus
                        │
                        │  either operator retires, or kill_all triggered
                        ▼
                   status=retired
```

**Backtest→paper auto-advance thresholds** (v1, configurable in `.env`):

- Sharpe ratio ≥ 1.0 over the backtest window
- Max drawdown ≤ 20%
- Number of trades ≥ 30 (avoids fluke)

**Paper→live promotion pre-conditions (all required):**

- `paper_started_at` set and `now() - paper_started_at ≥ 14 days`
- No `paper` metric snapshot in the last 14 days shows drawdown > `max_daily_loss`
- Operator token present on the `POST /promote` call

## 6. Portability

- Every path used by every service is under `/app/...` inside its container.
- Every configurable value comes from `.env` (mounted as `env_file` in compose).
- Persistent state:
  - `decipher-db` volume (SQLite file)
  - `decipher-cache` volume (historical market data cache for backtests)
- Backup: single command dumps volumes to a tarball. Restore = untar into fresh volumes on new host, `docker compose up`.
- All images pin exact versions. `docker-compose.yml` references pinned base images.

## 7. Safety Gates (invariants)

Every gate below is enforced in the codebase, not by operator discipline.

| # | Gate | Enforced where |
|---|---|---|
| G1 | `TRADING_MODE=live` refuses start if any `status=live` row lacks `promoted_at`+`promoted_by` | `nautilus-runner` startup check |
| G2 | Live Hyperliquid credentials mounted only when `TRADING_MODE=live`; testnet URL otherwise | `docker-compose.override.yml` per mode |
| G3 | Per-strategy `max_notional`, `max_daily_loss`, `max_position` fed into Nautilus `RiskEngine` before start | `nautilus-runner` config builder |
| G4 | Global kill switch: dashboard button + `POST /kill_all` on control-plane. Flips every `live`→`paper` within one event tick, broadcasts websocket event | `control-plane` |
| G5 | Every order intent + fill + promotion + agent decision written to append-only `audit_log` | Every service that mutates state |
| G6 | LLM-generated code passes `ast.parse` + import allow-list + 60 s sandbox before it can be stored as `draft` | `agent-service` |
| G7 | Promotion requires ≥14 days of continuous paper-forward on Hyperliquid testnet with no fatal drawdown snapshot | `control-plane.POST /promote` |
| G8 | Operator token required for every mutating endpoint | `control-plane` middleware |
| G9 | Live-mode operation logs a heartbeat every 30 s; two missed heartbeats trigger `kill_all` | `nautilus-runner` self-monitor |

## 8. Phased Delivery

### Phase 1 — Pipeline (no LLM)

Deliverables:

- `decipher-trader/` repo skeleton with `pyproject.toml` (uv-managed), `next.config.js`, `docker-compose.yml`, `.env.example`.
- `control-plane` service with all v1 endpoints and SQLite schema + Alembic migrations.
- `nautilus-runner` service with Hyperliquid factory wiring and one hand-written toy strategy (simple momentum on a single pair).
- `dashboard` with strategy list, detail, promote button, kill switch.
- End-to-end smoke test: docker-compose up, seed the toy strategy at `status=paper` on testnet, submit a real testnet order, assert audit log entry.
- G1, G2, G3, G4, G5, G7, G8, G9 implemented.

Exit criteria: operator can watch the toy strategy trade on testnet, click Promote (dry-run — no live keys yet), see the state change reflected in DB + audit log.

### Phase 2 — LLM strategy author

Deliverables:

- `agent-service` container with Claude client (`anthropic` SDK) and hourly loop.
- News provider integration (specific provider selected in a follow-up mini-spec).
- Sandbox pipeline (`ast.parse` + import allow-list + subprocess timeout).
- Backtest runner: nightly job iterates all `draft` strategies against cached historical data, auto-advances to `paper` if thresholds met.
- Dashboard page for reviewing `draft` strategies before advancing.
- G6 implemented.

Exit criteria: agent-service can generate a new strategy, it survives the sandbox, backtests, and appears in the dashboard as `paper` — all without operator intervention up to promotion.

### Phase 3 — LLM allocator

Deliverables:

- Allocator loop in `agent-service` (daily).
- Regime signal inputs (source selected in follow-up mini-spec).
- `capital_weight` update endpoint and dashboard visualisation.
- `allocator_autoapply` env flag.

Exit criteria: allocator recomputes weights daily, changes are visible in dashboard, and operator can accept or override.

## 9. Testing Strategy

- **Unit:** per strategy, using Nautilus backtest fixtures; per control-plane endpoint using FastAPI TestClient; per Next.js page component using its default testing setup.
- **Integration:** `docker compose up` in CI, seed a fixture strategy, drive the paper→live promotion path against a canned Hyperliquid testnet response (recorded fixtures — never call the live testnet in CI).
- **Sandbox tests (Phase 2+):** golden set of malicious LLM outputs (imports os, subprocess, network calls) that must be rejected by the allow-list.
- **Chaos tests:** kill nautilus-runner mid-order, assert audit log still reflects intent; kill control-plane, assert nautilus-runner refuses further orders until reconnect.
- **Manual pre-promotion checklist** (documented separately, referenced from dashboard promote flow).

## 10. Upgrade Path for NautilusTrader

- `nautilus-trader` version is pinned in `pyproject.toml`.
- Upgrade procedure: bump version, run `uv sync`, run full test suite. If breakage, remain on previous pin and file an issue upstream.
- Because we never modify upstream source, upgrade never conflicts with our diffs.

## 11. Open Questions (to resolve during planning)

1. News provider expansion (Phase 2+): v1 ships with CryptoPanic + CoinDesk RSS (settled). Future candidates for the 3rd+ adapter: Twitter/X firehose (paid), independent research aggregators, on-chain event feeds. Not blocking Phase 1 or Phase 2 launch.
2. Regime signal source (Phase 3) — Coinglass, on-chain metrics, custom composite. Not blocking Phase 1 or 2.
3. Historical data source for backtest cache — Hyperliquid public data endpoints vs a third-party (Tardis, Kaiko). Not blocking Phase 1's toy strategy which can use synthetic data initially.
4. Whether to run nautilus-runner and agent-service under supervisord inside their containers or as separate compose services — Phase 1 assumes separate compose services (simpler).

## 12. Out of Scope for v1

- Multi-account trading.
- Perpetuals or margin.
- Automated retraining / online learning.
- Any modification to the upstream `nautilus_trader` codebase.
- Any CI or workflow changes in the `nautilus_trader` repo (maintainer-only).
- Public deployment or SaaS-style hosting.
