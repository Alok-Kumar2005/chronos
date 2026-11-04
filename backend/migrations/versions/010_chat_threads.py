"""Add chat_threads table for Ask Chronos copilot.

Revision ID: 010_chat_threads
Revises: 009_universe_scenario_fields
Create Date: 2026-11-04 14:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision = "010_chat_threads"
down_revision = "009_universe_scenario_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create chat_threads table."""
    op.create_table(
        "chat_threads",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("universe_id", UUID(as_uuid=True), nullable=False),
        sa.Column("chat_id", sa.String(32), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["universe_id"], ["universes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("universe_id", "chat_id", name="uq_chat_thread_per_universe"),
    )
    op.create_index("ix_chat_threads_universe_id", "chat_threads", ["universe_id"])


def downgrade() -> None:
    """Drop chat_threads table."""
    op.drop_index("ix_chat_threads_universe_id", "chat_threads")
    op.drop_table("chat_threads")
