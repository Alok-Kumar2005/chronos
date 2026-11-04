"""Reality check API endpoints."""

import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.reality.compare import ComparisonEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/_chronos/api/reality", tags=["reality"])


@router.get("/report", response_model=dict[str, Any])
async def get_reality_report() -> dict[str, Any]:
    """
    Get reality check report (offline mode).

    Compares the twin with committed golden responses.
    Requires no credentials, so judges can click this endpoint.

    Returns:
    - overall_fidelity: Mean fidelity score across all probes (0.0 to 1.0)
    - perfect_probes: Count of 100% matching probes
    - good_probes: Count of 80%+ matching probes
    - probe_results: Detailed results per probe
    - top_recommendations: What to fix first
    - not_probed: List of operations not covered
    """
    try:
        engine = ComparisonEngine()
        report = engine.generate_report()
        return report
    except FileNotFoundError as e:
        raise HTTPException(
            status_code=404,
            detail="Golden responses not recorded yet. Run 'python -m app.reality.record' first.",
        ) from e
    except Exception as e:
        logger.error(f"Reality report error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/run", response_model=dict[str, Any])
async def run_reality_check(live: bool = Query(False)) -> dict[str, Any]:
    """
    Run reality checks (optionally live against real PayPal).

    Query parameters:
    - live=true: Run probes against real PayPal sandbox (requires credentials)
    - live=false: Use offline golden responses (default)

    Returns:
    - Same structure as /report, but with fresh data if live=true
    """
    if live:
        # Live mode: check credentials are configured
        paypal_client_id = os.getenv("PAYPAL_SANDBOX_CLIENT_ID")
        paypal_client_secret = os.getenv("PAYPAL_SANDBOX_CLIENT_SECRET")

        if not paypal_client_id or not paypal_client_secret:
            raise HTTPException(
                status_code=400,
                detail="Live mode requires PAYPAL_SANDBOX_CLIENT_ID and PAYPAL_SANDBOX_CLIENT_SECRET in .env",
            )

        # TODO: Implement live recording
        # For now, only offline mode is supported
        raise HTTPException(
            status_code=501,
            detail="Live reality checks not yet implemented. Use live=false for offline mode.",
        )

    # Offline mode: use committed goldens
    return await get_reality_report()
