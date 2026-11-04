"""OAuth 2.0 token endpoint for PayPal compatibility."""

import base64
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Header, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import PayPalError
from app.models import AccessToken, Universe

router = APIRouter(prefix="/v1/oauth2", tags=["oauth"])


@router.post("/token", status_code=status.HTTP_200_OK)
async def post_token(
    grant_type: Annotated[str, Form()],
    scope: Annotated[str | None, Form()] = None,
    authorization: Annotated[str | None, Header()] = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """
    OAuth 2.0 token endpoint (PayPal-compatible).

    HTTP Basic auth: client_id:client_secret
    Body: grant_type=client_credentials (form encoded)

    Returns PayPal's shape:
    {
        "scope": "...",
        "access_token": "...",
        "token_type": "Bearer",
        "app_id": "...",
        "expires_in": 32400,
        "nonce": "..."
    }
    """
    # Validate grant_type
    if grant_type != "client_credentials":
        raise PayPalError(
            name="INVALID_REQUEST",
            message="Invalid grant_type. Only client_credentials is supported.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # Parse HTTP Basic auth
    if not authorization or not authorization.startswith("Basic "):
        raise PayPalError(
            name="AUTHENTICATION_FAILURE",
            message="Missing or invalid Authorization header",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    try:
        encoded_creds = authorization[6:]  # Remove "Basic "
        decoded = base64.b64decode(encoded_creds).decode("utf-8")
        client_id, client_secret = decoded.split(":", 1)
    except Exception:
        raise PayPalError(
            name="AUTHENTICATION_FAILURE",
            message="Invalid Authorization header format",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # Look up universe by client_id
    result = await session.execute(
        select(Universe).where(Universe.client_id == client_id)
    )
    universe = result.scalar_one_or_none()

    if not universe:
        raise PayPalError(
            name="AUTHENTICATION_FAILURE",
            message="Invalid client credentials",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # Verify client_secret
    if not Universe.verify_secret(client_secret, universe.client_secret_hash):
        raise PayPalError(
            name="AUTHENTICATION_FAILURE",
            message="Invalid client credentials",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # Check universe expiry
    if datetime.now(UTC) > universe.expires_at:
        raise PayPalError(
            name="AUTHENTICATION_FAILURE",
            message="Universe has expired",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # Create access token
    token_scope = scope or "https://api.paypal.com/v1/payments/.*"
    expires_in = 32400  # 9 hours in seconds (PayPal default)

    token_obj, plaintext_token = AccessToken.create(
        universe_id=universe.id,
        scope=token_scope,
        issued_virtual_at=universe.virtual_now,
        expires_in_seconds=expires_in,
    )
    session.add(token_obj)
    await session.commit()

    # Return PayPal-shaped response
    import secrets
    return {
        "scope": token_scope,
        "access_token": plaintext_token,
        "token_type": "Bearer",
        "app_id": universe.client_id,
        "expires_in": expires_in,
        "nonce": secrets.token_hex(8),
    }
