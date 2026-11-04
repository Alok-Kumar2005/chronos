"""Tests for reality check system."""

import json
from pathlib import Path

import pytest

from app.reality.compare import ComparisonEngine, ProbeComparison
from app.reality.normalizer import Normalizer, flatten_for_comparison


class TestNormalizer:
    """Test ID and value normalization."""

    def test_normalize_order_id(self):
        """Order IDs should be normalized to [ORDER_ID]."""
        normalizer = Normalizer()
        response = {
            "id": "ABCDEFGHIJKLMNOPQ",  # 17 chars
            "order_id": "1234567890ABCDE",  # 15 chars - not order ID length, but should still normalize by key
        }
        normalized = normalizer.normalize_response(response)
        assert normalized["id"] == "[ORDER_ID]"
        # Key "order_id" should trigger normalization even if length doesn't match
        assert normalized["order_id"] == "[ORDER_ID]"

    def test_normalize_webhook_id(self):
        """Webhook IDs should be normalized to [WEBHOOK_ID]."""
        normalizer = Normalizer()
        response = {"webhook_id": "WH-ABCDEFGHIJ0123456789"}
        normalized = normalizer.normalize_response(response)
        assert normalized["webhook_id"] == "[WEBHOOK_ID]"

    def test_normalize_access_token(self):
        """Access tokens should be normalized to [ACCESS_TOKEN]."""
        normalizer = Normalizer()
        response = {"access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0"}
        normalized = normalizer.normalize_response(response)
        assert normalized["access_token"] == "[ACCESS_TOKEN]"

    def test_normalize_timestamp(self):
        """ISO8601 timestamps should be normalized to [TIMESTAMP]."""
        normalizer = Normalizer()
        response = {"created_at": "2026-11-04T10:00:00Z"}
        normalized = normalizer.normalize_response(response)
        assert normalized["created_at"] == "[TIMESTAMP]"

    def test_normalize_email(self):
        """Email addresses should be normalized to [EMAIL]."""
        normalizer = Normalizer()
        response = {"receiver": "merchant@example.com"}
        normalized = normalizer.normalize_response(response)
        assert normalized["receiver"] == "[EMAIL]"

    def test_normalize_url(self):
        """URLs should have hostname normalized to [HOST]."""
        normalizer = Normalizer()
        response = {"href": "https://api.sandbox.paypal.com/v2/orders/ABC123"}
        normalized = normalizer.normalize_response(response)
        assert "[HOST]" in normalized["href"]
        assert "/v2/orders/ABC123" in normalized["href"]

    def test_normalize_nested_object(self):
        """Nested objects should be normalized recursively."""
        normalizer = Normalizer()
        response = {
            "order": {
                "id": "ABCDEFGHIJKLMNOPQ",
                "created_at": "2026-11-04T10:00:00Z",
                "payer": {
                    "email": "customer@example.com",
                },
            }
        }
        normalized = normalizer.normalize_response(response)
        assert normalized["order"]["id"] == "[ORDER_ID]"
        assert normalized["order"]["created_at"] == "[TIMESTAMP]"
        assert normalized["order"]["payer"]["email"] == "[EMAIL]"

    def test_normalize_array(self):
        """Arrays should be normalized element-wise."""
        normalizer = Normalizer()
        response = {
            "items": [
                {"id": "ABCDEFGHIJKLMNOPQ", "created_at": "2026-11-04T10:00:00Z"},  # 17 char ID
                {"id": "ZYXWVUTSRQPONMLKJ", "created_at": "2026-11-04T11:00:00Z"},
            ]
        }
        normalized = normalizer.normalize_response(response)
        assert all(item["id"] == "[ORDER_ID]" for item in normalized["items"])
        assert all(item["created_at"] == "[TIMESTAMP]" for item in normalized["items"])


class TestFlattening:
    """Test response flattening to JSON paths."""

    def test_flatten_simple_object(self):
        """Simple object should flatten to root-level paths."""
        normalizer = Normalizer()
        obj = {"name": "test", "value": 42, "active": True}
        flattened = normalizer.flatten(obj)
        assert flattened["name"] == ("string", "test")
        assert flattened["value"] == ("integer", 42)
        assert flattened["active"] == ("boolean", True)

    def test_flatten_nested_object(self):
        """Nested objects should use path notation."""
        normalizer = Normalizer()
        obj = {"order": {"id": "ABC123", "status": "CREATED"}}
        flattened = normalizer.flatten(obj)
        assert flattened["order/id"] == ("string", "ABC123")
        assert flattened["order/status"] == ("string", "CREATED")

    def test_flatten_array(self):
        """Arrays should use index notation."""
        normalizer = Normalizer()
        obj = {"items": [{"id": "A"}, {"id": "B"}]}
        flattened = normalizer.flatten(obj)
        assert flattened["items/[0]/id"] == ("string", "A")
        assert flattened["items/[1]/id"] == ("string", "B")
        assert flattened["items/[_count]"] == ("integer", 2)

    def test_flatten_empty_array(self):
        """Empty arrays should be marked."""
        normalizer = Normalizer()
        obj = {"items": []}
        flattened = normalizer.flatten(obj)
        assert flattened["items"] == ("array", [])

    def test_flatten_null_value(self):
        """Null values should be typed as 'null'."""
        normalizer = Normalizer()
        obj = {"value": None}
        flattened = normalizer.flatten(obj)
        assert flattened["value"] == ("null", None)

    def test_type_detection_in_flatten(self):
        """Flattened paths should have correct types."""
        normalizer = Normalizer()
        obj = {
            "string": "test",
            "integer": 42,
            "float": 3.14,
            "boolean": True,
            "null": None,
            "array": [1, 2, 3],
            "object": {"nested": "value"},
        }
        flattened = normalizer.flatten(obj)
        assert flattened["string"][0] == "string"
        assert flattened["integer"][0] == "integer"
        assert flattened["float"][0] == "number"
        assert flattened["boolean"][0] == "boolean"
        assert flattened["null"][0] == "null"


class TestComparison:
    """Test response comparison."""

    def test_compare_identical_responses(self):
        """Identical flattened responses should have 100% match."""
        normalizer = Normalizer()
        response = {"id": "ABC123", "status": "CREATED", "value": 100}
        flat1 = normalizer.flatten(response)
        flat2 = normalizer.flatten(response)

        result = normalizer.compare_flattened(flat1, flat2)
        # Should have matched paths for id, status, value
        assert len(result["matched"]) >= 3
        assert len(result["missing_in_twin"]) == 0
        assert len(result["extra_in_twin"]) == 0

    def test_compare_missing_fields(self):
        """Missing fields should be detected."""
        normalizer = Normalizer()
        response_complete = {"id": "ABC123", "status": "CREATED"}  # This is the "real" response
        response_incomplete = {"id": "ABC123"}  # This is the "twin" response (missing status)

        flat_incomplete = normalizer.flatten(response_incomplete)  # twin
        flat_complete = normalizer.flatten(response_complete)  # real

        # compare_flattened(twin_flat, real_flat)
        result = normalizer.compare_flattened(flat_incomplete, flat_complete)
        # real has "status", twin doesn't, so it should be in missing_in_twin
        assert len(result["missing_in_twin"]) > 0

    def test_compare_extra_fields(self):
        """Extra fields should be detected."""
        normalizer = Normalizer()
        response1 = {"id": "ABC123", "status": "CREATED", "extra": "field"}
        response2 = {"id": "ABC123", "status": "CREATED"}

        flat1 = normalizer.flatten(response1)
        flat2 = normalizer.flatten(response2)

        result = normalizer.compare_flattened(flat1, flat2)
        assert "extra" in result["extra_in_twin"]

    def test_compare_type_mismatch(self):
        """Type mismatches should be detected."""
        normalizer = Normalizer()
        response1 = {"value": "100"}  # string
        response2 = {"value": 100}  # integer

        flat1 = normalizer.flatten(response1)
        flat2 = normalizer.flatten(response2)

        result = normalizer.compare_flattened(flat1, flat2)
        assert len(result["type_mismatches"]) > 0

    def test_compare_value_mismatch(self):
        """Value mismatches with same type should be detected."""
        normalizer = Normalizer()
        response1 = {"status": "CREATED"}
        response2 = {"status": "APPROVED"}

        flat1 = normalizer.flatten(response1)
        flat2 = normalizer.flatten(response2)

        result = normalizer.compare_flattened(flat1, flat2)
        assert len(result["value_mismatches"]) > 0


class TestProbeComparison:
    """Test probe comparison results."""

    def test_fidelity_perfect(self):
        """Perfect match should be 1.0 fidelity."""
        result = ProbeComparison("test_probe")
        result.matched_paths = ["a", "b", "c"]
        assert result.fidelity_score() == 1.0

    def test_fidelity_partial(self):
        """Partial match should be between 0 and 1."""
        result = ProbeComparison("test_probe")
        result.matched_paths = ["a", "b"]
        result.missing_in_twin = ["c", "d"]
        fidelity = result.fidelity_score()
        assert 0 < fidelity < 1
        assert fidelity == 0.5

    def test_fidelity_zero(self):
        """No paths means 0.0 fidelity."""
        result = ProbeComparison("test_probe")
        assert result.fidelity_score() == 0.0

    def test_comparison_to_dict(self):
        """Comparison should serialize to dict."""
        result = ProbeComparison("test_probe")
        result.status_code_match = True
        result.matched_paths = ["a", "b"]
        result.missing_in_twin = ["c"]

        d = result.to_dict()
        assert d["probe"] == "test_probe"
        assert d["status_code_match"] is True
        assert d["matched_paths"] == 2
        assert d["missing_in_twin"] == 1


class TestComparisonEngine:
    """Test the full comparison engine."""

    @pytest.fixture
    def golden_dir(self, tmp_path):
        """Create a temporary golden directory with test data."""
        golden_dir = tmp_path / "golden"
        golden_dir.mkdir()

        # Create a sample golden file
        golden_data = {
            "probe": "test_probe",
            "recorded_at": "2026-11-04T10:00:00Z",
            "paypal": {
                "status_code": 201,
                "body": {
                    "id": "ABCDEFGHIJKLMNOPQ",
                    "status": "CREATED",
                    "intent": "CAPTURE",
                },
                "headers": {"content-type": "application/json"},
            },
            "chronos": {
                "status_code": 201,
                "body": {
                    "id": "ZYXWVUTSRQPONMLKJ",
                    "status": "CREATED",
                    "intent": "CAPTURE",
                },
                "headers": {"content-type": "application/json"},
            },
        }

        with open(golden_dir / "test_probe.json", "w") as f:
            json.dump(golden_data, f)

        return golden_dir

    def test_load_golden(self, golden_dir, tmp_path):
        """Golden file should be loaded and parsed."""
        engine = ComparisonEngine(golden_dir=str(golden_dir))
        golden = engine.load_golden("test_probe")
        assert golden["probe"] == "test_probe"
        assert "paypal" in golden
        assert "chronos" in golden

    def test_compare_matching_statuses(self, golden_dir):
        """Matching status codes should pass status_code_match."""
        engine = ComparisonEngine(golden_dir=str(golden_dir))
        result = engine.compare_probe("test_probe")
        assert result.status_code_match is True

    def test_compare_fidelity(self, golden_dir):
        """Comparison should calculate fidelity score."""
        engine = ComparisonEngine(golden_dir=str(golden_dir))
        result = engine.compare_probe("test_probe")
        fidelity = result.fidelity_score()
        # Both responses have normalized IDs, so high fidelity expected
        assert 0 <= fidelity <= 1.0


class TestSecretLeakage:
    """Test that golden files don't leak secrets."""

    def test_golden_no_real_tokens(self):
        """Golden files should not contain real access tokens."""
        # This test checks that the normalizer is working
        normalizer = Normalizer()
        real_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" + "A" * 100
        response = {"access_token": real_token}
        normalized = normalizer.normalize_response(response)
        assert normalized["access_token"] == "[ACCESS_TOKEN]"
        assert real_token not in json.dumps(normalized)

    def test_golden_no_real_emails(self):
        """Golden files should not contain real email addresses."""
        normalizer = Normalizer()
        response = {"receiver": "real.person@example.com"}
        normalized = normalizer.normalize_response(response)
        assert normalized["receiver"] == "[EMAIL]"
        assert "real.person" not in json.dumps(normalized)


class TestNormalizerEdgeCases:
    """Test edge cases in normalization."""

    def test_normalize_empty_object(self):
        """Empty objects should stay empty."""
        normalizer = Normalizer()
        response = {}
        normalized = normalizer.normalize_response(response)
        assert normalized == {}

    def test_normalize_boolean_values(self):
        """Boolean values should not be normalized."""
        normalizer = Normalizer()
        response = {"active": True, "deleted": False}
        normalized = normalizer.normalize_response(response)
        assert normalized["active"] is True
        assert normalized["deleted"] is False

    def test_normalize_numeric_values(self):
        """Numeric values should not be normalized."""
        normalizer = Normalizer()
        response = {"count": 42, "ratio": 3.14}
        normalized = normalizer.normalize_response(response)
        assert normalized["count"] == 42
        assert normalized["ratio"] == 3.14

    def test_flatten_deeply_nested(self):
        """Deeply nested structures should flatten correctly."""
        normalizer = Normalizer()
        obj = {"a": {"b": {"c": {"d": {"e": "value"}}}}}
        flattened = normalizer.flatten(obj)
        assert flattened["a/b/c/d/e"] == ("string", "value")

    def test_normalize_array_of_objects(self):
        """Arrays of objects should normalize all elements."""
        normalizer = Normalizer()
        response = {
            "items": [
                {"id": "ABCDEFGHIJKLMNOPQ", "email": "user1@example.com"},
                {"id": "ZYXWVUTSRQPONMLKJ", "email": "user2@example.com"},
            ]
        }
        normalized = normalizer.normalize_response(response)
        for item in normalized["items"]:
            assert item["id"] == "[ORDER_ID]"
            assert item["email"] == "[EMAIL]"
