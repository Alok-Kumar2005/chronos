"""
Probe definitions for reality checks.

A probe is a named request specification that can be run against any base URL
(real PayPal sandbox or Chronos twin). Probes define realistic API calls that
test the fidelity of the twin's responses.

Each probe is a dict with:
- name: human-readable probe name
- method: HTTP method (GET, POST, PATCH, DELETE)
- path: URL path template (/{placeholder} for path parameters)
- auth: 'oauth' or 'bearer' or 'none'
- body: request body (dict or None)
- notes: why this probe matters for fidelity

Not probed:
- CAPTURE after authorization (requires real buyer approval in sandbox; cannot simulate)
- VOID authorization after capture (requires specific merchant config)
- Webhook deliveries (network-dependent, tested separately in delivery test)
- INSTRUMENT_DECLINED mock failure (requires special header + valid instrument setup)
- Subscription operations (not in MVP)
- Dispute resolution (not in MVP)
- Advanced payout scenarios with hold/onhold states (rare)

Confidence levels:
- 'baseline': Essential for fidelity, must match
- 'optional': Nice to have, can be ignored if not implemented
"""

from typing import Any

PROBES = {
    "oauth_token": {
        "name": "OAuth token creation",
        "method": "POST",
        "path": "/v1/oauth2/token",
        "auth": "none",
        "body": {
            "grant_type": "client_credentials",
            "scope": "https://api.paypal.com/v1/payments/.*",
        },
        "notes": "Token response shape: {scope, access_token, token_type, app_id, expires_in, nonce}",
        "confidence": "baseline",
    },
    "create_order_capture": {
        "name": "Create order with CAPTURE intent",
        "method": "POST",
        "path": "/v2/checkout/orders",
        "auth": "bearer",
        "body": {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": "test_capture_1",
                    "amount": {
                        "currency_code": "USD",
                        "value": "10.00",
                    },
                }
            ],
        },
        "notes": "Basic order creation; response includes order id, links, status=CREATED",
        "confidence": "baseline",
    },
    "create_order_authorize": {
        "name": "Create order with AUTHORIZE intent",
        "method": "POST",
        "path": "/v2/checkout/orders",
        "auth": "bearer",
        "body": {
            "intent": "AUTHORIZE",
            "purchase_units": [
                {
                    "reference_id": "test_auth_1",
                    "amount": {
                        "currency_code": "USD",
                        "value": "20.00",
                    },
                }
            ],
        },
        "notes": "Authorization-based order; response includes id, status=CREATED",
        "confidence": "baseline",
    },
    "get_order": {
        "name": "Get order details",
        "method": "GET",
        "path": "/v2/checkout/orders/{order_id}",
        "auth": "bearer",
        "body": None,
        "notes": "Retrieve existing order; includes intent, status, payer, purchase_units",
        "confidence": "baseline",
    },
    "get_nonexistent_order": {
        "name": "Get nonexistent order (404 shape)",
        "method": "GET",
        "path": "/v2/checkout/orders/INVALID_ID_12345",
        "auth": "bearer",
        "body": None,
        "notes": "Error response shape: {name, message, debug_id, details, links}; status=404",
        "confidence": "baseline",
    },
    "create_order_invalid_body": {
        "name": "Create order with invalid body (400/422 shape)",
        "method": "POST",
        "path": "/v2/checkout/orders",
        "auth": "bearer",
        "body": {
            "intent": "CAPTURE",
            "purchase_units": [],  # Empty purchase_units
        },
        "notes": "Validation error; status=400 or 422 with error details",
        "confidence": "baseline",
    },
    "patch_order": {
        "name": "Patch order",
        "method": "PATCH",
        "path": "/v2/checkout/orders/{order_id}",
        "auth": "bearer",
        "body": [
            {"op": "replace", "path": "/purchase_units/@reference_id=='test'/amount/value", "value": "25.00"}
        ],
        "notes": "Order update with JSON patch; response 204 no content or updated order",
        "confidence": "baseline",
    },
    "create_payout_batch": {
        "name": "Create payout batch",
        "method": "POST",
        "path": "/v1/payments/payouts",
        "auth": "bearer",
        "body": {
            "sender_batch_header": {
                "sender_batch_id": "probe_batch_001",
                "email_subject": "Probe Payout",
                "email_message": "Test payout batch",
            },
            "items": [
                {
                    "recipient_type": "EMAIL",
                    "amount": {
                        "value": "100.00",
                        "currency": "USD",
                    },
                    "note": "Probe payout",
                    "sender_item_id": "probe_item_001",
                    "receiver": "test-unregistered@sandbox.paypal.com",
                }
            ],
        },
        "notes": "Payout batch creation; status=201; response includes batch_id, status=PROCESSING",
        "confidence": "baseline",
    },
    "get_payout_batch": {
        "name": "Get payout batch",
        "method": "GET",
        "path": "/v1/payments/payouts/{batch_id}",
        "auth": "bearer",
        "body": None,
        "notes": "Retrieve batch; includes items, counts, summary",
        "confidence": "baseline",
    },
    "get_payout_item": {
        "name": "Get payout item",
        "method": "GET",
        "path": "/v1/payments/payouts-item/{item_id}",
        "auth": "bearer",
        "body": None,
        "notes": "Retrieve payout item; includes status, amount, receiver",
        "confidence": "baseline",
    },
    "duplicate_batch_id": {
        "name": "Duplicate sender_batch_id (error shape)",
        "method": "POST",
        "path": "/v1/payments/payouts",
        "auth": "bearer",
        "body": {
            "sender_batch_header": {
                "sender_batch_id": "probe_batch_001",  # Reuse first batch id
                "email_subject": "Duplicate",
                "email_message": "Should fail",
            },
            "items": [
                {
                    "recipient_type": "EMAIL",
                    "amount": {
                        "value": "50.00",
                        "currency": "USD",
                    },
                    "sender_item_id": "probe_item_002",
                    "receiver": "other@sandbox.paypal.com",
                }
            ],
        },
        "notes": "Duplicate batch ID should fail with status=400 USER_BUSINESS_ERROR",
        "confidence": "baseline",
    },
    "webhook_create": {
        "name": "Create webhook subscription",
        "method": "POST",
        "path": "/v1/notifications/webhooks",
        "auth": "bearer",
        "body": {
            "url": "https://example.com/probe-webhook",
            "event_types": [{"name": "CHECKOUT.ORDER.APPROVED"}],
        },
        "notes": "Webhook registration; response includes webhook_id, status=201",
        "confidence": "baseline",
    },
    "webhook_list": {
        "name": "List webhooks",
        "method": "GET",
        "path": "/v1/notifications/webhooks",
        "auth": "bearer",
        "body": None,
        "notes": "List all webhooks for this client; returns array with pagination",
        "confidence": "baseline",
    },
    "webhook_delete": {
        "name": "Delete webhook",
        "method": "DELETE",
        "path": "/v1/notifications/webhooks/{webhook_id}",
        "auth": "bearer",
        "body": None,
        "notes": "Delete webhook; status=204 no content",
        "confidence": "baseline",
    },
    "webhook_verify_signature": {
        "name": "Verify webhook signature",
        "method": "POST",
        "path": "/v1/notifications/verify-webhook-signature",
        "auth": "bearer",
        "body": {
            "transmission_id": "test-transmission-id",
            "transmission_time": "2026-11-04T10:00:00Z",
            "cert_url": "https://api.sandbox.paypal.com/cert/invalid",
            "auth_algo": "SHA256withRSA",
            "webhook_id": "test-webhook-id",
            "webhook_event": {
                "id": "WH-TEST",
                "event_type": "CHECKOUT.ORDER.APPROVED",
            },
            "transmission_sig": "invalid-signature",
        },
        "notes": "Verify webhook signature; response {verification_status: SUCCESS|FAILURE}",
        "confidence": "baseline",
    },
}


def get_probe(name: str) -> dict[str, Any]:
    """Get a probe by name."""
    if name not in PROBES:
        raise ValueError(f"Unknown probe: {name}")
    return PROBES[name]


def list_probes() -> list[str]:
    """List all probe names."""
    return list(PROBES.keys())
