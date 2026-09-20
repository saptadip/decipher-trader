"""Guards the CLI dispatch tables that caused two real bugs during first e2e run."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from nautilus_trader.model import BarType

REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "scripts" / "download_data.py"


def _load_cli_module():
    spec = importlib.util.spec_from_file_location("_cli_under_test", CLI)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bar_spec_and_ns_tables_cover_same_intervals():
    mod = _load_cli_module()
    assert set(mod.INTERVAL_TO_NS.keys()) == set(mod.INTERVAL_TO_BAR_SPEC.keys())


def test_every_interval_produces_a_valid_bar_type():
    mod = _load_cli_module()
    for interval, spec in mod.INTERVAL_TO_BAR_SPEC.items():
        bt = BarType.from_str(f"BTCUSDT-PERP.BINANCE-{spec}-LAST-EXTERNAL")
        assert str(bt).endswith(f"{spec}-LAST-EXTERNAL"), f"bad bar_type for interval={interval}"


def test_ns_table_values_match_interval_labels():
    mod = _load_cli_module()
    expect = {
        "1m": 60 * 1_000_000_000,
        "5m": 5 * 60 * 1_000_000_000,
        "15m": 15 * 60 * 1_000_000_000,
        "1h": 60 * 60 * 1_000_000_000,
        "4h": 4 * 60 * 60 * 1_000_000_000,
        "1d": 24 * 60 * 60 * 1_000_000_000,
    }
    assert mod.INTERVAL_TO_NS == expect
