"""PayPal webhook signature verification endpoint.

POST /v1/notifications/verify-webhook-signature
"""

import json

from fastapi import APIRouter, status

from app.twin.signing import get_or_create_signing_key, verify_webhook_signature

router = APIRouter(prefix="/v1/notifications", tags=["webhooks"])


# Cache the signing key at module load - must be consistent for verification
_signing_key_cache = None


def _get_cached_signing_key():
    """Get or create and cache the signing key (module-level singleton)."""
    global _signing_key_cache
    if _signing_key_cache is None:
        _signing_key_cache = get_or_create_signing_key()
    return _signing_key_cache


@router.post("/verify-webhook-signature", status_code=status.HTTP_200_OK)
async def verify_webhook_signature_endpoint(
    body: dict,
) -> dict:
    """Verify a webhook signature.
    
    Per PayPal's documented algorithm:
    https://developer.paypal.com/api/webhooks/v1/verify-webhook-signature-post
    
    Request body:
    {
        "auth_algo": "SHA256withRSA",
        "cert_url": "https://api.paypal.com/cert/...",
        "transmission_id": "...",
        "transmission_sig": "...",
        "transmission_time": "...",
        "webhook_id": "...",
        "webhook_event": {...}  # The JSON event body
    }
    
    Response:
    {
        "verification_status": "SUCCESS" | "FAILURE"
    }
    """
    # Validate required fields
    required = ["auth_algo", "cert_url", "transmission_id", "transmission_sig", "transmission_time", "webhook_id", "webhook_event"]
    for field in required:
        if field not in body:
            return {"verification_status": "FAILURE"}

    auth_algo = body["auth_algo"]
    transmission_id = body["transmission_id"]
    transmission_sig = body["transmission_sig"]
    transmission_time = body["transmission_time"]
    webhook_id = body["webhook_id"]
    webhook_event = body["webhook_event"]

    # In a real implementation, we would:
    # 1. Fetch the certificate from cert_url
    # 2. Extract the public key
    # 3. Verify the signature
    #
    # For Chronos, we use our own signing key and verify against it.
    # This endpoint is primarily for testing that the twin's signatures are correct.

    if auth_algo != "SHA256withRSA":
        return {"verification_status": "FAILURE"}

    try:
        # Get our signing key (cached globally)
        private_key, public_key = _get_cached_signing_key()

        # Serialize the webhook_event to JSON bytes for CRC32
        # Use the same serialization as sign_webhook for consistency
        webhook_event_json = json.dumps(webhook_event, separators=(",", ":"), sort_keys=True)
        raw_body = webhook_event_json.encode("utf-8")

        # Verify the signature
        is_valid = verify_webhook_signature(
            transmission_id=transmission_id,
            transmission_time=transmission_time,
            webhook_id=webhook_id,
            raw_body=raw_body,
            signature_b64=transmission_sig,
            public_key=public_key,
        )

        status_code = "SUCCESS" if is_valid else "FAILURE"
        return {"verification_status": status_code}
    except Exception:
        return {"verification_status": "FAILURE"}
