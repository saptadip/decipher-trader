"""Guards the walk_forward CLI dispatch + exit codes."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from nautilus_trader.model import BarType

REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "scripts" / "walk_forward.py"


def _load_cli_module():
    spec = importlib.util.spec_from_file_location("_wf_cli_under_test", CLI)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_interval_bar_spec_matches_run_backtest_cli():
    """walk_forward + run_backtest must agree on interval → BarSpec mapping."""
    rb_spec = importlib.util.spec_from_file_location(
        "_rb_cli", REPO / "scripts" / "run_backtest.py",
    )
    assert rb_spec is not None and rb_spec.loader is not None
    rb = importlib.util.module_from_spec(rb_spec)
    rb_spec.loader.exec_module(rb)

    wf = _load_cli_module()
    assert wf.INTERVAL_TO_BAR_SPEC == rb.INTERVAL_TO_BAR_SPEC
    assert wf.STRATEGY_CHOICES == rb.STRATEGY_CHOICES


def test_every_interval_produces_a_valid_bar_type():
    mod = _load_cli_module()
    for interval, spec in mod.INTERVAL_TO_BAR_SPEC.items():
        bt = BarType.from_str(f"BTCUSDT-PERP.BINANCE-{spec}-LAST-EXTERNAL")
        assert str(bt).endswith(f"{spec}-LAST-EXTERNAL"), f"bad bar_type for interval={interval}"


def test_cli_rejects_reversed_window(tmp_path, capsys):
    mod = _load_cli_module()
    rc = mod.main(
        [
            "--catalog", str(tmp_path),
            "--symbol", "BTCUSDT",
            "--interval", "1h",
            "--start", "2025-07-01",
            "--end", "2025-01-01",
        ],
    )
    assert rc == 2
    assert "must be after" in capsys.readouterr().err


def test_cli_rejects_non_btcusdt_symbol(tmp_path, capsys):
    mod = _load_cli_module()
    rc = mod.main(
        [
            "--catalog", str(tmp_path),
            "--symbol", "ETHUSDT",
            "--interval", "1h",
            "--start", "2025-01-01",
            "--end", "2025-07-01",
        ],
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "ETHUSDT" in err and "BTCUSDT" in err


def test_cli_rejects_zero_or_negative_month_args(tmp_path, capsys):
    """--train-months / --test-months / --step-months < 1 must exit 2 (arg validation),
    not 3 (data absence). Argparse's ``int`` accepts 0 and negatives; the CLI
    pre-validates so the exit-code contract is preserved."""
    mod = _load_cli_module()
    for flag in ("--train-months", "--test-months", "--step-months"):
        rc = mod.main(
            [
                "--catalog", str(tmp_path),
                "--symbol", "BTCUSDT",
                "--interval", "1h",
                "--start", "2025-01-01",
                "--end", "2025-07-01",
                flag, "0",
            ],
        )
        assert rc == 2, f"expected exit 2 for {flag} 0, got {rc}"
        err = capsys.readouterr().err
        assert f"{flag} must be >= 1" in err


def test_cli_exits_3_when_window_has_no_bars(tmp_path, capsys):
    """A catalog whose bars fall inside window 0 but not window 1 must exit 3."""
    import math
    from datetime import datetime, timezone

    from nautilus_trader.model import Bar, BarType, Price, Quantity

    from nautilus_runner.data.catalog import write_bars_to_catalog

    # Seed only January bars. Walk-forward with train=1m + test=1m + step=1m
    # over a Jan..Mar range yields one window (train Jan..Feb, test Feb..Mar).
    # The test-window catalog lookup returns [], so run_backtest raises
    # ValueError and the CLI must surface exit 3.
    bar_type = "BTCUSDT-PERP.BINANCE-1-HOUR-LAST-EXTERNAL"
    bt = BarType.from_str(bar_type)
    hour_ns = 60 * 60 * 1_000_000_000
    jan_start = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    bars = []
    for i in range(24 * 31):  # 744 hourly bars, all of Jan
        price = 50000.00 + 100 * math.sin(i / 24)
        p = Price(price, 2)
        ts_open = jan_start + i * hour_ns
        bars.append(Bar(bt, p, p, p, p, Quantity(1.0, 3), ts_open, ts_open + hour_ns - 1))
    write_bars_to_catalog(tmp_path, bars)

    mod = _load_cli_module()
    rc = mod.main(
        [
            "--catalog", str(tmp_path),
            "--symbol", "BTCUSDT",
            "--interval", "1h",
            "--start", "2025-01-01",
            "--end", "2025-03-01",
            "--train-months", "1",
            "--test-months", "1",
            "--step-months", "1",
        ],
    )
    assert rc == 3
    err = capsys.readouterr().err
    assert "no bars" in err and "walk-forward refused" in err


def test_cli_accepts_funding_reversion_strategy_and_hits_empty_catalog(tmp_path, capsys):
    """--strategy funding_reversion reaches the runner; empty catalog → exit 3."""
    mod = _load_cli_module()
    rc = mod.main(
        [
            "--catalog", str(tmp_path),
            "--symbol", "BTCUSDT",
            "--interval", "1h",
            "--start", "2025-01-01",
            "--end", "2025-03-01",
            "--strategy", "funding_reversion",
            "--train-months", "1",
            "--test-months", "1",
            "--step-months", "1",
        ],
    )
    assert rc == 3
    err = capsys.readouterr().err
    assert "no bars" in err and "walk-forward refused" in err


def test_cli_exits_4_when_no_windows_fit(tmp_path, capsys):
    """train=3m + test=1m needs a 4-month range minimum. A 2-month range yields 0 windows."""
    mod = _load_cli_module()
    rc = mod.main(
        [
            "--catalog", str(tmp_path),
            "--symbol", "BTCUSDT",
            "--interval", "1h",
            "--start", "2025-01-01",
            "--end", "2025-03-01",
            "--train-months", "3",
            "--test-months", "1",
            "--step-months", "1",
        ],
    )
    assert rc == 4
    assert "no walk-forward windows" in capsys.readouterr().err
