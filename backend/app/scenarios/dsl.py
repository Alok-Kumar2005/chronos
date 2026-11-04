"""Scenario DSL: Pydantic models for deterministic scenario definition and validation.

Scenarios are time-travel replays with strict bounds:
- Step types: shop actions, advance, set_chaos, set_handler_variant, check, snapshot
- Each step is a discriminated union with type validation
- All advances bounded: max 400 days per step, 2000 days per scenario
- Max 40 steps per scenario
"""

from decimal import Decimal
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ChaosConfig(BaseModel):
    """Configuration for chaos injection in the twin."""

    webhook_delay_ms: int = Field(default=0, ge=0, le=30000)
    webhook_failures_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    webhook_reorder: bool = Field(default=False)
    webhook_duplicate_rate: float = Field(default=0.0, ge=0.0, le=0.5)
    signature_tamper_rate: float = Field(default=0.0, ge=0.0, le=1.0)


class HandlerVariant(str, Enum):
    """Handler implementation to use."""

    BUGGY = "buggy"
    REFERENCE_FIX = "reference_fix"
    AGENT_PATCHED = "agent_patched"


# Shop action discriminators
class PlaceOrderAction(BaseModel):
    """Place an order for a product."""

    type: Literal["place_order"] = "place_order"
    product_id: str = Field(min_length=1)
    payer_email: str = Field(min_length=5)
    quantity: int = Field(default=1, ge=1, le=1000)


class ApproveOrderAction(BaseModel):
    """Approve a pending order (PayPal payer approval)."""

    type: Literal["approve_order"] = "approve_order"
    order_id: str = Field(min_length=1)


class ShipOrderAction(BaseModel):
    """Ship an order (capture funds)."""

    type: Literal["ship_order"] = "ship_order"
    order_id: str = Field(min_length=1)


class RefundOrderAction(BaseModel):
    """Refund a shipped order."""

    type: Literal["refund_order"] = "refund_order"
    order_id: str = Field(min_length=1)
    amount: Decimal = Field(decimal_places=2, ge=Decimal("0.01"))


class RunPayoutAction(BaseModel):
    """Run the weekly payout process."""

    type: Literal["run_payout"] = "run_payout"


class ClaimPayoutAction(BaseModel):
    """Claim unclaimed payout (recipient registration + claiming)."""

    type: Literal["claim_payout"] = "claim_payout"
    recipient_email: str = Field(min_length=5)


ShopActionUnion = (
    PlaceOrderAction
    | ApproveOrderAction
    | ShipOrderAction
    | RefundOrderAction
    | RunPayoutAction
    | ClaimPayoutAction
)


class AdvanceStep(BaseModel):
    """Advance virtual time."""

    type: Literal["advance"] = "advance"
    hours: int = Field(default=0, ge=0)
    days: int = Field(default=0, ge=0)

    @field_validator("hours", "days")
    @classmethod
    def check_bounds(cls, v: int) -> int:
        if v > 400:
            raise ValueError(f"Cannot advance more than 400 in single step: {v}")
        return v

    @model_validator(mode="after")
    def check_total_advance(self) -> "AdvanceStep":
        total_hours = self.hours + (self.days * 24)
        if total_hours > 400 * 24:  # 400 days worth of hours
            raise ValueError(
                f"Total advance exceeds 400 days: {self.days}d {self.hours}h"
            )
        # Ensure at least one is non-zero if advance is specified
        if self.hours == 0 and self.days == 0:
            raise ValueError("Advance must specify either hours or days")
        return self


class SetChaosStep(BaseModel):
    """Inject chaos configuration."""

    type: Literal["set_chaos"] = "set_chaos"
    chaos: ChaosConfig = Field(default_factory=ChaosConfig)


class SetHandlerVariantStep(BaseModel):
    """Switch handler implementation."""

    type: Literal["set_handler_variant"] = "set_handler_variant"
    variant: HandlerVariant


class CheckStep(BaseModel):
    """Check invariants."""

    type: Literal["check"] = "check"
    invariant_ids: list[str] = Field(default_factory=lambda: ["INV-1", "INV-2", "INV-3", "INV-4", "INV-5", "INV-6", "INV-7"])


class SnapshotStep(BaseModel):
    """Mark a point in the trace for reference."""

    type: Literal["snapshot"] = "snapshot"
    label: str = Field(min_length=1)


# Discriminated union for all step types
StepUnion = (
    PlaceOrderAction
    | ApproveOrderAction
    | ShipOrderAction
    | RefundOrderAction
    | RunPayoutAction
    | ClaimPayoutAction
    | AdvanceStep
    | SetChaosStep
    | SetHandlerVariantStep
    | CheckStep
    | SnapshotStep
)


class Step(BaseModel):
    """A single step in a scenario, using discriminated union."""

    root: StepUnion = Field(discriminator="type")

    def __getattr__(self, name: str) -> Any:
        """Delegate attribute access to the underlying step."""
        return getattr(self.root, name)


class Scenario(BaseModel):
    """A deterministic scenario for replay testing."""

    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    steps: list[Step] = Field(min_length=1, max_length=40)

    @field_validator("steps")
    @classmethod
    def check_total_advance(cls, steps: list[Step]) -> list[Step]:
        """Verify total advance across all steps does not exceed 2000 days."""
        total_hours = 0
        for step in steps:
            if step.root.type == "advance":
                advance_step = step.root
                total_hours += advance_step.hours + (advance_step.days * 24)

        if total_hours > 2000 * 24:  # 2000 days
            raise ValueError(
                f"Total scenario advance exceeds 2000 days: {total_hours // 24}d"
            )
        return steps

    @field_validator("tags", mode="before")
    @classmethod
    def deduplicate_tags(cls, v: list[str]) -> list[str]:
        """Remove duplicate tags."""
        return list(set(v)) if v else []

    def to_json_schema(self) -> dict[str, Any]:
        """Export JSON Schema for this scenario."""
        return self.model_json_schema()

    def to_json(self) -> str:
        """Serialize scenario to JSON."""
        return self.model_dump_json(indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> "Scenario":
        """Deserialize scenario from JSON."""
        return cls.model_validate_json(json_str)


class ScenarioLibrary(BaseModel):
    """Collection of scenarios with metadata."""

    scenarios: list[Scenario] = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_json_schema(self) -> dict[str, Any]:
        """Export JSON Schema."""
        return self.model_json_schema()
