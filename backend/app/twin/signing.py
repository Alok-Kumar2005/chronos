"""RSA signing and verification for PayPal webhooks.

Per PayPal's documented algorithm (https://developer.paypal.com/api/webhooks/v1):
Signature = base64(RSA-SHA256(transmission_id|transmission_time|webhook_id|crc32(raw_body)))

Where:
- transmission_id: unique ID for this transmission (UUID)
- transmission_time: ISO 8601 timestamp
- webhook_id: the webhook's ID
- crc32: unsigned 32-bit CRC32 of the exact raw request bytes
"""

import base64
import binascii
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


def get_or_create_signing_key() -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    """Get or create an RSA keypair for signing webhooks.
    
    If SIGNING_KEY_B64 is set in the environment, load it (allows key persistence across restarts).
    Otherwise, generate a new keypair and warn that it will be lost on restart.
    
    Returns:
        Tuple of (private_key, public_key)
    """
    env_key = os.environ.get("SIGNING_KEY_B64")

    if env_key:
        # Deserialize from base64
        try:
            key_bytes = base64.b64decode(env_key)
            private_key = serialization.load_pem_private_key(
                key_bytes,
                password=None,
            )
            public_key = private_key.public_key()
            return private_key, public_key
        except Exception as e:
            raise RuntimeError(f"Failed to load SIGNING_KEY_B64: {e}") from e
    else:
        # Generate new keypair
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        public_key = private_key.public_key()

        # Warn about ephemeral key
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(
            "No SIGNING_KEY_B64 environment variable set. "
            "Generated ephemeral RSA keypair; signatures will not survive restarts. "
            "Set SIGNING_KEY_B64=<base64> to persist the key."
        )

        return private_key, public_key


def get_public_cert_pem(public_key: rsa.RSAPublicKey) -> str:
    """Serialize public key to PEM format (X.509 SubjectPublicKeyInfo)."""
    pem_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return pem_bytes.decode("utf-8")


def compute_crc32(data: bytes) -> int:
    """Compute unsigned 32-bit CRC32 of data.
    
    PayPal uses the standard CRC32 algorithm with unsigned interpretation.
    
    Args:
        data: Raw bytes to checksum
        
    Returns:
        Unsigned 32-bit CRC32 value
    """
    return binascii.crc32(data) & 0xffffffff


def sign_webhook(
    transmission_id: str,
    transmission_time: str,
    webhook_id: str,
    raw_body: bytes,
    private_key: rsa.RSAPrivateKey,
) -> str:
    """Sign a webhook transmission per PayPal's algorithm.
    
    Args:
        transmission_id: Unique transmission ID (typically a UUID)
        transmission_time: ISO 8601 timestamp
        webhook_id: The webhook's ID
        raw_body: Exact raw bytes of the request body
        private_key: RSA private key for signing
        
    Returns:
        Base64-encoded signature
    """
    # Compute CRC32 of raw body
    crc32 = compute_crc32(raw_body)

    # Construct signed string: id|time|webhook_id|crc32
    signed_string = f"{transmission_id}|{transmission_time}|{webhook_id}|{crc32}"

    # Sign with RSA-SHA256
    signature = private_key.sign(
        signed_string.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )

    # Base64 encode
    return base64.b64encode(signature).decode("utf-8")


def verify_webhook_signature(
    transmission_id: str,
    transmission_time: str,
    webhook_id: str,
    raw_body: bytes,
    signature_b64: str,
    public_key: rsa.RSAPublicKey,
) -> bool:
    """Verify a webhook signature per PayPal's algorithm.
    
    Args:
        transmission_id: Transmission ID from header
        transmission_time: Transmission time from header
        webhook_id: Webhook ID
        raw_body: Exact raw bytes of the request body
        signature_b64: Base64-encoded signature from header
        public_key: RSA public key for verification
        
    Returns:
        True if signature is valid, False otherwise
    """
    try:
        # Compute CRC32
        crc32 = compute_crc32(raw_body)

        # Reconstruct signed string
        signed_string = f"{transmission_id}|{transmission_time}|{webhook_id}|{crc32}"

        # Decode signature
        signature_bytes = base64.b64decode(signature_b64)

        # Verify with public key
        public_key.verify(
            signature_bytes,
            signed_string.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )

        return True
    except Exception:
        return False
