"""Add scenario run tables for deterministic testing.

Revision ID: 008_scenarios
Revises: 007_shop
Create Date: 2026-11-04 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "008_scenarios"
down_revision = "007_shop"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create scenario run tables."""
    # ScenarioRun table
    op.create_table(
        "scenario_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("universe_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scenario_id", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["universe_id"], ["universes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scenario_runs_universe_id", "scenario_runs", ["universe_id"])
    op.create_index("ix_scenario_runs_scenario_id", "scenario_runs", ["scenario_id"])
    op.create_index("ix_scenario_runs_status", "scenario_runs", ["status"])

    # ScenarioRunStep table
    op.create_table(
        "scenario_run_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("step_type", sa.String(50), nullable=False),
        sa.Column("step_input", sa.JSON(), nullable=False),
        sa.Column("virtual_time_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("virtual_time_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("step_output", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("event_log_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.ForeignKeyConstraint(["run_id"], ["scenario_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scenario_run_steps_run_id", "scenario_run_steps", ["run_id"])
    op.create_index(
        "ix_scenario_run_steps_step_index",
        "scenario_run_steps",
        ["run_id", "step_index"],
    )

    # ScenarioFinding table
    op.create_table(
        "scenario_findings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invariant_id", sa.String(20), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["scenario_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scenario_findings_run_id", "scenario_findings", ["run_id"])
    op.create_index(
        "ix_scenario_findings_invariant_id", "scenario_findings", ["invariant_id"]
    )


def downgrade() -> None:
    """Drop scenario run tables."""
    op.drop_index("ix_scenario_findings_invariant_id", table_name="scenario_findings")
    op.drop_index("ix_scenario_findings_run_id", table_name="scenario_findings")
    op.drop_table("scenario_findings")

    op.drop_index("ix_scenario_run_steps_step_index", table_name="scenario_run_steps")
    op.drop_index("ix_scenario_run_steps_run_id", table_name="scenario_run_steps")
    op.drop_table("scenario_run_steps")

    op.drop_index("ix_scenario_runs_status", table_name="scenario_runs")
    op.drop_index("ix_scenario_runs_scenario_id", table_name="scenario_runs")
    op.drop_index("ix_scenario_runs_universe_id", table_name="scenario_runs")
    op.drop_table("scenario_runs")
