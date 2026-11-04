"""Control API for managing universes and advancing virtual clocks."""

import secrets
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import PayPalError
from app.models import EventLog, ScheduledEvent, Universe
from app.twin.clock import advance as advance_clock
from app.twin.rate_limit import universe_creation_limiter

router = APIRouter(prefix="/_chronos/api", tags=["control"])


async def get_control_key(
    x_chronos_key: Annotated[str | None, Header()] = None,
) -> str:
    """Extract and validate control key header."""
    if not x_chronos_key:
        raise PayPalError(
            name="AUTHENTICATION_FAILURE",
            message="Missing X-Chronos-Key header",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    return x_chronos_key


@router.post("/universes", status_code=status.HTTP_201_CREATED)
async def create_universe(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """
    Create a new universe.

    Returns: {id, client_id, client_secret, control_key, base_url}

    Rate limited: 10 per hour per IP.
    """
    # Get client IP
    client_ip = request.client.host if request.client else "unknown"

    # Check rate limit
    if not universe_creation_limiter.is_allowed(client_ip):
        raise PayPalError(
            name="INVALID_REQUEST",
            message="Rate limit exceeded. Max 10 universes per hour per IP.",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    # Create universe
    from app.config import settings

    universe, client_secret, control_key, client_id = Universe.create(
        name=f"Universe-{secrets.token_hex(4)}",
        universe_ttl_days=settings.UNIVERSE_TTL_DAYS,
    )

    session.add(universe)
    await session.commit()

    # Secrets are returned once
    return {
        "id": str(universe.id),
        "client_id": universe.client_id,
        "client_secret": client_secret,
        "control_key": control_key,
        "base_url": settings.PUBLIC_BASE_URL,
    }


@router.get("/universes/{universe_id}")
async def get_universe(
    universe_id: str,
    x_chronos_key: Annotated[str, Depends(get_control_key)],
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Get universe details."""
    import uuid
    try:
        uid = uuid.UUID(universe_id)
    except ValueError:
        raise PayPalError(
            name="RESOURCE_NOT_FOUND",
            message="Invalid universe ID",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    result = await session.execute(select(Universe).where(Universe.id == uid))
    universe = result.scalar_one_or_none()

    if not universe:
        raise PayPalError(
            name="RESOURCE_NOT_FOUND",
            message="Universe not found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    # Verify control key
    if not Universe.verify_secret(x_chronos_key, universe.control_key_hash):
        raise PayPalError(
            name="AUTHENTICATION_FAILURE",
            message="Invalid control key",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    return {
        "id": str(universe.id),
        "name": universe.name,
        "client_id": universe.client_id,
        "virtual_now": universe.virtual_now.isoformat(),
        "created_at": universe.created_at.isoformat(),
        "expires_at": universe.expires_at.isoformat(),
        "seed": universe.seed,
    }


@router.get("/universes/{universe_id}/clock")
async def get_universe_clock(
    universe_id: str,
    x_chronos_key: Annotated[str, Depends(get_control_key)],
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Get the current clock state for a universe."""
    import uuid
    try:
        uid = uuid.UUID(universe_id)
    except ValueError:
        raise PayPalError(
            name="RESOURCE_NOT_FOUND",
            message="Invalid universe ID",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    result = await session.execute(select(Universe).where(Universe.id == uid))
    universe = result.scalar_one_or_none()

    if not universe:
        raise PayPalError(
            name="RESOURCE_NOT_FOUND",
            message="Universe not found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    # Verify control key
    if not Universe.verify_secret(x_chronos_key, universe.control_key_hash):
        raise PayPalError(
            name="AUTHENTICATION_FAILURE",
            message="Invalid control key",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    return {
        "virtual_now": universe.virtual_now.isoformat(),
        "real_now": datetime.now(UTC).isoformat(),
    }


@router.post("/universes/{universe_id}/clock/advance")
async def advance_universe_clock(
    universe_id: str,
    body: dict,
    x_chronos_key: Annotated[str, Depends(get_control_key)],
    session: AsyncSession = Depends(get_session),
) -> dict:
    """
    Advance the virtual clock for a universe.

    Body: {to: ISO8601} or {days, hours, minutes, seconds} or {by_days}
    """
    import uuid
    try:
        uid = uuid.UUID(universe_id)
    except ValueError:
        raise PayPalError(
            name="RESOURCE_NOT_FOUND",
            message="Invalid universe ID",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    result = await session.execute(select(Universe).where(Universe.id == uid))
    universe = result.scalar_one_or_none()

    if not universe:
        raise PayPalError(
            name="RESOURCE_NOT_FOUND",
            message="Universe not found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    # Verify control key
    if not Universe.verify_secret(x_chronos_key, universe.control_key_hash):
        raise PayPalError(
            name="AUTHENTICATION_FAILURE",
            message="Invalid control key",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # Parse request
    target = None
    delta = None

    if "to" in body:
        target = datetime.fromisoformat(body["to"].replace("Z", "+00:00"))
    elif "by_days" in body:
        # Frontend convenience: by_days maps to days
        delta = {"days": body["by_days"]}
    elif any(k in body for k in ["days", "hours", "minutes", "seconds"]):
        delta = {k: v for k, v in body.items() if k in ["days", "hours", "minutes", "seconds"]}
    else:
        raise PayPalError(
            name="INVALID_REQUEST",
            message="Request body must contain 'to', 'by_days', or time delta keys (days, hours, minutes, seconds)",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # Advance clock
    try:
        response = await advance_clock(session, str(uid), to=target, by=delta)
    except ValueError as e:
        raise PayPalError(
            name="INVALID_REQUEST",
            message=str(e),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except RuntimeError as e:
        raise PayPalError(
            name="INVALID_REQUEST",
            message=str(e),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    return response.to_dict()


@router.post("/universes/{universe_id}/clock/reset")
async def reset_universe_clock(
    universe_id: str,
    x_chronos_key: Annotated[str, Depends(get_control_key)],
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Reset the clock to the universe creation time."""
    import uuid
    try:
        uid = uuid.UUID(universe_id)
    except ValueError:
        raise PayPalError(
            name="RESOURCE_NOT_FOUND",
            message="Invalid universe ID",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    result = await session.execute(select(Universe).where(Universe.id == uid))
    universe = result.scalar_one_or_none()

    if not universe:
        raise PayPalError(
            name="RESOURCE_NOT_FOUND",
            message="Universe not found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    # Verify control key
    if not Universe.verify_secret(x_chronos_key, universe.control_key_hash):
        raise PayPalError(
            name="AUTHENTICATION_FAILURE",
            message="Invalid control key",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # Reset virtual_now to created_at
    universe.virtual_now = universe.created_at
    await session.commit()

    return {
        "virtual_now": universe.virtual_now.isoformat(),
        "real_now": datetime.now(UTC).isoformat(),
    }


@router.get("/universes/{universe_id}/events")
async def list_events(
    universe_id: str,
    x_chronos_key: Annotated[str, Depends(get_control_key)],
    session: AsyncSession = Depends(get_session),
    category: Annotated[str | None, Query()] = None,
    resource_id: Annotated[str | None, Query()] = None,
    after_id: Annotated[int | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> dict:
    """
    List event log entries for a universe.

    Filters: category, resource_id, after_id
    """
    import uuid
    try:
        uid = uuid.UUID(universe_id)
    except ValueError:
        raise PayPalError(
            name="RESOURCE_NOT_FOUND",
            message="Invalid universe ID",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    result = await session.execute(select(Universe).where(Universe.id == uid))
    universe = result.scalar_one_or_none()

    if not universe:
        raise PayPalError(
            name="RESOURCE_NOT_FOUND",
            message="Universe not found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    # Verify control key
    if not Universe.verify_secret(x_chronos_key, universe.control_key_hash):
        raise PayPalError(
            name="AUTHENTICATION_FAILURE",
            message="Invalid control key",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # Build query
    query = select(EventLog).where(EventLog.universe_id == uid)

    if category:
        query = query.where(EventLog.category == category)
    if resource_id:
        query = query.where(EventLog.resource_id == resource_id)
    if after_id is not None:
        query = query.where(EventLog.id > after_id)

    query = query.order_by(EventLog.id).limit(limit + 1)

    result = await session.execute(query)
    events = result.scalars().all()

    # Check if there are more
    has_more = len(events) > limit
    events = events[:limit]

    return {
        "events": [
            {
                "id": e.id,
                "virtual_time": e.virtual_time.isoformat(),
                "wall_time": e.wall_time.isoformat(),
                "category": e.category,
                "kind": e.kind,
                "resource_type": e.resource_type,
                "resource_id": e.resource_id,
                "summary": e.summary,
                "data": e.data,
            }
            for e in events
        ],
        "has_more": has_more,
        "next_id": events[-1].id if has_more and events else None,
        "total": len(events),
    }


@router.get("/universes/{universe_id}/scheduled")
async def list_scheduled_events(
    universe_id: str,
    x_chronos_key: Annotated[str, Depends(get_control_key)],
    session: AsyncSession = Depends(get_session),
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> dict:
    """
    List upcoming pending scheduled events for a universe.

    Ordered by (due_at, id).
    """
    import uuid
    try:
        uid = uuid.UUID(universe_id)
    except ValueError:
        raise PayPalError(
            name="RESOURCE_NOT_FOUND",
            message="Invalid universe ID",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    result = await session.execute(select(Universe).where(Universe.id == uid))
    universe = result.scalar_one_or_none()

    if not universe:
        raise PayPalError(
            name="RESOURCE_NOT_FOUND",
            message="Universe not found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    # Verify control key
    if not Universe.verify_secret(x_chronos_key, universe.control_key_hash):
        raise PayPalError(
            name="AUTHENTICATION_FAILURE",
            message="Invalid control key",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # Get pending events
    result = await session.execute(
        select(ScheduledEvent)
        .where(
            and_(
                ScheduledEvent.universe_id == uid,
                ScheduledEvent.status == "pending",
            )
        )
        .order_by(ScheduledEvent.due_at, ScheduledEvent.id)
        .limit(limit)
    )
    events = result.scalars().all()

    return {
        "scheduled_events": [
            {
                "id": e.id,
                "due_at": e.due_at.isoformat(),
                "kind": e.kind,
                "payload": e.payload,
                "attempts": e.attempts,
            }
            for e in events
        ],
    }


@router.post("/universes/{universe_id}/reset")
async def reset_universe(
    universe_id: str,
    x_chronos_key: Annotated[str, Depends(get_control_key)],
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Reset a universe: clear events and set virtual_now to current time."""
    import uuid
    try:
        uid = uuid.UUID(universe_id)
    except ValueError:
        raise PayPalError(
            name="RESOURCE_NOT_FOUND",
            message="Invalid universe ID",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    result = await session.execute(select(Universe).where(Universe.id == uid))
    universe = result.scalar_one_or_none()

    if not universe:
        raise PayPalError(
            name="RESOURCE_NOT_FOUND",
            message="Universe not found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    # Verify control key
    if not Universe.verify_secret(x_chronos_key, universe.control_key_hash):
        raise PayPalError(
            name="AUTHENTICATION_FAILURE",
            message="Invalid control key",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # Delete all events and logs
    await session.execute(
        ScheduledEvent.__table__.delete().where(ScheduledEvent.universe_id == uid)
    )
    await session.execute(
        EventLog.__table__.delete().where(EventLog.universe_id == uid)
    )

    # Reset virtual_now
    universe.virtual_now = datetime.now(UTC)
    await session.commit()

    return {"status": "reset", "virtual_now": universe.virtual_now.isoformat()}


@router.delete("/universes/{universe_id}")
async def delete_universe(
    universe_id: str,
    x_chronos_key: Annotated[str, Depends(get_control_key)],
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Delete a universe and all its data."""
    import uuid
    try:
        uid = uuid.UUID(universe_id)
    except ValueError:
        raise PayPalError(
            name="RESOURCE_NOT_FOUND",
            message="Invalid universe ID",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    result = await session.execute(select(Universe).where(Universe.id == uid))
    universe = result.scalar_one_or_none()

    if not universe:
        raise PayPalError(
            name="RESOURCE_NOT_FOUND",
            message="Universe not found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    # Verify control key
    if not Universe.verify_secret(x_chronos_key, universe.control_key_hash):
        raise PayPalError(
            name="AUTHENTICATION_FAILURE",
            message="Invalid control key",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # Delete universe (cascades to all children)
    await session.delete(universe)
    await session.commit()

    return {"status": "deleted"}


@router.post("/cleanup")
async def cleanup_expired_universes(
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Delete expired universes. Called at startup and can be called manually."""
    result = await session.execute(
        select(Universe).where(Universe.expires_at <= datetime.now(UTC))
    )
    expired = result.scalars().all()

    count = len(expired)
    for universe in expired:
        await session.delete(universe)

    await session.commit()

    return {"deleted_count": count}
