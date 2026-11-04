"""Ask Chronos copilot graph for chat.

Implements a multi-turn conversation agent that:
- Maintains chat history in LangGraph checkpointer
- Answers questions about runs and findings
- Operates the universe (advance_clock, run_scenarios, reset_universe) with interrupt approval
- Stores cross-thread facts for context continuity
"""

import logging
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import BaseMessage
from langchain_core.tools import tool
from langgraph.graph import StateGraph, add_messages

logger = logging.getLogger(__name__)


# State for the copilot conversation
class CopilotState(TypedDict):
    """State for the copilot graph."""

    messages: Annotated[list[BaseMessage], add_messages]
    """Chat history, automatically merged/deduplicated by add_messages."""

    summary: str
    """Accumulated summary of older messages when context window is exceeded."""

    tool_calls_this_turn: int
    """Count of tool calls in this turn (capped at 6)."""

    interrupt_pending: dict[str, Any] | None
    """Pending tool call awaiting user approval via interrupt."""

    cross_thread_facts: list[str]
    """Facts stored and retrieved across threads in the store."""


class CopilotToolDefs:
    """Read-only and state-changing tools for the copilot."""

    # Read-only tools (no interrupt needed)

    @staticmethod
    @tool
    def list_runs(universe_id: str) -> list[dict[str, Any]]:
        """List all scenario runs in this universe.

        Args:
            universe_id: The universe ID (injected, not from model)

        Returns:
            List of run summaries with id, status, run_at, findings_count
        """
        # TODO: Query EventLog for run records from universe
        return [
            {
                "id": "run-001",
                "status": "completed",
                "scenario": "happy-path",
                "run_at": "2026-11-04T10:00:00Z",
                "findings": 0,
            }
        ]

    @staticmethod
    @tool
    def get_run_summary(universe_id: str, run_id: str) -> dict[str, Any]:
        """Get detailed summary of a specific run.

        Args:
            universe_id: The universe ID (injected, not from model)
            run_id: The run ID to summarize

        Returns:
            Run details including timeline, assertions passed/failed, findings
        """
        # TODO: Query scenario runner result and trace logs
        return {
            "id": run_id,
            "scenario": "happy-path",
            "duration_ms": 5000,
            "assertions": {"passed": 5, "failed": 0},
            "findings": [],
        }

    @staticmethod
    @tool
    def get_trace_slice(
        universe_id: str, run_id: str, step: int | None = None
    ) -> dict[str, Any]:
        """Get a slice of the trace from a run.

        Args:
            universe_id: The universe ID (injected, not from model)
            run_id: The run ID
            step: Optional specific step number (0-indexed); if omitted, returns summary

        Returns:
            Trace events: webhook calls, database operations, time advances, etc.
        """
        # TODO: Query EventLog for trace records
        return {
            "run_id": run_id,
            "step": step,
            "events": [
                {"type": "webhook", "url": "https://example.com/webhook", "status": 200},
            ],
        }

    @staticmethod
    @tool
    def get_events(
        universe_id: str, event_type: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Get recent events from the universe.

        Args:
            universe_id: The universe ID (injected, not from model)
            event_type: Optional filter (e.g., 'PAYMENT.CAPTURE.COMPLETED')
            limit: Max events to return

        Returns:
            Recent events in chronological order
        """
        # TODO: Query EventLog
        return []

    @staticmethod
    @tool
    def get_findings(
        universe_id: str, run_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Get findings and diagnoses from recent runs.

        Args:
            universe_id: The universe ID (injected, not from model)
            run_id: Optional specific run; if omitted, returns cross-run findings

        Returns:
            List of findings with severity, rule_id, trace evidence
        """
        # TODO: Query agent outputs
        return []

    @staticmethod
    @tool
    def explain_rule(rule_id: str) -> dict[str, Any]:
        """Explain a rule from the catalogue.

        Args:
            rule_id: Rule ID (e.g., 'AUTH-HONOR')

        Returns:
            Rule details: summary, parameters, source URL, confidence, notes
        """
        # TODO: Load from rules.yaml
        return {
            "id": rule_id,
            "summary": "Authorization honor period",
            "details": "Authorizations have a 3-day honor period during which reauthorization is allowed.",
            "source_url": "https://developer.paypal.com/...",
            "confidence": "documented",
        }

    @staticmethod
    @tool
    def remember_fact(universe_id: str, text: str) -> dict[str, str]:
        """Store a fact for cross-thread retrieval.

        Use this for things the user tells you that should persist:
        e.g., 'I'm testing the buggy handler', 'My webhook URL is https://...'

        Args:
            universe_id: The universe ID (injected, not from model)
            text: The fact to remember (e.g., 'webhook_url: https://example.com')

        Returns:
            Confirmation with fact_id
        """
        # TODO: Store in langgraph_store with key (universe_id, 'facts')
        return {"status": "remembered", "fact_id": "fact-001"}

    @staticmethod
    @tool
    def recall_facts(universe_id: str) -> list[str]:
        """Retrieve facts stored in previous conversations.

        Returns:
            List of remembered facts (e.g., webhook URLs, handler variants)
        """
        # TODO: Query from langgraph_store
        return ["webhook_url: https://example.com", "handler_variant: buggy"]

    # State-changing tools (guarded by interrupt)

    @staticmethod
    @tool
    def advance_clock(universe_id: str, days: int = 0, hours: int = 0) -> dict[str, Any]:
        """Advance the universe's virtual clock.

        Requires user approval. Max 60 days per call.

        Args:
            universe_id: The universe ID (injected, not from model)
            days: Days to advance (0-60)
            hours: Hours to advance (0-24)

        Returns:
            New virtual_now, scheduled events processed
        """
        # TODO: Inject interrupt before executing
        # TODO: Call SchedulerContext.advance()
        return {"virtual_now": "2026-11-04T10:00:00Z", "events_processed": 5}

    @staticmethod
    @tool
    def run_scenario(universe_id: str, scenario_id: str) -> dict[str, Any]:
        """Run a scenario and collect findings.

        Requires user approval. Scenario ID can be from library or generated.

        Args:
            universe_id: The universe ID (injected, not from model)
            scenario_id: Scenario ID (e.g., 'happy-path' or 'B1-capture-error')

        Returns:
            Run ID, status, findings
        """
        # TODO: Inject interrupt before executing
        # TODO: Call scenario runner
        return {"run_id": "run-001", "status": "completed", "findings": []}

    @staticmethod
    @tool
    def reset_universe(universe_id: str) -> dict[str, str]:
        """Reset the universe to its initial state (virtual_now, all data).

        Requires user approval. This is destructive and cannot be undone.

        Args:
            universe_id: The universe ID (injected, not from model)

        Returns:
            Confirmation
        """
        # TODO: Inject interrupt before executing
        # TODO: Clear all universe data and reset virtual_now
        return {"status": "reset", "virtual_now": "2026-11-04T10:00:00Z"}


def copilot_agent(state: CopilotState) -> CopilotState:
    """Primary copilot agent node: call Groq with tools."""
    # TODO: Inject cross-thread facts from store into system prompt
    # TODO: Trim messages to stay under context window (using trim_messages)
    # TODO: Call get_llm('copilot').invoke() with tools bound
    # TODO: If model returns tool calls, increment tool_calls_this_turn
    # TODO: If tool call is state-changing, set interrupt_pending and return
    # TODO: Otherwise, return with assistant message

    logger.info("copilot_agent called", extra={"tool_calls_this_turn": state.get("tool_calls_this_turn", 0)})
    return state


def copilot_tools(state: CopilotState) -> CopilotState:
    """Tool execution node: run non-state-changing tools."""
    # TODO: Use ToolNode to execute tools
    # TODO: Filter out state-changing tools (advance_clock, run_scenario, reset_universe)
    # TODO: Return tool results as ToolMessage
    logger.info("copilot_tools called")
    return state


def copilot_summarise(state: CopilotState) -> CopilotState:
    """Summarise old messages when context exceeds budget."""
    # TODO: If messages + summary exceed context budget:
    #   - Extract messages before oldest recent N (e.g., last 10)
    #   - Call LLM to summarise them into summary field
    #   - Return RemoveMessage for summarised messages
    # TODO: Always use trim_messages before model calls to enforce limit
    logger.info("copilot_summarise called", extra={"summary_len": len(state.get("summary", ""))})
    return state


def should_route_to_tools(state: CopilotState) -> Literal["tools", "summarise", "__end__"]:
    """Route after agent: to tools, summarise, or end."""
    # TODO: Check if last message is ToolCalls (route to tools)
    # TODO: Check if context budget exceeded (route to summarise)
    # TODO: Otherwise, end (send response to user)
    return "__end__"


def should_loop_to_agent(state: CopilotState) -> Literal["agent", "__end__"]:
    """Route after tools: back to agent or end."""
    # TODO: If tool_calls_this_turn >= 6, end (cap per turn)
    # TODO: Otherwise, loop back to agent for follow-up
    return "__end__"


def build_copilot_graph(
    checkpointer, store
):
    """Build the copilot StateGraph.

    Args:
        checkpointer: AsyncPostgresSaver for checkpointing
        store: AsyncPostgresStore for cross-thread facts

    Returns:
        Compiled StateGraph with checkpointer and store
    """
    builder = StateGraph(CopilotState)

    # Add nodes
    builder.add_node("agent", copilot_agent)
    builder.add_node("tools", copilot_tools)
    builder.add_node("summarise", copilot_summarise)

    # Add edges
    builder.add_edge("__start__", "agent")
    builder.add_conditional_edges("agent", should_route_to_tools)
    builder.add_conditional_edges("tools", should_loop_to_agent)
    builder.add_edge("summarise", "agent")

    # Compile with persistence
    graph = builder.compile(
        checkpointer=checkpointer,
        store=store,
    )

    logger.info("Copilot graph built and compiled")
    return graph
