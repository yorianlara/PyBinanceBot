"""Registro y gestión dinámica de bots y estrategias."""

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.strategies.base import Strategy
from app.strategies.bollinger import BollingerBreakoutStrategy
from app.strategies.ema_cross import EmaCrossStrategy
from app.strategies.macd import MacdStrategy
from app.strategies.rsi import RsiStrategy

logger = logging.getLogger(__name__)

DEFAULT_BOTS_PATH = Path("/opt/trading-bots/data/bots.json")


@dataclass
class BotConfig:
    """Configuración de un bot individual dentro del ecosistema."""

    key: str
    name: str
    strategy_type: str  # "ema_cross", "rsi", "bollinger", "macd"
    symbol: str
    parameters: dict[str, Any]
    allocation_eur: Decimal
    is_active: bool = True
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "strategy_type": self.strategy_type,
            "symbol": self.symbol.upper(),
            "parameters": self.parameters,
            "allocation_eur": str(self.allocation_eur),
            "is_active": self.is_active,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BotConfig":
        return cls(
            key=data["key"],
            name=data["name"],
            strategy_type=data["strategy_type"],
            symbol=data["symbol"].upper(),
            parameters=data["parameters"],
            allocation_eur=Decimal(str(data["allocation_eur"])),
            is_active=data.get("is_active", True),
            created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
        )


def instantiate_strategy(bot: BotConfig) -> Strategy:
    """Convierte un BotConfig en una instancia ejecutable de Strategy."""
    stype = bot.strategy_type.lower()
    params = bot.parameters

    if stype == "ema_cross":
        fast = int(params.get("fast_period", 9))
        slow = int(params.get("slow_period", 21))
        return EmaCrossStrategy(fast_period=fast, slow_period=slow)

    elif stype == "rsi":
        period = int(params.get("period", 14))
        oversold = Decimal(str(params.get("oversold", 30)))
        overbought = Decimal(str(params.get("overbought", 70)))
        return RsiStrategy(period=period, oversold=oversold, overbought=overbought)

    elif stype == "bollinger":
        period = int(params.get("period", 20))
        std_dev = Decimal(str(params.get("std_dev_multiplier", 2.0)))
        return BollingerBreakoutStrategy(period=period, std_dev_multiplier=std_dev)

    elif stype == "macd":
        fast = int(params.get("fast_period", 12))
        slow = int(params.get("slow_period", 26))
        signal = int(params.get("signal_period", 9))
        return MacdStrategy(fast_period=fast, slow_period=slow, signal_period=signal)

    raise ValueError(f"Tipo de estrategia desconocido: '{stype}'")


class BotRegistry:
    """Almacén persistente y gestor de ciclo de vida de bots configurables."""

    def __init__(self, storage_path: Path | str = DEFAULT_BOTS_PATH):
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._bots: dict[str, BotConfig] = {}
        self._load()

    def _default_bots(self) -> list[BotConfig]:
        now = datetime.now(timezone.utc).isoformat()
        return [
            BotConfig(
                key="ema_cross_9_21",
                name="Bot Tendencia (EMA 9/21)",
                strategy_type="ema_cross",
                symbol="BTCUSDT",
                parameters={"fast_period": 9, "slow_period": 21},
                allocation_eur=Decimal("20.00"),
                is_active=True,
                created_at=now,
            ),
            BotConfig(
                key="rsi_14",
                name="Bot Reversión (RSI 14)",
                strategy_type="rsi",
                symbol="BTCUSDT",
                parameters={"period": 14, "oversold": 30, "overbought": 70},
                allocation_eur=Decimal("20.00"),
                is_active=True,
                created_at=now,
            ),
            BotConfig(
                key="bollinger_20_2",
                name="Bot Rupturas (Bollinger 20/2.0)",
                strategy_type="bollinger",
                symbol="BTCUSDT",
                parameters={"period": 20, "std_dev_multiplier": 2.0},
                allocation_eur=Decimal("20.00"),
                is_active=True,
                created_at=now,
            ),
            BotConfig(
                key="macd_12_26_9",
                name="Bot Impulso (MACD 12/26/9)",
                strategy_type="macd",
                symbol="BTCUSDT",
                parameters={"fast_period": 12, "slow_period": 26, "signal_period": 9},
                allocation_eur=Decimal("20.00"),
                is_active=True,
                created_at=now,
            ),
        ]

    def _load(self) -> None:
        if not self.storage_path.exists():
            for b in self._default_bots():
                self._bots[b.key] = b
            self._save()
            return

        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._bots = {
                item["key"]: BotConfig.from_dict(item) for item in data.get("bots", [])
            }
        except Exception as exc:
            logger.error("Error leyendo %s, usando predeterminados: %s", self.storage_path, exc)
            for b in self._default_bots():
                self._bots[b.key] = b
            self._save()

    def _save(self) -> None:
        payload = {"bots": [b.to_dict() for b in self._bots.values()]}
        with open(self.storage_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

    def list_bots(self) -> list[BotConfig]:
        return list(self._bots.values())

    def get_bot(self, key: str) -> BotConfig | None:
        return self._bots.get(key)

    def set_bot_active(self, key: str, is_active: bool) -> BotConfig:
        bot = self._bots.get(key)
        if not bot:
            raise KeyError(f"No existe el bot con clave '{key}'")
        bot.is_active = is_active
        self._save()
        logger.info("Bot '%s' cambiado a %s", key, "ACTIVO" if is_active else "INACTIVO")
        return bot

    def create_bot(
        self,
        name: str,
        strategy_type: str,
        symbol: str,
        parameters: dict[str, Any],
        allocation_eur: Decimal = Decimal("20.00"),
        key: str | None = None,
    ) -> BotConfig:
        if not name.strip():
            raise ValueError("El nombre del bot no puede estar vacío.")
        if not symbol.strip():
            raise ValueError("El símbolo es obligatorio (ej: BTCUSDT).")
        if allocation_eur <= 0 or allocation_eur > Decimal("100"):
            raise ValueError("La asignación debe estar entre 0 y 100 EUR.")

        # Generar clave única si no se suministra
        auto_key = key or f"{strategy_type.lower()}_{symbol.lower()}_{len(self._bots) + 1}"
        if auto_key in self._bots:
            raise ValueError(f"Ya existe un bot con la clave '{auto_key}'.")

        bot = BotConfig(
            key=auto_key,
            name=name.strip(),
            strategy_type=strategy_type.lower(),
            symbol=symbol.upper().strip(),
            parameters=parameters,
            allocation_eur=allocation_eur,
            is_active=True,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        # Validar instanciación antes de guardar
        instantiate_strategy(bot)

        self._bots[bot.key] = bot
        self._save()
        logger.info("Nuevo bot creado: '%s' (%s %s)", bot.key, bot.name, bot.symbol)
        return bot

    def delete_bot(self, key: str) -> bool:
        if key in self._bots:
            del self._bots[key]
            self._save()
            logger.info("Bot eliminado: '%s'", key)
            return True
        return False

    def get_active_strategies(self) -> list[Strategy]:
        """Devuelve las instancias de estrategias que están marcadas como activas."""
        strategies = []
        for b in self._bots.values():
            if b.is_active:
                try:
                    strategies.append(instantiate_strategy(b))
                except Exception as exc:
                    logger.error("Error instanciando bot activo '%s': %s", b.key, exc)
        return strategies

