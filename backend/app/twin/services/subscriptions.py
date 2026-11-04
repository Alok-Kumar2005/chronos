"""PayPal Subscriptions v1 API service layer.

Implements billing plans, subscriptions, and recurring charges with
virtual clock-based scheduling and payment failure handling.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import uuid
from typing import Any

from sqlalchemy import select, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Universe,
    Product,
    BillingPlan,
    Subscription,
    SubscriptionPayment,
)
from app.twin.ids import (
    generate_product_id,
    generate_plan_id,
    generate_subscription_id,
)
from app.twin.clock import now
from app.twin.webhooks import emit_event


class SubscriptionError(Exception):
    """Subscription-specific error with PayPal-compatible error codes."""

    def __init__(
        self,
        name: str,
        message: str,
        details: list[dict[str, str]] | None = None,
    ):
        self.name = name
        self.message = message
        self.details = details or []


async def _get_universe_and_now(
    db_session: AsyncSession,
    universe_id: uuid.UUID,
) -> tuple[Universe, datetime]:
    """Helper to get universe and virtual time."""
    result = await db_session.execute(
        select(Universe).where(Universe.id == universe_id)
    )
    universe = result.scalar_one_or_none()
    if not universe:
        raise SubscriptionError(
            "RESOURCE_NOT_FOUND",
            "Universe not found",
        )
    virtual_time = await now(db_session, universe)
    return universe, virtual_time


class ProductService:
    """Service for managing catalog products."""

    @staticmethod
    async def create_product(
        db_session: AsyncSession,
        universe_id: uuid.UUID,
        name: str,
        product_type: str,
        description: str | None = None,
        category: str | None = None,
        image_url: str | None = None,
        home_url: str | None = None,
    ) -> Product:
        """Create a new catalog product.

        Args:
            db_session: Database session
            universe_id: Universe UUID
            name: Product name
            product_type: SERVICE, PHYSICAL_GOODS, or DIGITAL_GOODS
            description: Product description
            category: Product category
            image_url: Product image URL
            home_url: Product home page URL

        Returns:
            Created Product

        Raises:
            SubscriptionError: If product_type is invalid
        """
        if product_type not in ["SERVICE", "PHYSICAL_GOODS", "DIGITAL_GOODS"]:
            raise SubscriptionError(
                "INVALID_PRODUCT_TYPE",
                f"Invalid product type: {product_type}",
                [{"issue": "type", "description": "Must be SERVICE, PHYSICAL_GOODS, or DIGITAL_GOODS"}],
            )

        # Get universe to get virtual time
        result = await db_session.execute(
            select(Universe).where(Universe.id == universe_id)
        )
        universe = result.scalar_one_or_none()
        if not universe:
            raise SubscriptionError(
                "RESOURCE_NOT_FOUND",
                "Universe not found",
            )

        virtual_time = await now(db_session, universe)
        product = Product(
            id=generate_product_id(),
            universe_id=universe_id,
            name=name,
            description=description,
            type=product_type,
            category=category,
            image_url=image_url,
            home_url=home_url,
            status="ACTIVE",
            created_virtual_at=virtual_time,
        )

        db_session.add(product)
        await db_session.flush()

        return product

    @staticmethod
    async def get_product(
        db_session: AsyncSession,
        universe_id: uuid.UUID,
        product_id: str,
    ) -> Product:
        """Get a product by ID.

        Raises:
            SubscriptionError: If product not found
        """
        result = await db_session.execute(
            select(Product).where(
                and_(
                    Product.universe_id == universe_id,
                    Product.id == product_id,
                )
            )
        )
        product = result.scalar_one_or_none()

        if not product:
            raise SubscriptionError(
                "RESOURCE_NOT_FOUND",
                f"Product {product_id} not found",
            )

        return product


class BillingPlanService:
    """Service for managing billing plans."""

    @staticmethod
    async def create_plan(
        db_session: AsyncSession,
        universe_id: uuid.UUID,
        product_id: str,
        name: str,
        billing_cycles: list[dict[str, Any]],
        payment_preferences: dict[str, Any],
        description: str | None = None,
    ) -> BillingPlan:
        """Create a new billing plan with recurring billing cycles.

        Args:
            db_session: Database session
            universe_id: Universe UUID
            product_id: Product ID
            name: Plan name
            billing_cycles: List of billing cycle definitions
            payment_preferences: Payment preferences including payment_failure_threshold
            description: Plan description

        Returns:
            Created BillingPlan

        Raises:
            SubscriptionError: If product not found or validation fails
        """
        # Verify product exists
        product = await ProductService.get_product(db_session, universe_id, product_id)

        # Get universe to get virtual time
        result = await db_session.execute(
            select(Universe).where(Universe.id == universe_id)
        )
        universe = result.scalar_one_or_none()

        # Validate billing cycles
        if not billing_cycles:
            raise SubscriptionError(
                "INVALID_REQUEST",
                "At least one billing cycle is required",
                [{"issue": "billing_cycles", "description": "Must have at least one cycle"}],
            )

        for cycle in billing_cycles:
            if "frequency" not in cycle:
                raise SubscriptionError(
                    "INVALID_REQUEST",
                    "Billing cycle must have frequency",
                    [{"issue": "billing_cycles", "description": "Each cycle must have frequency"}],
                )
            if "pricing_scheme" not in cycle:
                raise SubscriptionError(
                    "INVALID_REQUEST",
                    "Billing cycle must have pricing_scheme",
                    [{"issue": "billing_cycles", "description": "Each cycle must have pricing_scheme"}],
                )

        # Validate payment failure threshold is an integer
        if "payment_failure_threshold" in payment_preferences:
            threshold = payment_preferences.get("payment_failure_threshold", 0)
            if not isinstance(threshold, int) or threshold < 0:
                raise SubscriptionError(
                    "INVALID_REQUEST",
                    "payment_failure_threshold must be a non-negative integer",
                    [
                        {
                            "issue": "payment_failure_threshold",
                            "description": "Must be a non-negative integer",
                        }
                    ],
                )

        virtual_time = await now(db_session, universe)
        plan = BillingPlan(
            id=generate_plan_id(),
            universe_id=universe_id,
            product_id=product_id,
            name=name,
            description=description,
            status="CREATED",
            billing_cycles=billing_cycles,
            payment_preferences=payment_preferences,
            created_virtual_at=virtual_time,
        )

        db_session.add(plan)
        await db_session.flush()

        return plan

    @staticmethod
    async def get_plan(
        db_session: AsyncSession,
        universe_id: uuid.UUID,
        plan_id: str,
    ) -> BillingPlan:
        """Get a billing plan by ID.

        Raises:
            SubscriptionError: If plan not found
        """
        result = await db_session.execute(
            select(BillingPlan).where(
                and_(
                    BillingPlan.universe_id == universe_id,
                    BillingPlan.id == plan_id,
                )
            )
        )
        plan = result.scalar_one_or_none()

        if not plan:
            raise SubscriptionError(
                "RESOURCE_NOT_FOUND",
                f"Plan {plan_id} not found",
            )

        return plan


class SubscriptionService:
    """Service for managing subscriptions and billing cycles."""

    @staticmethod
    async def create_subscription(
        db_session: AsyncSession,
        universe_id: uuid.UUID,
        plan_id: str,
        subscriber_name: dict[str, str],
        subscriber_email: str,
        quantity: str = "1",
        shipping_amount: Decimal | None = None,
        return_url: str | None = None,
        cancel_url: str | None = None,
    ) -> Subscription:
        """Create a new subscription.

        Returns subscription in APPROVAL_PENDING status. Must be approved
        via control endpoint to transition to ACTIVE and trigger first charge.

        Args:
            db_session: Database session
            universe_id: Universe UUID
            plan_id: Billing plan ID
            subscriber_name: {given_name, surname}
            subscriber_email: Subscriber email address
            quantity: Number of units (default "1")
            shipping_amount: Shipping amount if applicable
            return_url: Approval return URL
            cancel_url: Approval cancel URL

        Returns:
            Created Subscription in APPROVAL_PENDING status

        Raises:
            SubscriptionError: If plan not found
        """
        # Verify plan exists
        plan = await BillingPlanService.get_plan(db_session, universe_id, plan_id)

        _, virtual_time = await _get_universe_and_now(db_session, universe_id)
        payment_failure_threshold = plan.payment_preferences.get(
            "payment_failure_threshold", 0
        )

        subscription = Subscription(
            id=generate_subscription_id(),
            universe_id=universe_id,
            plan_id=plan_id,
            status="APPROVAL_PENDING",
            subscriber_name=subscriber_name,
            subscriber_email=subscriber_email,
            quantity=quantity,
            shipping_amount=shipping_amount,
            return_url=return_url,
            cancel_url=cancel_url,
            failed_payment_count=0,
            payment_failure_threshold=payment_failure_threshold,
            created_virtual_at=virtual_time,
        )

        db_session.add(subscription)
        await db_session.flush()

        return subscription

    @staticmethod
    async def get_subscription(
        db_session: AsyncSession,
        universe_id: uuid.UUID,
        subscription_id: str,
    ) -> Subscription:
        """Get a subscription by ID.

        Raises:
            SubscriptionError: If subscription not found
        """
        result = await db_session.execute(
            select(Subscription).where(
                and_(
                    Subscription.universe_id == universe_id,
                    Subscription.id == subscription_id,
                )
            )
        )
        subscription = result.scalar_one_or_none()

        if not subscription:
            raise SubscriptionError(
                "RESOURCE_NOT_FOUND",
                f"Subscription {subscription_id} not found",
            )

        return subscription

    @staticmethod
    async def approve_subscription(
        db_session: AsyncSession,
        universe_id: uuid.UUID,
        subscription_id: str,
    ) -> Subscription:
        """Approve subscription (control endpoint).

        Transitions subscription to ACTIVE and schedules first billing charge.

        Args:
            db_session: Database session
            universe_id: Universe UUID
            subscription_id: Subscription ID

        Returns:
            Updated subscription in ACTIVE status

        Raises:
            SubscriptionError: If subscription not found or already approved
        """
        subscription = await SubscriptionService.get_subscription(
            db_session, universe_id, subscription_id
        )

        if subscription.status != "APPROVAL_PENDING":
            raise SubscriptionError(
                "SUBSCRIPTION_STATUS_INVALID",
                f"Cannot approve subscription in {subscription.status} status",
            )

        _, virtual_time = await _get_universe_and_now(db_session, universe_id)
        subscription.status = "ACTIVE"
        subscription.start_time = virtual_time
        subscription.status_update_virtual_at = virtual_time

        # Schedule first billing charge immediately
        plan = await BillingPlanService.get_plan(
            db_session, universe_id, subscription.plan_id
        )
        billing_cycle = plan.billing_cycles[0]
        frequency = billing_cycle.get("frequency", {})
        
        # Calculate next billing time based on frequency
        interval_unit = frequency.get("interval_unit", "MONTH")
        interval_count = frequency.get("interval_count", 1)

        next_billing_time = SubscriptionService._calculate_next_billing_time(
            virtual_time, interval_unit, interval_count
        )
        subscription.next_billing_time = next_billing_time
        subscription.current_cycle_sequence = 0

        # Emit webhook event
        await emit_event(
            db_session,
            universe_id,
            "BILLING.SUBSCRIPTION.ACTIVATED",
            {"id": subscription.id},
            subscription,
        )

        await db_session.flush()

        return subscription

    @staticmethod
    async def cancel_subscription(
        db_session: AsyncSession,
        universe_id: uuid.UUID,
        subscription_id: str,
        reason: str | None = None,
    ) -> None:
        """Cancel a subscription.

        Prevents future billing cycles.

        Args:
            db_session: Database session
            universe_id: Universe UUID
            subscription_id: Subscription ID
            reason: Cancellation reason

        Raises:
            SubscriptionError: If subscription not found or already cancelled
        """
        subscription = await SubscriptionService.get_subscription(
            db_session, universe_id, subscription_id
        )

        if subscription.status == "CANCELLED":
            raise SubscriptionError(
                "SUBSCRIPTION_ALREADY_CANCELLED",
                "Subscription is already cancelled",
            )

        _, virtual_time = await _get_universe_and_now(db_session, universe_id)
        subscription.status = "CANCELLED"
        subscription.status_change_reason = reason or "CUSTOMER_REQUEST"
        subscription.status_update_virtual_at = virtual_time

        # Emit webhook event
        await emit_event(
            db_session,
            universe_id,
            "BILLING.SUBSCRIPTION.CANCELLED",
            {"id": subscription.id},
            subscription,
        )

        await db_session.flush()

    @staticmethod
    async def suspend_subscription(
        db_session: AsyncSession,
        universe_id: uuid.UUID,
        subscription_id: str,
        reason: str | None = None,
    ) -> None:
        """Suspend a subscription (control endpoint).

        Temporarily stops billing. Can be reactivated with activate_subscription.

        Args:
            db_session: Database session
            universe_id: Universe UUID
            subscription_id: Subscription ID
            reason: Suspension reason

        Raises:
            SubscriptionError: If subscription not found or already suspended
        """
        subscription = await SubscriptionService.get_subscription(
            db_session, universe_id, subscription_id
        )

        if subscription.status == "SUSPENDED":
            raise SubscriptionError(
                "SUBSCRIPTION_ALREADY_SUSPENDED",
                "Subscription is already suspended",
            )

        _, virtual_time = await _get_universe_and_now(db_session, universe_id)
        subscription.status = "SUSPENDED"
        subscription.status_change_reason = reason or "PAYMENT_FAILURE"
        subscription.status_update_virtual_at = virtual_time

        # Emit webhook event
        await emit_event(
            db_session,
            universe_id,
            "BILLING.SUBSCRIPTION.SUSPENDED",
            {"id": subscription.id},
            subscription,
        )

        await db_session.flush()

    @staticmethod
    async def activate_subscription(
        db_session: AsyncSession,
        universe_id: uuid.UUID,
        subscription_id: str,
    ) -> Subscription:
        """Reactivate a suspended subscription (control endpoint).

        Args:
            db_session: Database session
            universe_id: Universe UUID
            subscription_id: Subscription ID

        Returns:
            Updated subscription in ACTIVE status

        Raises:
            SubscriptionError: If subscription not found or not suspended
        """
        subscription = await SubscriptionService.get_subscription(
            db_session, universe_id, subscription_id
        )

        if subscription.status != "SUSPENDED":
            raise SubscriptionError(
                "SUBSCRIPTION_NOT_SUSPENDED",
                f"Cannot activate subscription in {subscription.status} status",
            )

        _, virtual_time = await _get_universe_and_now(db_session, universe_id)
        subscription.status = "ACTIVE"
        subscription.status_change_reason = None
        subscription.status_update_virtual_at = virtual_time

        # Emit webhook event
        await emit_event(
            db_session,
            universe_id,
            "BILLING.SUBSCRIPTION.ACTIVATED",
            {"id": subscription.id},
            subscription,
        )

        await db_session.flush()

        return subscription

    @staticmethod
    def _calculate_next_billing_time(
        current_time: datetime, interval_unit: str, interval_count: int
    ) -> datetime:
        """Calculate next billing time based on frequency.

        Args:
            current_time: Current virtual time
            interval_unit: MONTH, YEAR, WEEK, DAY
            interval_count: How many intervals

        Returns:
            Next billing datetime in UTC
        """
        if interval_unit == "MONTH":
            # Add months (preserving day of month where possible)
            month = current_time.month + interval_count
            year = current_time.year + (month - 1) // 12
            month = ((month - 1) % 12) + 1

            # Handle day overflow (e.g., Jan 31 + 1 month → Feb 28/29)
            import calendar

            max_day = calendar.monthrange(year, month)[1]
            day = min(current_time.day, max_day)

            return current_time.replace(year=year, month=month, day=day)

        elif interval_unit == "YEAR":
            return current_time + timedelta(days=365 * interval_count)

        elif interval_unit == "WEEK":
            return current_time + timedelta(weeks=interval_count)

        elif interval_unit == "DAY":
            return current_time + timedelta(days=interval_count)

        else:
            # Default to days
            return current_time + timedelta(days=interval_count)

    @staticmethod
    async def process_billing_cycles(
        db_session: AsyncSession,
        universe_id: uuid.UUID,
    ) -> None:
        """Process all due billing cycles.

        Called by scheduler. Processes subscriptions with
        next_billing_time <= virtual_now.

        This function:
        1. Finds all ACTIVE subscriptions with due billing cycles
        2. Charges each subscription
        3. Increments failed_payment_count for failures
        4. Suspends if payment_failure_threshold exceeded
        5. Emits webhook events
        6. Schedules next billing cycle

        Args:
            db_session: Database session
            universe_id: Universe UUID
        """
        _, virtual_time = await _get_universe_and_now(db_session, universe_id)

        # Find all ACTIVE subscriptions with due billing cycles
        result = await db_session.execute(
            select(Subscription).where(
                and_(
                    Subscription.universe_id == universe_id,
                    Subscription.status == "ACTIVE",
                    Subscription.next_billing_time <= virtual_time,
                )
            )
        )
        subscriptions = result.scalars().all()

        for subscription in subscriptions:
            plan = await BillingPlanService.get_plan(
                db_session, universe_id, subscription.plan_id
            )

            # Get the current billing cycle
            current_cycle = plan.billing_cycles[0]  # Simplified: use first cycle
            pricing_scheme = current_cycle.get("pricing_scheme", {})
            fixed_price = pricing_scheme.get("fixed_price", {})
            amount = Decimal(fixed_price.get("value", "0"))
            currency_code = fixed_price.get("currency_code", "USD")

            # Determine if this subscriber will fail payment
            # (use magic email domains for testing)
            will_fail = subscription.subscriber_email.endswith("@failed.test")

            if not will_fail:
                # Process successful charge
                payment = SubscriptionPayment(
                    id=f"txn_{subscription.id}_{subscription.current_cycle_sequence}",
                    universe_id=universe_id,
                    subscription_id=subscription.id,
                    cycle_sequence=subscription.current_cycle_sequence,
                    amount=amount,
                    currency_code=currency_code,
                    status="COMPLETED",
                    billing_virtual_at=virtual_time,
                    type="RECURRING",
                    created_virtual_at=virtual_time,
                )
                db_session.add(payment)

                # Emit webhook event
                await emit_event(
                    db_session,
                    universe_id,
                    "PAYMENT.SALE.COMPLETED",
                    {"id": payment.id},
                    payment,
                )

                # Reset failure count
                subscription.failed_payment_count = 0

            else:
                # Process failed charge
                payment = SubscriptionPayment(
                    id=f"txn_{subscription.id}_{subscription.current_cycle_sequence}",
                    universe_id=universe_id,
                    subscription_id=subscription.id,
                    cycle_sequence=subscription.current_cycle_sequence,
                    amount=amount,
                    currency_code=currency_code,
                    status="FAILED",
                    billing_virtual_at=virtual_time,
                    type="RECURRING",
                    failure_reason="INSTRUMENT_DECLINED",
                    created_virtual_at=virtual_time,
                )
                db_session.add(payment)

                # Emit webhook event
                await emit_event(
                    db_session,
                    universe_id,
                    "BILLING.SUBSCRIPTION.PAYMENT.FAILED",
                    {"id": payment.id},
                    payment,
                )

                # Increment failure count
                subscription.failed_payment_count += 1

                # Check if we've hit the threshold
                if (
                    subscription.payment_failure_threshold > 0
                    and subscription.failed_payment_count
                    >= subscription.payment_failure_threshold
                ):
                    # Suspend subscription
                    await SubscriptionService.suspend_subscription(
                        db_session,
                        universe_id,
                        subscription.id,
                        "PAYMENT_FAILURE",
                    )
                    await db_session.flush()
                    continue  # Don't schedule next billing for suspended

            # Schedule next billing cycle
            frequency = current_cycle.get("frequency", {})
            interval_unit = frequency.get("interval_unit", "MONTH")
            interval_count = frequency.get("interval_count", 1)

            subscription.current_cycle_sequence += 1
            subscription.next_billing_time = (
                SubscriptionService._calculate_next_billing_time(
                    virtual_time, interval_unit, interval_count
                )
            )

            await db_session.flush()

    @staticmethod
    async def get_subscription_transactions(
        db_session: AsyncSession,
        universe_id: uuid.UUID,
        subscription_id: str,
        start_index: int = 0,
        page_size: int = 20,
    ) -> tuple[list[SubscriptionPayment], int]:
        """Get paginated transaction list for a subscription.

        Args:
            db_session: Database session
            universe_id: Universe UUID
            subscription_id: Subscription ID
            start_index: Pagination start index
            page_size: Number of items per page

        Returns:
            Tuple of (transactions, total_count)

        Raises:
            SubscriptionError: If subscription not found
        """
        # Verify subscription exists
        await SubscriptionService.get_subscription(
            db_session, universe_id, subscription_id
        )

        # Get total count
        count_result = await db_session.execute(
            select(SubscriptionPayment).where(
                and_(
                    SubscriptionPayment.universe_id == universe_id,
                    SubscriptionPayment.subscription_id == subscription_id,
                )
            )
        )
        total_count = len(count_result.scalars().all())

        # Get paginated results
        result = await db_session.execute(
            select(SubscriptionPayment)
            .where(
                and_(
                    SubscriptionPayment.universe_id == universe_id,
                    SubscriptionPayment.subscription_id == subscription_id,
                )
            )
            .order_by(desc(SubscriptionPayment.billing_virtual_at))
            .offset(start_index)
            .limit(page_size)
        )

        return result.scalars().all(), total_count
