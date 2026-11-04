"""PayPal-compatible HTTP client for the Lumen Shop (does NOT import twin internals).

This client talks to the twin over HTTP like a real shop integration would.
It accepts a base URL (from config) and uses OAuth client credentials.
"""

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class PayPalClientError(Exception):
    """Exception raised when PayPal API returns an error."""

    def __init__(self, status_code: int, error_name: str, message: str, debug_id: str | None = None, details: list | None = None):
        self.status_code = status_code
        self.error_name = error_name
        self.message = message
        self.debug_id = debug_id
        self.details = details or []
        super().__init__(f"{error_name}: {message} (debug_id={debug_id})")


class PayPalClient:
    """HTTP client for PayPal API (v1 and v2)."""

    def __init__(
        self,
        base_url: str,
        client_id: str,
        client_secret: str,
        universe_id: str,
        timeout_seconds: float = 30.0,
    ):
        """Initialize the PayPal client.

        Args:
            base_url: Base URL of the PayPal API (e.g., 'http://localhost:8000/chronos')
            client_id: OAuth client ID (universe's client_id)
            client_secret: OAuth client secret
            universe_id: Universe ID (passed as header to all requests)
            timeout_seconds: HTTP timeout per request
        """
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.universe_id = universe_id
        self.timeout_seconds = timeout_seconds
        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None

    async def _get_access_token(self) -> str:
        """Get or refresh OAuth access token."""
        # Check if we have a valid cached token
        if self._access_token and self._token_expires_at:
            if datetime.now(UTC) < self._token_expires_at:
                return self._access_token

        # Request a new token
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/v1/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "scope": "https://api.paypal.com/v1/payments/.* https://api.paypal.com/v1/reporting/.*",
                },
                auth=(self.client_id, self.client_secret),
                headers={"X-Chronos-Universe": self.universe_id},
            )

            if response.status_code != 200:
                try:
                    error_data = response.json()
                except Exception:
                    error_data = {"error": "unknown", "error_description": response.text}
                raise PayPalClientError(
                    response.status_code,
                    error_data.get("error", "UNKNOWN_ERROR"),
                    error_data.get("error_description", "Unknown error"),
                )

            data = response.json()
            self._access_token = data["access_token"]
            # Cache expires_in seconds from now, with 30-second buffer
            expires_in = int(data.get("expires_in", 3600)) - 30
            if expires_in > 0:
                self._token_expires_at = datetime.fromtimestamp(
                    datetime.now(UTC).timestamp() + expires_in,
                    tz=UTC,
                )

        return self._access_token

    async def _request(
        self,
        method: str,
        path: str,
        json_data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make an authenticated request to PayPal API.

        Args:
            method: HTTP method (GET, POST, PATCH, etc.)
            path: API path (e.g., '/v2/checkout/orders')
            json_data: JSON body to send
            params: Query parameters

        Returns:
            Parsed JSON response

        Raises:
            PayPalClientError: If the API returns an error
        """
        access_token = await self._get_access_token()

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.request(
                method,
                f"{self.base_url}{path}",
                json=json_data,
                params=params,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                    "X-Chronos-Universe": self.universe_id,
                },
            )

            # Handle errors
            if response.status_code >= 400:
                try:
                    error_data = response.json()
                except Exception:
                    error_data = {
                        "name": "UNKNOWN_ERROR",
                        "message": response.text or "Unknown error",
                        "debug_id": "no-debug-id",
                    }

                raise PayPalClientError(
                    response.status_code,
                    error_data.get("name", "UNKNOWN_ERROR"),
                    error_data.get("message", "Unknown error"),
                    error_data.get("debug_id"),
                    error_data.get("details", []),
                )

            # Handle success
            if response.status_code == 204:
                return {}

            return response.json()

    # Orders v2 API

    async def create_order(
        self,
        intent: str,
        amount: Decimal,
        currency_code: str,
        return_url: str,
        cancel_url: str,
    ) -> dict[str, Any]:
        """Create a PayPal Order for pre-authorization.

        Args:
            intent: 'CAPTURE' or 'AUTHORIZE'
            amount: Amount in currency's base unit
            currency_code: 3-letter currency code (USD, EUR, etc.)
            return_url: URL to return to after approval
            cancel_url: URL if customer cancels

        Returns:
            Order object with id and links
        """
        return await self._request(
            "POST",
            "/v2/checkout/orders",
            json_data={
                "intent": intent,
                "purchase_units": [
                    {
                        "amount": {
                            "currency_code": currency_code,
                            "value": str(amount),
                        },
                    },
                ],
                "application_context": {
                    "return_url": return_url,
                    "cancel_url": cancel_url,
                },
            },
        )

    async def get_order(self, order_id: str) -> dict[str, Any]:
        """Retrieve an order by ID."""
        return await self._request("GET", f"/v2/checkout/orders/{order_id}")

    async def approve_order(self, order_id: str, payer_id: str) -> dict[str, Any]:
        """Approve an order (set payer).

        Args:
            order_id: PayPal Order ID
            payer_id: Payer ID from the approval flow

        Returns:
            Approved order object
        """
        return await self._request(
            "POST",
            f"/v2/checkout/orders/{order_id}/approve",
            json_data={"payer_id": payer_id},
        )

    async def authorize_order(self, order_id: str) -> dict[str, Any]:
        """Convert an approved order to an authorization.

        Args:
            order_id: PayPal Order ID

        Returns:
            Authorization object with id, status, expires_virtual_at
        """
        return await self._request(
            "POST",
            f"/v2/checkout/orders/{order_id}/authorize",
            json_data={},
        )

    async def capture_order_directly(self, order_id: str) -> dict[str, Any]:
        """Capture an approved order directly (without prior authorization).

        Args:
            order_id: PayPal Order ID

        Returns:
            Capture object with id and status
        """
        return await self._request(
            "POST",
            f"/v2/checkout/orders/{order_id}/capture",
            json_data={},
        )

    # Payments v2 API (Authorizations and Captures)

    async def get_authorization(self, authorization_id: str) -> dict[str, Any]:
        """Get authorization details."""
        return await self._request("GET", f"/v2/payments/authorizations/{authorization_id}")

    async def capture_authorization(
        self,
        authorization_id: str,
        amount: Decimal | None = None,
        final_capture: bool = False,
        invoice_id: str | None = None,
        note_to_payer: str | None = None,
    ) -> dict[str, Any]:
        """Capture (settle) an authorization.

        Args:
            authorization_id: Authorization ID
            amount: Amount to capture (None for full)
            final_capture: Whether this is the final capture
            invoice_id: Invoice ID for tracking
            note_to_payer: Note to send to payer

        Returns:
            Capture object with id and status
        """
        json_data: dict[str, Any] = {}

        if amount is not None:
            json_data["amount"] = {
                "value": str(amount),
            }

        if final_capture:
            json_data["final_capture"] = True

        if invoice_id:
            json_data["invoice_id"] = invoice_id

        if note_to_payer:
            json_data["note_to_payer"] = note_to_payer

        return await self._request(
            "POST",
            f"/v2/payments/authorizations/{authorization_id}/capture",
            json_data=json_data,
        )

    async def void_authorization(self, authorization_id: str) -> dict[str, Any]:
        """Void (cancel) an authorization."""
        return await self._request(
            "POST",
            f"/v2/payments/authorizations/{authorization_id}/void",
            json_data={},
        )

    async def reauthorize_authorization(self, authorization_id: str, amount: Decimal | None = None) -> dict[str, Any]:
        """Reauthorize an authorization (extend expiry, optionally change amount)."""
        json_data: dict[str, Any] = {}
        if amount is not None:
            json_data["amount"] = {"value": str(amount)}

        return await self._request(
            "POST",
            f"/v2/payments/authorizations/{authorization_id}/reauthorize",
            json_data=json_data,
        )

    async def get_capture(self, capture_id: str) -> dict[str, Any]:
        """Get capture details."""
        return await self._request("GET", f"/v2/payments/captures/{capture_id}")

    async def refund_capture(
        self,
        capture_id: str,
        amount: Decimal | None = None,
        note_to_payer: str | None = None,
    ) -> dict[str, Any]:
        """Refund a capture.

        Args:
            capture_id: Capture ID
            amount: Amount to refund (None for full)
            note_to_payer: Note to send to payer

        Returns:
            Refund object with id and status
        """
        json_data: dict[str, Any] = {}

        if amount is not None:
            json_data["amount"] = {
                "value": str(amount),
            }

        if note_to_payer:
            json_data["note_to_payer"] = note_to_payer

        return await self._request(
            "POST",
            f"/v2/payments/captures/{capture_id}/refund",
            json_data=json_data,
        )

    async def get_refund(self, refund_id: str) -> dict[str, Any]:
        """Get refund details."""
        return await self._request("GET", f"/v2/payments/refunds/{refund_id}")

    # Payouts v1 API

    async def create_payout_batch(
        self,
        sender_batch_id: str,
        email_subject: str,
        email_message: str | None,
        currency_code: str,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Create a payout batch.

        Args:
            sender_batch_id: Unique batch ID (idempotency key)
            email_subject: Subject for notification email
            email_message: Message for notification email (optional)
            currency_code: 3-letter currency code
            items: List of payout items, each with:
                - recipient_type: 'EMAIL'
                - receiver: recipient email
                - amount: amount to pay
                - sender_item_id: unique item ID
                - note (optional)

        Returns:
            Batch object with id and status
        """
        return await self._request(
            "POST",
            "/v1/payments/payouts",
            json_data={
                "sender_batch_header": {
                    "sender_batch_id": sender_batch_id,
                    "email_subject": email_subject,
                    "email_message": email_message or "",
                },
                "items": items,
            },
        )

    async def get_payout_batch(self, batch_id: str, page: int = 1, page_size: int = 25) -> dict[str, Any]:
        """Get payout batch details."""
        return await self._request(
            "GET",
            f"/v1/payments/payouts/{batch_id}",
            params={
                "page": page,
                "page_size": page_size,
            },
        )

    async def get_payout_item(self, item_id: str) -> dict[str, Any]:
        """Get payout item details."""
        return await self._request("GET", f"/v1/payments/payouts-item/{item_id}")

    async def cancel_payout_item(self, item_id: str) -> dict[str, Any]:
        """Cancel a payout item."""
        return await self._request(
            "POST",
            f"/v1/payments/payouts-item/{item_id}/cancel",
            json_data={},
        )
