"""Tests for Ask Chronos copilot with LangGraph persistence."""

import pytest

from app.agent.copilot import CopilotState, build_copilot_graph
from app.langgraph_setup import LangGraphPool


class TestLangGraphPool:
    """Test LangGraph pool initialization and persistence."""

    @pytest.mark.skip(reason="Requires live Postgres, use in manual testing")
    async def test_pool_initialization(self):
        """Test pool can initialize and close cleanly."""
        pool = LangGraphPool()
        assert pool.pool is None
        assert pool.checkpointer is None
        assert pool.store is None

        # Initialize
        await pool.initialize()
        assert pool.pool is not None
        assert pool.checkpointer is not None
        assert pool.store is not None

        # Close
        await pool.close()
        assert pool.pool is None

    async def test_copilot_state_structure(self):
        """Test CopilotState has required fields."""
        state: CopilotState = {
            "messages": [],
            "summary": "",
            "tool_calls_this_turn": 0,
            "interrupt_pending": None,
            "cross_thread_facts": [],
        }
        assert state["messages"] == []
        assert state["summary"] == ""
        assert state["tool_calls_this_turn"] == 0


class TestCopilotGraph:
    """Test copilot graph structure."""

    @pytest.mark.skip(reason="Requires live LangGraph pool")
    async def test_build_copilot_graph(self):
        """Test copilot graph builds successfully."""
        pool = LangGraphPool()
        await pool.initialize()

        try:
            graph = build_copilot_graph(pool.checkpointer, pool.store)
            assert graph is not None
            # Graph should have nodes and edges
            assert hasattr(graph, 'invoke')
            assert hasattr(graph, 'stream')
        finally:
            await pool.close()
