"""Scenario execution API routes under /_chronos/api."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import ScenarioFinding, ScenarioRun, ScenarioRunStep
from app.scenarios.dsl import Scenario
from app.scenarios.runner import ScenarioRunner

router = APIRouter(prefix="/_chronos/api/universes", tags=["scenarios"])


@router.get("/library", response_model=list[dict[str, Any]])
async def list_library_scenarios() -> list[dict[str, Any]]:
    """List all available library scenarios."""
    from app.scenarios.library import load_all_scenarios
    scenarios = load_all_scenarios()
    return [
        {
            "id": s.id,
            "title": s.title,
            "description": s.description,
            "tags": s.tags,
            "steps_count": len(s.steps),
        }
        for s in scenarios.values()
    ]


@router.post("/{universe_id}/scenarios/{scenario_id}/run", response_model=dict[str, Any])
async def run_scenario(
    universe_id: UUID,
    scenario_id: str,
    scenario: Scenario | None = None,
    reset: Annotated[bool, Query(description="Reset universe before running")] = True,
    session: Annotated[AsyncSession, Depends(get_session)] = None,
) -> dict[str, Any]:
    """
    Run a scenario and return the results.

    If scenario body is not provided, loads the scenario from the library by scenario_id.
    """
    try:
        if scenario is None:
            from app.scenarios.library import get_scenario
            scenario = get_scenario(scenario_id)
            if not scenario:
                raise HTTPException(status_code=404, detail=f"Scenario {scenario_id} not found in library")

        runner = ScenarioRunner(session, universe_id)
        run = await runner.run(scenario, reset=reset)

        # Fetch findings directly without touching lazy relationships
        findings_result = await session.execute(
            select(ScenarioFinding).where(ScenarioFinding.run_id == run.id)
        )
        findings_records = findings_result.scalars().all()

        findings = [
            {
                "invariant_id": f.invariant_id,
                "passed": f.passed,
                "evidence": f.evidence,
            }
            for f in findings_records
        ]

        # Fetch step count directly
        steps_count_result = await session.execute(
            select(func.count(ScenarioRunStep.id)).where(ScenarioRunStep.run_id == run.id)
        )
        steps_count = steps_count_result.scalar() or 0

        return {
            "run_id": str(run.id),
            "scenario_id": run.scenario_id,
            "status": run.status,
            "started_at": run.started_at.isoformat(),
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "error_message": run.error_message,
            "findings": findings,
            "steps_count": steps_count,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{universe_id}/runs/{run_id}", response_model=dict[str, Any])
async def get_run(
    universe_id: UUID,
    run_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
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

    findings_result = await session.execute(
        select(ScenarioFinding).where(ScenarioFinding.run_id == run_id)
    )
    findings_records = findings_result.scalars().all()

    findings = [
        {
            "invariant_id": f.invariant_id,
            "passed": f.passed,
            "evidence": f.evidence,
        }
        for f in findings_records
    ]

    steps_count_result = await session.execute(
        select(func.count(ScenarioRunStep.id)).where(ScenarioRunStep.run_id == run_id)
    )
    steps_count = steps_count_result.scalar() or 0

    return {
        "run_id": str(run.id),
        "scenario_id": run.scenario_id,
        "status": run.status,
        "started_at": run.started_at.isoformat(),
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "error_message": run.error_message,
        "findings": findings,
        "steps_count": steps_count,
    }


@router.get("/{universe_id}/runs/{run_id}/trace", response_model=dict[str, Any])
async def get_run_trace(
    universe_id: UUID,
    run_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
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

    steps_result = await session.execute(
        select(ScenarioRunStep)
        .where(ScenarioRunStep.run_id == run_id)
        .order_by(ScenarioRunStep.step_index)
    )
    steps = steps_result.scalars().all()

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
            for step in steps
        ],
    }

    return trace
