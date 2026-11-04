"""Tests for PayPal Payouts v1 API."""

import pytest
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Universe, AccountBalance
from app.twin.services.payouts import PayoutService, PayoutError


@pytest.mark.asyncio
async def test_create_payout_batch_happy_path(
    test_session: AsyncSession,
    universe: Universe,
) -> None:
    """Test creating a payout batch successfully."""
    virtual_now = universe.virtual_now

    # Create sender balance
    balance = AccountBalance(
        universe_id=universe.id,
        currency_code="USD",
        available=Decimal("1000.00"),
    )
    test_session.add(balance)
    await test_session.commit()

    sender_batch_header = {
        "sender_batch_id": "batch_1",
        "email_subject": "Payouts",
        "email_message": "You have a payout",
        "currency_code": "USD",
    }

    items = [
        {
            "sender_item_id": "item_1",
            "recipient_type": "EMAIL",
            "receiver": "recipient@example.com",
            "amount": "100.00",
            "note": "Payment for services",
        },
    ]

    result = await PayoutService.create_payout_batch(
        test_session,
        universe.id,
        "batch_1",
        sender_batch_header,
        items,
        virtual_now,
    )

    await test_session.commit()

    # Verify batch was created
    assert "batch_header" in result
    assert result["batch_header"]["batch_status"] == "PENDING"
    assert result["batch_header"]["sender_batch_id"] == "batch_1"

    # Verify sender balance was debited (amount + fee)
    balance_result = await test_session.execute(
        select(AccountBalance).where(
            AccountBalance.universe_id == universe.id,
            AccountBalance.currency_code == "USD",
        )
    )
    balance_obj = balance_result.scalars().first()
    assert balance_obj is not None
    # Balance should be reduced by payout amount + fee
    expected_debit = Decimal("100.00") + PayoutService.PAYOUT_FEE_PER_ITEM
    assert balance_obj.available == Decimal("1000.00") - expected_debit


@pytest.mark.asyncio
async def test_duplicate_sender_batch_id_returns_error(
    test_session: AsyncSession,
    universe: Universe,
) -> None:
    """Test that duplicate sender_batch_id returns an error."""
    virtual_now = universe.virtual_now

    # Create sender balance
    balance = AccountBalance(
        universe_id=universe.id,
        currency_code="USD",
        available=Decimal("1000.00"),
    )
    test_session.add(balance)
    await test_session.commit()

    sender_batch_header = {
        "sender_batch_id": "batch_1",
        "email_subject": "Payouts",
        "currency_code": "USD",
    }

    items = [
        {
            "sender_item_id": "item_1",
            "recipient_type": "EMAIL",
            "receiver": "recipient@example.com",
            "amount": "100.00",
        },
    ]

    # First batch succeeds
    await PayoutService.create_payout_batch(
        test_session,
        universe.id,
        "batch_1",
        sender_batch_header,
        items,
        virtual_now,
    )
    await test_session.commit()

    # Second batch with same sender_batch_id should fail
    with pytest.raises(PayoutError) as exc_info:
        await PayoutService.create_payout_batch(
            test_session,
            universe.id,
            "batch_1",
            sender_batch_header,
            items,
            virtual_now,
        )

    assert exc_info.value.name == "USER_BUSINESS_ERROR"
    assert exc_info.value.http_status == 400
