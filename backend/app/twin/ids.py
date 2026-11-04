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



def generate_authorization_id() -> str:
    """Generate a PayPal-style authorization ID.
    
    Format: 17 uppercase alphanumeric chars (same as order ID format)
    
    Returns:
        Authorization ID
    """
    chars = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(17))


def generate_capture_id() -> str:
    """Generate a PayPal-style capture ID.
    
    Format: 17 uppercase alphanumeric chars (same as order ID format)
    
    Returns:
        Capture ID
    """
    chars = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(17))


def generate_refund_id() -> str:
    """Generate a PayPal-style refund ID.
    
    Format: 17 uppercase alphanumeric chars (same as order ID format)
    
    Returns:
        Refund ID
    """
    chars = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(17))


def generate_payout_item_id() -> str:
    """Generate a PayPal-style payout item ID.
    
    Format: 18-20 uppercase alphanumeric chars
    
    Returns:
        Payout item ID
    """
    chars = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(18))


def generate_plan_id() -> str:
    """Generate a PayPal-style billing plan ID.
    
    Format: 3-character 'P-' prefix plus 12-16 uppercase alphanumeric chars
    
    Returns:
        Plan ID like 'P-ABC123XYZ'
    """
    chars = string.ascii_uppercase + string.digits
    suffix = "".join(secrets.choice(chars) for _ in range(15))
    return f"P-{suffix}"


def generate_subscription_id() -> str:
    """Generate a PayPal-style subscription ID.
    
    Format: 2-character 'I-' prefix plus 15-17 uppercase alphanumeric chars
    
    Returns:
        Subscription ID like 'I-ABC123XYZ'
    """
    chars = string.ascii_uppercase + string.digits
    suffix = "".join(secrets.choice(chars) for _ in range(16))
    return f"I-{suffix}"


def generate_product_id() -> str:
    """Generate a PayPal-style product ID.
    
    Format: 3-character 'PROD' prefix plus 12-16 uppercase alphanumeric chars
    
    Returns:
        Product ID like 'PROD-ABC123XYZ'
    """
    chars = string.ascii_uppercase + string.digits
    suffix = "".join(secrets.choice(chars) for _ in range(15))
    return f"PROD-{suffix}"
