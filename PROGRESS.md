# decipher-trader — Session progress + resume guide

Last updated: 2026-09-26 (post-PR #23 merge). HEAD: `69da5b6` on `origin/main`. Open PR: **#24** (docs-only wider-grid retest) on branch `momentum-4h-wider-grid` — includes this PROGRESS.md refresh; ready to merge.

Use this file to resume work in a **new Claude Code session**. Section [How to use this file](#how-to-use-this-file-in-a-new-session) at the bottom has step-by-step.

---

## Where we are

Autonomous overnight-safety stack is **complete**. Bot boots, runs on Hyperliquid testnet, self-halts on drawdown, alerts to Telegram. Live trade-event Feather persistence is **disabled on rc5** (nested Tokio panic in `StreamingFeatherWriter.subscribe()`; restore via `StreamingConfig` under Nautilus 2.0 stable — see the deferred item below). The **strategy** (`ToyMomentum`) is a demo — it will lose to fees over time. Next real work is finding actual edge.

### Verified end-to-end (2026-09-21)

- `smoke.py` — `SMOKE OK (with runner)` ✅
- `chaos.py A` — SIGKILL exit 137, audit rows preserved ✅
- `chaos.py B` — control-plane down → runner exits after 2 heartbeat misses ✅
- Telegram — pings deliver on promote / kill_all / disconnect ✅

---

## What's shipped (13 PRs since Phase 1 close)

| PR | Feature | Merge commit |
|---|---|---|
| Phase 1 | 24-task pipeline (control-plane + runner + dashboard + smoke) | ends at `bfbdd9d` |
| Phase 1.5 A (smoke) | Runner in smoke test | `fda4687` (later merged into `main`) |
| Phase 1.5 B (chaos) | `chaos.py` kill-runner + kill-control-plane | `23499fb` |
| Phase 1.5 C (kill_listener) | `ws.recv` race against `stop_event` | `78eb01c` |
| #1 doc | API wallet setup + `HYPERLIQUID_-` format + rc5 quirks | `1c988a9` |
| #2 | `GET /strategies/{id}` + dashboard rewire | `7006dea` |
| #3 | ToyMomentum bar-injection backtest test | `904265f` |
| #4 | Risk caps enforce (max_position/notional/daily_loss) + metrics emit | `4cad139` |
| #5 | Telegram alerts (promote/demote/kill/stale-heartbeat) | `7addf4b` |
| #6 | Auto-demote when `max_drawdown >= max_daily_loss` | `e49b555` |
| #7 | `datetime.utcnow()` → tz-aware `datetime.now(timezone.utc)` | `785bc98` |
| #8 | Position reconciler (5-min `Clock.set_timer`) + native `position_check_interval_secs` | `769f79a` |
| #9 | Real Sharpe + MaxDrawdown from PnL history | `68e84a5` |
| #10 | Flatten positions in `on_stop` before `node.stop()` | `883eebe` |
| #11 | Trade event Feather/Parquet persistence via `StreamingFeatherWriter` | `9cced80` |
| #12 | `on_socket_state` disconnect + reconnect alerts | `7f8917a` |
| #13 | Log telegram non-2xx + README `--build` in smoke/chaos | `22eaa2d` |
| #14 | Session 1: Binance historical downloader → Nautilus Parquet catalog | `8ba9b26` |
| #15 | Session 2 PR A: single-window backtest driver on top of the catalog | `e5d7ad2` |
| #16 | Session 2 PR B: ToyMomentum backtest-safety (self.id → self.strategy_id) | `bd47d10` |
| #17 | Session 2: disable StreamingFeatherWriter (rc5 Tokio-nest panic) | `c454fef` |
| #18 | Session 2 PR C: walk-forward evaluator on top of `run_backtest` | `88ba6f8` |
| #19 | Session 2 PR D: parameter-search grid on top of `walk_forward` | `e17875d` |
| #20 | Session 3 opener: FundingReversion strategy + funding_loader + CLI wiring | `4135e48` |
| #21 | Retest FundingReversion on 3-year Binance catalog — hypothesis rejected | `902dc53` |
| #22 | Add Hyperliquid data source + venue-agnostic runner; funding-reversion retest — rejected there too | `8253a46` |
| #23 | Retest ToyMomentum on 4h bars over 3 years — weak positive (+$54 OOS, Sharpe 0.10, fee-tolerant) | `69da5b6` |
| **#24 (open)** | Grid-sensitivity study on Momentum4h — parameter search cannot rescue thin edge | `momentum-4h-wider-grid` branch |

---

## Architecture snapshot

- **control-plane** (FastAPI + SQLite) — strategy lifecycle, audit log, kill switch WS broadcaster, Telegram bridge, heartbeat monitor, auto-demote on drawdown.
- **nautilus-runner** (Python + Nautilus rc5) — `ToyMomentum` strategy, LiveNode against Hyperliquid testnet, heartbeat + kill listener background loops, 5-min position reconciler, hourly metrics emission, flatten-on-stop, socket-disconnect alerts. (Feather trade log disabled on rc5 — see PR #17 and the deferred item below.)
- **dashboard** (Next.js 15) — login, strategy list + detail, promote/demote/start_paper, audit page, settings + global kill switch.
- **e2e** — `smoke.py` (full path with real testnet), `chaos.py` (A: kill runner, B: kill control-plane), Docker compose overlays for both.

Full details: `README.md` + `docs/superpowers/specs/2026-09-18-decipher-trader-design.md`.

---

## Deferred (ledgered, not lost)

1. **Path B refactor** (native config-flag flatten) — use `StrategyConfig.close_positions_on_stop=True` + `cancel_orders_on_stop=True`; keep `on_stop` only for the audit entry. Cleaner but ceremonial. PR-worthy after strategy-research arc lands or during a native-sweep pass.
2. **Nautilus 2.0 stable migration** — waiting on upstream release. When it lands:
   - Delete manual `sharpe_from_pnls` and `max_drawdown_from_pnls` (moved to `nautilus_runner.metrics` in Session 2 PR A) — swap for `nautilus_trader.analysis.SharpeRatio.calculate_from_realized_pnls()` etc.
   - **Restore live trade-event persistence** (disabled in PR #17 on rc5) — wire `StreamingConfig` on `LiveNodeBuilder` and drop the tombstone comment + regression fence in `main.py` / `tests/test_main_no_streaming_feather_writer.py`. Root cause: rc5's `StreamingFeatherWriter.subscribe()` nests `Tokio::block_on()` inside the LiveNode Tokio runtime and panics on the first msgbus event after data-client connect.
   - Verify `AccountId(str)` vs `AccountId.from_str(str)` behavior on 2.0 stable.
   - Re-visit R1 (`LiveRiskEngineConfig(bypass=True)`) — 2.0 may finally expose per-strategy risk caps that replace our strategy-layer G3 check.
3. **Multi-strategy timer name refactor** — currently `reconciler-{self.id}` per strategy. When multi-strategy runners are added, verify unique-name discipline.
4. **`_realized_pnl_history` prune** — unbounded MVP; O(n) per emission. Prune to last K entries at real trade frequency.
5. **`test_send_swallows_flatten_errors`-style hardening** for other error paths.

---

## Known Nautilus rc5 quirks (all documented in README)

- **Exit code 133** on `kill_all` / heartbeat-loss shutdown — SIGTRAP from Rust runtime when `node.stop()` fires from a non-main thread. Smoke + chaos accept `{0, 133, 137, 143}`. Fix path: rewire shutdown so main thread performs `node.stop()` from an event flag.
- **`StreamingConfig` is backtest-only** in rc5 — no `LiveNode` wiring. `StreamingFeatherWriter.subscribe()` is disabled (PR #17) because its Rust callback nests `Tokio::block_on()` inside LiveNode's runtime and panics. Live trade-event persistence is off until 2.0 stable exposes `StreamingConfig` on `LiveNodeBuilder`.
- **`SharpeRatio` / `MaxDrawdown` return `None`/`nan`** in rc5 (Rust PnL path unimplemented). We compute manually via `statistics` stdlib.
- **`LiveClock` not user-constructable** from Python in rc5. `Clock.new_test()` was previously used as a placeholder when wiring `StreamingFeatherWriter`; both are gone from live now (PR #17). The clock caveat matters again once the 2.0-stable `StreamingConfig` swap lands.
- **`SocketState` has no `.name` attribute** (pyo3 enum). Use `str(event.state).split(".")[-1]` for the short name.
- **`.subscribe_socket_state()` required** for `on_socket_state` to fire — Nautilus routes via msgbus, not virtual method dispatch.

---

## Environment prerequisites (for local runs)

- Docker Desktop running.
- `uv` installed.
- Python 3.12+ for control-plane, 3.13 for nautilus-runner (per each service's `.venv`).
- Node.js v20+ for dashboard.
- `.env` (in repo root) with:
  - `OPERATOR_TOKEN` — long random string (`python3 -c "import secrets; print(secrets.token_urlsafe(48))"`).
  - `DASHBOARD_SESSION_SECRET` — 32+ char random.
  - `DASHBOARD_OPERATOR_USERNAME` + `DASHBOARD_OPERATOR_PASSWORD_BCRYPT` — dashboard login.
  - `HYPERLIQUID_TESTNET_PRIVATE_KEY` — real testnet API-wallet key.
  - `HYPERLIQUID_TESTNET_ACCOUNT_ID=HYPERLIQUID-TESTNET-001` (Nautilus local label — NOT the wallet address).
  - `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` — for push alerts.
- On macOS Docker Desktop: `export SMOKE_DATA_DIR=$HOME/.decipher-e2e` before smoke/chaos runs (`/tmp` isn't visible from host).

---

## Working principles saved to memory

Two rules established over prior sessions. Both live in `~/.claude/projects/-Users-sapta-Documents-personal-myStage-claude-personal-nautilus-trader/memory/`:

1. **Never push directly to `main`**. All changes go through a PR (`gh pr create --base main --head <branch>`). Approval doesn't carry over between changes — each PR needs its own explicit `merge` from user.
2. Every merge is preceded by `superpowers:requesting-code-review`; user calls "apply fix + merge" or "hold" once findings are presented.

**Caveman mode is user's default** — terse output, no filler, no self-reference, code + facts direct. Full rules load at session start via SessionStart hook.

---

## Next course of action — real strategy research

The plumbing is complete; the strategy is the remaining gap. `ToyMomentum` (5/20 MA crossover on 1-min bars against BTC-PERP) will lose money over enough trades due to fees + slippage. To actually make money while sleeping, need a strategy with real edge.

Suggested arc (multi-session):

### Session 1 (DONE — PR #14 `8ba9b26`): Data pipeline
- **Data source chosen**: Binance USDM public data. Rejected Hyperliquid (only 5000 candles/call ≈ 3.5 days at 1m; venue too young). Binance archives go back to 2019. Basis mismatch to live Hyperliquid trading is accepted; Session 4 paper-forward covers venue transfer.
- **Downloader shipped**: `nautilus-runner/scripts/download_data.py` — async monthly-zip fetch from `data.binance.vision` + paginated funding-rate REST from `fapi.binance.com`. Trims to requested UTC window (exclusive end); funding endTime aligned with bars (`date_to_ms(end) - 1`). CLI args: `--symbol --interval --start --end --out [--funding] [--skip-integrity]`.
- **Module split**: `nautilus_runner.data.{binance,converter,integrity,catalog}`. Prices/quantities constructed via `Decimal` (`Price.from_decimal_dp`) per AGENTS.md exact-arithmetic rule.
- **Integrity report**: gaps, duplicates, non-positive prices, bar-to-bar ratio, row-count vs expected. Emitted as JSON alongside catalog; failure exits code 3 unless `--skip-integrity`. OHLC relational invariants enforced upstream by Nautilus `Bar` constructor. Empty-window exits code 4 to prevent silent empty catalogs.
- **Storage**: bars written via `ParquetDataCatalog.write_bars` under `data/bars/BTCUSDT-PERP.BINANCE-<spec>-LAST-EXTERNAL/*.parquet`. Funding written via raw `pyarrow` on schema `[ts_ns, funding_rate, mark_price, symbol]`.
- **Verified E2E**: 168 bars + 21 funding entries for 2025-06-01 → 2025-06-08 exclusive, integrity clean. Runner suite went from 57 → 91 tests. See `docs/data_pipeline.md`.
- **Deferred (Minor, tracked)**: friendly argparse date-error message; klines-client `base_url` unification; `funding_rate` as `decimal128`; `date_to_ms` relocation; debug log on skipped non-header CSV rows; `docs/data_pipeline.md` primary example uses a current-month date (works today but demonstrates the very command it warns about).

### Session 2 (in progress — PR A DONE at `e5d7ad2`): Backtest infrastructure
- **PR A (done)** — `run_backtest(catalog_path, bar_type, strategy, start, end)` on top of Nautilus `BacktestEngine`. `BTCUSDT-PERP.BINANCE` `CryptoPerpetual` factory (Binance USDM tier-0 fees), `MakerTakerFeeModel`, bars via `ParquetDataCatalog.query_bars`. Returns `BacktestSummary` (realized PnL, Sharpe, max drawdown, raw Nautilus stats). CLI at `scripts/run_backtest.py` with `--strategy buy_and_hold` (default) and `--strategy toy_momentum` (fail-fast, pending adaptation). Money and Quantity go through `Decimal` end-to-end. `nautilus_runner.metrics` (`sharpe_from_pnls`, `max_drawdown_from_pnls`) extracted from `strategies/toy_momentum/strategy.py`. `pandas==2.2.3` in a `backtest` optional-dep group. Verified E2E: 168 hourly BTCUSDT bars 2025-06-01 to 2025-06-08 → 1 closed position, +1.06 USDT realized PnL, annualized Sharpe 1.28. Runner suite 91 → 109.
- **PR B (done at `bd47d10`)** — ToyMomentum runs cleanly under `BacktestEngine`. Single root cause was `self.id` (never exposed by rc5 `Strategy`) → `self.strategy_id` at strategy.py:113,333. All other live-only side effects were already guarded on None module-level singletons. CLI un-gated with the full ToyMomentum hyperparameter surface. E2E: 168 hourly BTCUSDT bars 2025-06-01 to 2025-06-08 → 19 trades, -0.96 USDT (matches "demo loses to fees" narrative). Runner 109 → 110. Live-mode paper revalidation still pending — the pre-PR `self.id` was almost certainly broken in live too. Follow-up tickets: wall-clock `datetime.now(...).date()` daily-loss reset in `on_bar`; CLI-level `slow > fast` argparse validation.
- **PR C (done at PR #18)** — walk-forward evaluator on top of `run_backtest`. `iter_windows` yields `(train_start, train_end, test_start, test_end)` tuples with month-aligned rolls; `walk_forward(catalog, bar_type, strategy_factory, start, end, train_months, test_months, step_months)` runs a fresh strategy per phase (Nautilus won't accept re-attached instances). CLI at `scripts/walk_forward.py` with exit codes 0 / 2 / 3 / 4 and full parity vs `run_backtest.py` on strategy + instrument flags. Verified E2E: 6 months of hourly BTCUSDT (2025-01-01 to 2025-07-01), ToyMomentum fast=3/slow=10 OOS PnL +5.08 / -12.49 / -10.58 USDT (matches "demo loses to fees"). Runner suite 113 → 134.
- **PR D (done at #19)** — parameter-search grid on top of `walk_forward`. `walk_forward_search()` runs a fresh `walk_forward` per combo, picks per-window winner by selector (default: highest train `sharpe_from_pnls`). Runner 134 → 154. CLI `scripts/param_search.py` with `--fast-grid` / `--slow-grid` (ToyMomentum), `--trade-size-grid` (BuyAndHold), `--entry-threshold-grid` / `--exit-threshold-grid` (FundingReversion). Wrong-strategy grid rejection via `_GRID_OWNERS`.
- **Baseline metrics** (targets for Session 3 candidates, not this session): Sharpe > 1, max drawdown < 20%, ≥ 500 trades, positive edge after 5 bps taker fee.

### Session 3 (in progress): Strategy exploration

Empirical logs live per strategy: `docs/strategy_notes/<strategy>.md` (newest at top).

**Strategies tested so far:**

| Strategy | Best result | Fee erosion | Verdict |
|---|---|---|---|
| `buy_and_hold` | plumbing verification only | n/a | n/a |
| `toy_momentum` @ 1m Binance | −$18 net OOS | high | loses to fees |
| `funding_reversion` @ 1h Binance, 3y (PR #21) | −$0.29 net OOS | ~100% | **rejected** — fee-slaughter |
| `funding_reversion` @ 1h Hyperliquid, 6mo (PR #22) | +$11.41 / Sharpe 0.045 (zero-fee ceiling +$21.60) | ~50% | **rejected** — signal itself is trivial |
| `toy_momentum` @ 4h Binance, 3y baseline grid (PR #23) | **+$54.43 net OOS**, Sharpe 0.065, fee erosion ~5% | ~5% | **first non-underwater strategy**; not promotable (Sharpe << 1, 303 trades < 500) |
| `toy_momentum` @ 4h wider grid (PR #24 open) | +$15.44 / Sharpe −0.11 (worse) | — | wider grid HURTS |
| `toy_momentum` @ 4h dense grid (PR #24 open) | +$50.10 / Sharpe +0.013 (tie) | — | dense grid ties on PnL, loses on Sharpe |

**Consolidated learning:** Momentum4h edge on 3y BTCUSDT is real but caps at ~$54 OOS after fees regardless of grid density. Parameter search cannot rescue thin signal. `fast=12, slow=24` is the stable modal winner. Max drawdown across all Momentum4h test runs = 0.15% of capital — well under 20% G7 ceiling. **Signal is the bottleneck, not drawdown or grid tuning.**

**Next actionable direction (PR J when session resumes):** volatility filter on `ToyMomentum`. Losses cluster in choppy 2022-2023 windows; a Bollinger-band gate / ATR minimum that skips low-vol regimes should raise Sharpe by pruning chop losses without giving up trending-window wins. Adds ~30 lines to strategy + a `--vol-filter-min` param + tests. Small PR.

**Backlog of future strategy work (not started):**

- **Wilder venues for FundingReversion** — dYdX v4 funding regularly hits 50+ bp per 8h. Nautilus rc5 ships a dYdX adapter (`DydxDataClientFactory`, `DydxExecutionClientFactory`, `DydxNetwork` with mainnet+testnet); would need a matching indexer-API data fetcher. Deferred until FundingReversion (or a carry variant) shows promise anywhere.
- **Multi-symbol** (ETHUSDT, SOLUSDT). Trend more strongly than BTC on 4h; alt funding regimes are wilder. Requires generic `build_perp(exchange, symbol, ...)` factory + a per-symbol/exchange precision-and-fee registry + dropping the `BTCUSDT`-only guards in the 3 backtest CLIs. Estimated 1 medium PR.
- **Sibling funding carry strategy** — hold through many hourly Hyperliquid funding events as carry income (opposite design to FundingReversion's per-event mean-reversion). Would ship as new strategy alongside `funding_reversion` with shared funding_loader.
- **Ensemble** — once 2+ strategies each pass individually, weighted portfolio. Session 5+ concern.

### Session 4: Paper-forward validation (BLOCKED until a strategy meets promotion criteria)

- No strategy currently qualifies. Sharpe > 1 gate is the tight constraint (Momentum4h's 0.10 is the current best).
- Once qualified, deploy winner to testnet via existing decipher-trader stack. Wait 14 days (G7 gate). Monitor Telegram alerts, check drawdown. Only if paper Sharpe holds → promote to live mode with small `max_notional=100 USDC`.

### Deferred technical debt (own PRs, not session-blocking)

Reviewer-flagged items across PRs, deferred per reviewer's own guidance:

- **Wall-clock `datetime.now(...).date()` daily-loss reset in `on_bar`** — affects both `ToyMomentum` (strategy.py:197) AND `FundingReversion` (strategy.py:~110). Fixes deterministic daily-loss behavior across UTC midnight in backtest. Migrate both to `self.clock.utc_now()`. Small PR; 2 files + 2 tests.
- **CLI factory consolidation** — 3 CLIs × 3 strategies = 9 copy-pasted strategy dispatchers. Pull into `strategies.<name>.build_from_args(args, bar_type)` helpers so the CLIs are ~30 lines each and drift-guard tests aren't needed. Small-medium PR.
- **Multi-symbol support** — see Session 3 backlog above.
- **Multi-exchange abstraction** (`nautilus_runner.exchanges.<name>`) — Nautilus adapters (`HyperliquidDataClientFactory`, `DydxDataClientFactory`) are already the plugin surface; wrap in a runner-side registry so `main.py` picks based on `RunnerSettings.exchange`. Prerequisite for actually running any strategy on dYdX live.
- **Live-mode paper revalidation of the `self.strategy_id` fix (PR #16)** — reasonable operator confidence check; pre-PR-B `self.id` was likely broken silently in live too. Register `toy-momentum-1`, restart runner, tail logs for the 5-min reconciler heartbeat (already partially done in a prior session).
- **Nautilus 2.0 stable migration** (from the older list — still valid):
  - Delete manual `sharpe_from_pnls` / `max_drawdown_from_pnls` — swap for `nautilus_trader.analysis.SharpeRatio.calculate_from_realized_pnls()`.
  - Restore live trade-event persistence — wire `StreamingConfig` on `LiveNodeBuilder`; drop the PR #17 tombstone + regression fence.
  - Verify `AccountId(str)` vs `AccountId.from_str(str)` behavior on 2.0.
  - Re-visit R1 (`LiveRiskEngineConfig(bypass=True)`) — 2.0 may finally expose per-strategy risk caps.

### What is emphatically NOT built yet (spec item, deferred)

The `agent-service` (Python + Anthropic Claude SDK) defined in `docs/superpowers/specs/2026-09-18-decipher-trader-design.md` (§4.3, tagged Phase 2+) is **not implemented**. No `agent-service/` directory, no Anthropic SDK dep, no LLM inference in the trading loop. All strategies today are hand-written Python; the harness we've built is the substrate that Phase-2 agent-service will eventually plug into to auto-propose strategies for human review.

### Session 5+: Iterate

- Scale up `max_notional` gradually if live metrics match paper.
- Add second strategy (test multi-strategy runner scaffolding).
- Trigger the deferred items above.

### Session 4: Paper-forward validation
- Deploy winner to testnet via existing decipher-trader stack.
- Wait 14 days (G7 gate).
- Monitor Telegram alerts, check drawdown.
- Only if paper Sharpe holds → promote to live mode with small `max_notional=100 USDC`.

### Session 5+: Iterate
- Scale up `max_notional` gradually if live metrics match paper.
- Add second strategy (test multi-strategy runner scaffolding).
- Trigger the deferred items (Path B refactor, Nautilus 2.0 migration).

---

## How to use this file in a new session

Copy-paste this into the first message of the new Claude Code session (adjust the `<repo>` path if different on the machine):

```
Read /Users/sapta/Documents/personal/myStage/claude-personal/decipher-trader/PROGRESS.md.

Then:
1. Verify main is up to date: `cd /Users/sapta/Documents/personal/myStage/claude-personal/decipher-trader && git checkout main && git pull`.
2. Confirm HEAD matches the SHA at the top of PROGRESS.md (or is ahead — some PRs may have merged in another session).
3. Run all three test suites to confirm a clean baseline:
   - `cd control-plane && uv run pytest -q`
   - `cd nautilus-runner && uv run pytest -q`
   - `cd dashboard && npm test && npx tsc --noEmit`
4. Report the baseline result and the section of PROGRESS.md's "Next course of action" I want to start on (default: Session 1 — Data pipeline).
5. Follow the working principles in PROGRESS.md: PR-first, code review before merge, caveman mode terse output.
```

That prompt gets the new session grounded, verified, and ready to work in one turn. Do NOT skip step 3 — a clean baseline before change is the difference between "I broke it" and "this was already broken."

### If something in PROGRESS.md is out of date

Trust `git log` over the ledger. If HEAD > `69da5b6`, run `git log --oneline 69da5b6..HEAD` to see the diff and update PROGRESS.md as one of the session's first tasks.

### If the new session's tests don't match the baseline

Don't proceed with new work. Debug the regression first — the plumbing under this file is what future strategies rest on.
