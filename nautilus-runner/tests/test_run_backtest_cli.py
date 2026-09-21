"""Guards the run_backtest CLI dispatch table."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from nautilus_trader.model import BarType

REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "scripts" / "run_backtest.py"


def _load_cli_module():
    spec = importlib.util.spec_from_file_location("_run_bt_cli_under_test", CLI)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_interval_bar_spec_matches_download_cli():
    """The two CLIs must agree on interval-to-BarSpec so a caught catalog wins."""
    from nautilus_runner.data import __init__ as _  # noqa: F401 - keep package resolution warm

    dl_spec = importlib.util.spec_from_file_location(
        "_dl_cli_under_test",
        REPO / "scripts" / "download_data.py",
    )
    assert dl_spec is not None and dl_spec.loader is not None
    dl = importlib.util.module_from_spec(dl_spec)
    dl_spec.loader.exec_module(dl)

    bt = _load_cli_module()
    assert bt.INTERVAL_TO_BAR_SPEC == dl.INTERVAL_TO_BAR_SPEC


def test_every_interval_produces_a_valid_bar_type():
    mod = _load_cli_module()
    for interval, spec in mod.INTERVAL_TO_BAR_SPEC.items():
        bt = BarType.from_str(f"BTCUSDT-PERP.BINANCE-{spec}-LAST-EXTERNAL")
        assert str(bt).endswith(f"{spec}-LAST-EXTERNAL"), f"bad bar_type for interval={interval}"


def test_strategy_choices_is_non_empty():
    mod = _load_cli_module()
    assert "toy_momentum" in mod.STRATEGY_CHOICES


def test_cli_rejects_non_btcusdt_symbol(tmp_path, capsys):
    """--symbol other than BTCUSDT must exit 2 before touching the engine."""
    mod = _load_cli_module()
    rc = mod.main(
        [
            "--catalog", str(tmp_path),
            "--symbol", "ETHUSDT",
            "--interval", "1h",
            "--start", "2025-06-01",
            "--end", "2025-06-08",
        ],
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "ETHUSDT" in err and "BTCUSDT" in err


def test_cli_rejects_toy_momentum_strategy(tmp_path, capsys):
    """--strategy toy_momentum must exit 2 with a clear pending-adaptation message."""
    mod = _load_cli_module()
    rc = mod.main(
        [
            "--catalog", str(tmp_path),
            "--symbol", "BTCUSDT",
            "--interval", "1h",
            "--start", "2025-06-01",
            "--end", "2025-06-08",
            "--strategy", "toy_momentum",
        ],
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "toy_momentum" in err and "buy_and_hold" in err


def test_cli_rejects_reversed_window(tmp_path, capsys):
    """--end on or before --start must exit 2."""
    mod = _load_cli_module()
    rc = mod.main(
        [
            "--catalog", str(tmp_path),
            "--symbol", "BTCUSDT",
            "--interval", "1h",
            "--start", "2025-06-08",
            "--end", "2025-06-01",
        ],
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "must be after" in err
