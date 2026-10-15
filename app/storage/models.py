
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.database import Base


class BotRun(Base):
    __tablename__ = "bot_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    mode: Mapped[str] = mapped_column(String(20), default="testnet")
    status: Mapped[str] = mapped_column(String(20), default="running")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    details: Mapped[str | None] = mapped_column(Text)


class MarketCandle(Base):
    __tablename__ = "market_candles"
    __table_args__ = (
        UniqueConstraint(
            "symbol", "interval", "open_time",
            name="uq_market_candle_symbol_interval_open_time",
        ),
        Index("ix_market_candles_symbol_open_time", "symbol", "open_time"),
        CheckConstraint("high >= low", name="ck_candle_high_gte_low"),
        CheckConstraint("volume >= 0", name="ck_candle_volume_nonnegative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(30))
    interval: Mapped[str] = mapped_column(String(10))
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    close_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    open: Mapped[Decimal] = mapped_column(Numeric(20, 10))
    high: Mapped[Decimal] = mapped_column(Numeric(20, 10))
    low: Mapped[Decimal] = mapped_column(Numeric(20, 10))
    close: Mapped[Decimal] = mapped_column(Numeric(20, 10))
    volume: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    is_closed: Mapped[bool] = mapped_column(Boolean, default=True)


class TradeOrder(Base):
    __tablename__ = "trade_orders"
    __table_args__ = (
        UniqueConstraint("client_order_id", name="uq_trade_orders_client_order_id"),
        UniqueConstraint("exchange_order_id", name="uq_trade_orders_exchange_order_id"),
        Index("ix_trade_orders_symbol_status", "symbol", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    bot_run_id: Mapped[int | None] = mapped_column(ForeignKey("bot_runs.id"))
    client_order_id: Mapped[str | None] = mapped_column(String(64))
    exchange_order_id: Mapped[str | None] = mapped_column(String(64))
    symbol: Mapped[str] = mapped_column(String(30))
    side: Mapped[str] = mapped_column(String(10))
    order_type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="new")
    quantity: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    raw_response: Mapped[str | None] = mapped_column(Text)


class TradePosition(Base):
    __tablename__ = "trade_positions"
    __table_args__ = (
        Index("ix_trade_positions_symbol_status", "symbol", "status"),
        CheckConstraint("quantity >= 0", name="ck_position_quantity_nonnegative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    bot_run_id: Mapped[int | None] = mapped_column(ForeignKey("bot_runs.id"))
    symbol: Mapped[str] = mapped_column(String(30))
    side: Mapped[str] = mapped_column(String(10), default="long")
    status: Mapped[str] = mapped_column(String(20), default="open")
    quantity: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    entry_price: Mapped[Decimal] = mapped_column(Numeric(20, 10))
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    realized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TradeFill(Base):
    __tablename__ = "trade_fills"
    __table_args__ = (
        UniqueConstraint("exchange_trade_id", name="uq_trade_fills_exchange_trade_id"),
        Index("ix_trade_fills_order_id", "order_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("trade_orders.id"))
    exchange_trade_id: Mapped[str | None] = mapped_column(String(64))
    quantity: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    price: Mapped[Decimal] = mapped_column(Numeric(20, 10))
    commission: Mapped[Decimal] = mapped_column(Numeric(20, 10), default=0)
    commission_asset: Mapped[str | None] = mapped_column(String(20))
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RiskSnapshot(Base):
    __tablename__ = "risk_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    bot_run_id: Mapped[int | None] = mapped_column(ForeignKey("bot_runs.id"))
    capital_eur: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    daily_pnl_eur: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    total_pnl_eur: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    open_positions: Mapped[int] = mapped_column(Integer, default=0)
    risk_allowed: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str | None] = mapped_column(Text)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
