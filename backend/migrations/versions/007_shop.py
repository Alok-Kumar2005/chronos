"""Add shop tables for Lumen Store demo.

Revision ID: 007_shop
Revises: 006_subscriptions
Create Date: 2026-11-04 10:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "007_shop"
down_revision = "006_subscriptions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create shop tables."""
    # ShopSettings table
    op.create_table(
        "shop_settings",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("universe_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("handler_variant", sa.String(50), nullable=False, server_default="buggy"),
        sa.Column("shop_currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("creator_payout_percentage", sa.Numeric(19, 8), nullable=False, server_default="0.70"),
        sa.Column("payout_fee_per_item", sa.Numeric(19, 8), nullable=False, server_default="0.20"),
        sa.Column("payout_batch_due_day_of_week", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["universe_id"], ["universes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("universe_id", name="uq_shop_settings_universe"),
    )

    # ShopProduct table
    op.create_table(
        "shop_products",
        sa.Column("id", sa.String(255), nullable=False),
        sa.Column("universe_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price", sa.Numeric(19, 8), nullable=False),
        sa.Column("stock", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("creator_email", sa.String(255), nullable=False),
        sa.Column("created_virtual_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["universe_id"], ["universes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_shop_products_universe_id", "shop_products", ["universe_id"])
    op.create_index("ix_shop_products_creator_email", "shop_products", ["universe_id", "creator_email"])

    # ShopOrder table
    op.create_table(
        "shop_orders",
        sa.Column("id", sa.String(255), nullable=False),
        sa.Column("universe_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", sa.String(255), nullable=False),
        sa.Column("paypal_order_id", sa.String(255), nullable=True),
        sa.Column("paypal_authorization_id", sa.String(255), nullable=True),
        sa.Column("paypal_capture_id", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="created"),
        sa.Column("amount", sa.Numeric(19, 8), nullable=False),
        sa.Column("payer_email", sa.String(255), nullable=True),
        sa.Column("customer_name", sa.String(255), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_virtual_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("authorized_virtual_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("shipped_virtual_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_virtual_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refunded_virtual_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["product_id"], ["shop_products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["universe_id"], ["universes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_shop_orders_universe_id", "shop_orders", ["universe_id"])
    op.create_index("ix_shop_orders_status", "shop_orders", ["universe_id", "status"])
    op.create_index("ix_shop_orders_paypal_order_id", "shop_orders", ["universe_id", "paypal_order_id"])

    # ShopLedgerEntry table
    op.create_table(
        "shop_ledger_entries",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("universe_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account", sa.String(255), nullable=False),
        sa.Column("amount", sa.Numeric(19, 8), nullable=False),
        sa.Column("order_id", sa.String(255), nullable=True),
        sa.Column("source_event_id", sa.String(255), nullable=True),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("created_virtual_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["shop_orders.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["universe_id"], ["universes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_shop_ledger_entries_universe_id", "shop_ledger_entries", ["universe_id"])
    op.create_index("ix_shop_ledger_entries_account", "shop_ledger_entries", ["universe_id", "account"])
    op.create_index("ix_shop_ledger_entries_order_id", "shop_ledger_entries", ["order_id"])
    op.create_index("ix_shop_ledger_entries_source_event_id", "shop_ledger_entries", ["universe_id", "source_event_id"])

    # ShopCreator table
    op.create_table(
        "shop_creators",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("universe_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("balance", sa.Numeric(19, 8), nullable=False, server_default="0.00"),
        sa.Column("payout_status", sa.String(50), nullable=False, server_default="ready"),
        sa.Column("last_payout_virtual_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_payout_batch_id", sa.String(255), nullable=True),
        sa.ForeignKeyConstraint(["universe_id"], ["universes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("universe_id", "email", name="uq_shop_creator_universe_email"),
    )
    op.create_index("ix_shop_creators_universe_id", "shop_creators", ["universe_id"])

    # ShopAppliedWebhookEvent table
    op.create_table(
        "shop_applied_webhook_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("universe_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_event_id", sa.String(255), nullable=False),
        sa.Column("applied_virtual_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["universe_id"], ["universes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("universe_id", "source_event_id", name="uq_shop_applied_webhook_event_universe_event"),
    )
    op.create_index("ix_shop_applied_webhook_events_universe_id", "shop_applied_webhook_events", ["universe_id"])

    # ShopErrorLog table
    op.create_table(
        "shop_error_logs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("universe_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation", sa.String(255), nullable=False),
        sa.Column("error_code", sa.String(50), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("swallowed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_virtual_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["universe_id"], ["universes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_shop_error_logs_universe_id", "shop_error_logs", ["universe_id"])
    op.create_index("ix_shop_error_logs_error_code", "shop_error_logs", ["universe_id", "error_code"])


def downgrade() -> None:
    """Drop shop tables."""
    op.drop_index("ix_shop_error_logs_error_code", table_name="shop_error_logs")
    op.drop_index("ix_shop_error_logs_universe_id", table_name="shop_error_logs")
    op.drop_table("shop_error_logs")
    
    op.drop_index("ix_shop_applied_webhook_events_universe_id", table_name="shop_applied_webhook_events")
    op.drop_table("shop_applied_webhook_events")
    
    op.drop_index("ix_shop_creators_universe_id", table_name="shop_creators")
    op.drop_table("shop_creators")
    
    op.drop_index("ix_shop_ledger_entries_source_event_id", table_name="shop_ledger_entries")
    op.drop_index("ix_shop_ledger_entries_order_id", table_name="shop_ledger_entries")
    op.drop_index("ix_shop_ledger_entries_account", table_name="shop_ledger_entries")
    op.drop_index("ix_shop_ledger_entries_universe_id", table_name="shop_ledger_entries")
    op.drop_table("shop_ledger_entries")
    
    op.drop_index("ix_shop_orders_paypal_order_id", table_name="shop_orders")
    op.drop_index("ix_shop_orders_status", table_name="shop_orders")
    op.drop_index("ix_shop_orders_universe_id", table_name="shop_orders")
    op.drop_table("shop_orders")
    
    op.drop_index("ix_shop_products_creator_email", table_name="shop_products")
    op.drop_index("ix_shop_products_universe_id", table_name="shop_products")
    op.drop_table("shop_products")
    
    op.drop_table("shop_settings")
