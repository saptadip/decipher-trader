"""Guards the param_search CLI dispatch + exit codes."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from nautilus_trader.model import BarType

REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "scripts" / "param_search.py"


def _load_cli_module():
    spec = importlib.util.spec_from_file_location("_ps_cli_under_test", CLI)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_interval_bar_spec_matches_run_backtest_cli():
    rb = _load_module("_rb_cli", REPO / "scripts" / "run_backtest.py")
    ps = _load_cli_module()
    assert ps.INTERVAL_TO_BAR_SPEC == rb.INTERVAL_TO_BAR_SPEC
    assert ps.STRATEGY_CHOICES == rb.STRATEGY_CHOICES


def test_shared_flag_defaults_agree_across_all_three_clis():
    """All three CLIs must ship identical defaults for shared flags.

    Silent drift (e.g. --maker-fee default changing in only one CLI) would
    produce different results for the "same" invocation. The Session-2-PR-C
    review deferred a shared cli_common module with "revisit if a third CLI
    arrives" — this is the parity fence in lieu of that consolidation.
    """
    rb = _load_module("_rb_cli", REPO / "scripts" / "run_backtest.py")
    wf = _load_module("_wf_cli", REPO / "scripts" / "walk_forward.py")
    ps = _load_cli_module()

    def defaults(mod, argv):
        # Parse a minimal successful arg set and read Namespace defaults.
        return vars(mod._parse_args(argv))

    base = [
        "--catalog", "/tmp/x",
        "--symbol", "BTCUSDT",
        "--interval", "1h",
        "--start", "2025-01-01",
        "--end", "2025-07-01",
    ]
    rb_d = defaults(rb, base)
    wf_d = defaults(wf, base)
    ps_d = defaults(ps, base)

    for flag in (
        "starting_usdt",
        "taker_fee",
        "maker_fee",
        "price_precision",
        "size_precision",
    ):
        assert rb_d[flag] == wf_d[flag] == ps_d[flag], (
            f"{flag} default drifts: run_backtest={rb_d[flag]!r} "
            f"walk_forward={wf_d[flag]!r} param_search={ps_d[flag]!r}"
        )

    # Scalar ToyMomentum flags are in run_backtest + walk_forward + param_search,
    # but param_search uses -grid variants for fast/slow, so compare only what
    # is truly shared as scalars.
    for flag in ("max_position", "max_notional", "max_daily_loss"):
        assert rb_d[flag] == wf_d[flag] == ps_d[flag], (
            f"{flag} default drifts: run_backtest={rb_d[flag]!r} "
            f"walk_forward={wf_d[flag]!r} param_search={ps_d[flag]!r}"
        )

    for flag in ("train_months", "test_months", "step_months"):
        assert wf_d[flag] == ps_d[flag], (
            f"{flag} default drifts: walk_forward={wf_d[flag]!r} param_search={ps_d[flag]!r}"
        )


def test_every_interval_produces_a_valid_bar_type():
    mod = _load_cli_module()
    for interval, spec in mod.INTERVAL_TO_BAR_SPEC.items():
        bt = BarType.from_str(f"BTCUSDT-PERP.BINANCE-{spec}-LAST-EXTERNAL")
        assert str(bt).endswith(f"{spec}-LAST-EXTERNAL")


def test_cli_rejects_reversed_window(tmp_path, capsys):
    mod = _load_cli_module()
    rc = mod.main(
        [
            "--catalog", str(tmp_path),
            "--symbol", "BTCUSDT", "--interval", "1h",
            "--start", "2025-07-01", "--end", "2025-01-01",
        ],
    )
    assert rc == 2
    assert "must be after" in capsys.readouterr().err


def test_cli_rejects_non_btcusdt_symbol(tmp_path, capsys):
    mod = _load_cli_module()
    rc = mod.main(
        [
            "--catalog", str(tmp_path),
            "--symbol", "ETHUSDT", "--interval", "1h",
            "--start", "2025-01-01", "--end", "2025-07-01",
        ],
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "ETHUSDT" in err and "BTCUSDT" in err


def test_cli_rejects_zero_month_args(tmp_path, capsys):
    mod = _load_cli_module()
    rc = mod.main(
        [
            "--catalog", str(tmp_path),
            "--symbol", "BTCUSDT", "--interval", "1h",
            "--start", "2025-01-01", "--end", "2025-07-01",
            "--train-months", "0",
        ],
    )
    assert rc == 2
    assert "--train-months must be >= 1" in capsys.readouterr().err


def test_cli_rejects_toy_momentum_grid_flag_under_buy_and_hold(tmp_path, capsys):
    """--fast-grid / --slow-grid on buy_and_hold would silently no-op; refuse."""
    mod = _load_cli_module()
    rc = mod.main(
        [
            "--catalog", str(tmp_path),
            "--symbol", "BTCUSDT", "--interval", "1h",
            "--start", "2025-01-01", "--end", "2025-07-01",
            "--strategy", "buy_and_hold",
            "--fast-grid", "3,5,10",
        ],
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "--fast-grid" in err and "toy_momentum" in err


def test_cli_rejects_buy_and_hold_grid_flag_under_toy_momentum(tmp_path, capsys):
    """--trade-size-grid on toy_momentum would silently no-op; refuse."""
    mod = _load_cli_module()
    rc = mod.main(
        [
            "--catalog", str(tmp_path),
            "--symbol", "BTCUSDT", "--interval", "1h",
            "--start", "2025-01-01", "--end", "2025-07-01",
            "--strategy", "toy_momentum",
            "--trade-size-grid", "0.001,0.002",
        ],
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "--trade-size-grid" in err and "buy_and_hold" in err


def test_cli_rejects_toy_momentum_slow_le_fast(tmp_path, capsys):
    """ToyMomentum asserts slow>fast on construction; the CLI fails earlier."""
    mod = _load_cli_module()
    rc = mod.main(
        [
            "--catalog", str(tmp_path),
            "--symbol", "BTCUSDT", "--interval", "1h",
            "--start", "2025-01-01", "--end", "2025-07-01",
            "--strategy", "toy_momentum",
            "--fast-grid", "5,10",
            "--slow-grid", "5,20",  # (fast=5, slow=5) and (fast=10, slow=5) are illegal
        ],
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "slow must exceed fast" in err


def test_cli_exits_4_when_no_windows_fit(tmp_path, capsys):
    mod = _load_cli_module()
    rc = mod.main(
        [
            "--catalog", str(tmp_path),
            "--symbol", "BTCUSDT", "--interval", "1h",
            "--start", "2025-01-01", "--end", "2025-03-01",
            "--train-months", "3", "--test-months", "1", "--step-months", "1",
        ],
    )
    assert rc == 4
    assert "no walk-forward windows" in capsys.readouterr().err


def test_parse_int_grid_helper():
    mod = _load_cli_module()
    assert mod._parse_int_grid("3,5,10") == [3, 5, 10]
    assert mod._parse_int_grid(" 3 , 5 , 10 ") == [3, 5, 10]
    assert mod._parse_int_grid("42") == [42]
