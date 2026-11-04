"""Add webhook tables.

Revision ID: 003_webhooks
Revises: 002_core_schema
Create Date: 2026-11-04 10:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "003_webhooks"
down_revision = "002_core_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create webhooks table
    op.create_table(
        "webhooks",
        sa.Column("id", sa.String(255), nullable=False),
        sa.Column("universe_id", sa.UUID(), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("event_types", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["universe_id"], ["universes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_webhooks_universe_id", "webhooks", ["universe_id"])

    # Create webhook_events table
    op.create_table(
        "webhook_events",
        sa.Column("id", sa.String(255), nullable=False),
        sa.Column("universe_id", sa.UUID(), nullable=False),
        sa.Column("event_type", sa.String(255), nullable=False),
        sa.Column("resource_type", sa.String(100), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("resource", sa.JSON(), nullable=False),
        sa.Column(
            "created_virtual_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["universe_id"], ["universes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_webhook_events_universe_id", "webhook_events", ["universe_id"])

    # Create webhook_deliveries table
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.BigInteger(), nullable=False, autoincrement=True),
        sa.Column("universe_id", sa.UUID(), nullable=False),
        sa.Column("webhook_event_id", sa.String(255), nullable=False),
        sa.Column("webhook_id", sa.String(255), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_ms", sa.Integer(), nullable=True),
        sa.Column("signature_tampered", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "due_virtual_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "delivered_virtual_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["universe_id"], ["universes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["webhook_event_id"], ["webhook_events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["webhook_id"], ["webhooks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_webhook_deliveries_universe_status_due",
        "webhook_deliveries",
        ["universe_id", "status", "due_virtual_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_webhook_deliveries_universe_status_due", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")
    op.drop_index("ix_webhook_events_universe_id", table_name="webhook_events")
    op.drop_table("webhook_events")
    op.drop_index("ix_webhooks_universe_id", table_name="webhooks")
    op.drop_table("webhooks")
