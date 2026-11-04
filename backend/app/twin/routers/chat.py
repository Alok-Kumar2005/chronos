"""Ask Chronos chat API endpoints."""

import logging
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import ChatThread, Universe
from app.twin.dependencies import get_universe_from_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/_chronos/api/universes/{universe_id}/chat", tags=["Chat"])


@router.post(
    "/threads",
    status_code=status.HTTP_201_CREATED,
    summary="Create a new chat thread",
)
async def create_chat_thread(
    universe_id: str,
    title: str = Query(..., min_length=1, max_length=255),
    session: AsyncSession = Depends(get_session),
    universe: Universe = Depends(get_universe_from_id),
) -> dict[str, Any]:
    """Create a new chat thread in this universe.

    Args:
        universe_id: Universe ID from path
        title: Thread title
        session: Database session
        universe: Universe dependency (verifies access)

    Returns:
        Chat thread details with thread ID
    """
    # Generate a unique chat_id (32 chars)
    chat_id = secrets.token_urlsafe(24)[:32]

    # Thread ID is f'{universe_id}:{chat_id}'
    thread_id = f"{universe_id}:{chat_id}"

    thread = ChatThread(
        id=thread_id,
        universe_id=universe.id,
        chat_id=chat_id,
        title=title,
    )

    session.add(thread)
    try:
        await session.commit()
    except Exception as e:
        logger.error("Failed to create chat thread", exc_info=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create thread",
        )

    logger.info(
        "Chat thread created",
        extra={"universe_id": universe_id, "thread_id": thread_id},
    )

    return {
        "thread_id": thread_id,
        "chat_id": chat_id,
        "universe_id": universe_id,
        "title": title,
        "created_at": thread.created_at.isoformat() + "Z",
    }


@router.get(
    "/threads",
    summary="List chat threads",
)
async def list_chat_threads(
    universe_id: str,
    session: AsyncSession = Depends(get_session),
    universe: Universe = Depends(get_universe_from_id),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """List all chat threads in this universe.

    Args:
        universe_id: Universe ID from path
        session: Database session
        universe: Universe dependency (verifies access)
        limit: Max threads to return
        offset: Offset for pagination

    Returns:
        List of thread summaries
    """
    # Query threads for this universe
    result = await session.execute(
        select(ChatThread)
        .where(ChatThread.universe_id == universe.id)
        .order_by(ChatThread.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )

    threads = result.scalars().all()

    return {
        "threads": [
            {
                "thread_id": t.id,
                "chat_id": t.chat_id,
                "title": t.title,
                "created_at": t.created_at.isoformat() + "Z",
                "updated_at": t.updated_at.isoformat() + "Z",
            }
            for t in threads
        ],
        "limit": limit,
        "offset": offset,
    }


@router.get(
    "/threads/{chat_id}/messages",
    summary="Get chat history",
)
async def get_chat_messages(
    universe_id: str,
    chat_id: str,
    session: AsyncSession = Depends(get_session),
    universe: Universe = Depends(get_universe_from_id),
) -> dict[str, Any]:
    """Get message history for a chat thread.

    Rebuilds history from LangGraph checkpointer state.

    Args:
        universe_id: Universe ID from path
        chat_id: Chat ID
        session: Database session
        universe: Universe dependency (verifies access)

    Returns:
        List of messages in chronological order
    """
    thread_id = f"{universe_id}:{chat_id}"

    # Verify thread exists and belongs to this universe
    result = await session.execute(
        select(ChatThread).where(
            (ChatThread.id == thread_id) & (ChatThread.universe_id == universe.id)
        )
    )
    thread = result.scalar_one_or_none()

    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat thread not found",
        )

    # TODO: Query langgraph checkpointer to get state for thread_id
    # TODO: Rebuild message history from state["messages"]
    # TODO: Return as list of {role, content, timestamp} objects

    return {
        "thread_id": thread_id,
        "messages": [],  # Placeholder
    }


@router.post(
    "/threads/{chat_id}/messages",
    summary="Send message and get streaming response",
)
async def send_chat_message(
    universe_id: str,
    chat_id: str,
    message: str = Query(..., min_length=1, max_length=10000),
    session: AsyncSession = Depends(get_session),
    universe: Universe = Depends(get_universe_from_id),
):
    """Send a message to the copilot and get a streaming response.

    Returns SSE stream with:
    - token deltas (text chunks)
    - tool_call events (with action description)
    - interrupt events (pending action awaiting approval)
    - tool_result events (result after approval)
    - final event (conversation complete)

    Args:
        universe_id: Universe ID from path
        chat_id: Chat ID
        message: User message
        session: Database session
        universe: Universe dependency (verifies access)

    Returns:
        SSE streaming response
    """
    thread_id = f"{universe_id}:{chat_id}"

    # Verify thread exists
    result = await session.execute(
        select(ChatThread).where(
            (ChatThread.id == thread_id) & (ChatThread.universe_id == universe.id)
        )
    )
    thread = result.scalar_one_or_none()

    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat thread not found",
        )

    async def event_generator():
        """Generate SSE events from copilot graph."""
        # TODO: Get copilot graph from app.state.langgraph_pool
        # TODO: Call graph.stream() or graph.astream() with:
        #   - input: {"messages": [HumanMessage(content=message)]}
        #   - config: {"configurable": {"thread_id": thread_id}}
        # TODO: For each chunk in stream:
        #   - If content delta: yield "data: {\"type\": \"token\", \"content\": \"...\"}\n\n"
        #   - If tool_calls: yield interrupt event with pending action
        #   - If tool_results: yield tool_result event
        # TODO: On completion, yield final event with summary

        # Placeholder: yield a simple token stream
        yield 'data: {"type": "token", "content": "Hello from copilot!"}\n\n'
        yield 'data: {"type": "final"}\n\n'

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@router.post(
    "/threads/{chat_id}/resume",
    summary="Approve or reject pending tool execution",
)
async def resume_after_interrupt(
    universe_id: str,
    chat_id: str,
    approve: bool = Query(...),
    session: AsyncSession = Depends(get_session),
    universe: Universe = Depends(get_universe_from_id),
) -> dict[str, Any]:
    """Resume the copilot after approving or rejecting a pending tool call.

    Args:
        universe_id: Universe ID from path
        chat_id: Chat ID
        approve: True to approve, False to reject
        session: Database session
        universe: Universe dependency (verifies access)

    Returns:
        Status confirmation
    """
    thread_id = f"{universe_id}:{chat_id}"

    # Verify thread exists
    result = await session.execute(
        select(ChatThread).where(
            (ChatThread.id == thread_id) & (ChatThread.universe_id == universe.id)
        )
    )
    thread = result.scalar_one_or_none()

    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat thread not found",
        )

    # TODO: Get LangGraph checkpointer
    # TODO: Call checkpointer.get_state({"configurable": {"thread_id": thread_id}})
    # TODO: If not interrupt_pending, return error
    # TODO: If approve:
    #   - Call checkpointer.put_state(..., metadata={"step": "tool_execution"})
    #   - Graph resumes and executes tool
    # TODO: If not approve:
    #   - Pop the interrupt_pending
    #   - Continue without executing tool

    return {
        "status": "resumed",
        "approved": approve,
    }


@router.delete(
    "/threads/{chat_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a chat thread",
)
async def delete_chat_thread(
    universe_id: str,
    chat_id: str,
    session: AsyncSession = Depends(get_session),
    universe: Universe = Depends(get_universe_from_id),
) -> None:
    """Delete a chat thread and all its LangGraph checkpoints.

    Args:
        universe_id: Universe ID from path
        chat_id: Chat ID
        session: Database session
        universe: Universe dependency (verifies access)
    """
    thread_id = f"{universe_id}:{chat_id}"

    # Verify thread exists
    result = await session.execute(
        select(ChatThread).where(
            (ChatThread.id == thread_id) & (ChatThread.universe_id == universe.id)
        )
    )
    thread = result.scalar_one_or_none()

    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat thread not found",
        )

    # TODO: Delete from LangGraph checkpointer
    # TODO: Call langgraph_store to delete facts for this thread

    # Delete the thread record
    await session.delete(thread)
    await session.commit()

    logger.info(
        "Chat thread deleted",
        extra={"universe_id": universe_id, "thread_id": thread_id},
    )
