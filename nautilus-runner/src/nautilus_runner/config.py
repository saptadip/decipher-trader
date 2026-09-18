from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from nautilus_trader.adapters.hyperliquid import HyperliquidEnvironment
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---------------------------------------------------------------------------
# RC5 compatibility shim
#
# In nautilus-trader 2.0.0rc5 the Rust-backed HyperliquidEnvironment enum
# returns lower-case variant names (e.g. "testnet") from its .name property,
# whereas the plan was authored against rc6 which returns upper-case names.
# _EnvHandle wraps the real enum and normalises .name to upper-case so that
# the rest of the codebase can use conventional enum-style comparisons and the
# test assertions (.name == "TESTNET" / "MAINNET") remain correct.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _EnvHandle:
    """Thin adapter around HyperliquidEnvironment that exposes an upper-case name."""

    env: HyperliquidEnvironment

    @property
    def name(self) -> str:  # noqa: D102
        return self.env.name.upper()

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _EnvHandle):
            return self.env == other.env
        return self.env == other

    def __hash__(self) -> int:
        return hash(self.env)


class RunnerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    trading_mode: Literal["paper", "live"] = Field(default="paper", alias="TRADING_MODE")
    trader_id: str = Field(default="DECIPHER-001", alias="NAUTILUS_TRADER_ID")
    control_plane_url: str = Field(alias="CONTROL_PLANE_URL")
    operator_token: str = Field(alias="OPERATOR_TOKEN")
    heartbeat_interval_secs: int = Field(default=30, alias="HEARTBEAT_INTERVAL_SECS")
    heartbeat_miss_limit: int = Field(default=2, alias="HEARTBEAT_MISS_LIMIT")
    testnet_private_key: str | None = Field(default=None, alias="HYPERLIQUID_TESTNET_PRIVATE_KEY")
    testnet_account_id: str = Field(default="HYPERLIQUID-TESTNET-001", alias="HYPERLIQUID_TESTNET_ACCOUNT_ID")
    mainnet_private_key: str | None = Field(default=None, alias="HYPERLIQUID_MAINNET_PRIVATE_KEY")
    mainnet_account_id: str | None = Field(default=None, alias="HYPERLIQUID_MAINNET_ACCOUNT_ID")


def hyperliquid_env_for(trading_mode: str) -> _EnvHandle:
    if trading_mode == "paper":
        return _EnvHandle(HyperliquidEnvironment.TESTNET)
    if trading_mode == "live":
        return _EnvHandle(HyperliquidEnvironment.MAINNET)
    raise ValueError(f"unknown trading_mode {trading_mode!r}; expected 'paper' or 'live'")


def assert_live_startup_safe(strategies: list[dict[str, Any]], trading_mode: str) -> None:
    if trading_mode != "live":
        return
    for row in strategies:
        if row["status"] != "live":
            continue
        if not row.get("promoted_at") or not row.get("promoted_by"):
            raise RuntimeError(
                f"refuse to start in live mode: strategy id={row['id']} is live but never promoted"
            )


def build_risk_caps(row: dict[str, Any]) -> dict[str, float]:
    return {
        "strategy_id": row["id"],
        "max_notional": float(row["max_notional"]),
        "max_daily_loss": float(row["max_daily_loss"]),
        "max_position": float(row["max_position"]),
    }
