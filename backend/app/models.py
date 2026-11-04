"""SQLAlchemy 2.0 typed models for Chronos."""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
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
    handler_variant: Mapped[str] = mapped_column(
        String(50), nullable=False, default="buggy"
    )  # buggy|reference_fix|agent_patched
    chaos_config: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )  # For scenario chaos injection
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


class Order(Base):
    """A PayPal Order (v2 API)."""

    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)  # 17-char alphanumeric
    universe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("universes.id", ondelete="CASCADE"), nullable=False
    )
    intent: Mapped[str] = mapped_column(String(20), nullable=False)  # CAPTURE or AUTHORIZE
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="CREATED")  # CREATED, APPROVED, VOIDED, COMPLETED, PAYER_ACTION_REQUIRED
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)  # ISO 4217 code (USD, EUR, JPY, etc.)
    value: Mapped[Decimal] = mapped_column(Numeric(19, 8), nullable=False)  # Amount, stored as Decimal
    payer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Set on approval
    return_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    cancel_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    created_virtual_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    approved_virtual_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_virtual_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    universe: Mapped[Universe] = relationship()
    authorizations: Mapped[list["Authorization"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    captures: Mapped[list["Capture"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_orders_universe_id", "universe_id"),
        Index("ix_orders_status", "universe_id", "status"),
    )


class Authorization(Base):
    """A PayPal Authorization (v2 Payments API)."""

    __tablename__ = "authorizations"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)  # PayPal auth ID
    universe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("universes.id", ondelete="CASCADE"), nullable=False
    )
    order_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="CREATED")  # CREATED, APPROVED, VOIDED, EXPIRED
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 8), nullable=False)
    created_virtual_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    honor_period_ends_virtual_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_virtual_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    voided_virtual_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    universe: Mapped[Universe] = relationship()
    order: Mapped[Order] = relationship(back_populates="authorizations")
    captures: Mapped[list["Capture"]] = relationship(
        back_populates="authorization", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_authorizations_universe_order", "universe_id", "order_id"),
        Index("ix_authorizations_status", "universe_id", "status"),
    )


class Capture(Base):
    """A PayPal Capture (part of v2 Payments API)."""

    __tablename__ = "captures"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)  # PayPal capture ID
    universe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("universes.id", ondelete="CASCADE"), nullable=False
    )
    order_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    authorization_id: Mapped[str | None] = mapped_column(
        String(255), ForeignKey("authorizations.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="COMPLETED")  # COMPLETED, PENDING, DENIED, REFUNDED
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 8), nullable=False)
    final_capture: Mapped[bool] = mapped_column(default=False, nullable=False)
    invoice_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    note_to_payer: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_virtual_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Relationships
    universe: Mapped[Universe] = relationship()
    order: Mapped[Order] = relationship(back_populates="captures")
    authorization: Mapped[Authorization | None] = relationship(back_populates="captures")
    refunds: Mapped[list["Refund"]] = relationship(
        back_populates="capture", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_captures_universe_order", "universe_id", "order_id"),
        Index("ix_captures_authorization", "universe_id", "authorization_id"),
    )


class Refund(Base):
    """A PayPal Refund (part of v2 Payments API)."""

    __tablename__ = "refunds"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)  # PayPal refund ID
    universe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("universes.id", ondelete="CASCADE"), nullable=False
    )
    capture_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("captures.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="COMPLETED")  # COMPLETED, PENDING, DENIED
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 8), nullable=False)  # Refund amount
    note_to_payer: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_virtual_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Relationships
    universe: Mapped[Universe] = relationship()
    capture: Mapped[Capture] = relationship(back_populates="refunds")

    __table_args__ = (
        Index("ix_refunds_universe_capture", "universe_id", "capture_id"),
    )


class AccountBalance(Base):
    """Track merchant account balance for money movements."""

    __tablename__ = "account_balances"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    universe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("universes.id", ondelete="CASCADE"), nullable=False
    )
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    available: Mapped[Decimal] = mapped_column(Numeric(19, 8), nullable=False, default=Decimal("0.00"))

    # Relationships
    universe: Mapped[Universe] = relationship()

    __table_args__ = (
        UniqueConstraint("universe_id", "currency_code", name="uq_account_balance_universe_currency"),
    )


class PayoutBatch(Base):
    """A PayPal Payout Batch (v1 API)."""

    __tablename__ = "payout_batches"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)  # 13-char batch ID (e.g., "DJMFB0QQLLDAC")
    universe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("universes.id", ondelete="CASCADE"), nullable=False
    )
    sender_batch_id: Mapped[str] = mapped_column(String(255), nullable=False)  # User-supplied batch ID
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PENDING"
    )  # PENDING, PROCESSING, SUCCESS, DENIED
    sender_batch_header: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)  # email_subject, email_message, etc.
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(19, 8), nullable=False)  # Sum of all item amounts
    total_fee: Mapped[Decimal] = mapped_column(Numeric(19, 8), nullable=False, default=Decimal("0.00"))  # Total fee charged
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    successful_item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unclaimed_item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_virtual_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    processed_virtual_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    universe: Mapped[Universe] = relationship()
    items: Mapped[list["PayoutItem"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_payout_batches_universe_id", "universe_id"),
        Index("ix_payout_batches_status", "universe_id", "status"),
        UniqueConstraint("universe_id", "sender_batch_id", name="uq_payout_batch_sender_id"),
    )


class PayoutItem(Base):
    """An individual item in a payout batch."""

    __tablename__ = "payout_items"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)  # Item ID assigned by PayPal
    universe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("universes.id", ondelete="CASCADE"), nullable=False
    )
    batch_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("payout_batches.id", ondelete="CASCADE"), nullable=False
    )
    sender_item_id: Mapped[str] = mapped_column(String(255), nullable=False)  # User-supplied item ID
    recipient_type: Mapped[str] = mapped_column(String(20), nullable=False)  # EMAIL, PHONE, PAYPAL_ID
    receiver: Mapped[str] = mapped_column(String(255), nullable=False)  # Email, phone, or PayPal ID
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PENDING"
    )  # PENDING, SUCCESS, FAILED, RETURNED, CANCELED, DENIED, PROCESSING, UNCLAIMED, BLOCKED, ONHOLD, REFUNDED
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 8), nullable=False)
    fee: Mapped[Decimal] = mapped_column(Numeric(19, 8), nullable=False)  # Per-item fee
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    recipient_wallet: Mapped[str | None] = mapped_column(String(50), nullable=True)  # VENMO, SRCC (if specified)
    created_virtual_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    processed_virtual_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    return_scheduled_virtual_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )  # When the auto-return for UNCLAIMED is scheduled
    returned_virtual_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    universe: Mapped[Universe] = relationship()
    batch: Mapped[PayoutBatch] = relationship(back_populates="items")

    __table_args__ = (
        Index("ix_payout_items_universe_id", "universe_id"),
        Index("ix_payout_items_batch_id", "batch_id"),
        Index("ix_payout_items_receiver", "universe_id", "receiver"),
        Index("ix_payout_items_status", "universe_id", "status"),
    )


class PayoutRecipient(Base):
    """Tracks registered payout recipients in a universe."""

    __tablename__ = "payout_recipients"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    universe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("universes.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    registered: Mapped[bool] = mapped_column(default=False, nullable=False)  # Whether recipient has registered/claimed
    registered_virtual_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    universe: Mapped[Universe] = relationship()

    __table_args__ = (
        UniqueConstraint("universe_id", "email", name="uq_payout_recipient_universe_email"),
    )


class Product(Base):
    """A PayPal Catalog Product (v1 API)."""

    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)  # PROD-<alphanumeric>
    universe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("universes.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    type: Mapped[str] = mapped_column(String(50), nullable=False)  # SERVICE, PHYSICAL_GOODS, DIGITAL_GOODS
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    home_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")  # ACTIVE, INACTIVE
    created_virtual_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Relationships
    universe: Mapped[Universe] = relationship()
    plans: Mapped[list["BillingPlan"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_products_universe_id", "universe_id"),
        Index("ix_products_status", "universe_id", "status"),
    )


class BillingPlan(Base):
    """A PayPal Billing Plan (v1 API) with recurring billing cycles."""

    __tablename__ = "billing_plans"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)  # P-<alphanumeric>
    universe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("universes.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="CREATED")  # CREATED, ACTIVE, INACTIVE
    
    # Billing cycles (stored as JSON array of billing cycle objects)
    billing_cycles: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    
    # Payment preferences
    payment_preferences: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    # Contains: service_type, auto_bill_amount, setup_fee, setup_fee_failure_action, payment_failure_threshold
    
    created_virtual_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Relationships
    universe: Mapped[Universe] = relationship()
    product: Mapped[Product] = relationship(back_populates="plans")
    subscriptions: Mapped[list["Subscription"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_billing_plans_universe_id", "universe_id"),
        Index("ix_billing_plans_status", "universe_id", "status"),
        Index("ix_billing_plans_product_id", "product_id"),
    )


class Subscription(Base):
    """A PayPal Subscription (v1 API) to a billing plan."""

    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)  # I-<alphanumeric>
    universe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("universes.id", ondelete="CASCADE"), nullable=False
    )
    plan_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("billing_plans.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="APPROVAL_PENDING"
    )  # APPROVAL_PENDING, ACTIVE, SUSPENDED, CANCELLED, EXPIRED
    
    # Subscriber information
    subscriber_name: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)  # {given_name, surname}
    subscriber_email: Mapped[str] = mapped_column(String(255), nullable=False)
    
    # Subscription details
    quantity: Mapped[str] = mapped_column(String(50), nullable=False, default="1")
    shipping_amount: Mapped[Decimal | None] = mapped_column(Numeric(19, 8), nullable=True)
    
    # Billing tracking
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # When subscription becomes active
    next_billing_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # Next scheduled charge
    current_cycle_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # Which cycle we're on
    
    # Payment failure tracking
    failed_payment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payment_failure_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # From plan
    
    # Status tracking
    status_change_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status_update_virtual_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Application context (redirect URLs)
    return_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    cancel_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    
    created_virtual_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Relationships
    universe: Mapped[Universe] = relationship()
    plan: Mapped[BillingPlan] = relationship(back_populates="subscriptions")
    payments: Mapped[list["SubscriptionPayment"]] = relationship(
        back_populates="subscription", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_subscriptions_universe_id", "universe_id"),
        Index("ix_subscriptions_status", "universe_id", "status"),
        Index("ix_subscriptions_plan_id", "plan_id"),
        Index("ix_subscriptions_next_billing", "universe_id", "next_billing_time"),
    )


class SubscriptionPayment(Base):
    """A payment transaction for a subscription."""

    __tablename__ = "subscription_payments"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)  # Unique payment ID
    universe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("universes.id", ondelete="CASCADE"), nullable=False
    )
    subscription_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=False
    )
    cycle_sequence: Mapped[int] = mapped_column(Integer, nullable=False)  # Which cycle this payment is for
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 8), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="COMPLETED"
    )  # COMPLETED, DECLINED, PENDING, FAILED
    
    billing_virtual_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )  # Virtual time when this payment was processed
    
    # Payment details
    type: Mapped[str] = mapped_column(String(50), nullable=False)  # RECURRING, SETUP, TRIAL
    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Why payment failed
    
    created_virtual_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Relationships
    universe: Mapped[Universe] = relationship()
    subscription: Mapped[Subscription] = relationship(back_populates="payments")

    __table_args__ = (
        Index("ix_subscription_payments_universe_id", "universe_id"),
        Index("ix_subscription_payments_subscription_id", "subscription_id"),
        Index("ix_subscription_payments_status", "universe_id", "status"),
    )


class ShopSettings(Base):
    """Shop configuration per universe (which handler variant to use, etc.)."""

    __tablename__ = "shop_settings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    universe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("universes.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    handler_variant: Mapped[str] = mapped_column(String(50), nullable=False, default="buggy")  # buggy|reference_fix|agent_patched
    shop_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    creator_payout_percentage: Mapped[Decimal] = mapped_column(Numeric(19, 8), nullable=False, default=Decimal("0.70"))  # 70%
    payout_fee_per_item: Mapped[Decimal] = mapped_column(Numeric(19, 8), nullable=False, default=Decimal("0.20"))  # per-item fee
    payout_batch_due_day_of_week: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 0=Monday, 6=Sunday

    # Relationships
    universe: Mapped[Universe] = relationship()

    __table_args__ = (
        UniqueConstraint("universe_id", name="uq_shop_settings_universe"),
    )


class ShopProduct(Base):
    """Product in the shop (simplified version for demo)."""

    __tablename__ = "shop_products"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)  # SKU or product ID
    universe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("universes.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    price: Mapped[Decimal] = mapped_column(Numeric(19, 8), nullable=False)  # Pre-order price
    stock: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    creator_email: Mapped[str] = mapped_column(String(255), nullable=False)  # Creator's PayPal email
    created_virtual_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Relationships
    universe: Mapped[Universe] = relationship()
    orders: Mapped[list["ShopOrder"]] = relationship(back_populates="product", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_shop_products_universe_id", "universe_id"),
        Index("ix_shop_products_creator_email", "universe_id", "creator_email"),
    )


class ShopOrder(Base):
    """Customer order in the shop (pre-order with authorization, captures on ship)."""

    __tablename__ = "shop_orders"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)  # Order ID (not PayPal order ID)
    universe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("universes.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("shop_products.id", ondelete="CASCADE"), nullable=False
    )
    paypal_order_id: Mapped[str | None] = mapped_column(String(255), nullable=True)  # PayPal Order ID (set after creation)
    paypal_authorization_id: Mapped[str | None] = mapped_column(String(255), nullable=True)  # PayPal Authorization ID (set after authorize)
    paypal_capture_id: Mapped[str | None] = mapped_column(String(255), nullable=True)  # PayPal Capture ID (set on ship)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="created"
    )  # created|authorized|shipped|paid|refunded|failed
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 8), nullable=False)
    payer_email: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Customer email
    customer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_virtual_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    authorized_virtual_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    shipped_virtual_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_virtual_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    refunded_virtual_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    universe: Mapped[Universe] = relationship()
    product: Mapped[ShopProduct] = relationship(back_populates="orders")
    ledger_entries: Mapped[list["ShopLedgerEntry"]] = relationship(back_populates="order", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_shop_orders_universe_id", "universe_id"),
        Index("ix_shop_orders_status", "universe_id", "status"),
        Index("ix_shop_orders_paypal_order_id", "universe_id", "paypal_order_id"),
    )


class ShopLedgerEntry(Base):
    """Financial ledger entry tracking money flows in the shop."""

    __tablename__ = "shop_ledger_entries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    universe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("universes.id", ondelete="CASCADE"), nullable=False
    )
    account: Mapped[str] = mapped_column(String(255), nullable=False)  # "shop_revenue", "creator_{email}", "platform_fees"
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 8), nullable=False)  # Positive or negative
    order_id: Mapped[str | None] = mapped_column(
        String(255), ForeignKey("shop_orders.id", ondelete="SET NULL"), nullable=True
    )
    source_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)  # PayPal webhook ID, etc.
    kind: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # capture|refund|payout|fee|correction
    created_virtual_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Relationships
    universe: Mapped[Universe] = relationship()
    order: Mapped[ShopOrder | None] = relationship(back_populates="ledger_entries")

    __table_args__ = (
        Index("ix_shop_ledger_entries_universe_id", "universe_id"),
        Index("ix_shop_ledger_entries_account", "universe_id", "account"),
        Index("ix_shop_ledger_entries_order_id", "order_id"),
        Index("ix_shop_ledger_entries_source_event_id", "universe_id", "source_event_id"),
    )


class ShopCreator(Base):
    """Tracks creators and their payout status."""

    __tablename__ = "shop_creators"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    universe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("universes.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    balance: Mapped[Decimal] = mapped_column(Numeric(19, 8), nullable=False, default=Decimal("0.00"))  # Pending payout balance
    payout_status: Mapped[str] = mapped_column(String(50), nullable=False, default="ready")  # ready|submitted|paid|failed
    last_payout_virtual_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_payout_batch_id: Mapped[str | None] = mapped_column(String(255), nullable=True)  # PayPal payout batch ID

    # Relationships
    universe: Mapped[Universe] = relationship()

    __table_args__ = (
        UniqueConstraint("universe_id", "email", name="uq_shop_creator_universe_email"),
        Index("ix_shop_creators_universe_id", "universe_id"),
    )


class ShopAppliedWebhookEvent(Base):
    """Tracks which PayPal webhook events have been applied to shop records."""

    __tablename__ = "shop_applied_webhook_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    universe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("universes.id", ondelete="CASCADE"), nullable=False
    )
    source_event_id: Mapped[str] = mapped_column(String(255), nullable=False)  # PayPal event ID
    applied_virtual_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Relationships
    universe: Mapped[Universe] = relationship()

    __table_args__ = (
        UniqueConstraint("universe_id", "source_event_id", name="uq_shop_applied_webhook_event_universe_event"),
        Index("ix_shop_applied_webhook_events_universe_id", "universe_id"),
    )


class ShopErrorLog(Base):
    """Log of errors encountered when calling PayPal (for INV-7 compliance)."""

    __tablename__ = "shop_error_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    universe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("universes.id", ondelete="CASCADE"), nullable=False
    )
    operation: Mapped[str] = mapped_column(String(255), nullable=False)  # e.g. "authorize", "capture", "create_payout"
    error_code: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g. "AUTHENTICATION_FAILURE", "401"
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    swallowed: Mapped[bool] = mapped_column(default=False, nullable=False)  # Whether the error was swallowed instead of propagated
    created_virtual_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Relationships
    universe: Mapped[Universe] = relationship()

    __table_args__ = (
        Index("ix_shop_error_logs_universe_id", "universe_id"),
        Index("ix_shop_error_logs_error_code", "universe_id", "error_code"),
    )



class ScenarioRun(Base):
    """Record of a scenario run with pass/fail status and findings."""

    __tablename__ = "scenario_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    universe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("universes.id", ondelete="CASCADE"), nullable=False
    )
    scenario_id: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # "passed" | "failed" | "error" | "timeout"
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    universe: Mapped[Universe] = relationship()
    steps: Mapped[list["ScenarioRunStep"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    findings: Mapped[list["ScenarioFinding"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_scenario_runs_universe_id", "universe_id"),
        Index("ix_scenario_runs_scenario_id", "scenario_id"),
        Index("ix_scenario_runs_status", "status"),
    )


class ScenarioRunStep(Base):
    """Record of a single step in a scenario run."""

    __tablename__ = "scenario_run_steps"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scenario_runs.id", ondelete="CASCADE"), nullable=False
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)  # 0-indexed
    step_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # "place_order", "advance", "check", etc.
    step_input: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False
    )  # The step definition
    virtual_time_before: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    virtual_time_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    step_output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Slice of EventLog entries produced by this step
    event_log_ids: Mapped[list[uuid.UUID]] = mapped_column(
        JSON, nullable=False, default=[]
    )  # UUIDs of EventLog entries

    # Relationships
    run: Mapped[ScenarioRun] = relationship(back_populates="steps")

    __table_args__ = (
        Index("ix_scenario_run_steps_run_id", "run_id"),
        Index("ix_scenario_run_steps_step_index", "run_id", "step_index"),
    )


class ScenarioFinding(Base):
    """A failed invariant check with evidence."""

    __tablename__ = "scenario_findings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scenario_runs.id", ondelete="CASCADE"), nullable=False
    )
    invariant_id: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # "INV-1", "INV-2", etc.
    passed: Mapped[bool] = mapped_column(default=False, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)  # Evidence data
    step_index: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # Step where check occurred, or None for final check

    # Relationships
    run: Mapped[ScenarioRun] = relationship(back_populates="findings")

    __table_args__ = (
        Index("ix_scenario_findings_run_id", "run_id"),
        Index("ix_scenario_findings_invariant_id", "invariant_id"),
    )



class ChatThread(Base):
    """A conversation thread with an AI copilot for a universe."""

    __tablename__ = "chat_threads"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # {universe_id}:{chat_id}
    universe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("universes.id", ondelete="CASCADE"), nullable=False
    )
    chat_id: Mapped[str] = mapped_column(String(32), nullable=False)  # unique within universe
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    universe: Mapped[Universe] = relationship()

    __table_args__ = (
        Index("ix_chat_threads_universe_id", "universe_id"),
        UniqueConstraint("universe_id", "chat_id", name="uq_chat_thread_per_universe"),
    )
