"""LLM integration for Chronos agents.

Provides Groq-based ChatLLM with:
- Model validation at startup against Groq model list
- Automatic fallback to available models if configured model is retired
- Retry with exponential backoff on 429 and 5xx errors
- Token usage tracking and logging
- Global concurrency limit (2 concurrent calls)
- FakeLLM test double for deterministic testing
"""

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

from langchain_core.language_models import BaseLLM
from langchain_core.outputs import Generation, LLMResult
from langchain_groq import ChatGroq
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings

logger = logging.getLogger(__name__)

# Global concurrency semaphore (lazy initialized)
_CONCURRENCY_LIMIT: asyncio.Semaphore | None = None

def _get_concurrency_limit() -> asyncio.Semaphore:
    """Get or create the concurrency semaphore."""
    global _CONCURRENCY_LIMIT
    if _CONCURRENCY_LIMIT is None:
        try:
            _CONCURRENCY_LIMIT = asyncio.Semaphore(2)
        except RuntimeError:
            # No event loop yet; create one temporarily
            loop = asyncio.new_event_loop()
            _CONCURRENCY_LIMIT = asyncio.Semaphore(2)
            asyncio.set_event_loop(loop)
    return _CONCURRENCY_LIMIT

# Cache for validated models
_VALIDATED_MODELS: dict[str, str] = {}

# FakeLLM for testing
_FAKE_LLM_RESPONSES: list[str] = []
_FAKE_LLM_RESPONSE_INDEX = 0


class GroqModelError(Exception):
    """Raised when Groq model validation or initialization fails."""

    pass


async def get_groq_models() -> list[dict[str, Any]]:
    """
    Fetch available models from Groq API.

    Returns:
        List of model metadata dicts with 'id' and 'owned_by' fields.
    """
    from groq import Groq

    try:
        client = Groq(api_key=settings.GROQ_API_KEY)
        models = client.models.list()
        return [
            {"id": m.id, "owned_by": m.owned_by, "active": True}
            for m in models.data
        ]
    except Exception as e:
        logger.error(f"Failed to fetch Groq models: {e}")
        raise GroqModelError(f"Cannot fetch Groq model list: {e}") from e


async def validate_model_exists(
    model_name: str, available_models: list[dict[str, Any]]
) -> str | None:
    """
    Check if model exists in available models; return it or closest alternative.

    Args:
        model_name: Configured model name
        available_models: List of models from Groq

    Returns:
        Model name to use (may differ from input if configured model is retired)
    """
    if not available_models:
        return model_name  # Can't validate without list

    model_ids = {m["id"] for m in available_models}

    if model_name in model_ids:
        logger.info(f"Model {model_name} is available")
        return model_name

    # Model not available; find closest match
    logger.warning(
        f"Configured model '{model_name}' not found in Groq model list. "
        f"Available models: {sorted(model_ids)}"
    )

    # Heuristics: prefer same family (e.g., llama-3 variants)
    family = model_name.split("-")[0] if "-" in model_name else None
    candidates = [m for m in model_ids if family and m.startswith(family)]

    if candidates:
        chosen = sorted(candidates)[-1]  # Prefer newest version
        logger.warning(f"Falling back to {chosen}")
        return chosen

    # Last resort: use first available model
    chosen = sorted(model_ids)[0]
    logger.warning(f"Falling back to {chosen}")
    return chosen


async def validate_and_pick_models() -> tuple[str, str]:
    """
    Validate configured models exist, pick fallbacks if needed.

    Returns:
        Tuple of (primary_model, fast_model) to use for this session.
    """
    try:
        models = await get_groq_models()
        primary = await validate_model_exists(settings.GROQ_MODEL_PRIMARY, models)
        fast = await validate_model_exists(settings.GROQ_MODEL_FAST, models)
        return primary, fast
    except Exception as e:
        logger.error(f"Model validation failed: {e}")
        # Fall back to configured names and let Groq client handle errors
        return settings.GROQ_MODEL_PRIMARY, settings.GROQ_MODEL_FAST


def get_llm(role: str = "primary") -> ChatGroq:
    """
    Get a ChatGroq instance configured for the given role.

    Args:
        role: "primary" (complex reasoning) or "fast" (simple tasks)

    Returns:
        Configured ChatGroq instance with retry and token tracking.

    Raises:
        GroqModelError: If Groq API key is missing or model initialization fails.
    """
    if not settings.GROQ_API_KEY:
        raise GroqModelError("GROQ_API_KEY not configured")

    model_name = (
        settings.GROQ_MODEL_PRIMARY if role == "primary" else settings.GROQ_MODEL_FAST
    )

    llm = ChatGroq(
        api_key=settings.GROQ_API_KEY,
        model=model_name,
        temperature=0,  # Deterministic responses for reproducibility
        max_retries=3,
        # Groq retries are built-in; we add exponential backoff on top
    )

    # Wrap with retry logic
    return _wrap_with_retry(llm, role)


def _wrap_with_retry(llm: ChatGroq, role: str) -> ChatGroq:
    """Wrap ChatGroq with retry logic and concurrency limiting."""

    # Store original ainvoke for wrapping
    original_ainvoke = llm.ainvoke

    def invoke_with_retry(input: Any, **kwargs: Any) -> Any:
        """Synchronous invoke with retry."""
        return asyncio.run(ainvoke_with_retry(input, **kwargs))

    async def ainvoke_with_retry(input: Any, **kwargs: Any) -> Any:
        """Asynchronous invoke with retry and concurrency limit."""
        async with _get_concurrency_limit():
            async for attempt in AsyncRetrying(
                retry=retry_if_exception_type((Exception,)),
                stop=stop_after_attempt(4),
                wait=wait_exponential(multiplier=1, min=2, max=10),
            ):
                with attempt:
                    start_time = datetime.now(UTC)
                    result = await original_ainvoke(input, **kwargs)
                    elapsed = (datetime.now(UTC) - start_time).total_seconds()

                    # Log token usage if available
                    if hasattr(result, "response_metadata"):
                        metadata = result.response_metadata or {}
                        token_usage = metadata.get("usage", {})
                        logger.info(
                            f"LLM call ({role})",
                            extra={
                                "model": llm.model,
                                "role": role,
                                "input_tokens": token_usage.get("input_tokens", 0),
                                "output_tokens": token_usage.get("output_tokens", 0),
                                "elapsed_seconds": elapsed,
                            },
                        )

                    return result

    # Monkey-patch the methods (not ideal but necessary for langchain integration)
    llm.invoke = invoke_with_retry
    llm.ainvoke = ainvoke_with_retry

    return llm


class FakeLLM(BaseLLM):
    """
    Test double for deterministic LLM behavior.

    Usage:
        set_fake_llm_responses(["response1", "response2", ...])
        llm = FakeLLM()
        result = await llm.ainvoke("prompt")  # Returns "response1"
    """

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(
        self, prompts: list[str], stop: list[str] | None = None, **kwargs: Any
    ) -> LLMResult:
        """Synchronous generation (not used in async context)."""
        raise NotImplementedError("Use ainvoke for async")

    async def _agenerate(
        self, prompts: list[str], stop: list[str] | None = None, **kwargs: Any
    ) -> LLMResult:
        """Asynchronous generation."""
        global _FAKE_LLM_RESPONSE_INDEX, _FAKE_LLM_RESPONSES

        if not _FAKE_LLM_RESPONSES:
            raise ValueError("No fake LLM responses configured")

        if _FAKE_LLM_RESPONSE_INDEX >= len(_FAKE_LLM_RESPONSES):
            raise ValueError(
                f"Fake LLM exhausted responses: expected {_FAKE_LLM_RESPONSE_INDEX + 1}, "
                f"but only {len(_FAKE_LLM_RESPONSES)} configured"
            )

        response = _FAKE_LLM_RESPONSES[_FAKE_LLM_RESPONSE_INDEX]
        _FAKE_LLM_RESPONSE_INDEX += 1

        # Return LMResult with single generation per prompt
        generations = [
            [Generation(text=response, generation_info={"fake": True})]
            for _ in prompts
        ]
        return LLMResult(generations=generations)

    async def ainvoke(
        self, input: Any, config: Any | None = None, **kwargs: Any
    ) -> Any:
        """Invoke (returns structured output if requested)."""
        global _FAKE_LLM_RESPONSE_INDEX, _FAKE_LLM_RESPONSES

        if not _FAKE_LLM_RESPONSES:
            raise ValueError("No fake LLM responses configured")

        if _FAKE_LLM_RESPONSE_INDEX >= len(_FAKE_LLM_RESPONSES):
            raise ValueError(
                f"Fake LLM exhausted responses: expected {_FAKE_LLM_RESPONSE_INDEX + 1}, "
                f"but only {len(_FAKE_LLM_RESPONSES)} configured"
            )

        response = _FAKE_LLM_RESPONSES[_FAKE_LLM_RESPONSE_INDEX]
        _FAKE_LLM_RESPONSE_INDEX += 1

        # Try to parse as JSON for structured output
        try:
            return json.loads(response)
        except (json.JSONDecodeError, ValueError):
            # Return as plain text
            from langchain_core.messages import AIMessage

            return AIMessage(content=response)


def set_fake_llm_responses(responses: list[str]) -> None:
    """Configure FakeLLM responses for testing."""
    global _FAKE_LLM_RESPONSES, _FAKE_LLM_RESPONSE_INDEX
    _FAKE_LLM_RESPONSES = responses
    _FAKE_LLM_RESPONSE_INDEX = 0


def get_fake_llm() -> FakeLLM:
    """Get a FakeLLM instance for testing."""
    return FakeLLM()


def reset_fake_llm() -> None:
    """Reset FakeLLM state between tests."""
    global _FAKE_LLM_RESPONSE_INDEX, _FAKE_LLM_RESPONSES
    _FAKE_LLM_RESPONSES = []
    _FAKE_LLM_RESPONSE_INDEX = 0
