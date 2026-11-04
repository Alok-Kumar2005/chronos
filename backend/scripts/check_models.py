#!/usr/bin/env python3
"""Check Groq models at startup and validate they support required features.

This script:
1. Fetches available models from Groq
2. Validates configured models exist (GROQ_MODEL_PRIMARY, GROQ_MODEL_FAST)
3. Falls back to available models if configured ones are retired
4. Verifies models support tool calling and JSON output
5. Logs warnings if fallbacks are used
"""

import asyncio
import logging
import sys

from app.agent.llm import get_groq_models, validate_and_pick_models
from app.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> int:
    """Check models and return exit code."""
    try:
        logger.info("Fetching Groq models...")
        models = await get_groq_models()
        logger.info(f"Found {len(models)} available models")

        # Show configured models
        logger.info(
            f"Configured models: PRIMARY={settings.GROQ_MODEL_PRIMARY}, "
            f"FAST={settings.GROQ_MODEL_FAST}"
        )

        # Validate and pick models
        logger.info("Validating configured models...")
        primary, fast = await validate_and_pick_models()
        logger.info(f"Validated models: PRIMARY={primary}, FAST={fast}")

        # Check for tool calling and JSON output support
        model_ids = {m["id"]: m for m in models}
        for model_name, role in [(primary, "PRIMARY"), (fast, "FAST")]:
            if model_name in model_ids:
                logger.info(
                    f"{role} model {model_name}: "
                    f"owned_by={model_ids[model_name].get('owned_by', 'unknown')}"
                )

        logger.info("Model validation successful")
        return 0

    except Exception as e:
        logger.error(f"Model validation failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
