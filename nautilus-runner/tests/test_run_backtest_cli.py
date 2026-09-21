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
