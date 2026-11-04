"""PayPal-compatible ID generation."""

import secrets
import string


def generate_webhook_id() -> str:
    """Generate a PayPal-style webhook ID.
    
    Format: WH-<uppercase alphanumeric>
    PayPal's webhook IDs appear to be 17-20 chars total.
    
    Returns:
        Webhook ID like 'WH-ABC123XYZ'
    """
    chars = string.ascii_uppercase + string.digits
    suffix = "".join(secrets.choice(chars) for _ in range(20))
    return f"WH-{suffix}"


def generate_webhook_event_id() -> str:
    """Generate a PayPal-style webhook event ID.
    
    Format: WH-<uppercase alphanumeric> (same as webhook IDs)
    
    Returns:
        Event ID like 'WH-ABC123XYZ'
    """
    return generate_webhook_id()


def generate_order_id() -> str:
    """Generate a PayPal-style order ID.
    
    Format: 17 uppercase alphanumeric chars
    
    Returns:
        Order ID
    """
    chars = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(17))


def generate_batch_id() -> str:
    """Generate a PayPal-style payout batch ID.
    
    Format: 13 uppercase alphanumeric chars
    
    Returns:
        Batch ID
    """
    chars = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(13))
