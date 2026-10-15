
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.database import Base


class RiskAccount(Base):
    __tablename__ = "risk_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    pnl_day: Mapped[date] = mapped_column(Date, nullable=False)
    daily_pnl_eur: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("0"), nullable=False
    )
    total_pnl_eur: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("0"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class RiskAllocation(Base):
    __tablename__ = "risk_allocations"
    __table_args__ = (
        UniqueConstraint("order_id", name="uq_risk_allocation_order"),
        UniqueConstraint("position_id", name="uq_risk_allocation_position"),
        CheckConstraint("amount_eur > 0", name="ck_risk_allocation_amount"),
        CheckConstraint(
            "allocation_type IN ('order', 'position')",
            name="ck_risk_allocation_type",
        ),
        CheckConstraint(
            "status IN ('active', 'released')",
            name="ck_risk_allocation_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("trade_orders.id"), nullable=True
    )
    position_id: Mapped[int | None] = mapped_column(
        ForeignKey("trade_positions.id"), nullable=True
    )
    strategy_key: Mapped[str] = mapped_column(String(80), nullable=False)
    amount_eur: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False
    )
    allocation_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="active", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
