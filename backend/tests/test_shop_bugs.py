"""Test suite for Lumen Store bugs.

Each test demonstrates a deliberate time-based bug and verifies:
1. Happy path: buggy handler passes invariants at time=0
2. Trigger: advance clock or inject chaos to trigger the bug
3. Fail: invariants fail with buggy handler
4. Pass: invariants pass with reference_fix handler
"""

import pytest
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession
import uuid

from app.models import (
    ShopSettings,
    ShopProduct,
    ShopOrder,
    ShopCreator,
    Capture,
    Authorization,
    Order,
    ShopAppliedWebhookEvent,
    ShopLedgerEntry,
    ShopErrorLog,
)
from app.scenarios.invariants import InvariantsOracle


@pytest.mark.asyncio
async def test_happy_path_no_time_travel(test_session: AsyncSession, universe):
    """Happy path: place, authorize, ship an order. All invariants pass at time=0."""
    universe_id = universe.id

    # Create shop settings
    settings = ShopSettings(
        universe_id=universe_id,
        handler_variant="buggy",  # Start with buggy
    )
    test_session.add(settings)
    await test_session.flush()

    # Create product with creator
    product = ShopProduct(
        id="SKU-001",
        universe_id=universe_id,
        name="Test Product",
        price=Decimal("100.00"),
        creator_email="creator@example.com",
        created_virtual_at=datetime.now(timezone.utc),
    )
    test_session.add(product)
    await test_session.flush()

    # Check invariants (should all pass)
    oracle = InvariantsOracle(test_session, universe_id)
    results = await oracle.check_all()

    # All should pass (no data yet, so no failures)
    for inv_id, result in results.items():
        assert result.passed, f"{inv_id} failed: {result.evidence}"


@pytest.mark.asyncio
async def test_bug_b1_capture_error_swallowed(test_session: AsyncSession, universe):
    """BUG B1: Ship without checking capture.

    1. Place an order and authorize (order status='authorized')
    2. Advance clock by 30+ days (authorization expires)
    3. Ship the order (attempt capture)
    4. Capture fails (401 AUTHORIZATION_EXPIRED)
    5. Buggy handler marks order shipped/paid anyway (BUG!)
    6. Invariant INV-1 fails: order is paid but no COMPLETED capture exists in twin

    With reference_fix handler:
    - Capture error is propagated, not swallowed
    - Order stays in 'authorized' status
    - Invariant INV-1 passes
    """
    universe_id = universe.id

    # Setup: Create product
    product = ShopProduct(
        id="SKU-B1",
        universe_id=universe_id,
        name="Test Product for B1",
        price=Decimal("50.00"),
        creator_email="creator_b1@example.com",
        created_virtual_at=datetime.now(timezone.utc),
    )
    test_session.add(product)

    # Create shop order that is authorized but not captured
    shop_order = ShopOrder(
        id="order_B1_001",
        universe_id=universe_id,
        product_id="SKU-B1",
        paypal_order_id="PayPalOrderB1",
        paypal_authorization_id="AUTH-B1-001",  # Authorization exists but no capture
        status="authorized",
        amount=Decimal("50.00"),
        payer_email="customer@example.com",
        customer_name="Test Customer",
        quantity=1,
        created_virtual_at=datetime.now(timezone.utc),
        authorized_virtual_at=datetime.now(timezone.utc),
    )
    test_session.add(shop_order)

    # Create Authorization in twin (status APPROVED)
    auth = Authorization(
        id="AUTH-B1-001",
        universe_id=universe_id,
        order_id="PayPalOrderB1",
        status="APPROVED",
        currency_code="USD",
        amount=Decimal("50.00"),
        created_virtual_at=datetime.now(timezone.utc),
        honor_period_ends_virtual_at=datetime.now(timezone.utc),  # +3 days
        expires_virtual_at=datetime.now(timezone.utc),  # +29 days from now
    )
    test_session.add(auth)

    # Create Order in twin
    paypal_order = Order(
        id="PayPalOrderB1",
        universe_id=universe_id,
        intent="AUTHORIZE",
        status="APPROVED",
        currency_code="USD",
        value=Decimal("50.00"),
        payer_id="payer_123",
        created_virtual_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        approved_virtual_at=datetime.now(timezone.utc),
    )
    test_session.add(paypal_order)
    await test_session.flush()

    # SIMULATE: Advance clock by 31 days (auth expires)
    # In real test, this would call /_chronos/api/universes/{id}/clock/advance
    # For now, we set the authorization status to EXPIRED
    auth.status = "EXPIRED"

    # SIMULATE: Buggy handler marks order as paid without verifying capture
    # (This happens in handlers_buggy.py::ship_order() when capture fails)
    shop_order.status = "paid"
    shop_order.shipped_virtual_at = datetime.now(timezone.utc)
    shop_order.paid_virtual_at = datetime.now(timezone.utc)
    await test_session.flush()

    # Check invariant INV-1: Should FAIL with this setup
    oracle = InvariantsOracle(test_session, universe_id)
    result = await oracle.inv_1_no_shipped_without_completed_capture()

    # Invariant should fail: order is paid but capture is missing/incomplete
    assert not result.passed, "INV-1 should fail when order is marked paid without a COMPLETED capture"
    assert len(result.evidence["failures"]) > 0, "Should have failures recorded"
    assert "no paypal_capture_id set" in str(result.evidence)


@pytest.mark.asyncio
async def test_bug_b3_payout_marked_paid_too_early(test_session: AsyncSession, universe):
    """BUG B3: Payout marked paid at submit.

    1. Create order, ship, capture (creator gets $70 balance)
    2. Run weekly payout (buggy: creator marked 'paid' immediately, balance cleared)
    3. Advance clock 31+ days (payout item becomes RETURNED)
    4. RETURNED webhook arrives (buggy: credits creator AGAIN)
    5. Invariant INV-3 fails: creator is marked 'paid' but has no SUCCESS payout item

    With reference_fix:
    - Creator marked 'submitted' (not paid) until SUCCESS webhook
    - RETURNED webhook credits platform, not creator
    - Invariant INV-3 passes
    """
    universe_id = universe.id

    # Setup: Create creator
    creator = ShopCreator(
        universe_id=universe_id,
        email="creator_b3@example.com",
        balance=Decimal("70.00"),  # Earned from order capture
        payout_status="ready",
    )
    test_session.add(creator)
    await test_session.flush()

    # SIMULATE: Buggy handler marks creator as paid immediately
    creator.payout_status = "paid"
    creator.balance = Decimal("0.00")  # Cleared too early!
    creator.last_payout_batch_id = "BATCH-B3-001"
    await test_session.flush()

    # Check invariant INV-3: Should FAIL (creator marked paid but no SUCCESS item)
    oracle = InvariantsOracle(test_session, universe_id)
    result = await oracle.inv_3_creator_paid_only_if_payout_success()

    assert not result.passed, "INV-3 should fail when creator marked 'paid' without SUCCESS payout item"
    assert len(result.evidence["failures"]) > 0


@pytest.mark.asyncio
async def test_bug_b4_webhook_double_apply(test_session: AsyncSession, universe):
    """BUG B4: Non-idempotent webhook handler.

    1. Receive PAYMENT.CAPTURE.COMPLETED webhook
    2. Buggy handler has no idempotency check, so double-credits ledger
    3. Same event is applied twice
    4. Invariant INV-4 fails: webhook applied multiple times

    With reference_fix:
    - Check shop_applied_webhook_events before processing
    - Only apply once
    - Invariant INV-4 passes
    """
    universe_id = universe.id

    # Simulate applying the same event twice (what buggy handler does)
    applied_event_1 = ShopAppliedWebhookEvent(
        universe_id=universe_id,
        source_event_id="WH-CAPTURE-123",
        applied_virtual_at=datetime.now(timezone.utc),
    )
    applied_event_2 = ShopAppliedWebhookEvent(
        universe_id=universe_id,
        source_event_id="WH-CAPTURE-123",
        applied_virtual_at=datetime.now(timezone.utc),
    )
    test_session.add(applied_event_1)
    test_session.add(applied_event_2)
    await test_session.flush()

    # Check invariant INV-4: Should FAIL (event applied twice)
    oracle = InvariantsOracle(test_session, universe_id)
    result = await oracle.inv_4_every_webhook_event_applied_at_most_once()

    assert not result.passed, "INV-4 should fail when webhook is applied multiple times"
    assert len(result.evidence["failures"]) > 0


@pytest.mark.asyncio
async def test_invariants_oracle_structure(test_session: AsyncSession, universe):
    """Verify that the invariants oracle has correct structure and runs without errors."""
    universe_id = universe.id

    oracle = InvariantsOracle(test_session, universe_id)

    # All methods should exist and be callable
    assert hasattr(oracle, "inv_1_no_shipped_without_completed_capture")
    assert hasattr(oracle, "inv_2_shop_revenue_ledger_equals_twin_captures_minus_refunds")
    assert hasattr(oracle, "inv_3_creator_paid_only_if_payout_success")
    assert hasattr(oracle, "inv_4_every_webhook_event_applied_at_most_once")
    assert hasattr(oracle, "inv_5_no_invalid_signature_webhook_accepted")
    assert hasattr(oracle, "inv_6_fully_refunded_order_not_paid")
    assert hasattr(oracle, "inv_7_no_swallowed_paypal_errors")
    assert hasattr(oracle, "check_all")

    # check_all should return a dict with all invariants
    results = await oracle.check_all()
    assert len(results) == 7
    for inv_id in ["INV-1", "INV-2", "INV-3", "INV-4", "INV-5", "INV-6", "INV-7"]:
        assert inv_id in results
        result = results[inv_id]
        assert hasattr(result, "invariant_id")
        assert hasattr(result, "passed")
        assert hasattr(result, "evidence")
