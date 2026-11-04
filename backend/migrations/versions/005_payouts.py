"""Add Payout Batches, Payout Items, and Payout Recipients tables.

Revision ID: 005_payouts
Revises: 004_payments_orders
Create Date: 2026-11-04 10:00:00.000000+00:00

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "005_payouts"
down_revision = "004_payments_orders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create Payout Batches, Payout Items, and Payout Recipients tables."""
    # Create payout_batches table
    op.create_table(
        "payout_batches",
        sa.Column("id", sa.String(255), nullable=False),
        sa.Column("universe_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sender_batch_id", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("sender_batch_header", sa.JSON(), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("total_amount", sa.Numeric(19, 8), nullable=False),
        sa.Column("total_fee", sa.Numeric(19, 8), nullable=False, server_default="0.00"),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("successful_item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unclaimed_item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_virtual_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_virtual_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["universe_id"],
            ["universes.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_payout_batches_universe_id",
        "payout_batches",
        ["universe_id"],
    )
    op.create_index(
        "ix_payout_batches_status",
        "payout_batches",
        ["universe_id", "status"],
    )
    op.create_unique_constraint(
        "uq_payout_batch_sender_id",
        "payout_batches",
        ["universe_id", "sender_batch_id"],
    )

    # Create payout_items table
    op.create_table(
        "payout_items",
        sa.Column("id", sa.String(255), nullable=False),
        sa.Column("universe_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", sa.String(255), nullable=False),
        sa.Column("sender_item_id", sa.String(255), nullable=False),
        sa.Column("recipient_type", sa.String(20), nullable=False),
        sa.Column("receiver", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("amount", sa.Numeric(19, 8), nullable=False),
        sa.Column("fee", sa.Numeric(19, 8), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("recipient_wallet", sa.String(50), nullable=True),
        sa.Column("created_virtual_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_virtual_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("return_scheduled_virtual_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("returned_virtual_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["universe_id"],
            ["universes.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["payout_batches.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_payout_items_universe_id",
        "payout_items",
        ["universe_id"],
    )
    op.create_index(
        "ix_payout_items_batch_id",
        "payout_items",
        ["batch_id"],
    )
    op.create_index(
        "ix_payout_items_receiver",
        "payout_items",
        ["universe_id", "receiver"],
    )
    op.create_index(
        "ix_payout_items_status",
        "payout_items",
        ["universe_id", "status"],
    )

    # Create payout_recipients table
    op.create_table(
        "payout_recipients",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("universe_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("registered", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("registered_virtual_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["universe_id"],
            ["universes.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_unique_constraint(
        "uq_payout_recipient_universe_email",
        "payout_recipients",
        ["universe_id", "email"],
    )


def downgrade() -> None:
    """Drop payout tables."""
    op.drop_constraint("uq_payout_recipient_universe_email", "payout_recipients", type_="unique")
    op.drop_table("payout_recipients")
    op.drop_index("ix_payout_items_status", "payout_items")
    op.drop_index("ix_payout_items_receiver", "payout_items")
    op.drop_index("ix_payout_items_batch_id", "payout_items")
    op.drop_index("ix_payout_items_universe_id", "payout_items")
    op.drop_constraint("uq_payout_batch_sender_id", "payout_batches", type_="unique")
    op.drop_table("payout_items")
    op.drop_index("ix_payout_batches_status", "payout_batches")
    op.drop_index("ix_payout_batches_universe_id", "payout_batches")
    op.drop_table("payout_batches")
