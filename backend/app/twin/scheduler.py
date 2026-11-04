"""Event scheduler and handler registry for Chronos."""

import random
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EventLog, ScheduledEvent, Universe

# Handler registry: maps event kind -> coroutine function
_handlers: dict[str, Callable[["SchedulerContext"], Coroutine[Any, Any, None]]] = {}


def handler(kind: str) -> Callable:
    """
    Decorator to register an event handler.

    Usage:
        @handler('order.created')
        async def handle_order_created(context: SchedulerContext):
            ...
    """

    def decorator(func: Callable[["SchedulerContext"], Coroutine[Any, Any, None]]) -> Callable:
        _handlers[kind] = func
        return func

    return decorator


class SchedulerContext:
    """Context passed to event handlers."""

    def __init__(
        self,
        session: AsyncSession,
        universe: Universe,
        event: ScheduledEvent,
    ):
        self.session = session
        self.universe = universe
        self.event = event
        self._rng: random.Random | None = None

    @property
    def db(self) -> AsyncSession:
        """Database session."""
        return self.session

    @property
    def now(self) -> datetime:
        """Current virtual time (event's due_at)."""
        return self.event.due_at

    @property
    def rng(self) -> random.Random:
        """Seeded RNG for deterministic randomness."""
        if self._rng is None:
            # Seed from universe seed + event id for determinism
            seed_value = self.universe.seed ^ self.event.id
            self._rng = random.Random(seed_value)
        return self._rng

    async def schedule(
        self,
        kind: str,
        due_at: datetime,
        payload: dict[str, Any] | None = None,
    ) -> ScheduledEvent:
        """
        Schedule a new event to run later.

        This event will be picked up by the current advance() call if due_at <= target_virtual.

        Args:
            kind: Event kind (must be registered with @handler)
            due_at: Virtual time when this event should run
            payload: Event payload (JSON-serializable)

        Returns:
            The scheduled event
        """
        event = ScheduledEvent(
            universe_id=self.universe.id,
            kind=kind,
            due_at=due_at,
            payload=payload or {},
            status="pending",
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def log(
        self,
        category: str,
        kind: str,
        resource_type: str | None = None,
        resource_id: str | None = None,
        summary: str = "",
        data: dict[str, Any] | None = None,
    ) -> EventLog:
        """
        Log an event to the immutable event log.

        Args:
            category: Event category (e.g., 'payment', 'authorization')
            kind: Event kind (e.g., 'order.created', 'payment.captured')
            resource_type: Type of affected resource (e.g., 'order', 'payment')
            resource_id: ID of affected resource
            summary: Human-readable summary
            data: Additional event data

        Returns:
            The log entry
        """
        log_entry = EventLog(
            universe_id=self.universe.id,
            virtual_time=self.now,
            wall_time=datetime.now(UTC),
            category=category,
            kind=kind,
            resource_type=resource_type,
            resource_id=resource_id,
            summary=summary,
            data=data or {},
        )
        self.session.add(log_entry)
        await self.session.flush()
        return log_entry


async def dispatch_handler(
    context: SchedulerContext,
) -> None:
    """
    Dispatch an event to its registered handler.

    If no handler is registered, this is a no-op (allows forward-declaration).

    Args:
        context: Scheduler context with session, universe, event

    Raises:
        Any exception raised by the handler
    """
    handler_func = _handlers.get(context.event.kind)
    if handler_func:
        await handler_func(context)


def get_handler(kind: str) -> Callable[["SchedulerContext"], Coroutine[Any, Any, None]] | None:
    """Get a registered handler by kind, or None if not found."""
    return _handlers.get(kind)


def list_handlers() -> dict[str, Callable]:
    """List all registered handlers."""
    return dict(_handlers)
