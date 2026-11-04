"""Virtual clock for Chronos universes with ordered event processing."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EventLog, ScheduledEvent, Universe


class ClockResponse:
    """Response from a clock operation."""

    def __init__(
        self,
        events_processed: int = 0,
        events_failed: int = 0,
        new_virtual_now: datetime | None = None,
        failures: list[dict[str, Any]] | None = None,
    ):
        self.events_processed = events_processed
        self.events_failed = events_failed
        self.new_virtual_now = new_virtual_now
        self.failures = failures or []

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for JSON response."""
        return {
            "virtual_now": self.new_virtual_now.isoformat() if self.new_virtual_now else None,
            "real_now": datetime.now(UTC).isoformat(),
            "events_processed": self.events_processed,
            "events_failed": self.events_failed,
            "failures": self.failures,
        }


async def now(session: AsyncSession, universe: Universe) -> datetime:
    """Get the current virtual time for a universe."""
    result = await session.execute(
        select(Universe.virtual_now).where(Universe.id == universe.id)
    )
    row = result.scalar_one_or_none()
    return row if row else universe.virtual_now


async def advance(
    session: AsyncSession,
    universe_id: str,
    to: datetime | None = None,
    by: dict[str, float] | None = None,
    max_events: int = 10000,
) -> ClockResponse:
    """
    Advance the virtual clock for a universe and process scheduled events.

    Algorithm:
    1. Lock the universe row (SELECT ... FOR UPDATE)
    2. Recover stuck 'processing' events from previous crashes
    3. Loop: pick next pending event with due_at <= target, ordered by (due_at, id)
    4. Set universe.virtual_now = event.due_at
    5. Mark event as processing
    6. Dispatch to handler (not yet implemented)
    7. Mark event as done
    8. COMMIT PER EVENT
    9. After loop completes, set virtual_now = target

    Args:
        session: AsyncSession
        universe_id: UUID string
        to: Target virtual time (absolute)
        by: Time delta dict (days, hours, minutes, seconds)
        max_events: Max events to process per advance (cap)

    Returns:
        ClockResponse with summary
    """
    import uuid

    universe_uuid = uuid.UUID(universe_id) if isinstance(universe_id, str) else universe_id

    # Recover stuck processing events (from previous crashes)
    await session.execute(
        select(ScheduledEvent)
        .where(
            (ScheduledEvent.universe_id == universe_uuid)
            & (ScheduledEvent.status == "processing")
        )
        .with_for_update()
    )
    await session.execute(
        ScheduledEvent.__table__.update()
        .where(
            (ScheduledEvent.universe_id == universe_uuid)
            & (ScheduledEvent.status == "processing")
        )
        .values(status="pending")
    )

    # Get the locked universe
    result = await session.execute(
        select(Universe).where(Universe.id == universe_uuid).with_for_update()
    )
    universe = result.scalar_one_or_none()
    if not universe:
        raise ValueError(f"Universe {universe_id} not found")

    # Calculate target time
    current_virtual = universe.virtual_now
    if to and by:
        raise ValueError("Cannot specify both 'to' and 'by'")

    if to:
        target_virtual = to
    elif by:
        from datetime import timedelta

        delta = timedelta(**by)
        target_virtual = datetime.fromtimestamp(
            current_virtual.timestamp() + delta.total_seconds(),
            tz=UTC,
        )
    else:
        target_virtual = current_virtual

    # Reject backwards advance
    if target_virtual < current_virtual:
        raise ValueError(
            f"Cannot advance backwards: target {target_virtual.isoformat()} < "
            f"current {current_virtual.isoformat()}"
        )

    # Process events in order
    response = ClockResponse()
    failures: list[dict[str, Any]] = []
    events_processed = 0

    while events_processed < max_events:
        # Get next pending event with due_at <= target
        result = await session.execute(
            select(ScheduledEvent)
            .where(
                (ScheduledEvent.universe_id == universe_uuid)
                & (ScheduledEvent.status == "pending")
                & (ScheduledEvent.due_at <= target_virtual)
            )
            .order_by(ScheduledEvent.due_at, ScheduledEvent.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        event = result.scalar_one_or_none()

        if not event:
            # No more events to process
            break

        # Update universe time to event time
        universe.virtual_now = event.due_at

        # Mark event as processing
        event.status = "processing"
        event.attempts += 1
        await session.flush()

        try:
            # Dispatch to handler
            from app.twin.scheduler import SchedulerContext, dispatch_handler

            context = SchedulerContext(session, universe, event)
            await dispatch_handler(context)

            # Mark as done
            event.status = "done"
            event.processed_at = datetime.now(UTC)
            await session.flush()

            # Commit this event's progress
            await session.commit()
            await session.begin()

            events_processed += 1

        except Exception as e:
            # Handler failed
            error_msg = str(e)
            event.status = "failed"
            event.error = error_msg
            event.processed_at = datetime.now(UTC)
            await session.flush()

            # Log the failure
            event_log_entry = EventLog(
                universe_id=universe_uuid,
                virtual_time=event.due_at,
                wall_time=datetime.now(UTC),
                category="event_processing",
                kind="handler_failed",
                resource_type="scheduled_event",
                resource_id=str(event.id),
                summary=f"Handler {event.kind} failed: {error_msg}",
                data={"error": error_msg, "event_kind": event.kind},
            )
            session.add(event_log_entry)
            await session.flush()

            # Commit and continue
            await session.commit()
            await session.begin()

            response.events_failed += 1
            failures.append(
                {
                    "event_id": event.id,
                    "kind": event.kind,
                    "error": error_msg,
                }
            )

            events_processed += 1

    # Reached max events cap
    if events_processed >= max_events:
        response_msg = f"Event processing cap reached ({max_events} events)"
        raise RuntimeError(response_msg)

    # Set final virtual_now to target
    result = await session.execute(
        select(Universe).where(Universe.id == universe_uuid).with_for_update()
    )
    universe = result.scalar_one()
    universe.virtual_now = target_virtual
    await session.flush()
    await session.commit()

    # Build response
    response.events_processed = events_processed
    response.new_virtual_now = target_virtual
    response.failures = failures

    return response
