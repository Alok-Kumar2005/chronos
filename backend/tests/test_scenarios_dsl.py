"""Tests for scenario DSL validation."""

import json
import pytest
from decimal import Decimal

from app.scenarios.dsl import (
    Scenario,
    Step,
    PlaceOrderAction,
    ApproveOrderAction,
    ShipOrderAction,
    AdvanceStep,
    CheckStep,
    ChaosConfig,
    SetChaosStep,
    SetHandlerVariantStep,
    HandlerVariant,
    SnapshotStep,
)


def test_place_order_action():
    """Test place_order action validation."""
    action = PlaceOrderAction(
        product_id="PROD-001",
        payer_email="customer@example.com",
        quantity=2,
    )
    assert action.type == "place_order"
    assert action.product_id == "PROD-001"
    assert action.quantity == 2


def test_place_order_requires_email():
    """Test that payer_email requires minimum length."""
    with pytest.raises(ValueError):
        PlaceOrderAction(
            product_id="PROD-001",
            payer_email="a@b",  # Too short - less than 5 chars
        )


def test_advance_step_hours_only():
    """Test advance step with hours only."""
    step = AdvanceStep(hours=24)
    assert step.hours == 24
    assert step.days == 0


def test_advance_step_days_only():
    """Test advance step with days only."""
    step = AdvanceStep(days=5)
    assert step.days == 5
    assert step.hours == 0


def test_advance_step_combined():
    """Test advance step with both hours and days."""
    step = AdvanceStep(days=1, hours=12)
    assert step.days == 1
    assert step.hours == 12


def test_advance_step_requires_nonzero():
    """Test that advance requires at least one non-zero value."""
    with pytest.raises(ValueError, match="must specify"):
        AdvanceStep(hours=0, days=0)


def test_advance_step_max_hours():
    """Test that single advance cannot exceed 400 hours (16+ days)."""
    with pytest.raises(ValueError, match="Cannot advance more than 400"):
        AdvanceStep(hours=401)


def test_advance_step_max_days():
    """Test that single advance cannot exceed 400 days."""
    with pytest.raises(ValueError, match="Cannot advance more than 400"):
        AdvanceStep(days=401)


def test_advance_step_combined_bounds():
    """Test combined advance bounds."""
    # Individual fields can't exceed 400, so 7200 hours will fail on the field validator
    with pytest.raises(ValueError):
        AdvanceStep(days=300, hours=7200)


def test_check_step_default_invariants():
    """Test check step with default invariants."""
    step = CheckStep()
    assert step.type == "check"
    assert len(step.invariant_ids) == 7
    assert "INV-1" in step.invariant_ids


def test_check_step_custom_invariants():
    """Test check step with custom invariants."""
    step = CheckStep(invariant_ids=["INV-1", "INV-2"])
    assert len(step.invariant_ids) == 2


def test_snapshot_step():
    """Test snapshot step."""
    step = SnapshotStep(label="after_first_order")
    assert step.type == "snapshot"
    assert step.label == "after_first_order"


def test_chaos_config():
    """Test chaos configuration."""
    chaos = ChaosConfig(
        webhook_delay_ms=100,
        webhook_failures_rate=0.1,
        webhook_reorder=True,
    )
    assert chaos.webhook_delay_ms == 100
    assert chaos.webhook_failures_rate == 0.1
    assert chaos.webhook_reorder is True


def test_chaos_config_bounds():
    """Test chaos config validation bounds."""
    with pytest.raises(ValueError):
        ChaosConfig(webhook_delay_ms=40001)  # Max 30000

    with pytest.raises(ValueError):
        ChaosConfig(webhook_failures_rate=1.1)  # Max 1.0


def test_set_chaos_step():
    """Test set_chaos step."""
    chaos = ChaosConfig(webhook_delay_ms=500)
    step = SetChaosStep(chaos=chaos)
    assert step.type == "set_chaos"
    assert step.chaos.webhook_delay_ms == 500


def test_set_handler_variant_step():
    """Test set_handler_variant step."""
    step = SetHandlerVariantStep(variant=HandlerVariant.BUGGY)
    assert step.type == "set_handler_variant"
    assert step.variant == HandlerVariant.BUGGY


def test_scenario_minimum():
    """Test minimum valid scenario."""
    scenario = Scenario(
        id="test-001",
        title="Test Scenario",
        description="A test scenario",
        steps=[
            Step(root=AdvanceStep(hours=1)),
        ],
    )
    assert scenario.id == "test-001"
    assert len(scenario.steps) == 1


def test_scenario_max_steps():
    """Test scenario cannot exceed 40 steps."""
    steps = [Step(root=AdvanceStep(hours=1)) for _ in range(41)]
    with pytest.raises(ValueError):
        Scenario(
            id="test-001",
            title="Test Scenario",
            description="A test scenario",
            steps=steps,
        )


def test_scenario_max_advance():
    """Test scenario cannot exceed 2000 days total advance."""
    steps = [Step(root=AdvanceStep(days=300)) for _ in range(7)]  # 2100 days
    with pytest.raises(ValueError, match="exceeds 2000 days"):
        Scenario(
            id="test-001",
            title="Test Scenario",
            description="A test scenario",
            steps=steps,
        )


def test_scenario_tags_deduped():
    """Test that scenario tags are deduplicated."""
    scenario = Scenario(
        id="test-001",
        title="Test Scenario",
        description="A test scenario",
        tags=["bug", "payment", "bug", "payment"],
        steps=[
            Step(root=AdvanceStep(hours=1)),
        ],
    )
    assert len(scenario.tags) == 2


def test_scenario_json_schema():
    """Test scenario JSON schema export."""
    scenario = Scenario(
        id="test-001",
        title="Test Scenario",
        description="A test scenario",
        steps=[
            Step(root=AdvanceStep(hours=1)),
        ],
    )
    schema = scenario.to_json_schema()
    assert "$defs" in schema or "definitions" in schema  # Pydantic includes definitions
    assert schema["type"] == "object"


def test_scenario_json_roundtrip():
    """Test scenario can be serialized and deserialized to JSON."""
    original = Scenario(
        id="test-001",
        title="Test Scenario",
        description="A test scenario",
        tags=["payment", "payout"],
        steps=[
            Step(root=PlaceOrderAction(
                product_id="PROD-001",
                payer_email="customer@example.com",
            )),
            Step(root=AdvanceStep(hours=24)),
            Step(root=CheckStep(invariant_ids=["INV-1", "INV-2"])),
        ],
    )

    # Serialize
    json_str = original.to_json()
    data = json.loads(json_str)

    # Deserialize
    restored = Scenario.from_json(json_str)

    assert restored.id == original.id
    assert restored.title == original.title
    assert len(restored.steps) == 3
    assert restored.steps[0].root.type == "place_order"
    assert restored.steps[1].root.type == "advance"
    assert restored.steps[2].root.type == "check"


def test_complex_scenario():
    """Test a realistic scenario combining multiple step types."""
    scenario = Scenario(
        id="bug-b1-capture-error",
        title="Authorization Expiry and Capture Error",
        description="Reproduce B1: capture fails after 29+ days, order wrongly marked shipped",
        tags=["B1", "capture", "authorization", "expiry"],
        steps=[
            Step(root=PlaceOrderAction(
                product_id="PROD-001",
                payer_email="customer@example.com",
            )),
            Step(root=ApproveOrderAction(order_id="ORDER-001")),
            Step(root=AdvanceStep(days=30)),  # Expire authorization (29d + 1d)
            Step(root=ShipOrderAction(order_id="ORDER-001")),  # Should fail, but buggy handler might not
            Step(root=CheckStep(invariant_ids=["INV-1"])),
        ],
    )
    assert scenario.id == "bug-b1-capture-error"
    assert "B1" in scenario.tags
    assert len(scenario.steps) == 5
