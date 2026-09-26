# FundingReversion — empirical log

Per-catalog backtest results for the ``funding_reversion`` strategy defined
in ``nautilus-runner/strategies/funding_reversion/strategy.py``. Newest at
the top. Numbers here are point-in-time findings on specific catalogs — the
strategy itself has not changed between entries unless noted.

---

## 2026-09-26 — Hyperliquid BTC-USD-PERP hourly, 2026-03-01 → 2026-09-01 (6 months)

**Verdict: hypothesis rejected on this venue too, but for a different reason.**

Session-3 diagnostic — retest on venue-native data after the 3-year Binance
result. Different reason for the rejection: on Binance fees eat 100%+ of
the signal; on Hyperliquid the signal itself is trivial.

### Catalog

- 4,380 hourly candles (99.2% of 4,416 expected — Hyperliquid returns
  zero-fill at pagination edges).
- 4,416 funding events (Hyperliquid fires funding every hour, not every 8h).
- Data horizon: Hyperliquid ``candleSnapshot`` returns at most 5000 candles
  from now (~208 days at 1h), so this retest is anchored to today's window.

### Funding distribution (Hyperliquid, per hour)

| quantile of `|rate|` | value |
|---|---|
| 50th | 0.000011 (1.1 bp / hr)                   |
| 90th | 0.000013                                  |
| 95th | 0.000014                                  |
| 99th | 0.000024                                  |
| 99.9th | 0.000036                                |
| max | 0.000044                                   |

Cadence-adjusted, Hyperliquid pays much more funding per unit time than
Binance (26 bp/day vs Binance's 3 bp/day), but each individual event is
smaller — so a per-event mean-reversion strategy sees less absolute PnL per
trigger.

### Single-window threshold sweep (6 months)

Instrument built inline with Hyperliquid taker=3.5 bp / maker=1.5 bp.

| `entry_threshold` | `exit_threshold` | trades | PnL (USDT) | Sharpe |
|---|---|---|---|---|
| 1 bp/hr  | 0.3 bp/hr | 206 | **+11.41** | +0.045 |
| 2 bp/hr  | 0.5 bp/hr | 20  | −0.54      | −0.036 |
| 3 bp/hr  | 1 bp/hr   | 3   | −3.06      | −1.552 |

Lowest threshold fires often enough to be statistically visible; higher
thresholds are event-starved (fewer than 5 triggers).

### Fee sensitivity (entry=1 bp/hr, exit=0.3 bp/hr)

| taker fee | PnL (USDT) | Sharpe |
|---|---|---|
| 3.5 bp (Hyperliquid default) | +11.41 | +0.045 |
| 2 bp                         | +15.78 | +0.062 |
| 1 bp                         | +18.69 | +0.073 |
| 0 (theoretical ceiling)      | +21.60 | +0.085 |

**Even at zero fees the strategy only clears $21.60 on $10k over 6 months
— ~0.4% APR.** Fees eat about 50% of the raw signal, but the raw signal
itself is trivially small.

### Root cause

Hyperliquid clamps its funding formula tighter than expected: 99.9th
percentile is 3.6 bp/hr, max 4.4 bp/hr — no events exceed 10 bp/hr in the
6-month window. Individual mean-reversion arb trades therefore net cents
each even before fees. The strategy design (enter-on-extreme,
exit-on-reversion, single-event holding) is not the right shape to capture
Hyperliquid's persistent-but-tiny funding regime; a strategy that HOLDS
through many funding events (accumulating funding as carry income) might
work, but that is a different edge class than mean-reversion.

### Follow-up options

- Move to a different edge class on the harness (Momentum4h,
  VolatilityBreakout). Same catalog, different strategy body.
- Try a **carry** strategy (hold through extreme funding for many hours to
  accumulate funding, not exit on first reversion) — different design
  than mean-reversion, would ship as a sibling strategy alongside
  `FundingReversion`.
- Test on wilder venues (dYdX regularly hits 50+ bp per 8h during regime
  shifts). Nautilus rc5 ships a dYdX adapter; we'd need to build the same
  data fetcher for their indexer API. Multi-exchange refactor deferred to
  its own PR arc.

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
