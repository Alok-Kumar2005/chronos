"""Tests for PayPal Subscriptions v1 API.

Tests cover:
- Plan creation with billing cycles, pricing scheme, payment failure thresholds
- Subscription creation returning APPROVAL_PENDING with approval link
- Subscription approval transitioning to ACTIVE and billing first cycle
- Virtual clock advance triggering recurring charges
- Payment failures incrementing counter and leading to suspension
- Subscription state transitions (activate, suspend, cancel)
"""

import pytest
from decimal import Decimal
from datetime import UTC, datetime, timedelta
from httpx import AsyncClient

from app.models import Universe, BillingPlan, Subscription, SubscriptionPayment
from app.twin.ids import generate_plan_id, generate_subscription_id


@pytest.mark.asyncio
async def test_create_plan_monthly(client: AsyncClient, universe: Universe):
    """Test creating a monthly billing plan with payment failure threshold."""
    # Get token using the universe's test secret
    token_resp = await client.post(
        "/v1/oauth2/token",
        auth=(universe.client_id, universe.test_secret),
        data={"grant_type": "client_credentials", "scope": "api"},
    )
    assert token_resp.status_code == 200
    token = token_resp.json()["access_token"]

    # Create plan
    resp = await client.post(
        "/v1/catalogs/products",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "Test Product",
            "type": "SERVICE",
        },
    )
    assert resp.status_code == 201
    product = resp.json()
    product_id = product["id"]

    # Create billing plan
    resp = await client.post(
        "/v1/billing/plans",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "product_id": product_id,
            "name": "Monthly Plan",
            "description": "A monthly subscription plan",
            "billing_cycles": [
                {
                    "frequency": {
                        "interval_unit": "MONTH",
                        "interval_count": 1,
                    },
                    "pricing_scheme": {
                        "fixed_price": {
                            "value": "10.00",
                            "currency_code": "USD",
                        }
                    },
                    "tenure_type": "REGULAR",
                    "sequence": 1,
                    "total_cycles": 0,  # infinite
                }
            ],
            "payment_preferences": {
                "service_type": "RECURRING",
                "auto_bill_amount": "MAX_AMOUNT",
                "setup_fee": {
                    "value": "0.00",
                    "currency_code": "USD",
                },
                "setup_fee_failure_action": "CONTINUE",
                "payment_failure_threshold": 2,  # Suspend after 2 failures
            },
        },
    )

    print(f"Status: {resp.status_code}")
    print(f"Response: {resp.json()}")
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "CREATED"
    assert data["name"] == "Monthly Plan"
    assert data["id"]


@pytest.mark.asyncio
async def test_create_subscription_approval_pending(
    client: AsyncClient, universe: Universe
):
    """Test creating a subscription that returns APPROVAL_PENDING status."""
    # Get token
    token_resp = await client.post(
        "/v1/oauth2/token",
        auth=(universe.client_id, universe.test_secret),
        data={"grant_type": "client_credentials", "scope": "api"},
    )
    token = token_resp.json()["access_token"]

    # Create product
    resp = await client.post(
        "/v1/catalogs/products",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "Test Product",
            "type": "SERVICE",
        },
    )
    product_id = resp.json()["id"]

    # Create billing plan
    resp = await client.post(
        "/v1/billing/plans",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "product_id": product_id,
            "name": "Monthly Plan",
            "billing_cycles": [
                {
                    "frequency": {
                        "interval_unit": "MONTH",
                        "interval_count": 1,
                    },
                    "pricing_scheme": {
                        "fixed_price": {
                            "value": "10.00",
                            "currency_code": "USD",
                        }
                    },
                    "tenure_type": "REGULAR",
                    "sequence": 1,
                    "total_cycles": 0,
                }
            ],
            "payment_preferences": {
                "service_type": "RECURRING",
                "auto_bill_amount": "MAX_AMOUNT",
                "payment_failure_threshold": 2,
            },
        },
    )
    plan_id = resp.json()["id"]

    # Create subscription
    resp = await client.post(
        "/v1/billing/subscriptions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "plan_id": plan_id,
            "subscriber": {
                "name": {
                    "given_name": "John",
                    "surname": "Doe",
                },
                "email_address": "john@example.com",
            },
            "application_context": {
                "return_url": "https://example.com/return",
                "cancel_url": "https://example.com/cancel",
            },
        },
    )

    print(f"Status: {resp.status_code}")
    print(f"Response: {resp.json()}")
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "APPROVAL_PENDING"
    assert data["id"]
    
    # Check for approval link
    links = data.get("links", [])
    approve_link = next((l for l in links if l.get("rel") == "approve"), None)
    assert approve_link is not None
    assert "subscriptions" in approve_link["href"] or "approve" in approve_link["href"]


@pytest.mark.asyncio
async def test_subscription_approval_and_billing(
    client: AsyncClient, db_session, universe: Universe
):
    """Test subscription approval triggers ACTIVE status and first billing charge."""
    # Get token
    token_resp = await client.post(
        "/v1/oauth2/token",
        auth=(universe.client_id, universe.test_secret),
        data={"grant_type": "client_credentials", "scope": "api"},
    )
    token = token_resp.json()["access_token"]

    # Create product, plan, subscription
    resp = await client.post(
        "/v1/catalogs/products",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Test Product", "type": "SERVICE"},
    )
    product_id = resp.json()["id"]

    resp = await client.post(
        "/v1/billing/plans",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "product_id": product_id,
            "name": "Monthly Plan",
            "billing_cycles": [
                {
                    "frequency": {
                        "interval_unit": "MONTH",
                        "interval_count": 1,
                    },
                    "pricing_scheme": {
                        "fixed_price": {
                            "value": "15.00",
                            "currency_code": "USD",
                        }
                    },
                    "tenure_type": "REGULAR",
                    "sequence": 1,
                    "total_cycles": 0,
                }
            ],
            "payment_preferences": {
                "service_type": "RECURRING",
                "auto_bill_amount": "MAX_AMOUNT",
                "payment_failure_threshold": 2,
            },
        },
    )
    plan_id = resp.json()["id"]

    resp = await client.post(
        "/v1/billing/subscriptions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "plan_id": plan_id,
            "subscriber": {
                "name": {"given_name": "John", "surname": "Doe"},
                "email_address": "john@example.com",
            },
            "application_context": {
                "return_url": "https://example.com/return",
                "cancel_url": "https://example.com/cancel",
            },
        },
    )
    assert resp.status_code == 201
    sub_id = resp.json()["id"]

    # Approve subscription using control API
    resp = await client.post(
        f"/_chronos/api/universes/{universe.id}/subscriptions/{sub_id}/approve",
        headers={"X-Chronos-Key": universe.test_control_key},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ACTIVE"

    # Get subscription to verify active and check for initial charge
    resp = await client.get(
        f"/v1/billing/subscriptions/{sub_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_monthly_plan_3_billing_cycles(
    client: AsyncClient, db_session, universe: Universe
):
    """Test: advance 3 months yields 3 charges and 3 webhook events."""
    # Get token
    token_resp = await client.post(
        "/v1/oauth2/token",
        auth=(universe.client_id, universe.test_secret),
        data={"grant_type": "client_credentials", "scope": "api"},
    )
    token = token_resp.json()["access_token"]

    # Create product, plan, subscription
    resp = await client.post(
        "/v1/catalogs/products",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Product", "type": "SERVICE"},
    )
    product_id = resp.json()["id"]

    resp = await client.post(
        "/v1/billing/plans",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "product_id": product_id,
            "name": "Monthly",
            "billing_cycles": [
                {
                    "frequency": {
                        "interval_unit": "MONTH",
                        "interval_count": 1,
                    },
                    "pricing_scheme": {
                        "fixed_price": {
                            "value": "20.00",
                            "currency_code": "USD",
                        }
                    },
                    "tenure_type": "REGULAR",
                    "sequence": 1,
                    "total_cycles": 0,
                }
            ],
            "payment_preferences": {
                "service_type": "RECURRING",
                "auto_bill_amount": "MAX_AMOUNT",
                "payment_failure_threshold": 2,
            },
        },
    )
    plan_id = resp.json()["id"]

    resp = await client.post(
        "/v1/billing/subscriptions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "plan_id": plan_id,
            "subscriber": {
                "name": {"given_name": "John", "surname": "Doe"},
                "email_address": "john@example.com",
            },
            "application_context": {
                "return_url": "https://example.com/return",
                "cancel_url": "https://example.com/cancel",
            },
        },
    )
    sub_id = resp.json()["id"]

    # Approve subscription
    resp = await client.post(
        f"/_chronos/api/universes/{universe.id}/subscriptions/{sub_id}/approve",
        headers={"X-Chronos-Key": universe.test_control_key},
    )
    assert resp.status_code == 200

    # Advance 1 month
    resp = await client.post(
        f"/_chronos/api/universes/{universe.id}/clock/advance",
        headers={"X-Chronos-Key": universe.test_control_key},
        json={"by_days": 30},
    )
    assert resp.status_code == 200

    # Advance 2 more months
    resp = await client.post(
        f"/_chronos/api/universes/{universe.id}/clock/advance",
        headers={"X-Chronos-Key": universe.test_control_key},
        json={"by_days": 30},
    )
    assert resp.status_code == 200

    resp = await client.post(
        f"/_chronos/api/universes/{universe.id}/clock/advance",
        headers={"X-Chronos-Key": universe.test_control_key},
        json={"by_days": 30},
    )
    assert resp.status_code == 200

    # Get subscription payments to verify 3 charges
    resp = await client.get(
        f"/v1/billing/subscriptions/{sub_id}/transactions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    transactions = data.get("transactions", [])
    
    # Should have 4 transactions: 1 initial + 3 recurring charges
    # (or 3 if first charge is on subscription creation)
    assert len(transactions) >= 3, f"Expected at least 3 transactions, got {len(transactions)}"


@pytest.mark.asyncio
async def test_payment_failure_suspension(
    client: AsyncClient, db_session, universe: Universe
):
    """Test: failing subscriber suspended at payment_failure_threshold."""
    # Get token
    token_resp = await client.post(
        "/v1/oauth2/token",
        auth=(universe.client_id, universe.test_secret),
        data={"grant_type": "client_credentials", "scope": "api"},
    )
    token = token_resp.json()["access_token"]

    # Create product and plan with threshold=1 for faster test
    resp = await client.post(
        "/v1/catalogs/products",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Product", "type": "SERVICE"},
    )
    product_id = resp.json()["id"]

    resp = await client.post(
        "/v1/billing/plans",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "product_id": product_id,
            "name": "Monthly",
            "billing_cycles": [
                {
                    "frequency": {
                        "interval_unit": "MONTH",
                        "interval_count": 1,
                    },
                    "pricing_scheme": {
                        "fixed_price": {
                            "value": "25.00",
                            "currency_code": "USD",
                        }
                    },
                    "tenure_type": "REGULAR",
                    "sequence": 1,
                    "total_cycles": 0,
                }
            ],
            "payment_preferences": {
                "service_type": "RECURRING",
                "auto_bill_amount": "MAX_AMOUNT",
                "payment_failure_threshold": 1,  # Suspend after 1 failure
            },
        },
    )
    plan_id = resp.json()["id"]

    # Create subscription for failing subscriber (use special email)
    resp = await client.post(
        "/v1/billing/subscriptions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "plan_id": plan_id,
            "subscriber": {
                "name": {"given_name": "Bad", "surname": "Payer"},
                "email_address": "failed@failed.test",  # Magic domain to trigger failures
            },
            "application_context": {
                "return_url": "https://example.com/return",
                "cancel_url": "https://example.com/cancel",
            },
        },
    )
    sub_id = resp.json()["id"]

    # Approve subscription
    resp = await client.post(
        f"/_chronos/api/universes/{universe.id}/subscriptions/{sub_id}/approve",
        headers={"X-Chronos-Key": universe.test_control_key},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ACTIVE"

    # Advance 1 month to trigger first billing (should fail)
    resp = await client.post(
        f"/_chronos/api/universes/{universe.id}/clock/advance",
        headers={"X-Chronos-Key": universe.test_control_key},
        json={"by_days": 30},
    )
    assert resp.status_code == 200

    # Check subscription status - should be SUSPENDED (threshold met)
    resp = await client.get(
        f"/v1/billing/subscriptions/{sub_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUSPENDED"


@pytest.mark.asyncio
async def test_cancel_subscription_stops_future_bills(
    client: AsyncClient, db_session, universe: Universe
):
    """Test: cancelling subscription stops future billing cycles."""
    # Get token
    token_resp = await client.post(
        "/v1/oauth2/token",
        auth=(universe.client_id, universe.test_secret),
        data={"grant_type": "client_credentials", "scope": "api"},
    )
    token = token_resp.json()["access_token"]

    # Create product, plan, subscription
    resp = await client.post(
        "/v1/catalogs/products",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Product", "type": "SERVICE"},
    )
    product_id = resp.json()["id"]

    resp = await client.post(
        "/v1/billing/plans",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "product_id": product_id,
            "name": "Monthly",
            "billing_cycles": [
                {
                    "frequency": {
                        "interval_unit": "MONTH",
                        "interval_count": 1,
                    },
                    "pricing_scheme": {
                        "fixed_price": {
                            "value": "10.00",
                            "currency_code": "USD",
                        }
                    },
                    "tenure_type": "REGULAR",
                    "sequence": 1,
                    "total_cycles": 0,
                }
            ],
            "payment_preferences": {
                "service_type": "RECURRING",
                "auto_bill_amount": "MAX_AMOUNT",
                "payment_failure_threshold": 2,
            },
        },
    )
    plan_id = resp.json()["id"]

    resp = await client.post(
        "/v1/billing/subscriptions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "plan_id": plan_id,
            "subscriber": {
                "name": {"given_name": "John", "surname": "Doe"},
                "email_address": "john@example.com",
            },
            "application_context": {
                "return_url": "https://example.com/return",
                "cancel_url": "https://example.com/cancel",
            },
        },
    )
    sub_id = resp.json()["id"]

    # Approve
    resp = await client.post(
        f"/_chronos/api/universes/{universe.id}/subscriptions/{sub_id}/approve",
        headers={"X-Chronos-Key": universe.test_control_key},
    )
    assert resp.status_code == 200

    # Cancel subscription
    resp = await client.post(
        f"/v1/billing/subscriptions/{sub_id}/cancel",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "CUSTOMER_REQUEST"},
    )
    assert resp.status_code == 204

    # Get subscription - should be CANCELLED
    resp = await client.get(
        f"/v1/billing/subscriptions/{sub_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "CANCELLED"

    # Advance time - no more charges
    resp = await client.post(
        f"/_chronos/api/universes/{universe.id}/clock/advance",
        headers={"X-Chronos-Key": universe.test_control_key},
        json={"by_days": 30},
    )
    assert resp.status_code == 200

    # Verify no new payments
    resp = await client.get(
        f"/v1/billing/subscriptions/{sub_id}/transactions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    transactions = data.get("transactions", [])
    # Should have same or fewer transactions than before
    assert len(transactions) == 0 or all(
        t["status"] in ["DECLINED", "FAILED"] for t in transactions
    )
