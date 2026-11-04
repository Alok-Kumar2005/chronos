"""SQLAlchemy 2.0 typed models for Chronos."""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all models."""

    pass


class Universe(Base):
    """A tenant's isolated virtual universe with its own clock and state."""

    __tablename__ = "universes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    client_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    client_secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    control_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    virtual_now: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    chaos: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Relationships
    access_tokens: Mapped[list["AccessToken"]] = relationship(
        back_populates="universe", cascade="all, delete-orphan"
    )
    scheduled_events: Mapped[list["ScheduledEvent"]] = relationship(
        back_populates="universe", cascade="all, delete-orphan"
    )
    event_log_entries: Mapped[list["EventLog"]] = relationship(
        back_populates="universe", cascade="all, delete-orphan"
    )
    idempotency_keys: Mapped[list["IdempotencyKey"]] = relationship(
        back_populates="universe", cascade="all, delete-orphan"
    )
    webhooks: Mapped[list["Webhook"]] = relationship(
        back_populates="universe", cascade="all, delete-orphan"
    )

    @classmethod
    def create(
        cls,
        name: str,
        universe_ttl_days: int,
    ) -> tuple["Universe", str, str, str]:
        """
        Create a new universe with generated secrets.

        Returns:
            Tuple of (universe, client_secret, control_key, base_url_path)
            Secrets are returned once; only hashes are stored.
        """
        client_id = f"client_{secrets.token_urlsafe(16)}"
        client_secret = secrets.token_urlsafe(32)
        control_key = secrets.token_urlsafe(32)

        universe = cls(
            name=name,
            client_id=client_id,
            client_secret_hash=cls.hash_secret(client_secret),
            control_key_hash=cls.hash_secret(control_key),
            seed=secrets.randbits(31),
            virtual_now=datetime.now(UTC),
            expires_at=datetime.fromtimestamp(
                datetime.now(UTC).timestamp() + (universe_ttl_days * 86400),
                tz=UTC,
            ),
        )

        return universe, client_secret, control_key, client_id

    @staticmethod
    def hash_secret(secret: str) -> str:
        """Hash a secret using SHA-256."""
        return hashlib.sha256(secret.encode()).hexdigest()

    @staticmethod
    def verify_secret(secret: str, secret_hash: str) -> bool:
        """Verify a secret against its hash using constant-time comparison."""
        import hmac

        return hmac.compare_digest(Universe.hash_secret(secret), secret_hash)


class AccessToken(Base):
    """OAuth access token with virtual time expiry."""

    __tablename__ = "access_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    universe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("universes.id", ondelete="CASCADE"), nullable=False
    )
    scope: Mapped[str] = mapped_column(String(255), nullable=False)
    issued_virtual_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_virtual_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Relationships
    universe: Mapped[Universe] = relationship(back_populates="access_tokens")

    @classmethod
    def create(
        cls,
        universe_id: uuid.UUID,
        scope: str,
        issued_virtual_at: datetime,
        expires_in_seconds: int,
    ) -> tuple["AccessToken", str]:
        """
        Create a new access token.

        Returns:
            Tuple of (token_object, access_token_plaintext)
            Plaintext is returned once; only hash is stored.
        """
        access_token = secrets.token_urlsafe(32)
        token_hash = cls.hash_token(access_token)

        expires_virtual_at = datetime.fromtimestamp(
            issued_virtual_at.timestamp() + expires_in_seconds,
            tz=UTC,
        )

        token_obj = cls(
            token_hash=token_hash,
            universe_id=universe_id,
            scope=scope,
            issued_virtual_at=issued_virtual_at,
            expires_virtual_at=expires_virtual_at,
        )

        return token_obj, access_token

    @staticmethod
    def hash_token(token: str) -> str:
        """Hash a token using SHA-256."""
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def verify_token(token: str, token_hash: str) -> bool:
        """Verify a token against its hash using constant-time comparison."""
        import hmac

        return hmac.compare_digest(AccessToken.hash_token(token), token_hash)


class ScheduledEvent(Base):
    """An event scheduled to run at a specific virtual time."""

    __tablename__ = "scheduled_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    universe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("universes.id", ondelete="CASCADE"), nullable=False
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )  # pending|processing|done|failed|cancelled
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    universe: Mapped[Universe] = relationship(back_populates="scheduled_events")

    __table_args__ = (
        Index("ix_scheduled_events_universe_status_due_at_id", "universe_id", "status", "due_at", "id"),
    )


class EventLog(Base):
    """Immutable log of all events that have occurred in a universe."""

    __tablename__ = "event_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    universe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("universes.id", ondelete="CASCADE"), nullable=False
    )
    virtual_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    wall_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    kind: Mapped[str] = mapped_column(String(255), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    # Relationships
    universe: Mapped[Universe] = relationship(back_populates="event_log_entries")

    __table_args__ = (
        Index("ix_event_log_universe_id", "universe_id", "id"),
        Index("ix_event_log_resource", "universe_id", "resource_type", "resource_id"),
    )


class IdempotencyKey(Base):
    """Tracks idempotent requests to prevent duplicate processing."""

    __tablename__ = "idempotency_keys"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    universe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("universes.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_virtual_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Relationships
    universe: Mapped[Universe] = relationship(back_populates="idempotency_keys")

    __table_args__ = (
        UniqueConstraint("universe_id", "key", "method", "path", name="uq_idempotency_key"),
    )


class Webhook(Base):
    """A webhook endpoint registered to receive PayPal event notifications."""

    __tablename__ = "webhooks"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)  # WH-<uppercase alphanumeric>
    universe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("universes.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    event_types: Mapped[list[str]] = mapped_column(JSON, nullable=False)  # ["PAYMENT.CAPTURE.COMPLETED", "*", ...]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    # Relationships
    universe: Mapped[Universe] = relationship(
        back_populates="webhooks"
    )
    webhook_deliveries: Mapped[list["WebhookDelivery"]] = relationship(
        back_populates="webhook", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_webhooks_universe_id", "universe_id"),
    )


class WebhookEvent(Base):
    """A webhook event payload created and ready for delivery."""

    __tablename__ = "webhook_events"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)  # WH-<uppercase alphanumeric>
    universe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("universes.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(255), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    resource: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_virtual_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Relationships (only back-references needed; universe FK is direct)
    webhook_deliveries: Mapped[list["WebhookDelivery"]] = relationship(
        back_populates="webhook_event", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_webhook_events_universe_id", "universe_id"),
    )


class WebhookDelivery(Base):
    """Tracks delivery attempts for a webhook event to a specific webhook endpoint."""

    __tablename__ = "webhook_deliveries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    universe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("universes.id", ondelete="CASCADE"), nullable=False
    )
    webhook_event_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("webhook_events.id", ondelete="CASCADE"), nullable=False
    )
    webhook_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )  # pending|delivered|failed|exhausted
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Response time in milliseconds
    signature_tampered: Mapped[bool] = mapped_column(
        default=False, nullable=False
    )  # Chaos: whether signature was intentionally corrupted
    due_virtual_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    delivered_virtual_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    webhook_event: Mapped[WebhookEvent] = relationship(back_populates="webhook_deliveries")
    webhook: Mapped[Webhook] = relationship(back_populates="webhook_deliveries")

    __table_args__ = (
        Index("ix_webhook_deliveries_universe_status_due", "universe_id", "status", "due_virtual_at"),
    )
