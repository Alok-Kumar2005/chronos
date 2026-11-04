"""Create universes, tokens, events, event_log, and idempotency_keys tables.

Revision ID: 002_core_schema
Revises: 001_baseline
Create Date: 2026-11-04 10:00:00.000000

This migration creates the core schema for Chronos:
- universes: tenant isolation with virtual clocks
- access_tokens: OAuth tokens with virtual time expiry
- scheduled_events: events to process in time order
- event_log: immutable record of all events
- idempotency_keys: prevent duplicate processing
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "002_core_schema"
down_revision: str | None = "001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create core schema."""
    # Create universes table
    op.create_table(
        "universes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("client_id", sa.String(255), nullable=False, unique=True),
        sa.Column("client_secret_hash", sa.String(64), nullable=False),
        sa.Column("control_key_hash", sa.String(64), nullable=False),
        sa.Column("virtual_now", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("chaos", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id", name="uq_universes_client_id"),
    )

    # Create access_tokens table
    op.create_table(
        "access_tokens",
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("universe_id", sa.UUID(), nullable=False),
        sa.Column("scope", sa.String(255), nullable=False),
        sa.Column("issued_virtual_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_virtual_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["universe_id"], ["universes.id"], name="fk_access_tokens_universe_id", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("token_hash"),
    )

    # Create scheduled_events table
    op.create_table(
        "scheduled_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("universe_id", sa.UUID(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(255), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["universe_id"], ["universes.id"], name="fk_scheduled_events_universe_id", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create index on scheduled_events
    op.create_index(
        "ix_scheduled_events_universe_status_due_at_id",
        "scheduled_events",
        ["universe_id", "status", "due_at", "id"],
    )

    # Create event_log table
    op.create_table(
        "event_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("universe_id", sa.UUID(), nullable=False),
        sa.Column("virtual_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("wall_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("kind", sa.String(255), nullable=False),
        sa.Column("resource_type", sa.String(100), nullable=True),
        sa.Column("resource_id", sa.String(255), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False, server_default="{}"),
        sa.ForeignKeyConstraint(
            ["universe_id"], ["universes.id"], name="fk_event_log_universe_id", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create indexes on event_log
    op.create_index(
        "ix_event_log_universe_id",
        "event_log",
        ["universe_id", "id"],
    )
    op.create_index(
        "ix_event_log_resource",
        "event_log",
        ["universe_id", "resource_type", "resource_id"],
    )

    # Create idempotency_keys table
    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("universe_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("path", sa.String(500), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_body", sa.JSON(), nullable=False),
        sa.Column("created_virtual_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["universe_id"], ["universes.id"], name="fk_idempotency_keys_universe_id", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("universe_id", "key", "method", "path", name="uq_idempotency_key"),
    )


def downgrade() -> None:
    """Drop core schema."""
    op.drop_table("idempotency_keys")
    op.drop_index("ix_event_log_resource", table_name="event_log")
    op.drop_index("ix_event_log_universe_id", table_name="event_log")
    op.drop_table("event_log")
    op.drop_index("ix_scheduled_events_universe_status_due_at_id", table_name="scheduled_events")
    op.drop_table("scheduled_events")
    op.drop_table("access_tokens")
    op.drop_table("universes")
