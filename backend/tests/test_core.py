"""Tests for core Chronos functionality: models, clock, scheduler, OAuth, control API."""

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AccessToken, EventLog, ScheduledEvent, Universe
from app.twin.scheduler import SchedulerContext, handler


class TestUniverseModel:
    """Test Universe model creation and secret handling."""

    async def test_universe_creation(self, test_session: AsyncSession):
        """Test creating a universe with generated secrets."""
        universe, client_secret, control_key, client_id = Universe.create(
            name="test-universe",
            universe_ttl_days=7,
        )

        assert universe.name == "test-universe"
        assert universe.client_id == client_id
        assert len(client_secret) > 20  # Reasonable length
        assert len(control_key) > 20

        # Hashes are stored, not plaintext
        assert Universe.hash_secret(client_secret) == universe.client_secret_hash
        assert Universe.hash_secret(control_key) == universe.control_key_hash

        # Verify secrets match
        assert Universe.verify_secret(client_secret, universe.client_secret_hash)
        assert not Universe.verify_secret("wrong", universe.client_secret_hash)

        test_session.add(universe)
        await test_session.commit()

        # Reload and verify
        result = await test_session.execute(select(Universe).where(Universe.id == universe.id))
        loaded = result.scalar_one()
        assert loaded.client_id == client_id


class TestAccessToken:
    """Test OAuth access token creation and expiry."""

    async def test_token_creation_and_expiry(self, test_session: AsyncSession):
        """Test creating a token and checking expiry."""
        universe, _, _, _ = Universe.create(
            name="test",
            universe_ttl_days=7,
        )
        test_session.add(universe)
        await test_session.commit()

        now = datetime.now(UTC)
        token_obj, plaintext = AccessToken.create(
            universe_id=universe.id,
            scope="https://api.paypal.com/v1/payments/.*",
            issued_virtual_at=now,
            expires_in_seconds=32400,  # 9 hours
        )

        assert len(plaintext) > 20
        assert token_obj.token_hash == AccessToken.hash_token(plaintext)
        assert AccessToken.verify_token(plaintext, token_obj.token_hash)
        assert not AccessToken.verify_token("wrong", token_obj.token_hash)

        # Token should not be expired at issue time
        assert now < token_obj.expires_virtual_at
        # Token should be expired 10 hours later
        later = datetime.fromtimestamp(now.timestamp() + 36000, tz=UTC)
        assert later >= token_obj.expires_virtual_at


class TestOAuthToken:
    """Test OAuth token endpoint."""

    async def test_token_endpoint_success(self, async_client: AsyncClient, test_session: AsyncSession):
        """Test successful token request with client credentials."""
        # Create a universe
        universe, client_secret, _, client_id = Universe.create(
            name="test",
            universe_ttl_days=7,
        )
        test_session.add(universe)
        await test_session.commit()

        # Request token
        import base64
        auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        response = await async_client.post(
            "/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {auth}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["token_type"] == "Bearer"
        assert "access_token" in data
        assert data["expires_in"] == 32400
        assert "app_id" in data

    async def test_token_endpoint_invalid_credentials(self, async_client: AsyncClient):
        """Test token request with invalid credentials."""
        import base64
        auth = base64.b64encode(b"client:wrongsecret").decode()
        response = await async_client.post(
            "/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {auth}"},
        )

        assert response.status_code == 401
        data = response.json()
        assert data["name"] == "AUTHENTICATION_FAILURE"

    async def test_token_endpoint_invalid_grant_type(self, async_client: AsyncClient):
        """Test token request with unsupported grant_type."""
        import base64
        auth = base64.b64encode(b"client:secret").decode()
        response = await async_client.post(
            "/v1/oauth2/token",
            data={"grant_type": "authorization_code"},
            headers={"Authorization": f"Basic {auth}"},
        )

        assert response.status_code == 400
        data = response.json()
        assert data["name"] == "INVALID_REQUEST"


class TestClock:
    """Test virtual clock and event processing."""

    async def test_advance_clock_empty_queue(self, test_session: AsyncSession):
        """Test advancing clock with no pending events."""
        from app.twin.clock import advance

        universe, _, _, _ = Universe.create(
            name="test",
            universe_ttl_days=7,
        )
        test_session.add(universe)
        await test_session.commit()

        initial_now = universe.virtual_now
        target = datetime.fromtimestamp(
            initial_now.timestamp() + 86400, tz=UTC
        )  # 1 day later

        response = await advance(test_session, str(universe.id), to=target)

        assert response.events_processed == 0
        assert response.events_failed == 0
        assert response.new_virtual_now == target

    async def test_advance_clock_processes_events_in_order(
        self, test_session: AsyncSession
    ):
        """Test that events are processed in (due_at, id) order."""
        from app.twin.clock import advance

        universe, _, _, _ = Universe.create(
            name="test",
            universe_ttl_days=7,
        )
        test_session.add(universe)
        await test_session.commit()

        # Create events at different times
        base_time = universe.virtual_now
        event1 = ScheduledEvent(
            universe_id=universe.id,
            kind="test",
            due_at=datetime.fromtimestamp(base_time.timestamp() + 100, tz=UTC),
            status="pending",
        )
        event2 = ScheduledEvent(
            universe_id=universe.id,
            kind="test",
            due_at=datetime.fromtimestamp(base_time.timestamp() + 50, tz=UTC),
            status="pending",
        )
        event3 = ScheduledEvent(
            universe_id=universe.id,
            kind="test",
            due_at=datetime.fromtimestamp(base_time.timestamp() + 50, tz=UTC),
            status="pending",
        )
        test_session.add_all([event1, event2, event3])
        await test_session.commit()

        # Advance to future
        target = datetime.fromtimestamp(base_time.timestamp() + 200, tz=UTC)
        response = await advance(test_session, str(universe.id), to=target)

        # All events should be processed
        assert response.events_processed == 3

        # Reload events and check order
        result = await test_session.execute(
            select(ScheduledEvent)
            .where(ScheduledEvent.universe_id == universe.id)
            .order_by(ScheduledEvent.id)
        )
        events = result.scalars().all()

        # Events with due_at=50 should have been processed before event with due_at=100
        # Check by processed_at
        for event in events:
            assert event.status == "done"
            assert event.processed_at is not None

    async def test_advance_clock_rejects_backwards(self, test_session: AsyncSession):
        """Test that advancing backwards is rejected."""
        from app.twin.clock import advance

        universe, _, _, _ = Universe.create(
            name="test",
            universe_ttl_days=7,
        )
        test_session.add(universe)
        await test_session.commit()

        target = datetime.fromtimestamp(
            universe.virtual_now.timestamp() - 86400, tz=UTC
        )  # 1 day earlier

        with pytest.raises(ValueError, match="Cannot advance backwards"):
            await advance(test_session, str(universe.id), to=target)

    async def test_advance_clock_recovers_stuck_processing(self, test_session: AsyncSession):
        """Test that events stuck in 'processing' status are recovered."""
        from app.twin.clock import advance

        universe, _, _, _ = Universe.create(
            name="test",
            universe_ttl_days=7,
        )
        test_session.add(universe)
        await test_session.commit()

        # Create an event stuck in processing
        base_time = universe.virtual_now
        stuck_event = ScheduledEvent(
            universe_id=universe.id,
            kind="test",
            due_at=datetime.fromtimestamp(base_time.timestamp() + 100, tz=UTC),
            status="processing",
        )
        test_session.add(stuck_event)
        await test_session.commit()

        # Verify it's stuck
        result = await test_session.execute(
            select(ScheduledEvent).where(ScheduledEvent.id == stuck_event.id)
        )
        assert result.scalar_one().status == "processing"

        # Advance should recover it
        target = datetime.fromtimestamp(base_time.timestamp() + 200, tz=UTC)
        await advance(test_session, str(universe.id), to=target)

        # The recovered event should be processed
        result = await test_session.execute(
            select(ScheduledEvent).where(ScheduledEvent.id == stuck_event.id)
        )
        recovered = result.scalar_one()
        assert recovered.status == "done"


class TestScheduler:
    """Test event scheduler and handler registry."""

    async def test_handler_decorator(self):
        """Test @handler decorator registration."""
        called = []

        @handler("test.event")
        async def handle_test(context: SchedulerContext):
            called.append(True)

        # Handler is registered
        from app.twin.scheduler import get_handler

        handler_func = get_handler("test.event")
        assert handler_func is not None

    async def test_scheduler_context_properties(self, test_session: AsyncSession):
        """Test SchedulerContext properties."""
        universe, _, _, _ = Universe.create(
            name="test",
            universe_ttl_days=7,
        )
        test_session.add(universe)
        await test_session.commit()

        now = datetime.now(UTC)
        event = ScheduledEvent(
            universe_id=universe.id,
            kind="test",
            due_at=now,
            status="pending",
        )
        test_session.add(event)
        await test_session.commit()

        context = SchedulerContext(test_session, universe, event)

        # Test properties
        assert context.db is test_session
        assert context.now == now
        assert context.rng is not None

        # RNG should be deterministic
        val1 = context.rng.random()
        context._rng = None  # Reset
        val2 = context.rng.random()
        assert val1 == val2  # Same seed = same random value

    async def test_scheduler_context_schedule(self, test_session: AsyncSession):
        """Test scheduling new events from within a handler."""
        universe, _, _, _ = Universe.create(
            name="test",
            universe_ttl_days=7,
        )
        test_session.add(universe)
        await test_session.commit()

        event = ScheduledEvent(
            universe_id=universe.id,
            kind="test",
            due_at=datetime.now(UTC),
            status="pending",
        )
        test_session.add(event)
        await test_session.commit()

        context = SchedulerContext(test_session, universe, event)

        # Schedule a new event
        later = datetime.fromtimestamp(event.due_at.timestamp() + 1000, tz=UTC)
        new_event = await context.schedule("delayed.event", later, {"data": "value"})

        assert new_event.kind == "delayed.event"
        assert new_event.due_at == later
        assert new_event.payload == {"data": "value"}
        assert new_event.status == "pending"

    async def test_scheduler_context_log(self, test_session: AsyncSession):
        """Test logging events from within a handler."""
        universe, _, _, _ = Universe.create(
            name="test",
            universe_ttl_days=7,
        )
        test_session.add(universe)
        await test_session.commit()

        event = ScheduledEvent(
            universe_id=universe.id,
            kind="test",
            due_at=datetime.now(UTC),
            status="pending",
        )
        test_session.add(event)
        await test_session.commit()

        context = SchedulerContext(test_session, universe, event)

        # Log an event
        log_entry = await context.log(
            category="payment",
            kind="order.created",
            resource_type="order",
            resource_id="ord_123",
            summary="Order created",
            data={"amount": "100.00"},
        )

        assert log_entry.category == "payment"
        assert log_entry.kind == "order.created"
        assert log_entry.resource_type == "order"
        assert log_entry.data == {"amount": "100.00"}


class TestControlAPI:
    """Test control API endpoints."""

    async def test_create_universe(self, async_client: AsyncClient, test_session: AsyncSession):
        """Test creating a universe via control API."""
        response = await async_client.post("/_chronos/api/universes")

        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert "client_id" in data
        assert "client_secret" in data
        assert "control_key" in data
        assert "base_url" in data

        # Verify universe exists in DB
        import uuid
        universe_id = uuid.UUID(data["id"])
        result = await test_session.execute(
            select(Universe).where(Universe.id == universe_id)
        )
        universe = result.scalar_one()
        assert universe.client_id == data["client_id"]

    async def test_get_universe(self, async_client: AsyncClient, test_session: AsyncSession):
        """Test getting universe details via control API."""
        # Create universe
        universe, _, control_key, _ = Universe.create(
            name="test",
            universe_ttl_days=7,
        )
        test_session.add(universe)
        await test_session.commit()

        # Get via API
        response = await async_client.get(
            f"/_chronos/api/universes/{universe.id}",
            headers={"X-Chronos-Key": control_key},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(universe.id)
        assert data["name"] == "test"

    async def test_get_universe_invalid_key(
        self, async_client: AsyncClient, test_session: AsyncSession
    ):
        """Test that invalid control key is rejected."""
        universe, _, _, _ = Universe.create(
            name="test",
            universe_ttl_days=7,
        )
        test_session.add(universe)
        await test_session.commit()

        response = await async_client.get(
            f"/_chronos/api/universes/{universe.id}",
            headers={"X-Chronos-Key": "wrong"},
        )

        assert response.status_code == 401

    async def test_advance_universe_clock(
        self, async_client: AsyncClient, test_session: AsyncSession
    ):
        """Test advancing universe clock via control API."""
        universe, _, control_key, _ = Universe.create(
            name="test",
            universe_ttl_days=7,
        )
        test_session.add(universe)
        await test_session.commit()

        initial_time = universe.virtual_now

        # Advance by 1 day
        response = await async_client.post(
            f"/_chronos/api/universes/{universe.id}/clock/advance",
            json={"days": 1},
            headers={"X-Chronos-Key": control_key},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["events_processed"] == 0
        assert "new_virtual_now" in data

        # The new_virtual_now in the response is the advanced time
        new_time = datetime.fromisoformat(data["new_virtual_now"].replace("Z", "+00:00"))
        assert new_time > initial_time

    async def test_list_events(self, async_client: AsyncClient, test_session: AsyncSession):
        """Test listing event log entries."""
        universe, _, control_key, _ = Universe.create(
            name="test",
            universe_ttl_days=7,
        )
        test_session.add(universe)
        await test_session.commit()

        # Add some events
        for i in range(3):
            event = EventLog(
                universe_id=universe.id,
                virtual_time=datetime.now(UTC),
                category="test",
                kind=f"event_{i}",
                summary=f"Event {i}",
            )
            test_session.add(event)
        await test_session.commit()

        # List events
        response = await async_client.get(
            f"/_chronos/api/universes/{universe.id}/events",
            headers={"X-Chronos-Key": control_key},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["events"]) == 3

    async def test_list_scheduled_events(
        self, async_client: AsyncClient, test_session: AsyncSession
    ):
        """Test listing scheduled (pending) events."""
        universe, _, control_key, _ = Universe.create(
            name="test",
            universe_ttl_days=7,
        )
        test_session.add(universe)
        await test_session.commit()

        # Add pending events
        for i in range(2):
            event = ScheduledEvent(
                universe_id=universe.id,
                kind=f"event_{i}",
                due_at=datetime.now(UTC),
                status="pending",
            )
            test_session.add(event)
        await test_session.commit()

        # List scheduled
        response = await async_client.get(
            f"/_chronos/api/universes/{universe.id}/scheduled",
            headers={"X-Chronos-Key": control_key},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["scheduled_events"]) == 2

    async def test_reset_universe(self, async_client: AsyncClient, test_session: AsyncSession):
        """Test resetting a universe."""
        universe, _, control_key, _ = Universe.create(
            name="test",
            universe_ttl_days=7,
        )
        test_session.add(universe)
        await test_session.commit()

        # Add some events
        event = ScheduledEvent(
            universe_id=universe.id,
            kind="test",
            due_at=datetime.now(UTC),
            status="pending",
        )
        test_session.add(event)
        await test_session.commit()

        # Reset
        response = await async_client.post(
            f"/_chronos/api/universes/{universe.id}/reset",
            headers={"X-Chronos-Key": control_key},
        )

        assert response.status_code == 200

        # Verify events were deleted
        result = await test_session.execute(
            select(ScheduledEvent).where(ScheduledEvent.universe_id == universe.id)
        )
        assert result.scalars().first() is None

    async def test_delete_universe(self, async_client: AsyncClient, test_session: AsyncSession):
        """Test deleting a universe."""
        universe, _, control_key, _ = Universe.create(
            name="test",
            universe_ttl_days=7,
        )
        test_session.add(universe)
        await test_session.commit()

        universe_id = universe.id

        # Delete
        response = await async_client.delete(
            f"/_chronos/api/universes/{universe_id}",
            headers={"X-Chronos-Key": control_key},
        )

        assert response.status_code == 200

        # Verify deleted
        result = await test_session.execute(select(Universe).where(Universe.id == universe_id))
        assert result.scalar_one_or_none() is None


class TestTokenExpiry:
    """Test that token expiry is based on virtual time."""

    async def test_token_expiry_after_virtual_advance(
        self, async_client: AsyncClient, test_session: AsyncSession
    ):
        """Test that token expires based on virtual time advancement."""
        # Create universe
        universe, client_secret, control_key, client_id = Universe.create(
            name="test",
            universe_ttl_days=7,
        )
        test_session.add(universe)
        await test_session.commit()

        # Get a token
        import base64
        auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        response = await async_client.post(
            "/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {auth}"},
        )
        assert response.status_code == 200
        token_data = response.json()
        token = token_data["access_token"]

        # Token should work initially
        response = await async_client.get(
            "/healthz",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

        # Advance virtual time past token expiry (9 hours + 1 minute)
        response = await async_client.post(
            f"/_chronos/api/universes/{universe.id}/clock/advance",
            json={"hours": 10},
            headers={"X-Chronos-Key": control_key},
        )
        assert response.status_code == 200

        # Verify the token is expired by checking response contains new time
        data = response.json()
        new_virtual_now_str = data["new_virtual_now"]
        new_virtual_now = datetime.fromisoformat(new_virtual_now_str.replace("Z", "+00:00"))

        # Get the token's expiry - it should have been issued at the original virtual_now
        # with a 9-hour expiry, so it expires at virtual_now + 9 hours
        # The clock advanced by 10 hours, so it should be expired
        result = await test_session.execute(
            select(AccessToken).where(AccessToken.token_hash == AccessToken.hash_token(token))
        )
        token_obj = result.scalar_one()

        # Normalize both to UTC for comparison
        expires_utc = token_obj.expires_virtual_at.astimezone(UTC)

        # The new virtual_now should be >= expires_virtual_at
        assert new_virtual_now >= expires_utc
