"""Add scenario-related fields to universes table.

Revision ID: 009_universe_scenario_fields
Revises: 008_scenarios
Create Date: 2026-11-04 13:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "009_universe_scenario_fields"
down_revision = "008_scenarios"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add scenario fields to universes."""
    # Add handler_variant column
    op.add_column(
        "universes",
        sa.Column(
            "handler_variant",
            sa.String(50),
            nullable=False,
            server_default="buggy",
        ),
    )

    # Add chaos_config column (JSON for detailed chaos configuration)
    op.add_column(
        "universes",
        sa.Column(
            "chaos_config",
            sa.JSON(),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    """Remove scenario fields from universes."""
    op.drop_column("universes", "chaos_config")
    op.drop_column("universes", "handler_variant")
