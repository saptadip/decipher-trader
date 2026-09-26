# ToyMomentum — empirical log

Per-catalog backtest results for the ``toy_momentum`` strategy (fast/slow SMA
crossover, defined in ``nautilus-runner/strategies/toy_momentum/strategy.py``).
Newest at the top. Numbers here are point-in-time findings on specific
catalogs; the strategy body itself has not changed between entries unless
noted.

The strategy is fully parameterized by ``fast_period`` / ``slow_period`` /
``bar_type``, so "Momentum1m" / "Momentum4h" / "Momentum1d" are all the same
class invoked with different config values — no separate strategy files.

---

## 2026-09-26 — Binance BTCUSDT 4-hour, 2022-01-01 → 2025-01-01 (3 years)

**Verdict: weak positive signal — first non-underwater strategy on the
harness, but far from promotable.**

Direct comparison to the FundingReversion 3-year retest (which produced
−$0.29 net OOS across 30 test months): momentum on 4h bars comes back with
**+$54.43 net OOS** across the same window shape. Meaningfully positive,
fee-tolerant, but far from meeting the G7 gate (which requires Sharpe > 1).

### Catalog

- 6,570 4-hour BTCUSDT bars (integrity clean).
- Same catalog directory shape as the 1-hour Binance catalog used elsewhere.
- Funding included but not used by this strategy — the momentum branch
  does not consume `funding_events`.

### Grid × walk-forward

`--fast-grid 3,6,12 --slow-grid 24,48,96` (9 combos) with
`--train-months 6 --test-months 3 --step-months 3` gives 10 walk-forward
windows on the 3-year range. 9 × 10 × 2 = 180 engine spins.

Per-window winner + OOS test result:

| window | train              | winner (fast, slow) | OOS trades | OOS PnL (USDT) | OOS Sharpe |
|--------|--------------------|---------------------|------------|----------------|------------|
| w0     | 2022-01..07        | (12, 48)            | 12         | −3.77          | −0.234     |
| w1     | 2022-04..10        | (12, 96)            | 5          | +0.24          | +0.047     |
| w2     | 2022-07..2023-01   | (3, 24)             | 27         | +5.92          | +0.150     |
| w3     | 2022-10..2023-04   | (3, 96)             | 18         | −2.84          | −0.142     |
| w4     | 2023-01..07        | (12, 24)            | 27         | +0.99          | +0.051     |
| w5     | 2023-04..10        | (12, 24)            | 24         | +9.58          | +0.192     |
| w6     | 2023-07..2024-01   | (12, 24)            | 24         | **+27.62**     | +0.302     |
| w7     | 2023-10..2024-04   | (12, 96)            | 9          | −1.99          | −0.066     |
| w8     | 2024-01..07        | (12, 48)            | 12         | +2.25          | +0.050     |
| w9     | 2024-04..10        | (12, 96)            | 5          | +16.44         | +0.297     |

- **Total OOS PnL: +$54.43** over 30 test months on $10k starting capital.
- 7 of 10 windows positive.
- `fast=12` wins 6/10 windows (most stable axis).
- Two big-win windows (w6 +$27.62, w9 +$16.44) drove most of the total;
  the rest are noise around zero — trend-following captures rallies,
  breaks even in chop. Standard momentum signature.

### Fee sensitivity (in-sample, fixed `fast=12, slow=24`, full 3 years)

| taker fee            | trades | PnL (USDT) | Sharpe |
|----------------------|--------|-----------:|-------:|
| 1.8 bp (Binance USDM)| 303    | **+79.22** | +0.102 |
| 1 bp                 | 303    | +81.12     | +0.104 |
| 0.5 bp               | 303    | +82.31     | +0.105 |
| 0 (theoretical)      | 303    | +83.50     | +0.107 |

**Fees eat only ~5% of the raw signal** — this strategy is fee-tolerant,
which is exactly the property FundingReversion lacks (fees erased 50%+
of that strategy's signal on Hyperliquid, 100%+ on Binance).

### Interpretation

- **In-sample (fixed params, full range) PnL +$79 → OOS PnL +$54:**
  ~30% OOS decay. Normal for lightly-fit crossover strategies.
- **Annualized return: ~0.24% APR** at real fees. Small but real; not
  wiped by taker friction.
- **Sharpe 0.06-0.10 is well below promotion criteria** (G7 gate wants
  Sharpe > 1). The strategy is a positive-drift result, not a real edge.
- **Winner-param drift:** `fast=12` is the modal choice but `slow` varies
  between 24/48/96 by window. Suggests the underlying edge is thin
  enough that regime shifts move the optimum.

### Why this matters even though Sharpe is low

- **First strategy on the harness that is net-positive OOS after fees.**
  FundingReversion is net-flat or slightly negative on both Binance and
  Hyperliquid; ToyMomentum at 1-minute loses to fees; Momentum4h clears.
- Proves the backtest + walk-forward + param-search stack can actually
  distinguish a working strategy from a losing one on real 3-year data
  — the harness produces the expected sign.
- Establishes a **fee-tolerance baseline**: any Session-3 candidate whose
  fees erode >20% of raw signal is a red flag, regardless of Sharpe.

### Follow-up options

- **Wider grid, longer periods.** `fast in [12,24,48]` × `slow in
  [72,96,144,192]` might catch more of the "trend regime → hold longer"
  behavior. Would need the walk-forward window to grow (train >= slow ×
  bar_interval).
- **Add a volatility filter.** Momentum decays in chop; a Bollinger /
  ATR filter that switches the strategy off during low-vol regimes could
  raise Sharpe without sacrificing the trending-window wins.
- **Same shape on other symbols.** ETHUSDT and SOLUSDT trend more
  strongly than BTCUSDT; the same fast=12/slow=24 pair may produce
  higher Sharpe on alts. Requires the multi-symbol refactor (deferred).
- **Combine with a mean-reversion signal.** ToyMomentum losses cluster in
  choppy 2022 windows (w0, w3); if a mean-reversion strategy is
  net-positive in those same windows, an ensemble could raise the
  aggregate Sharpe. Requires a working second strategy first.

### Not promotable, and here's why explicitly

Session-4 paper-forward is gated by:

- Sharpe > 1 (this strategy: 0.06-0.10)
- Max drawdown < 20% (not measured here)
- ≥ 500 trades (this strategy: 303 over 3 years)
- Positive edge after 5 bps taker fee (this strategy: yes, +$79 → +$45
  extrapolated at 5 bp taker)

Only the fee criterion is passed. Do not promote to paper mode.

---
