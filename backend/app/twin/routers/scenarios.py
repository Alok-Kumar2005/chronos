"""Scenario execution API routes under /_chronos/api."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import ScenarioRun
from app.scenarios.dsl import Scenario
from app.scenarios.runner import ScenarioRunner

router = APIRouter(prefix="/_chronos/api/universes", tags=["scenarios"])


@router.post("/{universe_id}/scenarios/{scenario_id}/run", response_model=dict[str, Any])
async def run_scenario(
    universe_id: UUID,
    scenario_id: str,
    scenario: Scenario,
    reset: bool = Query(True, description="Reset universe before running"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """
    Run a scenario and return the results.

    Args:
        universe_id: Universe ID
        scenario_id: Scenario ID (from path, for consistency)
        scenario: Scenario definition (from body)
        reset: Whether to reset universe state before running
        session: Database session

    Returns:
        {
            "run_id": "...",
            "scenario_id": "...",
            "status": "passed|failed|error|timeout",
            "started_at": "...",
            "completed_at": "...",
            "findings": [{"invariant_id": "INV-1", "passed": true, "evidence": {...}}],
            "steps_count": 5,
        }
    """
    try:
        runner = ScenarioRunner(session, universe_id)
        run = await runner.run(scenario, reset=reset)

        # Fetch findings
        result = await session.execute(
            select(ScenarioRun).where(ScenarioRun.id == run.id)
        )
        run_record = result.scalar_one()

        # Load findings
        findings = [
            {
                "invariant_id": f.invariant_id,
                "passed": f.passed,
                "evidence": f.evidence,
            }
            for f in run_record.findings
        ]

        return {
            "run_id": str(run.id),
            "scenario_id": run.scenario_id,
            "status": run.status,
            "started_at": run.started_at.isoformat(),
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "error_message": run.error_message,
            "findings": findings,
            "steps_count": len(run_record.steps),
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{universe_id}/runs/{run_id}", response_model=dict[str, Any])
async def get_run(
    universe_id: UUID,
    run_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Get a scenario run by ID."""
    result = await session.execute(
        select(ScenarioRun).where(
            ScenarioRun.id == run_id,
            ScenarioRun.universe_id == universe_id,
        )
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    # Load findings
    findings = [
        {
            "invariant_id": f.invariant_id,
            "passed": f.passed,
            "evidence": f.evidence,
        }
        for f in run.findings
    ]

    return {
        "run_id": str(run.id),
        "scenario_id": run.scenario_id,
        "status": run.status,
        "started_at": run.started_at.isoformat(),
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "error_message": run.error_message,
        "findings": findings,
        "steps_count": len(run.steps),
    }


@router.get("/{universe_id}/runs/{run_id}/trace", response_model=dict[str, Any])
async def get_run_trace(
    universe_id: UUID,
    run_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Get the full trace of a scenario run."""
    result = await session.execute(
        select(ScenarioRun).where(
            ScenarioRun.id == run_id,
            ScenarioRun.universe_id == universe_id,
        )
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    # Build trace
    trace = {
        "run_id": str(run.id),
        "scenario_id": run.scenario_id,
        "steps": [
            {
                "index": step.step_index,
                "type": step.step_type,
                "input": step.step_input,
                "output": step.step_output,
                "error": step.error_message,
                "virtual_time_before": step.virtual_time_before.isoformat(),
                "virtual_time_after": step.virtual_time_after.isoformat(),
                "duration_ms": step.duration_ms,
                "event_log_ids": [str(eid) for eid in step.event_log_ids],
            }
            for step in sorted(run.steps, key=lambda s: s.step_index)
        ],
    }

    return trace
