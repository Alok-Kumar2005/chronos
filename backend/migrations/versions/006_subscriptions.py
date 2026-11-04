"""Add subscription models - billing plans, subscriptions, and payments.

Revision ID: 006_subscriptions
Revises: 005
Create Date: 2026-11-04 10:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "006_subscriptions"
down_revision = "005_payouts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create products table
    op.create_table(
        "products",
        sa.Column("id", sa.String(255), nullable=False),
        sa.Column(
            "universe_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("image_url", sa.String(2048), nullable=True),
        sa.Column("home_url", sa.String(2048), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column(
            "created_virtual_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["universe_id"],
            ["universes.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_products_universe_id",
        "products",
        ["universe_id"],
    )
    op.create_index(
        "ix_products_status",
        "products",
        ["universe_id", "status"],
    )

    # Create billing_plans table
    op.create_table(
        "billing_plans",
        sa.Column("id", sa.String(255), nullable=False),
        sa.Column(
            "universe_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("product_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="CREATED"),
        sa.Column("billing_cycles", postgresql.JSON(), nullable=False, server_default="[]"),
        sa.Column("payment_preferences", postgresql.JSON(), nullable=False),
        sa.Column(
            "created_virtual_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["universe_id"],
            ["universes.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_billing_plans_universe_id",
        "billing_plans",
        ["universe_id"],
    )
    op.create_index(
        "ix_billing_plans_status",
        "billing_plans",
        ["universe_id", "status"],
    )
    op.create_index(
        "ix_billing_plans_product_id",
        "billing_plans",
        ["product_id"],
    )

    # Create subscriptions table
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.String(255), nullable=False),
        sa.Column(
            "universe_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("plan_id", sa.String(255), nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="APPROVAL_PENDING",
        ),
        sa.Column("subscriber_name", postgresql.JSON(), nullable=False),
        sa.Column("subscriber_email", sa.String(255), nullable=False),
        sa.Column("quantity", sa.String(50), nullable=False, server_default="1"),
        sa.Column("shipping_amount", sa.Numeric(19, 8), nullable=True),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_billing_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_cycle_sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_payment_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payment_failure_threshold", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status_change_reason", sa.String(255), nullable=True),
        sa.Column("status_update_virtual_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("return_url", sa.String(2048), nullable=True),
        sa.Column("cancel_url", sa.String(2048), nullable=True),
        sa.Column(
            "created_virtual_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["billing_plans.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["universe_id"],
            ["universes.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_subscriptions_universe_id",
        "subscriptions",
        ["universe_id"],
    )
    op.create_index(
        "ix_subscriptions_status",
        "subscriptions",
        ["universe_id", "status"],
    )
    op.create_index(
        "ix_subscriptions_plan_id",
        "subscriptions",
        ["plan_id"],
    )
    op.create_index(
        "ix_subscriptions_next_billing",
        "subscriptions",
        ["universe_id", "next_billing_time"],
    )

    # Create subscription_payments table
    op.create_table(
        "subscription_payments",
        sa.Column("id", sa.String(255), nullable=False),
        sa.Column(
            "universe_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("subscription_id", sa.String(255), nullable=False),
        sa.Column("cycle_sequence", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(19, 8), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="COMPLETED"),
        sa.Column(
            "billing_virtual_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("failure_reason", sa.String(255), nullable=True),
        sa.Column(
            "created_virtual_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            ["subscriptions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["universe_id"],
            ["universes.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_subscription_payments_universe_id",
        "subscription_payments",
        ["universe_id"],
    )
    op.create_index(
        "ix_subscription_payments_subscription_id",
        "subscription_payments",
        ["subscription_id"],
    )
    op.create_index(
        "ix_subscription_payments_status",
        "subscription_payments",
        ["universe_id", "status"],
    )


def downgrade() -> None:
    op.drop_table("subscription_payments")
    op.drop_table("subscriptions")
    op.drop_table("billing_plans")
    op.drop_table("products")
