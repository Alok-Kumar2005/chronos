"""Loads the appropriate shop handler variant based on shop_settings."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ShopSettings
from app.shop.paypal_client import PayPalClient


async def get_shop_handlers(
    db: AsyncSession,
    universe_id: uuid.UUID,
    paypal_client: PayPalClient,
):
    """Load the appropriate handler variant for the universe."""
    from app.shop.handlers_buggy import BuggyShopHandlers
    from app.shop.handlers_reference_fix import ReferenceFixShopHandlers

    # Get shop settings
    result = await db.execute(
        select(ShopSettings).where(ShopSettings.universe_id == universe_id)
    )
    settings = result.scalar_one_or_none()

    if not settings:
        # Create default settings (buggy variant)
        settings = ShopSettings(
            universe_id=universe_id,
            handler_variant="buggy",
        )
        db.add(settings)
        await db.flush()

    # Load the appropriate handler based on settings
    if settings.handler_variant == "buggy":
        return BuggyShopHandlers(paypal_client, db)
    elif settings.handler_variant == "reference_fix":
        return ReferenceFixShopHandlers(paypal_client, db)
    elif settings.handler_variant == "agent_patched":
        # For now, same as reference_fix (agent could patch at runtime)
        return ReferenceFixShopHandlers(paypal_client, db)
    else:
        raise ValueError(f"Unknown handler variant: {settings.handler_variant}")
