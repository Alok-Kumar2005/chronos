"""Baseline migration - empty schema for Step 1.

Revision ID: 001_baseline
Revises:
Create Date: 2026-11-04 10:00:00.000000

This is the baseline migration created during bootstrap.
Subsequent steps will add schema as features are implemented.
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create baseline schema."""
    pass


def downgrade() -> None:
    """Downgrade baseline schema."""
    pass
