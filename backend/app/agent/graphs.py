"""LangGraph state graphs for Chronos agent workflows.

Implements:
1. ScenarioPlannerGraph: Generate adversarial scenarios
2. InvestigatorGraph: Run scenarios, diagnose failures, propose/verify patches
"""

import json
import logging
from typing import Any

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from app.agent.llm import get_fake_llm, get_llm
from app.agent.schemas import (
    FindingList,
    PatchProposal,
    ScenarioBatch,
)
from app.scenarios.dsl import Scenario as DslScenario

logger = logging.getLogger(__name__)


# ==================== Scenario Planner Graph ====================


class PlannerState(dict[str, Any]):
    """State for scenario planner graph."""

    pass


def planner_gather_context(state: PlannerState) -> PlannerState:
    """Gather context: rules, invariants, DSL schema, existing scenarios."""
    # TODO: Fetch from database or configuration
    state["context"] = {
        "rules": "AUTH-HONOR: 3 days, AUTH-VALID: 29 days, PAYOUT-UNCLAIMED: 30 days",
        "invariants": [
            "INV-1: No shipped without completed capture",
            "INV-2: Revenue ledger equals captures minus refunds",
            "INV-3: Creator paid only if payout success",
            "INV-4: Webhook idempotency",
            "INV-5: No invalid signature accepted",
            "INV-6: Fully refunded not paid",
            "INV-7: No swallowed errors",
        ],
        "existing_scenarios": ["happy-path", "bug-b1-capture-error"],
    }
    return state


def planner_propose(state: PlannerState, use_fake_llm: bool = False) -> PlannerState:
    """Propose scenarios using LLM structured output."""
    context = state.get("context", {})

    llm = get_fake_llm() if use_fake_llm else get_llm("primary")

    # Build prompt
    context_text = json.dumps(context, indent=2)
    prompt = f"""
Generate at most 8 adversarial scenarios for the Chronos payment twin.

Context:
{context_text}

Generate scenarios that:
1. Touch uncovered rules or invariants
2. Stress edge cases (time boundaries, state transitions, errors)
3. Stay within DSL bounds (max 40 steps, 400 days per advance, 2000 days total)
4. Avoid duplicating existing scenarios

Return only valid JSON as a ScenarioBatch object.
"""

    # TODO: Use with_structured_output for guaranteed structured output
    # For now, parse raw response
    response = llm.invoke(prompt)

    # Parse and validate
    try:
        if isinstance(response, str):
            batch_data = json.loads(response)
        else:
            batch_data = json.loads(response.content)

        batch = ScenarioBatch(**batch_data)
        state["batch"] = batch
        state["proposal_error"] = None
    except (json.JSONDecodeError, ValidationError) as e:
        logger.error(f"Failed to parse scenario batch: {e}")
        state["proposal_error"] = str(e)
        state["batch"] = None

    return state


def planner_validate(state: PlannerState) -> PlannerState:
    """Validate generated scenarios against DSL."""
    batch = state.get("batch")
    if not batch:
        state["validation_errors"] = [state.get("proposal_error", "Unknown error")]
        return state

    errors = []
    valid_scenarios = []

    for scenario in batch.scenarios:
        try:
            # Convert to DSL scenario for validation
            dsl_scenario = DslScenario(**scenario.model_dump())
            valid_scenarios.append(dsl_scenario)
        except ValidationError as e:
            errors.append(f"Scenario {scenario.id}: {e}")

    state["valid_scenarios"] = valid_scenarios
    state["validation_errors"] = errors

    if errors and len(errors) < len(batch.scenarios):
        state["repair_round"] = state.get("repair_round", 0) + 1
    elif errors:
        state["repair_round"] = state.get("repair_round", 0) + 1

    return state


def planner_should_repair(state: PlannerState) -> str:
    """Check if we should repair or stop."""
    repair_round = state.get("repair_round", 0)
    errors = state.get("validation_errors", [])

    if not errors:
        return "rank"  # All valid, proceed to ranking
    elif repair_round < 2:
        return "propose"  # Try to fix, max 2 rounds
    else:
        return "persist"  # Max retries, persist what we have


def planner_rank(state: PlannerState) -> PlannerState:
    """Rank scenarios by coverage and deduplicate."""
    valid_scenarios = state.get("valid_scenarios", [])

    # TODO: Implement ranking by invariant coverage and deduplication
    # For now, just use all valid scenarios
    state["ranked_scenarios"] = valid_scenarios

    return state


def planner_persist(state: PlannerState) -> PlannerState:
    """Persist ranked scenarios."""
    scenarios = state.get("ranked_scenarios", [])
    # TODO: Store in database
    state["persisted_count"] = len(scenarios)
    logger.info(f"Persisted {len(scenarios)} scenarios")
    return state


def build_planner_graph() -> StateGraph:
    """Build the scenario planner graph."""
    graph = StateGraph(PlannerState)

    # Add nodes
    graph.add_node("gather_context", planner_gather_context)
    graph.add_node("propose", planner_propose)
    graph.add_node("validate", planner_validate)
    graph.add_node("rank", planner_rank)
    graph.add_node("persist", planner_persist)

    # Add edges
    graph.add_edge(START, "gather_context")
    graph.add_edge("gather_context", "propose")
    graph.add_edge("propose", "validate")
    graph.add_conditional_edges(
        "validate",
        planner_should_repair,
        {
            "propose": "propose",
            "rank": "rank",
            "persist": "persist",
        },
    )
    graph.add_edge("rank", "persist")
    graph.add_edge("persist", END)

    return graph


# ==================== Investigator Graph ====================


class InvestigatorState(dict[str, Any]):
    """State for investigator graph."""

    pass


async def investigator_run(state: InvestigatorState) -> InvestigatorState:
    """Run the scenario and get invariant results."""
    scenario_id = state.get("scenario_id")
    # TODO: Actually run the scenario via scenario runner
    state["run_result"] = {
        "scenario_id": scenario_id,
        "passed": False,
        "failed_invariants": ["INV-1", "INV-7"],
    }
    return state


async def investigator_triage(state: InvestigatorState) -> InvestigatorState:
    """Check if all invariants passed."""
    run_result = state.get("run_result", {})
    state["is_clean"] = not run_result.get("failed_invariants", [])
    return state


def investigator_should_stop(state: InvestigatorState) -> str:
    """Check if we should stop (all passed) or diagnose."""
    if state.get("is_clean"):
        return "report"
    else:
        return "gather_evidence"


async def investigator_gather_evidence(state: InvestigatorState) -> InvestigatorState:
    """Gather evidence for failed invariants."""
    scenario_id = state.get("scenario_id")
    failed_invariants = state.get("run_result", {}).get("failed_invariants", [])

    # TODO: Fetch trace, extract event IDs, read source code
    state["evidence"] = {
        "scenario_id": scenario_id,
        "failed_invariants": failed_invariants,
        "events": [],
        "source_code": {},
    }
    return state


async def investigator_diagnose(state: InvestigatorState) -> InvestigatorState:
    """Diagnose root causes."""
    evidence = state.get("evidence", {})
    llm = get_llm("primary")

    # Build prompt from evidence
    prompt = f"""
DATA
Evidence: {json.dumps(evidence, indent=2)}
END DATA

Diagnose the root cause of each failed invariant. Return a FindingList object.
"""

    response = llm.invoke(prompt)

    try:
        if isinstance(response, str):
            findings_data = json.loads(response)
        else:
            findings_data = json.loads(response.content)

        findings = FindingList(**findings_data)
        state["findings"] = findings.findings
    except (json.JSONDecodeError, ValidationError) as e:
        logger.error(f"Failed to parse findings: {e}")
        state["findings"] = []
        state["diagnosis_error"] = str(e)

    return state


async def investigator_verify_findings(state: InvestigatorState) -> InvestigatorState:
    """Discard hallucinated findings without deterministic support."""
    findings = state.get("findings", [])

    # TODO: Verify each finding against evidence
    # For now, keep all
    state["verified_findings"] = findings

    return state


async def investigator_propose_patch(state: InvestigatorState) -> InvestigatorState:
    """Propose a patch for verified findings."""
    verified_findings = state.get("verified_findings", [])
    llm = get_llm("primary")

    if not verified_findings:
        state["patch"] = None
        return state

    prompt = f"""
DATA
Findings: {json.dumps([f.model_dump() for f in verified_findings], indent=2)}
END DATA

Propose a unified diff patch that fixes these findings.
Return a PatchProposal object with 'diff' and 'rationale' fields.
"""

    response = llm.invoke(prompt)

    try:
        if isinstance(response, str):
            patch_data = json.loads(response)
        else:
            patch_data = json.loads(response.content)

        patch = PatchProposal(**patch_data)
        state["patch"] = patch
    except (json.JSONDecodeError, ValidationError) as e:
        logger.error(f"Failed to parse patch proposal: {e}")
        state["patch"] = None
        state["patch_error"] = str(e)

    return state


async def investigator_apply_patch(state: InvestigatorState) -> InvestigatorState:
    """Apply patch to working copy."""
    patch = state.get("patch")
    if not patch:
        state["patch_applied"] = False
        return state

    # TODO: Apply diff to shop/work/handlers_agent_patched.py
    state["patch_applied"] = True
    return state


async def investigator_static_checks(state: InvestigatorState) -> InvestigatorState:
    """Run static checks on patched code."""
    patch_applied = state.get("patch_applied", False)
    if not patch_applied:
        state["static_checks_pass"] = False
        return state

    # TODO: ast.parse, ruff, forbidden imports check
    state["static_checks_pass"] = True
    return state


def investigator_should_reject_patch(state: InvestigatorState) -> str:
    """Check if patch should be rejected."""
    if not state.get("static_checks_pass", False):
        return "reject"
    else:
        return "rerun"


async def investigator_rerun(state: InvestigatorState) -> InvestigatorState:
    """Re-run scenario with patched handler."""
    # TODO: Re-run failing scenario, happy-path, and all bug scenarios
    state["verification"] = {
        "original_scenario_pass": True,
        "happy_path_pass": True,
        "regression_tests_pass": True,
    }

    return state


async def investigator_decide(state: InvestigatorState) -> InvestigatorState:
    """Decide if patch is verified."""
    verification = state.get("verification", {})
    retry_count = state.get("retry_count", 0)

    all_pass = all(
        verification.get(k, False)
        for k in ["original_scenario_pass", "happy_path_pass", "regression_tests_pass"]
    )

    if all_pass:
        state["decision"] = "verified_fix"
    elif retry_count < 2:
        state["retry_count"] = retry_count + 1
        state["decision"] = "retry"
    else:
        state["decision"] = "not_verified"

    return state


def investigator_should_retry(state: InvestigatorState) -> str:
    """Check if we should retry patch proposal."""
    decision = state.get("decision")
    if decision == "retry":
        return "propose_patch"
    else:
        return "report"


async def investigator_report(state: InvestigatorState) -> InvestigatorState:
    """Generate final report."""
    state["final_status"] = state.get("decision", "unknown")
    return state


def build_investigator_graph() -> StateGraph:
    """Build the investigator graph."""
    graph = StateGraph(InvestigatorState)

    # Add nodes (async)
    graph.add_node("run", investigator_run)
    graph.add_node("triage", investigator_triage)
    graph.add_node("gather_evidence", investigator_gather_evidence)
    graph.add_node("diagnose", investigator_diagnose)
    graph.add_node("verify_findings", investigator_verify_findings)
    graph.add_node("propose_patch", investigator_propose_patch)
    graph.add_node("apply_patch", investigator_apply_patch)
    graph.add_node("static_checks", investigator_static_checks)
    graph.add_node("rerun", investigator_rerun)
    graph.add_node("decide", investigator_decide)
    graph.add_node("report", investigator_report)

    # Add edges
    graph.add_edge(START, "run")
    graph.add_edge("run", "triage")
    graph.add_conditional_edges(
        "triage",
        investigator_should_stop,
        {
            "report": "report",
            "gather_evidence": "gather_evidence",
        },
    )
    graph.add_edge("gather_evidence", "diagnose")
    graph.add_edge("diagnose", "verify_findings")
    graph.add_edge("verify_findings", "propose_patch")
    graph.add_edge("propose_patch", "apply_patch")
    graph.add_edge("apply_patch", "static_checks")
    graph.add_conditional_edges(
        "static_checks",
        investigator_should_reject_patch,
        {
            "reject": "propose_patch",
            "rerun": "rerun",
        },
    )
    graph.add_edge("rerun", "decide")
    graph.add_conditional_edges(
        "decide",
        investigator_should_retry,
        {
            "propose_patch": "propose_patch",
            "report": "report",
        },
    )
    graph.add_edge("report", END)

    return graph
