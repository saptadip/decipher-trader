# FundingReversion — empirical log

Per-catalog backtest results for the ``funding_reversion`` strategy defined
in ``nautilus-runner/strategies/funding_reversion/strategy.py``. Newest at
the top. Numbers here are point-in-time findings on specific catalogs — the
strategy itself has not changed between entries unless noted.

---

## 2026-09-26 — BTCUSDT hourly, 2022-01-01 → 2025-01-01 (3 years)

**Verdict: hypothesis rejected on this venue.**

Ran a grid × walk-forward retest to answer whether the earlier H1-2025 null
result was regime-specific (quiet funding period) or fundamental (venue
economics). Result: fundamental.

### Catalog

- 26,304 hourly BTCUSDT bars (integrity clean, 0 gaps / dups).
- 3,288 funding events. 2005 of them (61%) have ``NaN`` mark_price — early
  Binance history didn't populate the field. Uncovered a bug in
  ``parse_funding_entry`` that crashed on the empty string; fixed in this
  PR with a ``NaN`` fallback + regression test.

### Funding distribution (3-year)

| quantile of `|rate|` | value |
|---|---|
| 50th | 0.0001 (1 bp / 8h — the Binance clamp)     |
| 75th | 0.0001                                    |
| 90th | 0.0001                                    |
| 95th | 0.000207                                  |
| 99th | 0.000472                                  |
| 99.9th | 0.000817                                |
| max (2022, Luna/FTX crisis) | 0.001192       |

Only **174 events (5.3%)** exceeded 2 bp / 8h; **28 events (0.85%)**
exceeded 5 bp; **2 events (0.06%)** exceeded 10 bp; zero exceeded 20 bp.

### Single window (baseline)

`--entry-threshold 0.0002 --exit-threshold 0.00005` over the full 3 years:
9 trades, realized PnL **−$32.73** on $10k, Sharpe **−0.44**.

### Grid × walk-forward (final result)

`--entry-threshold-grid 0.0001,0.0002,0.0003,0.0005 --exit-threshold-grid 0.00003,0.00005`
with `--train-months 6 --test-months 3 --step-months 3`:

- 8 combos × 10 windows × 2 phases = **160 engine spins**.
- Winner params drift between windows (`entry=0.0001` on w2-w5/w7/w8 with
  many trades; `entry=0.0005` on w6/w9 with almost none) — classic overfit-
  to-train symptom.
- Individual OOS test windows range from **−$15.53 to +$9.60**.
- **Total OOS PnL: −$0.29 USDT** across 30 test months. Barely
  distinguishable from zero.

### Root cause

Binance USDM taker fee is 1.8 bp per side (3.6 bp round-trip). A funding
event pays `rate * notional`. For a trade to overcome the round-trip fee
via funding alone, the captured funding event must exceed **≈ 3.6 bp per
8h**. Only 5.3% of Binance BTCUSDT events in the 3-year window meet that
bar, and the strategy also needs the *next* event to revert toward zero
inside the exit threshold — which further prunes the population. Combined
with the imperfect timing of bar-execution fills (order fires on the bar
whose close aligns with the funding event; fill happens at the next bar's
open), the net edge is essentially fee-neutral.

### Follow-up options

- **Different venue.** dYdX, Bybit, and some alt perpetuals see funding
  regularly hit 50+ bp per 8h; the same strategy shape may show edge
  where BTCUSDT does not. Would require a new adapter (Hyperliquid is
  the live venue; the backtest catalog is Binance-only today).
- **Alt symbols on Binance USDM.** SOLUSDT, DOGEUSDT, and memecoins have
  wilder funding than BTCUSDT. Requires the multi-symbol refactor
  (deferred in PR F).
- **Different edge class.** Move to Momentum4h or VolatilityBreakout —
  the harness is ready; only the strategy body changes.

---

## 2026-09-26 — BTCUSDT hourly, 2025-01-01 → 2025-07-01 (H1 2025)

**Verdict: falsified on quiet regime.** *(First empirical result, shipped
with the strategy in PR #20.)*

- 6-combo grid × 3 windows × walk-forward with train=3m / test=1m / step=1m.
- OOS PnL per window: +$5.08, −$12.49, −$10.58. Net **−$18 USDT**.
- Regime notes: H1 2025 funding was unusually quiet. `|rate|` stayed
  under 0.000122 (12 bp per 8h ceiling) for the whole window.
- Left open: could still be an edge on volatile regimes. Answered in the
  3-year retest above — no, the effect doesn't survive to longer catalogs
  either.
