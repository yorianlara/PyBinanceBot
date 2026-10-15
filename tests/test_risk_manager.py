
import pytest

from app.risk.manager import RiskManager, RiskState


def test_allows_valid_allocation():
    manager = RiskManager()
    manager.validate(RiskState(), 20.0)


def test_rejects_allocation_above_strategy_limit():
    manager = RiskManager()

    with pytest.raises(ValueError, match="por estrategia"):
        manager.validate(RiskState(), 21.0)


def test_rejects_daily_loss_limit():
    manager = RiskManager()

    state = RiskState(daily_loss_eur=2.0)

    with pytest.raises(ValueError, match="diaria"):
        manager.validate(state, 10.0)


def test_rejects_total_loss_limit():
    manager = RiskManager()

    state = RiskState(total_loss_eur=5.0)

    with pytest.raises(ValueError, match="total"):
        manager.validate(state, 10.0)


def test_rejects_second_open_position():
    manager = RiskManager()

    state = RiskState(open_positions=1)

    with pytest.raises(ValueError, match="posiciones"):
        manager.validate(state, 10.0)


def test_rejects_capital_overflow():
    manager = RiskManager()

    state = RiskState(allocated_capital_eur=90.0)

    with pytest.raises(ValueError, match="capital máximo"):
        manager.validate(state, 20.0)
