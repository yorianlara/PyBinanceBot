"""Pruebas unitarias para BotRegistry y persistencia de configuración de bots."""

from decimal import Decimal
from pathlib import Path
import pytest

from app.strategies.base import Strategy
from app.strategies.bollinger import BollingerBreakoutStrategy
from app.strategies.ema_cross import EmaCrossStrategy
from app.strategies.macd import MacdStrategy
from app.strategies.rsi import RsiStrategy
from app.strategies.registry import BotConfig, BotRegistry, instantiate_strategy


def test_registry_initializes_with_defaults(tmp_path: Path):
    bot_file = tmp_path / "bots.json"
    registry = BotRegistry(storage_path=bot_file)

    bots = registry.list_bots()
    assert len(bots) == 4
    keys = [b.key for b in bots]
    assert "ema_cross_9_21" in keys
    assert "rsi_14" in keys
    assert "bollinger_20_2" in keys
    assert "macd_12_26_9" in keys

    # Verificar que se creó el archivo
    assert bot_file.exists()


def test_registry_persists_and_reloads(tmp_path: Path):
    bot_file = tmp_path / "bots.json"
    reg1 = BotRegistry(storage_path=bot_file)

    # Desactivar un bot
    reg1.set_bot_active("rsi_14", False)

    # Crear una nueva instancia apuntando al mismo archivo
    reg2 = BotRegistry(storage_path=bot_file)
    rsi_bot = reg2.get_bot("rsi_14")
    assert rsi_bot is not None
    assert rsi_bot.is_active is False

    # Activar de nuevo
    reg2.set_bot_active("rsi_14", True)
    reg3 = BotRegistry(storage_path=bot_file)
    assert reg3.get_bot("rsi_14").is_active is True


def test_registry_create_bot_success(tmp_path: Path):
    bot_file = tmp_path / "bots.json"
    registry = BotRegistry(storage_path=bot_file)

    bot = registry.create_bot(
        name="Scalping EMA 5/13",
        strategy_type="ema_cross",
        symbol="BTCUSDT",
        parameters={"fast_period": 5, "slow_period": 13},
        allocation_eur=Decimal("15.00"),
        key="custom_scalper_1",
    )

    assert bot.key == "custom_scalper_1"
    assert bot.name == "Scalping EMA 5/13"
    assert bot.allocation_eur == Decimal("15.00")
    assert bot.is_active is True

    # Verificar en list_bots
    assert registry.get_bot("custom_scalper_1") is not None
    assert len(registry.list_bots()) == 5


def test_registry_create_all_supported_strategy_types(tmp_path: Path):
    bot_file = tmp_path / "bots.json"
    registry = BotRegistry(storage_path=bot_file)

    b_rsi = registry.create_bot(
        name="RSI Custom",
        strategy_type="rsi",
        symbol="ETHUSDT",
        parameters={"period": 7, "oversold": 25, "overbought": 75},
        allocation_eur=Decimal("20.00"),
    )
    assert isinstance(instantiate_strategy(b_rsi), RsiStrategy)

    b_boll = registry.create_bot(
        name="Bollinger Custom",
        strategy_type="bollinger",
        symbol="BTCUSDT",
        parameters={"period": 30, "std_dev_multiplier": 2.5},
        allocation_eur=Decimal("20.00"),
    )
    assert isinstance(instantiate_strategy(b_boll), BollingerBreakoutStrategy)

    b_macd = registry.create_bot(
        name="MACD Custom",
        strategy_type="macd",
        symbol="BTCUSDT",
        parameters={"fast_period": 8, "slow_period": 17, "signal_period": 9},
        allocation_eur=Decimal("20.00"),
    )
    assert isinstance(instantiate_strategy(b_macd), MacdStrategy)


def test_registry_create_validation_errors(tmp_path: Path):
    bot_file = tmp_path / "bots.json"
    registry = BotRegistry(storage_path=bot_file)

    # Nombre vacío
    with pytest.raises(ValueError, match="nombre"):
        registry.create_bot("", "ema_cross", "BTCUSDT", {"fast_period": 5, "slow_period": 10})

    # Símbolo vacío
    with pytest.raises(ValueError, match="símbolo"):
        registry.create_bot("Bot", "ema_cross", "  ", {"fast_period": 5, "slow_period": 10})

    # Asignación inválida
    with pytest.raises(ValueError, match="asignación"):
        registry.create_bot("Bot", "ema_cross", "BTCUSDT", {"fast_period": 5, "slow_period": 10}, allocation_eur=Decimal("-5"))

    with pytest.raises(ValueError, match="asignación"):
        registry.create_bot("Bot", "ema_cross", "BTCUSDT", {"fast_period": 5, "slow_period": 10}, allocation_eur=Decimal("150"))

    # Estrategia inválida
    with pytest.raises(ValueError, match="desconocido"):
        registry.create_bot("Bot", "invalid_strategy", "BTCUSDT", {})

    # Clave duplicada
    with pytest.raises(ValueError, match="Ya existe"):
        registry.create_bot("Bot Duplicado", "ema_cross", "BTCUSDT", {"fast_period": 9, "slow_period": 21}, key="ema_cross_9_21")


def test_registry_delete_bot(tmp_path: Path):
    bot_file = tmp_path / "bots.json"
    registry = BotRegistry(storage_path=bot_file)

    assert registry.get_bot("macd_12_26_9") is not None
    assert registry.delete_bot("macd_12_26_9") is True
    assert registry.get_bot("macd_12_26_9") is None
    assert registry.delete_bot("macd_12_26_9") is False


def test_registry_get_active_strategies(tmp_path: Path):
    bot_file = tmp_path / "bots.json"
    registry = BotRegistry(storage_path=bot_file)

    active_strats = registry.get_active_strategies()
    assert len(active_strats) == 4

    # Desactivar dos bots
    registry.set_bot_active("ema_cross_9_21", False)
    registry.set_bot_active("rsi_14", False)

    active_strats_after = registry.get_active_strategies()
    assert len(active_strats_after) == 2
    types = [type(s) for s in active_strats_after]
    assert BollingerBreakoutStrategy in types
    assert MacdStrategy in types
