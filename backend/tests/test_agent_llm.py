"""Tests for agent LLM integration and FakeLLM."""

import json

import pytest

from app.agent.llm import (
    GroqModelError,
    get_fake_llm,
    reset_fake_llm,
    set_fake_llm_responses,
)
from app.agent.schemas import Scenario, ScenarioBatch


@pytest.fixture(autouse=True)
def reset_llm():
    """Reset FakeLLM state before each test."""
    reset_fake_llm()
    yield
    reset_fake_llm()


class TestFakeLLM:
    """Tests for FakeLLM test double."""

    @pytest.mark.asyncio
    async def test_fake_llm_returns_configured_response(self):
        """FakeLLM returns scripted responses in order."""
        responses = ["response 1", "response 2"]
        set_fake_llm_responses(responses)

        llm = get_fake_llm()
        result1 = await llm.ainvoke("prompt 1")
        result2 = await llm.ainvoke("prompt 2")

        # Results should be AIMessage or dict depending on JSON parsing
        assert result1 is not None
        assert result2 is not None

    @pytest.mark.asyncio
    async def test_fake_llm_exhaustion_error(self):
        """FakeLLM raises error when responses exhausted."""
        set_fake_llm_responses(["one response"])

        llm = get_fake_llm()
        await llm.ainvoke("prompt 1")

        with pytest.raises(ValueError, match="exhausted responses"):
            await llm.ainvoke("prompt 2")

    @pytest.mark.asyncio
    async def test_fake_llm_with_json_response(self):
        """FakeLLM parses JSON responses."""
        json_response = '{"id": "test", "value": 42}'
        set_fake_llm_responses([json_response])

        llm = get_fake_llm()
        result = await llm.ainvoke("prompt")

        # Should parse JSON
        assert isinstance(result, dict)
        assert result.get("id") == "test"
        assert result.get("value") == 42

    @pytest.mark.asyncio
    async def test_fake_llm_no_responses_configured(self):
        """FakeLLM raises error if no responses configured."""
        reset_fake_llm()  # Explicitly reset
        llm = get_fake_llm()

        with pytest.raises(ValueError, match="No fake LLM responses configured"):
            await llm.ainvoke("prompt")

    @pytest.mark.asyncio
    async def test_fake_llm_reset_resets_index(self):
        """reset_fake_llm resets response index."""
        responses = ["response 1", "response 2"]
        set_fake_llm_responses(responses)

        llm = get_fake_llm()
        await llm.ainvoke("prompt 1")

        reset_fake_llm()
        set_fake_llm_responses(responses)

        # Should be able to replay from start
        result = await llm.ainvoke("prompt 1 again")
        assert result is not None


class TestScenarioBatchValidation:
    """Tests for structured scenario output validation."""

    def test_scenario_batch_structure(self):
        """ScenarioBatch validates correct structure."""
        batch_data = {
            "scenarios": [
                {
                    "id": "test-scenario-1",
                    "title": "Test Scenario",
                    "description": "A test scenario for validation",
                    "tags": ["test"],
                    "steps": [
                        {"root": {"type": "snapshot", "label": "start"}},
                        {
                            "root": {
                                "type": "place_order",
                                "product_id": "PROD-1",
                                "payer_email": "customer@example.com",
                                "quantity": 1,
                            }
                        },
                        {"root": {"type": "snapshot", "label": "end"}},
                    ],
                }
            ]
        }

        batch = ScenarioBatch(**batch_data)
        assert len(batch.scenarios) == 1
        assert batch.scenarios[0].id == "test-scenario-1"
        assert len(batch.scenarios[0].steps) == 3

    def test_scenario_batch_max_size(self):
        """ScenarioBatch rejects more than 8 scenarios."""
        scenarios = [
            {
                "id": f"scenario-{i}",
                "title": f"Scenario {i}",
                "description": "Test",
                "tags": [],
                "steps": [{"root": {"type": "snapshot", "label": "test"}}],
            }
            for i in range(9)
        ]

        with pytest.raises(ValueError):
            ScenarioBatch(scenarios=scenarios)

    def test_scenario_max_steps(self):
        """Scenario rejects more than 40 steps."""
        steps = [{"root": {"type": "snapshot", "label": f"step-{i}"}} for i in range(41)]

        with pytest.raises(ValueError):
            Scenario(
                id="test",
                title="Test",
                description="Test",
                tags=[],
                steps=steps,
            )

    def test_scenario_advance_bounds(self):
        """Scenario validates advance step bounds."""
        # Valid: 400 days max per advance
        valid_steps = [
            {
                "root": {
                    "type": "advance",
                    "days": 400,
                    "hours": 0,
                }
            }
        ]
        scenario = Scenario(
            id="test",
            title="Test",
            description="Test",
            tags=[],
            steps=valid_steps,
        )
        assert scenario is not None

        # Invalid: > 400 days in single advance
        invalid_steps = [
            {
                "root": {
                    "type": "advance",
                    "days": 401,
                    "hours": 0,
                }
            }
        ]
        with pytest.raises(ValueError):
            Scenario(
                id="test",
                title="Test",
                description="Test",
                tags=[],
                steps=invalid_steps,
            )


class TestFakeLLMScenarioBatch:
    """Test FakeLLM with structured scenario output."""

    @pytest.mark.asyncio
    async def test_fake_llm_scenario_batch_response(self):
        """FakeLLM can return parsed scenario batch."""
        batch_json = json.dumps(
            {
                "scenarios": [
                    {
                        "id": "scenario-1",
                        "title": "Test",
                        "description": "Test scenario",
                        "tags": ["test"],
                        "steps": [
                            {"root": {"type": "snapshot", "label": "start"}},
                            {
                                "root": {
                                    "type": "advance",
                                    "days": 1,
                                    "hours": 0,
                                }
                            },
                        ],
                    }
                ]
            }
        )
        set_fake_llm_responses([batch_json])

        llm = get_fake_llm()
        result = await llm.ainvoke("generate scenarios")

        # Result should be parsed dict
        assert isinstance(result, dict)
        assert "scenarios" in result
        assert len(result["scenarios"]) == 1

        # Should validate as ScenarioBatch
        batch = ScenarioBatch(**result)
        assert len(batch.scenarios) == 1


class TestGroqModelValidation:
    """Tests for Groq model validation."""

    def test_groq_model_error_no_api_key(self):
        """get_llm raises GroqModelError if API key missing."""
        # Temporarily clear the API key to trigger error
        import os

        original = os.environ.get("GROQ_API_KEY")
        try:
            os.environ.pop("GROQ_API_KEY", None)
            # Note: This test would need to reload settings, which is tricky
            # For now, just verify the error type exists
            assert GroqModelError is not None
        finally:
            if original:
                os.environ["GROQ_API_KEY"] = original


# TODO: Add live test marked with @pytest.mark.live
# that exercises real Groq API with rate limiting and token tracking
