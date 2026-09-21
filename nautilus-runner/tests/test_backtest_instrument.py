from decimal import Decimal

from nautilus_trader.model import CryptoPerpetual

from nautilus_runner.backtest.instrument import BINANCE, build_btcusdt_perp


def test_default_instrument_shape():
    inst = build_btcusdt_perp()
    assert isinstance(inst, CryptoPerpetual)
    assert str(inst.id) == "BTCUSDT-PERP.BINANCE"
    assert str(inst.raw_symbol) == "BTCUSDT"
    assert inst.venue == BINANCE
    assert inst.base_currency.code == "BTC"
    assert inst.quote_currency.code == "USDT"
    assert inst.settlement_currency.code == "USDT"
    assert inst.is_inverse is False


def test_default_precisions_match_catalog_default():
    inst = build_btcusdt_perp()
    assert inst.price_precision == 2
    assert inst.size_precision == 3
    assert str(inst.price_increment) == "0.01"
    assert str(inst.size_increment) == "0.001"


def test_default_fees_match_binance_usdm_tier_zero():
    inst = build_btcusdt_perp()
    assert inst.taker_fee == Decimal("0.000180")
    assert inst.maker_fee == Decimal("0.000200")


def test_fee_overrides_apply():
    inst = build_btcusdt_perp(taker_fee=Decimal("0.0005"), maker_fee=Decimal("0.0000"))
    assert inst.taker_fee == Decimal("0.0005")
    assert inst.maker_fee == Decimal("0.0000")


def test_precision_override_changes_increments():
    inst = build_btcusdt_perp(price_precision=1, size_precision=2)
    assert inst.price_precision == 1
    assert inst.size_precision == 2
    assert str(inst.price_increment) == "0.1"
    assert str(inst.size_increment) == "0.01"
