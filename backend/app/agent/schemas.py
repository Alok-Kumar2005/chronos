"""Pydantic schemas for structured agent outputs.

These are used for LLM structured output (via with_structured_output),
ensuring all agent proposals are valid before execution.
"""

from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# Scenario generation
class PlaceOrderAction(BaseModel):
    """Place an order for a product."""

    type: Literal["place_order"] = "place_order"
    product_id: str = Field(min_length=1)
    payer_email: str = Field(min_length=5)
    quantity: int = Field(default=1, ge=1, le=1000)


class ApproveOrderAction(BaseModel):
    """Approve a pending order."""

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
    """Claim unclaimed payout."""

    type: Literal["claim_payout"] = "claim_payout"
    recipient_email: str = Field(min_length=5)


class AdvanceStep(BaseModel):
    """Advance virtual time."""

    type: Literal["advance"] = "advance"
    hours: int = Field(default=0, ge=0, le=400)
    days: int = Field(default=0, ge=0, le=400)


class SetChaosStep(BaseModel):
    """Inject chaos configuration."""

    type: Literal["set_chaos"] = "set_chaos"
    webhook_delay_ms: int = Field(default=0, ge=0, le=30000)
    webhook_failures_rate: float = Field(default=0.0, ge=0.0, le=1.0)


class SetHandlerVariantStep(BaseModel):
    """Switch handler implementation."""

    type: Literal["set_handler_variant"] = "set_handler_variant"
    variant: Literal["buggy", "reference_fix", "agent_patched"]


class CheckStep(BaseModel):
    """Check invariants."""

    type: Literal["check"] = "check"
    invariant_ids: list[str] = Field(
        default=["INV-1", "INV-2", "INV-3", "INV-4", "INV-5", "INV-6", "INV-7"]
    )


class SnapshotStep(BaseModel):
    """Mark a point in the trace."""

    type: Literal["snapshot"] = "snapshot"
    label: str = Field(min_length=1)


StepType = (
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
    """A single step in a scenario."""

    root: StepType = Field(discriminator="type")


class Scenario(BaseModel):
    """A deterministic scenario."""

    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    steps: list[Step] = Field(min_length=1, max_length=40)


class ScenarioBatch(BaseModel):
    """A batch of generated scenarios."""

    scenarios: list[Scenario] = Field(min_length=1, max_length=8)


# Investigation findings
class Severity(str, Enum):
    """Finding severity level."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Finding(BaseModel):
    """A diagnosed bug finding."""

    invariant_id: str = Field(description="Which invariant failed (e.g., INV-1)")
    root_cause: str = Field(
        min_length=10, max_length=500, description="Plain English explanation"
    )
    evidence_event_ids: list[int] = Field(
        description="Event IDs from trace that prove the issue"
    )
    file: str = Field(
        description="Handler file name (e.g., shop/handlers_buggy.py)"
    )
    line_start: int = Field(ge=1, description="Start line of problematic code")
    line_end: int = Field(ge=1, description="End line of problematic code")
    severity: Severity = Field(description="Severity level")
    plain_explanation: str = Field(
        min_length=20, max_length=300, description="Summary for non-technical users"
    )


class FindingList(BaseModel):
    """List of findings from investigation."""

    findings: list[Finding] = Field(min_length=1)


# Patch verification
class VerificationStatus(str, Enum):
    """Patch verification status."""

    VERIFIED_FIX = "verified_fix"
    NOT_VERIFIED = "not_verified"
    REQUIRES_RETRY = "requires_retry"


class VerificationResult(BaseModel):
    """Result of patch verification."""

    status: VerificationStatus
    original_scenario_pass: bool
    happy_path_pass: bool
    regression_tests_pass: bool
    verified_invariants: list[str] = Field(
        default_factory=list, description="Invariants now passing"
    )
    notes: str = Field(default="", description="Human-readable notes")
    failed_invariants: list[str] = Field(
        default_factory=list, description="Invariants still failing (for retry)"
    )


class PatchProposal(BaseModel):
    """Proposed unified diff patch."""

    diff: str = Field(
        min_length=10, description="Unified diff (git format), can be multi-line"
    )
    rationale: str = Field(
        min_length=20, max_length=500, description="Why this patch fixes the issue"
    )


# Agent state types
class AgentRunContext(BaseModel):
    """Context for an agent run."""

    universe_id: str
    scenario_id: str
    handler_variant: Literal["buggy", "reference_fix", "agent_patched"]
    run_number: int = Field(ge=1)
    timestamp: str = Field(description="ISO 8601 UTC timestamp")


class AgentRunRecord(BaseModel):
    """Record of an agent run (persisted)."""

    id: str = Field(description="Unique run ID")
    context: AgentRunContext
    findings: list[Finding] = Field(default_factory=list)
    patch: PatchProposal | None = None
    verification: VerificationResult | None = None
    status: Literal["pending", "in_progress", "completed", "failed"]
    error: str | None = None
