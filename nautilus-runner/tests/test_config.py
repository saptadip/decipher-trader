import pytest

from nautilus_runner.config import (
    RunnerSettings,
    assert_live_startup_safe,
    build_risk_caps,
    hyperliquid_env_for,
)


def test_env_paper_maps_to_testnet():
    env = hyperliquid_env_for("paper")
    assert env.name.upper() == "TESTNET"


def test_env_live_maps_to_mainnet():
    env = hyperliquid_env_for("live")
    assert env.name.upper() == "MAINNET"


def test_env_invalid_mode_raises():
    with pytest.raises(ValueError):
        hyperliquid_env_for("prod")


def test_g1_rejects_live_row_missing_promotion_evidence():
    rows = [{"id": 1, "status": "live", "promoted_at": None, "promoted_by": None}]
    with pytest.raises(RuntimeError, match="never promoted"):
        assert_live_startup_safe(rows, "live")


def test_g1_allows_promoted_live_rows():
    rows = [{"id": 1, "status": "live", "promoted_at": "2026-01-01T00:00:00Z", "promoted_by": "operator"}]
    assert_live_startup_safe(rows, "live") is None


def test_g1_no_check_in_paper_mode():
    rows = [{"id": 1, "status": "live", "promoted_at": None, "promoted_by": None}]
    assert_live_startup_safe(rows, "paper") is None


def test_build_risk_caps_shape():
    row = {"id": 5, "max_notional": 100.0, "max_daily_loss": 10.0, "max_position": 0.5}
    caps = build_risk_caps(row)
    assert caps == {"strategy_id": 5, "max_notional": 100.0, "max_daily_loss": 10.0, "max_position": 0.5}


def test_settings_defaults(monkeypatch):
    monkeypatch.setenv("CONTROL_PLANE_URL", "http://cp:8000")
    monkeypatch.setenv("OPERATOR_TOKEN", "t")
    s = RunnerSettings()
    assert s.trading_mode == "paper"
    assert s.trader_id == "DECIPHER-001"
