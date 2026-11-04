"""Add Orders, Authorizations, Captures, Refunds, and AccountBalances tables.

Revision ID: 004_payments_orders
Revises: 003_webhooks
Create Date: 2026-11-04 10:00:00.000000+00:00

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "004_payments_orders"
down_revision = "003_webhooks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create Orders, Authorizations, Captures, Refunds, and AccountBalances tables."""
    # Create orders table
    op.create_table(
        "orders",
        sa.Column("id", sa.String(255), nullable=False),
        sa.Column("universe_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("intent", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="CREATED"),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("value", sa.Numeric(19, 8), nullable=False),
        sa.Column("payer_id", sa.String(255), nullable=True),
        sa.Column("return_url", sa.String(2048), nullable=True),
        sa.Column("cancel_url", sa.String(2048), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_virtual_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_virtual_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_virtual_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["universe_id"],
            ["universes.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_orders_universe_id",
        "orders",
        ["universe_id"],
    )
    op.create_index(
        "ix_orders_status",
        "orders",
        ["universe_id", "status"],
    )

    # Create authorizations table
    op.create_table(
        "authorizations",
        sa.Column("id", sa.String(255), nullable=False),
        sa.Column("universe_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="CREATED"),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("amount", sa.Numeric(19, 8), nullable=False),
        sa.Column("created_virtual_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("honor_period_ends_virtual_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_virtual_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("voided_virtual_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["universe_id"],
            ["universes.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_authorizations_universe_order",
        "authorizations",
        ["universe_id", "order_id"],
    )
    op.create_index(
        "ix_authorizations_status",
        "authorizations",
        ["universe_id", "status"],
    )

    # Create captures table
    op.create_table(
        "captures",
        sa.Column("id", sa.String(255), nullable=False),
        sa.Column("universe_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", sa.String(255), nullable=False),
        sa.Column("authorization_id", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="COMPLETED"),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("amount", sa.Numeric(19, 8), nullable=False),
        sa.Column("final_capture", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("invoice_id", sa.String(255), nullable=True),
        sa.Column("note_to_payer", sa.Text(), nullable=True),
        sa.Column("created_virtual_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["universe_id"],
            ["universes.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["authorization_id"],
            ["authorizations.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_captures_universe_order",
        "captures",
        ["universe_id", "order_id"],
    )
    op.create_index(
        "ix_captures_authorization",
        "captures",
        ["universe_id", "authorization_id"],
    )

    # Create refunds table
    op.create_table(
        "refunds",
        sa.Column("id", sa.String(255), nullable=False),
        sa.Column("universe_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("capture_id", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="COMPLETED"),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("amount", sa.Numeric(19, 8), nullable=False),
        sa.Column("note_to_payer", sa.Text(), nullable=True),
        sa.Column("created_virtual_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["universe_id"],
            ["universes.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["capture_id"],
            ["captures.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_refunds_universe_capture",
        "refunds",
        ["universe_id", "capture_id"],
    )

    # Create account_balances table
    op.create_table(
        "account_balances",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("universe_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("available", sa.Numeric(19, 8), nullable=False, server_default="0.00"),
        sa.ForeignKeyConstraint(
            ["universe_id"],
            ["universes.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "universe_id",
            "currency_code",
            name="uq_account_balance_universe_currency",
        ),
    )


def downgrade() -> None:
    """Drop Orders, Authorizations, Captures, Refunds, and AccountBalances tables."""
    op.drop_table("account_balances")
    op.drop_table("refunds")
    op.drop_table("captures")
    op.drop_table("authorizations")
    op.drop_table("orders")
