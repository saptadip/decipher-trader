from __future__ import annotations

import asyncio
from decimal import Decimal

from nautilus_trader.adapters.hyperliquid import (
    HyperliquidDataClientConfig,
    HyperliquidDataClientFactory,
    HyperliquidExecutionClientConfig,
    HyperliquidExecutionClientFactory,
)
from nautilus_trader.common import Environment
from nautilus_trader.live import LiveNode, LiveRiskEngineConfig
from nautilus_trader.model import AccountId, BarType, InstrumentId, StrategyId, TraderId

from nautilus_runner.config import (
    RunnerSettings,
    assert_live_startup_safe,
    build_risk_caps,
    hyperliquid_env_for,
)
from nautilus_runner.control_plane_client import ControlPlaneClient
from strategies.toy_momentum.strategy import ToyMomentum, ToyMomentumConfig

HYPERLIQUID = "HYPERLIQUID"


async def _fetch_strategies(settings: RunnerSettings) -> list[dict]:
    client = ControlPlaneClient(settings.control_plane_url, settings.operator_token)
    wanted = ["paper"] if settings.trading_mode == "paper" else ["live"]
    return await client.fetch_strategies(wanted)


def _build_node(settings: RunnerSettings, strategy_rows: list[dict]) -> LiveNode:
    env = hyperliquid_env_for(settings.trading_mode)
    account_id_str = (
        settings.testnet_account_id
        if settings.trading_mode == "paper"
        else settings.mainnet_account_id
    )
    private_key = (
        settings.testnet_private_key
        if settings.trading_mode == "paper"
        else settings.mainnet_private_key
    )
    if not account_id_str or not private_key:
        raise RuntimeError(
            f"missing Hyperliquid credentials for mode={settings.trading_mode}"
        )

    # R1: bypass=True — no per-instrument RiskEngine config in Phase 1; G3 enforced at
    # the strategy layer via ToyMomentum._submit_capped.
    node = (
        LiveNode.builder(
            f"DECIPHER-{settings.trading_mode.upper()}",
            TraderId.from_str(settings.trader_id),
            Environment.LIVE,
        )
        .with_reconciliation(reconciliation=True)
        .with_risk_engine_config(LiveRiskEngineConfig(bypass=True))
        .add_data_client(
            None,
            HyperliquidDataClientFactory(),
            HyperliquidDataClientConfig(environment=env, private_key=private_key),
        )
        .add_exec_client(
            None,
            HyperliquidExecutionClientFactory(),
            HyperliquidExecutionClientConfig(
                account_id=AccountId(account_id_str),
                environment=env,
                private_key=private_key,
            ),
        )
        .build()
    )

    for row in strategy_rows:
        caps = build_risk_caps(row)
        instrument = InstrumentId.from_str(f"BTC-USD-PERP.{HYPERLIQUID}")
        bar_type = BarType.from_str(f"{instrument}-1-MINUTE-MID-INTERNAL")
        cfg = ToyMomentumConfig(
            instrument_id=instrument,
            bar_type=bar_type,
            trade_size=Decimal("0.001"),
            max_notional=caps["max_notional"],
            max_daily_loss=caps["max_daily_loss"],
            max_position=caps["max_position"],
            strategy_id=StrategyId.from_str(f"DECIPHER-{row['id']:04d}"),
        )
        node.add_strategy(ToyMomentum(cfg))

    return node


def main() -> None:
    settings = RunnerSettings()
    rows = asyncio.run(_fetch_strategies(settings))
    assert_live_startup_safe(rows, settings.trading_mode)
    node = _build_node(settings, rows)
    node.run()


if __name__ == "__main__":
    main()
