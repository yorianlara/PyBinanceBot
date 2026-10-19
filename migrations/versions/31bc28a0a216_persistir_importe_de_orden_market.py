"""persistir importe de órdenes de mercado

Revision ID: 31bc28a0a216
Revises: 900ed6bc69d2
Create Date: 2026-10-19 15:56:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "31bc28a0a216"
down_revision: str | Sequence[str] | None = "900ed6bc69d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "trade_orders",
        sa.Column("quote_order_qty", sa.Numeric(precision=28, scale=10)),
    )


def downgrade() -> None:
    op.drop_column("trade_orders", "quote_order_qty")
