"""PayPal-shaped error handling and exceptions."""

import secrets
from typing import Any

from fastapi import Request, status
from fastapi.responses import JSONResponse


class PayPalError(Exception):
    """Base exception for PayPal-compatible errors."""

    def __init__(
        self,
        name: str,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: list[dict[str, str]] | None = None,
    ) -> None:
        """
        Initialize a PayPal-shaped error.

        Args:
            name: Error name (e.g., INVALID_REQUEST, RESOURCE_NOT_FOUND)
            message: Human-readable error message
            status_code: HTTP status code
            details: List of detailed error descriptions
        """
        self.name = name
        self.message = message
        self.status_code = status_code
        self.details = details or []
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        """Convert error to PayPal-shaped dictionary."""
        return {
            "name": self.name,
            "message": self.message,
            "debug_id": secrets.token_hex(8),
            "details": self.details,
            "links": [],
        }


async def paypal_error_handler(request: Request, exc: PayPalError) -> JSONResponse:
    """
    Handle PayPalError exceptions and return PayPal-shaped responses.

    Args:
        request: FastAPI request
        exc: PayPalError exception

    Returns:
        JSONResponse with PayPal error format
    """
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_dict(),
    )


async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Handle unexpected exceptions with PayPal-shaped responses.

    Args:
        request: FastAPI request
        exc: Any exception

    Returns:
        JSONResponse with PayPal error format
    """
    # Log the error (structured logging will handle it)
    import logging
    logger = logging.getLogger(__name__)
    logger.exception("Unexpected error", exc_info=exc)

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "name": "INTERNAL_SERVER_ERROR",
            "message": "An unexpected error occurred",
            "debug_id": secrets.token_hex(8),
            "details": [],
            "links": [],
        },
    )
