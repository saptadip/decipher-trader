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
