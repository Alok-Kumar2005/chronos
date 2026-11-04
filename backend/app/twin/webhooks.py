"""Webhook event emission and management."""

from typing import Any

from sqlalchemy import select

from app.models import ScheduledEvent, Webhook, WebhookEvent
from app.twin.ids import generate_webhook_event_id
from app.twin.scheduler import SchedulerContext


async def emit_event(
    ctx: SchedulerContext,
    event_type: str,
    resource_type: str,
    resource: dict[str, Any],
    summary: str,
) -> None:
    """Emit a webhook event and schedule delivery to matching webhooks.
    
    This is the core function called by all other handlers that need to send webhooks.
    It:
    1. Creates a WebhookEvent record
    2. Logs the event to the event_log
    3. For each matching webhook, creates a WebhookDelivery scheduled for immediate delivery
       (plus any chaos delays)
    
    The delivery is scheduled as a 'webhook.deliver' event that will be processed when
    the clock advances.
    
    Args:
        ctx: SchedulerContext with database session, virtual clock, RNG, etc.
        event_type: PayPal event type like 'PAYMENT.CAPTURE.COMPLETED'
        resource_type: Resource type like 'capture', 'order', etc.
        resource: The resource object (order, capture, etc.) as a dict
        summary: Human-readable summary of the event
    """
    event_id = generate_webhook_event_id()

    # Create the WebhookEvent record
    event = WebhookEvent(
        id=event_id,
        universe_id=ctx.universe_id,
        event_type=event_type,
        resource_type=resource_type,
        summary=summary,
        resource=resource,
        created_virtual_at=ctx.now,
    )
    ctx.db.add(event)
    await ctx.db.flush()

    # Log the event
    await ctx.log(
        category="webhook_event",
        kind="event_created",
        resource_type="webhook_event",
        resource_id=event_id,
        summary=f"Webhook event created: {event_type}",
        data={
            "event_id": event_id,
            "event_type": event_type,
            "resource_type": resource_type,
        },
    )

    # Find all matching webhooks
    result = await ctx.db.execute(
        select(Webhook).where(Webhook.universe_id == ctx.universe_id)
    )
    webhooks = result.scalars().all()

    for webhook in webhooks:
        # Check if this webhook should receive this event
        should_deliver = False

        if "*" in webhook.event_types:
            # Subscribed to all events
            should_deliver = True
        elif event_type in webhook.event_types:
            # Explicitly subscribed to this event
            should_deliver = True

        if should_deliver:
            # Schedule delivery
            # For now, schedule immediately (no chaos delay); chaos is applied later
            delivery_event = ScheduledEvent(
                universe_id=ctx.universe_id,
                due_at=ctx.now,
                kind="webhook.deliver",
                payload={
                    "webhook_id": webhook.id,
                    "webhook_event_id": event_id,
                },
                status="pending",
            )
            ctx.db.add(delivery_event)

            await ctx.log(
                category="webhook_delivery",
                kind="delivery_scheduled",
                resource_type="webhook_delivery",
                resource_id=webhook.id,
                summary=f"Webhook delivery scheduled for {webhook.url}",
                data={
                    "webhook_id": webhook.id,
                    "event_id": event_id,
                    "event_type": event_type,
                },
            )
