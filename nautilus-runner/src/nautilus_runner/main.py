from __future__ import annotations

import asyncio
import os
from decimal import Decimal

from nautilus_trader.adapters.hyperliquid import (
    HyperliquidDataClientConfig,
    HyperliquidDataClientFactory,
    HyperliquidExecutionClientConfig,
    HyperliquidExecutionClientFactory,
)
from nautilus_trader.common import Clock, Environment
from nautilus_trader.live import LiveExecutionEngineConfig, LiveNode, LiveRiskEngineConfig
from nautilus_trader.model import AccountId, BarType, InstrumentId, StrategyId, TraderId
from nautilus_trader.persistence import StreamingFeatherWriter

from nautilus_runner import state
from nautilus_runner.config import (
    RunnerSettings,
    assert_live_startup_safe,
    build_risk_caps,
    hyperliquid_env_for,
)
from nautilus_runner.control_plane_client import ControlPlaneClient
from nautilus_runner.heartbeat import heartbeat_loop
from nautilus_runner.kill_listener import kill_listener_loop
from nautilus_runner.state import AuditWriter, MetricsWriter
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
        .with_exec_engine_config(LiveExecutionEngineConfig(position_check_interval_secs=300.0))
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
            strategy_db_id=row["id"],
        )
        node.add_strategy(ToyMomentum(cfg))

    return node


def main() -> None:
    import signal
    import threading

    settings = RunnerSettings()
    state.metrics = MetricsWriter(settings.control_plane_url, settings.operator_token)
    state.audit_writer = AuditWriter(settings.control_plane_url, settings.operator_token)
    rows = asyncio.run(_fetch_strategies(settings))
    assert_live_startup_safe(rows, settings.trading_mode)

    os.makedirs(settings.streaming_catalog_path, exist_ok=True)

    node = _build_node(settings, rows)

    # Attach a StreamingFeatherWriter so every Nautilus event (orders, fills,
    # positions, bars, etc.) is persisted to Parquet files under the catalog path.
    # StreamingConfig is not available for LiveNode in the installed rc5 wheel
    # (the `streaming` feature flag is absent), so we wire via the standalone
    # StreamingFeatherWriter which subscribes directly to the global message bus.
    feather_writer = StreamingFeatherWriter(
        path=settings.streaming_catalog_path,
        cache=node.cache,
        clock=Clock.new_test(),
        fs_protocol="file",
        flush_interval_ms=1000,
    )
    feather_writer.subscribe()

    stop_event = asyncio.Event()
    ws_url = (
        settings.control_plane_url.replace("http://", "ws://").replace(
            "https://", "wss://"
        )
        + "/events"
    )

    def _trigger_kill() -> None:
        try:
            node.stop()
        except Exception:
            pass

    loop = asyncio.new_event_loop()

    def _background() -> None:
        asyncio.set_event_loop(loop)
        client = ControlPlaneClient(settings.control_plane_url, settings.operator_token)
        loop.run_until_complete(
            asyncio.gather(
                heartbeat_loop(
                    client,
                    settings.heartbeat_interval_secs,
                    settings.heartbeat_miss_limit,
                    stop_event,
                    _trigger_kill,
                ),
                kill_listener_loop(ws_url, _trigger_kill, stop_event),
            )
        )

    t = threading.Thread(target=_background, daemon=True)
    t.start()

    def _handle_sig(_signum, _frame) -> None:
        loop.call_soon_threadsafe(stop_event.set)
        node.stop()

    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)

    node.run()
    loop.call_soon_threadsafe(stop_event.set)


if __name__ == "__main__":
    main()
