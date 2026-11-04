"""
Normalizer for dynamic values in API responses.

Converts dynamic, environment-specific values to placeholders so we can
compare real PayPal responses with Chronos responses structurally.

Dynamic values that need normalization:
- IDs (order_id, batch_id, webhook_id, etc.) → [ID_TYPE]
- Tokens (access_token) → [TOKEN]
- Timestamps (ISO8601 strings) → [TIMESTAMP]
- Hostnames in URLs → [HOST]
- Email addresses (personal) → [EMAIL]
- CRC32 hashes in webhook signatures → [CRC32]
"""

import re
from typing import Any


class Normalizer:
    """Normalize API responses for comparison."""

    def __init__(self):
        """Initialize normalizer with pattern matchers."""
        # Patterns for ID types
        self.patterns = {
            # PayPal-style IDs
            "order_id": re.compile(r'^[A-Z0-9]{17}$'),  # 17 char uppercase alphanumeric
            "batch_id": re.compile(r'^[A-Z0-9]{13}$'),  # 13 char batch IDs
            "webhook_id": re.compile(r'^WH-[A-Z0-9]{20}$'),
            "event_id": re.compile(r'^WH-[A-Z0-9]{20}$'),
            "auth_id": re.compile(r'^[A-Z0-9]{17}$'),  # Authorization IDs same format
            "capture_id": re.compile(r'^[A-Z0-9]{17}$'),  # Capture IDs same format
            "refund_id": re.compile(r'^[A-Z0-9]{17}$'),  # Refund IDs same format
            "item_id": re.compile(r'^[A-Z0-9]{18}$'),  # Item IDs 18 chars
            # Tokens
            "access_token": re.compile(r'^[A-Za-z0-9._\-]{50,}$'),  # JWT-like tokens
            # UUIDs / general GUIDs
            "uuid": re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I),
            # Timestamps
            "timestamp": re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$'),
            # Email
            "email": re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'),
            # URLs
            "url": re.compile(r'^https?://'),
        }

    def normalize_value(self, value: Any, path: str = "") -> Any:
        """Normalize a single value based on context and pattern matching."""
        if value is None:
            return None

        if isinstance(value, bool):
            return value

        if isinstance(value, int | float):
            # Don't normalize numbers, but could add precision handling here
            return value

        if isinstance(value, str):
            # Try to identify what kind of value this is
            path_lower = path.lower()

            # Check path hints - order matters, be specific first
            if "webhook_id" in path_lower or "event_id" in path_lower:
                if self.patterns["webhook_id"].match(value) or self.patterns["event_id"].match(value):
                    return "[WEBHOOK_ID]"

            if "batch_id" in path_lower:
                if self.patterns["batch_id"].match(value):
                    return "[BATCH_ID]"
                # Even if length doesn't match, batch_id key suggests batch
                if len(value) > 0 and value.isupper():
                    return "[BATCH_ID]"

            if "item_id" in path_lower:
                if self.patterns["item_id"].match(value):
                    return "[ITEM_ID]"
                if len(value) > 0 and value.isupper():
                    return "[ITEM_ID]"

            if "auth_id" in path_lower or "authorization_id" in path_lower:
                if self.patterns["auth_id"].match(value):
                    return "[AUTH_ID]"

            if "capture_id" in path_lower:
                if self.patterns["capture_id"].match(value):
                    return "[CAPTURE_ID]"

            if "refund_id" in path_lower:
                if self.patterns["refund_id"].match(value):
                    return "[REFUND_ID]"

            # Generic ID checks
            if any(x in path_lower for x in ["order_id", "id", "order"]):
                if self.patterns["order_id"].match(value):
                    return "[ORDER_ID]"
                # Key name "order_id" or "id" suggests it's an ID
                if "order_id" in path_lower or path_lower == "id":
                    if len(value) > 10 and value.isupper():
                        return "[ORDER_ID]"
                if self.patterns["uuid"].match(value):
                    return "[UUID]"

            if "token" in path_lower or "access_token" in path_lower:
                if self.patterns["access_token"].match(value):
                    return "[ACCESS_TOKEN]"

            if any(x in path_lower for x in ["timestamp", "time", "created", "updated", "expires"]):
                if self.patterns["timestamp"].match(value):
                    return "[TIMESTAMP]"

            if "email" in path_lower or "receiver" in path_lower:
                if self.patterns["email"].match(value):
                    return "[EMAIL]"

            if "url" in path_lower or "href" in path_lower:
                if self.patterns["url"].match(value):
                    # Normalize hostname in URL
                    match = re.match(r'(https?://)([^/]+)(.*)', value)
                    if match:
                        return f"{match.group(1)}[HOST]{match.group(3)}"
                    return "[URL]"

            # Generic UUID check
            if self.patterns["uuid"].match(value):
                return "[UUID]"

            return value

        if isinstance(value, dict):
            return self.normalize_object(value, path)

        if isinstance(value, list):
            return [self.normalize_value(item, path) for item in value]

        return value

    def normalize_object(self, obj: dict[str, Any], path: str = "") -> dict[str, Any]:
        """Recursively normalize an object."""
        result = {}
        for key, value in obj.items():
            new_path = f"{path}.{key}" if path else key
            result[key] = self.normalize_value(value, new_path)
        return result

    def normalize_response(self, response: dict[str, Any]) -> dict[str, Any]:
        """Normalize an entire API response."""
        normalized = self.normalize_object(response)

        # Sanitize known sensitive fields even if not caught by patterns
        if "access_token" in normalized:
            normalized["access_token"] = "[ACCESS_TOKEN]"
        if "refresh_token" in normalized:
            normalized["refresh_token"] = "[REFRESH_TOKEN]"

        return normalized

    def flatten(self, obj: Any, prefix: str = "", parent_is_array: bool = False) -> dict[str, tuple[str, Any]]:
        """
        Flatten object to JSON paths.

        Returns dict mapping 'path' -> (type, normalized_value)
        For arrays, uses index: path/[0], path/[1], etc.
        """
        result: dict[str, tuple[str, Any]] = {}

        if obj is None:
            return {prefix: ("null", None)}

        if isinstance(obj, bool):
            # Must check bool before int since bool is subclass of int
            return {prefix: ("boolean", obj)}

        if isinstance(obj, int):
            return {prefix: ("integer", obj)}

        if isinstance(obj, float):
            return {prefix: ("number", obj)}

        if isinstance(obj, str):
            normalized = self.normalize_value(obj, prefix)
            return {prefix: ("string", normalized)}

        if isinstance(obj, list):
            if not obj:
                return {prefix: ("array", [])}

            for i, item in enumerate(obj):
                item_path = f"{prefix}/[{i}]"
                item_flat = self.flatten(item, item_path, parent_is_array=True)
                result.update(item_flat)
            # Also add array marker
            result[f"{prefix}/[_count]"] = ("integer", len(obj))
            return result

        if isinstance(obj, dict):
            if not obj:
                return {prefix: ("object", {})}

            for key, value in obj.items():
                key_path = f"{prefix}/{key}" if prefix else key
                value_flat = self.flatten(value, key_path, parent_is_array=False)
                result.update(value_flat)
            return result

        # Fallback
        return {prefix: ("unknown", str(obj))}

    def compare_flattened(
        self,
        twin_flat: dict[str, tuple[str, Any]],
        real_flat: dict[str, tuple[str, Any]],
    ) -> dict[str, Any]:
        """
        Compare two flattened responses.

        Returns dict with:
        - matched: list of matching paths
        - missing_in_twin: paths in real but not twin
        - extra_in_twin: paths in twin but not real
        - type_mismatches: paths where types differ
        - value_mismatches: paths where values differ (same type)
        """
        twin_paths = set(twin_flat.keys())
        real_paths = set(real_flat.keys())

        matched = []
        type_mismatches = []
        value_mismatches = []

        for path in twin_paths & real_paths:
            twin_type, twin_value = twin_flat[path]
            real_type, real_value = real_flat[path]

            if twin_type != real_type:
                type_mismatches.append(
                    {
                        "path": path,
                        "twin_type": twin_type,
                        "real_type": real_type,
                    }
                )
            elif twin_value != real_value:
                value_mismatches.append(
                    {
                        "path": path,
                        "twin_value": twin_value,
                        "real_value": real_value,
                    }
                )
            else:
                matched.append(path)

        return {
            "matched": matched,
            "missing_in_twin": list(real_paths - twin_paths),
            "extra_in_twin": list(twin_paths - real_paths),
            "type_mismatches": type_mismatches,
            "value_mismatches": value_mismatches,
        }


def flatten_for_comparison(response: dict[str, Any]) -> dict[str, tuple[str, Any]]:
    """Flatten and normalize a response for comparison."""
    normalizer = Normalizer()
    normalized = normalizer.normalize_response(response)
    return normalizer.flatten(normalized)
