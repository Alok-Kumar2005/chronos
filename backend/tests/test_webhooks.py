"""Tests for webhook functionality."""

import json
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Universe, Webhook
from app.twin.signing import (
    compute_crc32,
    get_or_create_signing_key,
    sign_webhook,
    verify_webhook_signature,
)


class TestWebhookSigningCrypto:
    """Test the signing and verification cryptography."""

    @pytest.mark.asyncio
    async def test_sign_and_verify_roundtrip(self):
        """Test that a signed payload can be verified."""
        private_key, public_key = get_or_create_signing_key()

        transmission_id = "abc123"
        transmission_time = "2026-11-04T10:00:00Z"
        webhook_id = "WH-XYZ789"
        raw_body = b'{"order_id": "12345"}'

        # Sign
        signature = sign_webhook(
            transmission_id=transmission_id,
            transmission_time=transmission_time,
            webhook_id=webhook_id,
            raw_body=raw_body,
            private_key=private_key,
        )

        # Verify
        is_valid = verify_webhook_signature(
            transmission_id=transmission_id,
            transmission_time=transmission_time,
            webhook_id=webhook_id,
            raw_body=raw_body,
            signature_b64=signature,
            public_key=public_key,
        )

        assert is_valid is True

    @pytest.mark.asyncio
    async def test_verify_fails_with_tampered_body(self):
        """Test that verification fails if body is tampered with."""
        private_key, public_key = get_or_create_signing_key()

        transmission_id = "abc123"
        transmission_time = "2026-11-04T10:00:00Z"
        webhook_id = "WH-XYZ789"
        raw_body = b'{"order_id": "12345"}'

        # Sign the original body
        signature = sign_webhook(
            transmission_id=transmission_id,
            transmission_time=transmission_time,
            webhook_id=webhook_id,
            raw_body=raw_body,
            private_key=private_key,
        )

        # Try to verify with a tampered body
        tampered_body = b'{"order_id": "99999"}'
        is_valid = verify_webhook_signature(
            transmission_id=transmission_id,
            transmission_time=transmission_time,
            webhook_id=webhook_id,
            raw_body=tampered_body,
            signature_b64=signature,
            public_key=public_key,
        )

        assert is_valid is False

    @pytest.mark.asyncio
    async def test_crc32_computation(self):
        """Test CRC32 computation matches expected values."""
        # CRC32 of empty string
        assert compute_crc32(b"") == 0

        # CRC32 of "123456789" is a well-known test value: 0xCBF43926
        assert compute_crc32(b"123456789") == 0xCBF43926


class TestWebhookCRUD:
    """Test webhook create, read, update, delete operations."""

    @pytest.mark.asyncio
    async def test_create_webhook(self, async_client: AsyncClient, test_session: AsyncSession):
        """Test creating a webhook."""
        # Create universe
        from app.models import Universe

        universe, secret, key, client_id = Universe.create(
            name="test", universe_ttl_days=1
        )
        test_session.add(universe)
        await test_session.commit()

        # Create access token
        from app.models import AccessToken

        token_obj, token = AccessToken.create(
            universe_id=universe.id,
            scope="https://api.paypal.com/v1/payments/.*",
            issued_virtual_at=datetime.now(UTC),
            expires_in_seconds=3600,
        )
        test_session.add(token_obj)
        await test_session.commit()

        # Create webhook
        response = await async_client.post(
            "/v1/notifications/webhooks",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "url": "https://example.com/webhooks",
                "event_types": [
                    {"name": "PAYMENT.CAPTURE.COMPLETED"},
                    {"name": "PAYMENT.CAPTURE.REFUNDED"},
                ],
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["url"] == "https://example.com/webhooks"
        assert len(data["event_types"]) == 2
        assert data["event_types"][0]["name"] == "PAYMENT.CAPTURE.COMPLETED"
        webhook_id = data["id"]

        # Verify it was created in the database
        result = await test_session.execute(
            select(Webhook).where(
                (Webhook.id == webhook_id) & (Webhook.universe_id == universe.id)
            )
        )
        webhook = result.scalar_one_or_none()
        assert webhook is not None
        assert webhook.url == "https://example.com/webhooks"

    @pytest.mark.asyncio
    async def test_list_webhooks(self, async_client: AsyncClient, test_session: AsyncSession):
        """Test listing webhooks."""
        # Create universe and token
        universe, secret, key, client_id = Universe.create(
            name="test", universe_ttl_days=1
        )
        test_session.add(universe)
        await test_session.commit()

        from app.models import AccessToken

        token_obj, token = AccessToken.create(
            universe_id=universe.id,
            scope="https://api.paypal.com/v1/payments/.*",
            issued_virtual_at=datetime.now(UTC),
            expires_in_seconds=3600,
        )
        test_session.add(token_obj)
        await test_session.commit()

        # Create a webhook
        webhook1 = Webhook(
            id="WH-123",
            universe_id=universe.id,
            url="https://example.com/1",
            event_types=["PAYMENT.CAPTURE.COMPLETED"],
        )
        test_session.add(webhook1)
        await test_session.commit()

        # List webhooks
        response = await async_client.get(
            "/v1/notifications/webhooks",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["webhooks"]) == 1
        assert data["webhooks"][0]["id"] == "WH-123"

    @pytest.mark.asyncio
    async def test_get_webhook(self, async_client: AsyncClient, test_session: AsyncSession):
        """Test getting a specific webhook."""
        universe, secret, key, client_id = Universe.create(
            name="test", universe_ttl_days=1
        )
        test_session.add(universe)
        await test_session.commit()

        from app.models import AccessToken

        token_obj, token = AccessToken.create(
            universe_id=universe.id,
            scope="https://api.paypal.com/v1/payments/.*",
            issued_virtual_at=datetime.now(UTC),
            expires_in_seconds=3600,
        )
        test_session.add(token_obj)
        await test_session.commit()

        webhook = Webhook(
            id="WH-123",
            universe_id=universe.id,
            url="https://example.com/1",
            event_types=["PAYMENT.CAPTURE.COMPLETED"],
        )
        test_session.add(webhook)
        await test_session.commit()

        response = await async_client.get(
            "/v1/notifications/webhooks/WH-123",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "WH-123"
        assert data["url"] == "https://example.com/1"

    @pytest.mark.asyncio
    async def test_update_webhook(self, async_client: AsyncClient, test_session: AsyncSession):
        """Test updating a webhook."""
        universe, secret, key, client_id = Universe.create(
            name="test", universe_ttl_days=1
        )
        test_session.add(universe)
        await test_session.commit()

        from app.models import AccessToken

        token_obj, token = AccessToken.create(
            universe_id=universe.id,
            scope="https://api.paypal.com/v1/payments/.*",
            issued_virtual_at=datetime.now(UTC),
            expires_in_seconds=3600,
        )
        test_session.add(token_obj)
        await test_session.commit()

        webhook = Webhook(
            id="WH-123",
            universe_id=universe.id,
            url="https://example.com/1",
            event_types=["PAYMENT.CAPTURE.COMPLETED"],
        )
        test_session.add(webhook)
        await test_session.commit()

        response = await async_client.patch(
            "/v1/notifications/webhooks/WH-123",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "url": "https://example.com/updated",
                "event_types": [{"name": "*"}],
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["url"] == "https://example.com/updated"
        assert data["event_types"][0]["name"] == "*"

    @pytest.mark.asyncio
    async def test_delete_webhook(self, async_client: AsyncClient, test_session: AsyncSession):
        """Test deleting a webhook."""
        universe, secret, key, client_id = Universe.create(
            name="test", universe_ttl_days=1
        )
        test_session.add(universe)
        await test_session.commit()

        from app.models import AccessToken

        token_obj, token = AccessToken.create(
            universe_id=universe.id,
            scope="https://api.paypal.com/v1/payments/.*",
            issued_virtual_at=datetime.now(UTC),
            expires_in_seconds=3600,
        )
        test_session.add(token_obj)
        await test_session.commit()

        webhook = Webhook(
            id="WH-123",
            universe_id=universe.id,
            url="https://example.com/1",
            event_types=["PAYMENT.CAPTURE.COMPLETED"],
        )
        test_session.add(webhook)
        await test_session.commit()

        response = await async_client.delete(
            "/v1/notifications/webhooks/WH-123",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 204

        # Verify it's gone
        result = await test_session.execute(
            select(Webhook).where(Webhook.id == "WH-123")
        )
        webhook = result.scalar_one_or_none()
        assert webhook is None


class TestVerifyWebhookSignatureEndpoint:
    """Test the verify-webhook-signature endpoint."""

    @pytest.mark.asyncio
    async def test_verify_valid_signature(self, async_client: AsyncClient):
        """Test verifying a valid signature."""
        # Get the cached key from the endpoint's module
        from app.twin.routers import webhook_verify
        private_key, public_key = webhook_verify._get_cached_signing_key()

        transmission_id = "abc123"
        transmission_time = "2026-11-04T10:00:00Z"
        webhook_id = "WH-XYZ789"
        webhook_event = {"id": "WH-EVT-123", "event_type": "PAYMENT.CAPTURE.COMPLETED"}
        webhook_event_json = json.dumps(webhook_event, separators=(",", ":"), sort_keys=True)
        raw_body = webhook_event_json.encode("utf-8")

        signature = sign_webhook(
            transmission_id=transmission_id,
            transmission_time=transmission_time,
            webhook_id=webhook_id,
            raw_body=raw_body,
            private_key=private_key,
        )

        response = await async_client.post(
            "/v1/notifications/verify-webhook-signature",
            json={
                "auth_algo": "SHA256withRSA",
                "cert_url": "https://api.paypal.com/cert/",
                "transmission_id": transmission_id,
                "transmission_sig": signature,
                "transmission_time": transmission_time,
                "webhook_id": webhook_id,
                "webhook_event": webhook_event,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["verification_status"] == "SUCCESS"

    @pytest.mark.asyncio
    async def test_verify_invalid_signature(self, async_client: AsyncClient):
        """Test that invalid signature fails verification."""
        response = await async_client.post(
            "/v1/notifications/verify-webhook-signature",
            json={
                "auth_algo": "SHA256withRSA",
                "cert_url": "https://api.paypal.com/cert/",
                "transmission_id": "abc123",
                "transmission_sig": "invalid_base64_signature",
                "transmission_time": "2026-11-04T10:00:00Z",
                "webhook_id": "WH-XYZ789",
                "webhook_event": {"id": "WH-EVT-123"},
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["verification_status"] == "FAILURE"
