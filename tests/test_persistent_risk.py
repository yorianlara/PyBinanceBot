from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.risk.persistent_manager import PersistentRiskManager, RiskRejected
from app.storage.database import SessionLocal
from app.storage.models import TradeFill, TradeOrder, TradePosition
from app.storage.risk_models import RiskAllocation


@pytest.fixture
def manager():
    return PersistentRiskManager()


@pytest.fixture
def cleanup():
    order_ids = []
    position_ids = []

    yield order_ids, position_ids

    with SessionLocal.begin() as session:
        if position_ids:
            session.execute(
                delete(RiskAllocation).where(
                    RiskAllocation.position_id.in_(position_ids)
                )
            )
            session.execute(
                delete(TradePosition).where(TradePosition.id.in_(position_ids))
            )

        if order_ids:
            session.execute(delete(TradeFill).where(TradeFill.order_id.in_(order_ids)))

            session.execute(
                delete(RiskAllocation).where(RiskAllocation.order_id.in_(order_ids))
            )
            session.execute(delete(TradeOrder).where(TradeOrder.id.in_(order_ids)))


def reserve(manager, strategy, client_id):
    return manager.reserve_order(
        strategy_key=strategy,
        symbol="BTCUSDT",
        side="buy",
        order_type="LIMIT",
        quantity=Decimal("0.001"),
        allocation_eur=Decimal(10),
        client_order_id=client_id,
        limit_price=Decimal(50000),
    )


def test_rejects_second_active_reservation(manager, cleanup):
    order_ids, _ = cleanup
    strategy = f"test-{uuid4().hex}"

    first = reserve(manager, strategy, f"client-{uuid4().hex}")
    order_ids.append(first)

    with pytest.raises(RiskRejected):
        reserve(manager, strategy, f"client-{uuid4().hex}")

    manager.release_order(first)


def test_cannot_convert_unfilled_order(manager, cleanup):
    order_ids, _ = cleanup
    order_id = reserve(manager, f"test-{uuid4().hex}", f"client-{uuid4().hex}")
    order_ids.append(order_id)

    with pytest.raises(RiskRejected):
        manager.convert_to_position(
            order_id,
            quantity=Decimal("0.001"),
            entry_price=Decimal(50000),
        )


def test_releasing_reservation_allows_another(manager, cleanup):
    order_ids, _ = cleanup
    strategy = f"test-{uuid4().hex}"

    first = reserve(manager, strategy, f"client-{uuid4().hex}")
    order_ids.append(first)
    manager.release_order(first)

    second = reserve(manager, strategy, f"client-{uuid4().hex}")
    order_ids.append(second)

    assert second != first
    manager.release_order(second)


def test_cannot_release_partially_filled_order(manager, cleanup):
    order_ids, _ = cleanup

    order_id = reserve(
        manager,
        f"test-{uuid4().hex}",
        f"client-{uuid4().hex}",
    )
    order_ids.append(order_id)

    manager.mark_submitted(order_id, f"exchange-{uuid4().hex}")

    with SessionLocal.begin() as session:
        order = session.get(TradeOrder, order_id)
        order.status = "partially_filled"

    with pytest.raises(RiskRejected, match="parcialmente ejecutada"):
        manager.release_order(order_id)

    with SessionLocal() as session:
        allocation = session.scalar(
            select(RiskAllocation).where(RiskAllocation.order_id == order_id)
        )
        assert allocation is not None
        assert allocation.status == "active"


def test_duplicate_client_order_id_is_rejected(manager, cleanup):
    order_ids, _ = cleanup
    strategy = f"test-{uuid4().hex}"
    client_id = f"client-{uuid4().hex}"

    first = reserve(manager, strategy, client_id)
    order_ids.append(first)

    with pytest.raises(RiskRejected, match="identificador de cliente"):
        reserve(manager, strategy, client_id)

    with SessionLocal() as session:
        orders = session.scalars(
            select(TradeOrder).where(TradeOrder.client_order_id == client_id)
        ).all()

        allocations = session.scalars(
            select(RiskAllocation).where(
                RiskAllocation.order_id == first,
                RiskAllocation.status == "active",
            )
        ).all()

        assert len(orders) == 1
        assert len(allocations) == 1

    manager.release_order(first)


def test_mark_filled_rejects_order_without_fills(manager, cleanup):
    order_ids, _ = cleanup

    order_id = reserve(
        manager,
        f"test-{uuid4().hex}",
        f"client-{uuid4().hex}",
    )
    order_ids.append(order_id)

    manager.mark_submitted(
        order_id,
        f"exchange-{uuid4().hex}",
    )

    with pytest.raises(RiskRejected, match="fills registrados"):
        manager.mark_filled(order_id)

    with SessionLocal() as session:
        order = session.get(TradeOrder, order_id)
        assert order.status == "new"

        fills = session.scalars(
            select(TradeFill).where(TradeFill.order_id == order_id)
        ).all()
        assert fills == []

    manager.release_order(order_id)


def test_reconcile_fills_does_not_duplicate_trade(manager, cleanup):
    order_ids, _ = cleanup

    order_id = reserve(
        manager,
        f"test-{uuid4().hex}",
        f"client-{uuid4().hex}",
    )
    order_ids.append(order_id)

    exchange_order_id = "12345"
    manager.mark_submitted(order_id, exchange_order_id)

    exchange_order = {
        "orderId": 12345,
        "symbol": "BTCUSDT",
        "status": "FILLED",
        "executedQty": "0.001",
    }

    exchange_trades = [
        {
            "id": 98765,
            "orderId": 12345,
            "symbol": "BTCUSDT",
            "qty": "0.001",
            "price": "50000",
            "commission": "0.00001",
            "commissionAsset": "BTC",
            "time": 1760000000000,
        }
    ]

    manager.reconcile_fills(
        order_id,
        exchange_order,
        exchange_trades,
    )

    manager.reconcile_fills(
        order_id,
        exchange_order,
        exchange_trades,
    )

    with SessionLocal() as session:
        order = session.get(TradeOrder, order_id)

        fills = session.scalars(
            select(TradeFill).where(TradeFill.order_id == order_id)
        ).all()

        assert order.status == "filled"
        assert len(fills) == 1
        assert fills[0].quantity == Decimal("0.001")
        assert fills[0].price == Decimal(50000)


def test_quote_market_order_reconciles_actual_filled_quantity(manager, cleanup):
    order_ids, position_ids = cleanup
    exchange_order_id = str(uuid4().int)
    order_id = manager.reserve_order(
        strategy_key=f"test-{uuid4().hex}",
        symbol="BTCUSDT",
        side="buy",
        order_type="MARKET",
        quantity=Decimal("0.00040"),
        quote_order_qty=Decimal(20),
        allocation_eur=Decimal("18.19"),
        client_order_id=f"client-{uuid4().hex}",
    )
    order_ids.append(order_id)
    manager.mark_submitted(order_id, exchange_order_id)

    manager.reconcile_fills(
        order_id,
        {
            "orderId": int(exchange_order_id),
            "symbol": "BTCUSDT",
            "status": "FILLED",
            "executedQty": "0.00039",
        },
        [
            {
                "id": uuid4().int,
                "orderId": int(exchange_order_id),
                "symbol": "BTCUSDT",
                "qty": "0.00039",
                "price": "50000",
                "commission": "0",
                "commissionAsset": "BNB",
                "time": 1760000000000,
            }
        ],
    )

    position_id = manager.convert_to_position(
        order_id,
        quantity=Decimal("0.00040"),
        entry_price=Decimal(50000),
    )
    position_ids.append(position_id)

    with SessionLocal() as session:
        position = session.get(TradePosition, position_id)
        assert position.quantity == Decimal("0.00039")
        assert position.entry_price == Decimal(50000)


def test_quote_market_order_rejects_fills_over_budget(manager, cleanup):
    order_ids, _ = cleanup
    exchange_order_id = str(uuid4().int)
    order_id = manager.reserve_order(
        strategy_key=f"test-{uuid4().hex}",
        symbol="BTCUSDT",
        side="buy",
        order_type="MARKET",
        quantity=Decimal("0.00040"),
        quote_order_qty=Decimal(20),
        allocation_eur=Decimal("18.19"),
        client_order_id=f"client-{uuid4().hex}",
    )
    order_ids.append(order_id)
    manager.mark_submitted(order_id, exchange_order_id)

    with pytest.raises(RiskRejected, match="supera el importe cotizado"):
        manager.reconcile_fills(
            order_id,
            {
                "orderId": int(exchange_order_id),
                "symbol": "BTCUSDT",
                "status": "FILLED",
                "executedQty": "0.00042",
            },
            [
                {
                    "id": uuid4().int,
                    "orderId": int(exchange_order_id),
                    "symbol": "BTCUSDT",
                    "qty": "0.00042",
                    "price": "50000",
                    "commission": "0",
                    "commissionAsset": "BNB",
                    "time": 1760000000000,
                }
            ],
        )

    with SessionLocal() as session:
        order = session.get(TradeOrder, order_id)
        fills = session.scalars(
            select(TradeFill).where(TradeFill.order_id == order_id)
        ).all()
        assert order.status == "new"
        assert fills == []


def create_open_position(
    manager,
    cleanup,
    *,
    quantity="0.001",
    entry_price="50000",
    commission="0",
    commission_asset="USDT",
):
    order_ids, position_ids = cleanup
    exchange_order_id = str(uuid4().int)
    order_id = reserve(
        manager,
        f"test-{uuid4().hex}",
        f"client-{uuid4().hex}",
    )
    order_ids.append(order_id)
    manager.mark_submitted(order_id, exchange_order_id)
    manager.reconcile_fills(
        order_id,
        {
            "orderId": int(exchange_order_id),
            "symbol": "BTCUSDT",
            "status": "FILLED",
            "executedQty": quantity,
        },
        [
            {
                "id": uuid4().int,
                "orderId": int(exchange_order_id),
                "symbol": "BTCUSDT",
                "qty": quantity,
                "price": entry_price,
                "commission": commission,
                "commissionAsset": commission_asset,
                "time": 1760000000000,
            }
        ],
    )
    position_id = manager.convert_to_position(
        order_id,
        quantity=Decimal(quantity),
        entry_price=Decimal(entry_price),
        base_asset="BTC",
        quote_asset="USDT",
    )
    position_ids.append(position_id)
    return position_id


def test_exit_fills_close_position_and_realize_eur_pnl(manager, cleanup):
    order_ids, position_ids = cleanup
    position_id = create_open_position(
        manager,
        cleanup,
        commission="0.001",
        commission_asset="BNB",
    )
    exchange_order_id = str(uuid4().int)
    exit_order_id = manager.reserve_exit_order(
        position_id=position_id,
        quantity=Decimal("0.000999"),
        client_order_id=f"exit-{uuid4().hex}",
    )
    order_ids.append(exit_order_id)
    manager.mark_submitted(exit_order_id, exchange_order_id)
    manager.reconcile_fills(
        exit_order_id,
        {
            "orderId": int(exchange_order_id),
            "symbol": "BTCUSDT",
            "status": "FILLED",
            "executedQty": "0.000999",
        },
        [
            {
                "id": uuid4().int,
                "orderId": int(exchange_order_id),
                "symbol": "BTCUSDT",
                "qty": "0.000999",
                "price": "51000",
                "commission": "0.000001",
                "commissionAsset": "BTC",
                "time": 1760000000000,
            }
        ],
        expected_side="sell",
    )

    quantity, average_exit, fully_closed, external_fees = (
        manager.finalize_exit_order(
            position_id=position_id,
            order_id=exit_order_id,
            eur_usdt_rate=Decimal("1.10"),
            base_asset="BTC",
            quote_asset="USDT",
        )
    )
    position_ids.append(position_id)

    with SessionLocal() as session:
        position = session.get(TradePosition, position_id)
        order = session.get(TradeOrder, exit_order_id)
        allocation = session.scalar(
            select(RiskAllocation).where(RiskAllocation.position_id == position_id)
        )
        assert quantity == Decimal("0.000999")
        assert average_exit == Decimal(51000)
        assert fully_closed is True
        assert external_fees == ["BNB"]
        assert position.status == "closed"
        assert position.quantity == Decimal(0)
        assert position.realized_pnl == Decimal("0.86181818")
        assert order.status == "reconciled"
        assert allocation.status == "released"


def test_partial_exit_reduces_open_position_without_releasing_risk(
    manager,
    cleanup,
):
    order_ids, position_ids = cleanup
    position_id = create_open_position(manager, cleanup)
    exchange_order_id = str(uuid4().int)
    exit_order_id = manager.reserve_exit_order(
        position_id=position_id,
        quantity=Decimal("0.001"),
        client_order_id=f"exit-{uuid4().hex}",
    )
    order_ids.append(exit_order_id)
    manager.mark_submitted(exit_order_id, exchange_order_id)
    manager.reconcile_fills(
        exit_order_id,
        {
            "orderId": int(exchange_order_id),
            "symbol": "BTCUSDT",
            "status": "CANCELED",
            "executedQty": "0.0004",
        },
        [
            {
                "id": uuid4().int,
                "orderId": int(exchange_order_id),
                "symbol": "BTCUSDT",
                "qty": "0.0004",
                "price": "51000",
                "commission": "0",
                "commissionAsset": "USDT",
                "time": 1760000000000,
            }
        ],
        expected_side="sell",
    )

    _, _, fully_closed, _ = manager.finalize_exit_order(
        position_id=position_id,
        order_id=exit_order_id,
        eur_usdt_rate=Decimal(1),
        base_asset="BTC",
        quote_asset="USDT",
    )
    position_ids.append(position_id)

    with SessionLocal() as session:
        position = session.get(TradePosition, position_id)
        allocation = session.scalar(
            select(RiskAllocation).where(RiskAllocation.position_id == position_id)
        )
        assert fully_closed is False
        assert position.status == "open"
        assert position.quantity == Decimal("0.0006")
        assert position.realized_pnl == Decimal("0.4")
        assert allocation.status == "active"


def test_cannot_convert_order_to_position_twice(manager, cleanup):
    order_ids, position_ids = cleanup

    order_id = reserve(
        manager,
        f"test-{uuid4().hex}",
        f"client-{uuid4().hex}",
    )
    order_ids.append(order_id)

    manager.mark_submitted(order_id, "12345")

    manager.reconcile_fills(
        order_id,
        {
            "orderId": 12345,
            "symbol": "BTCUSDT",
            "status": "FILLED",
            "executedQty": "0.001",
        },
        [
            {
                "id": 98766,
                "orderId": 12345,
                "symbol": "BTCUSDT",
                "qty": "0.001",
                "price": "50000",
                "commission": "0.00001",
                "commissionAsset": "BTC",
                "time": 1760000000000,
            }
        ],
    )

    position_id = manager.convert_to_position(
        order_id,
        quantity=Decimal("0.001"),
        entry_price=Decimal(50000),
    )
    position_ids.append(position_id)

    with pytest.raises(RiskRejected, match="reserva activa"):
        manager.convert_to_position(
            order_id,
            quantity=Decimal("0.001"),
            entry_price=Decimal(50000),
        )
