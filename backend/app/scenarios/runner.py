"""Scenario runner: executes scenarios deterministically, records trace, evaluates invariants."""

import asyncio
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    EventLog,
    ScenarioFinding,
    ScenarioRun,
    ScenarioRunStep,
    Universe,
)
from app.scenarios.dsl import Scenario, Step
from app.scenarios.handlers import (
    handle_approve_order,
    handle_claim_payout,
    handle_place_order,
    handle_refund_order,
    handle_run_payout,
    handle_ship_order,
)
from app.scenarios.invariants import InvariantsOracle
from app.twin.clock import advance as clock_advance

logger = logging.getLogger(__name__)

# Maximum real-world time for a single scenario run (in seconds)
STEP_TIMEOUT_SECONDS = 30
RUN_TIMEOUT_SECONDS = 120

# Maximum events per advance step
MAX_EVENTS_PER_ADVANCE = 10000


class StepExecutor:
    """Executes individual scenario steps."""

    def __init__(self, session: AsyncSession, universe: Universe):
        self.session = session
        self.universe = universe

    async def get_current_virtual_time(self) -> datetime:
        """Get current virtual time."""
        result = await self.session.execute(
            select(Universe.virtual_now).where(Universe.id == self.universe.id)
        )
        return result.scalar_one()

    async def execute(self, step: Step) -> tuple[dict[str, Any], list[EventLog]]:
        """
        Execute a single step and return (output, event_slice).

        Args:
            step: Step to execute

        Returns:
            (output dict, list of EventLog entries produced)

        Raises:
            Various exceptions on error
        """
        # Capture event log state before step
        result = await self.session.execute(
            select(EventLog.id)
            .where(EventLog.universe_id == self.universe.id)
            .order_by(EventLog.id.desc())
            .limit(1)
        )
        last_event_id_before = result.scalar_one_or_none()

        # Execute based on step type
        step_root = step.root
        output: dict[str, Any] = {}

        if step_root.type == "place_order":
            output = await handle_place_order(
                self.session,
                self.universe,
                product_id=step_root.product_id,
                payer_email=step_root.payer_email,
                quantity=step_root.quantity,
            )

        elif step_root.type == "approve_order":
            output = await handle_approve_order(
                self.session,
                self.universe,
                order_id=step_root.order_id,
            )

        elif step_root.type == "ship_order":
            output = await handle_ship_order(
                self.session,
                self.universe,
                order_id=step_root.order_id,
            )

        elif step_root.type == "refund_order":
            output = await handle_refund_order(
                self.session,
                self.universe,
                order_id=step_root.order_id,
                amount=step_root.amount,
            )

        elif step_root.type == "run_payout":
            output = await handle_run_payout(self.session, self.universe)

        elif step_root.type == "claim_payout":
            output = await handle_claim_payout(
                self.session,
                self.universe,
                recipient_email=step_root.recipient_email,
            )

        elif step_root.type == "advance":
            # Calculate target time
            hours = step_root.hours
            days = step_root.days
            delta = timedelta(days=days, hours=hours)
            current_time = await self.get_current_virtual_time()
            target = datetime.fromtimestamp(
                current_time.timestamp() + delta.total_seconds(),
                tz=UTC,
            )
            response = await clock_advance(
                self.session,
                str(self.universe.id),
                to=target,
                max_events=MAX_EVENTS_PER_ADVANCE,
            )
            output = response.to_dict()

        elif step_root.type == "set_chaos":
            self.universe.chaos_config = step_root.chaos.model_dump()
            await self.session.flush()
            output = {"chaos_config": self.universe.chaos_config}

        elif step_root.type == "set_handler_variant":
            self.universe.handler_variant = step_root.variant.value
            await self.session.flush()
            output = {"handler_variant": self.universe.handler_variant}

        elif step_root.type == "check":
            # Evaluate invariants
            oracle = InvariantsOracle(self.session, self.universe.id)
            results = await oracle.check_all()
            output = {
                inv_id: {
                    "passed": result.passed,
                    "evidence": result.evidence,
                }
                for inv_id, result in results.items()
            }

        elif step_root.type == "snapshot":
            current_time = await self.get_current_virtual_time()
            output = {
                "label": step_root.label,
                "virtual_time": current_time.isoformat(),
            }

        # Capture new events generated by step
        if last_event_id_before:
            result = await self.session.execute(
                select(EventLog)
                .where(
                    EventLog.universe_id == self.universe.id,
                    EventLog.id > last_event_id_before,
                )
                .order_by(EventLog.id)
            )
        else:
            result = await self.session.execute(
                select(EventLog)
                .where(
                    EventLog.universe_id == self.universe.id,
                )
                .order_by(EventLog.id)
            )

        event_slice = result.scalars().all()

        return output, event_slice


class ScenarioRunner:
    """Main scenario execution engine."""

    def __init__(self, db_session: AsyncSession, universe_id: uuid.UUID):
        self.db_session = db_session
        self.universe_id = universe_id

    async def run(
        self,
        scenario: Scenario,
        reset: bool = True,
    ) -> ScenarioRun:
        """
        Run a scenario.

        Args:
            scenario: Scenario definition
            reset: Whether to reset the universe before running

        Returns:
            ScenarioRun with trace, findings, and status
        """
        # Get universe first (before creating run) so we can reset if needed
        result = await self.db_session.execute(
            select(Universe).where(Universe.id == self.universe_id)
        )
        universe = result.scalar_one_or_none()
        if not universe:
            raise ValueError(f"Universe {self.universe_id} not found")

        # Reset BEFORE creating the run record (so delete doesn't wipe our run)
        if reset:
            await self._reset_universe(universe)

        run = ScenarioRun(
            universe_id=self.universe_id,
            scenario_id=scenario.id,
            status="in_progress",
            started_at=datetime.now(UTC),
        )
        self.db_session.add(run)
        await self.db_session.flush()

        try:

            # Execute steps
            executor = StepExecutor(self.db_session, universe)

            for step_index, step in enumerate(scenario.steps):
                if step_index > 40:  # Safety check
                    raise ValueError("Scenario exceeds 40 steps")

                step_start = time.time()
                virtual_time_before = await executor.get_current_virtual_time()

                try:
                    # Execute step with timeout
                    step_output, event_slice = await asyncio.wait_for(
                        executor.execute(step),
                        timeout=STEP_TIMEOUT_SECONDS,
                    )

                    virtual_time_after = await executor.get_current_virtual_time()
                    duration_ms = int((time.time() - step_start) * 1000)

                    # Record step
                    step_record = ScenarioRunStep(
                        run_id=run.id,
                        step_index=step_index,
                        step_type=step.root.type,
                        step_input=step.root.model_dump(),
                        virtual_time_before=virtual_time_before,
                        virtual_time_after=virtual_time_after,
                        step_output=step_output,
                        duration_ms=duration_ms,
                        event_log_ids=[ev.id for ev in event_slice],
                    )
                    self.db_session.add(step_record)

                except TimeoutError:
                    # Step timeout
                    virtual_time_after = await executor.get_current_virtual_time()
                    duration_ms = int((time.time() - step_start) * 1000)

                    step_record = ScenarioRunStep(
                        run_id=run.id,
                        step_index=step_index,
                        step_type=step.root.type,
                        step_input=step.root.model_dump(),
                        virtual_time_before=virtual_time_before,
                        virtual_time_after=virtual_time_after,
                        error_message=f"Step timeout after {STEP_TIMEOUT_SECONDS}s",
                        duration_ms=duration_ms,
                        event_log_ids=[],
                    )
                    self.db_session.add(step_record)

                    run.status = "timeout"
                    run.error_message = f"Step {step_index} timed out"
                    break

                except Exception as e:
                    # Step error
                    virtual_time_after = await executor.get_current_virtual_time()
                    duration_ms = int((time.time() - step_start) * 1000)

                    step_record = ScenarioRunStep(
                        run_id=run.id,
                        step_index=step_index,
                        step_type=step.root.type,
                        step_input=step.root.model_dump(),
                        virtual_time_before=virtual_time_before,
                        virtual_time_after=virtual_time_after,
                        error_message=str(e),
                        duration_ms=duration_ms,
                        event_log_ids=[],
                    )
                    self.db_session.add(step_record)

                    run.status = "error"
                    run.error_message = str(e)
                    break

            # Evaluate final invariants if not already failed
            if run.status == "in_progress":
                oracle = InvariantsOracle(self.db_session, self.universe_id)
                invariant_results = await oracle.check_all()

                all_passed = all(r.passed for r in invariant_results.values())
                run.status = "passed" if all_passed else "failed"

                # Record findings
                for inv_id, result in invariant_results.items():
                    finding = ScenarioFinding(
                        run_id=run.id,
                        invariant_id=inv_id,
                        passed=result.passed,
                        evidence=result.evidence,
                        step_index=None,  # Final check
                    )
                    self.db_session.add(finding)

        except Exception as e:
            run.status = "error"
            run.error_message = str(e)
            logger.exception(f"Scenario run error: {e}")

        finally:
            run.completed_at = datetime.now(UTC)
            await self.db_session.flush()
            await self.db_session.commit()

        return run

    async def _reset_universe(self, universe: Universe) -> None:
        """Reset universe state for scenario execution."""
        # Truncate tables (preserve universe row)
        await self.db_session.execute(
            delete(EventLog).where(EventLog.universe_id == universe.id)
        )
        await self.db_session.execute(
            delete(ScenarioRun).where(ScenarioRun.universe_id == universe.id)
        )
        # TODO: Reset other tables as needed (orders, payouts, shop tables, etc.)
        await self.db_session.flush()
