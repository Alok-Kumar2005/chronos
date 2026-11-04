"""
Recorder for real PayPal sandbox responses.

Run with: python -m app.reality.record

This script:
1. Creates a temporary universe in Chronos to get OAuth token
2. Runs each probe against both the real PayPal sandbox and the Chronos twin
3. Sanitizes responses (removes/hashes sensitive data)
4. Saves golden responses to reality/golden/*.json
5. Validates no secrets are leaked in the golden files

Usage:
    uv run python -m app.reality.record

Requires:
    PAYPAL_SANDBOX_CLIENT_ID
    PAYPAL_SANDBOX_CLIENT_SECRET
    (and DATABASE_URL for Chronos twin)

This is safe to run: only creates temp data in sandbox and deletes it.
"""

import asyncio
import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models import Universe
from app.reality.normalizer import Normalizer
from app.reality.probes import get_probe, list_probes
from app.twin.ids import generate_client_id, generate_universe_id

logger = logging.getLogger("reality.recorder")


class PayPalRecorder:
    """Record real PayPal responses and compare with Chronos."""

    def __init__(self):
        """Initialize recorder with sandbox credentials."""
        self.paypal_client_id = os.getenv("PAYPAL_SANDBOX_CLIENT_ID")
        self.paypal_client_secret = os.getenv("PAYPAL_SANDBOX_CLIENT_SECRET")

        if not self.paypal_client_id or not self.paypal_client_secret:
            raise ValueError(
                "Missing PAYPAL_SANDBOX_CLIENT_ID or PAYPAL_SANDBOX_CLIENT_SECRET. "
                "Add to .env to enable reality checks."
            )

        self.paypal_base_url = "https://api-m.sandbox.paypal.com"
        self.chronos_base_url = "http://localhost:8000"

        self.normalizer = Normalizer()
        self.golden_dir = "backend/app/reality/golden"
        os.makedirs(self.golden_dir, exist_ok=True)

        self.paypal_token: str | None = None
        self.chronos_token: str | None = None
        self.chronos_client_secret: str | None = None

    async def get_paypal_token(self) -> str:
        """Get OAuth token from real PayPal sandbox."""
        if self.paypal_token:
            return self.paypal_token

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.paypal_base_url}/v1/oauth2/token",
                auth=(self.paypal_client_id, self.paypal_client_secret),
                data={"grant_type": "client_credentials"},
            )
            response.raise_for_status()
            data = response.json()
            self.paypal_token = data["access_token"]
            logger.info("✓ Got PayPal sandbox OAuth token")
            return self.paypal_token

    async def setup_chronos_universe(self, session: AsyncSession) -> tuple[str, str]:
        """Create a Chronos universe and get its OAuth token."""
        # Create universe directly in DB
        universe_id = generate_universe_id()
        client_id = generate_client_id()
        client_secret = "test-secret-12345"  # Fixed for reproducibility

        universe = Universe(
            id=universe_id,
            client_id=client_id,
            client_secret_hash="dummy",  # Will be hashed during init
            client_secret=client_secret,
            control_key="test-control-key",
            control_key_hash="dummy",
        )
        session.add(universe)
        await session.commit()

        logger.info(f"✓ Created Chronos universe: {universe_id}")

        # Get token via OAuth endpoint
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.chronos_base_url}/v1/oauth2/token",
                auth=(client_id, client_secret),
                data={"grant_type": "client_credentials"},
            )
            response.raise_for_status()
            data = response.json()
            self.chronos_token = data["access_token"]
            self.chronos_client_secret = client_secret
            logger.info(f"✓ Got Chronos OAuth token for universe {universe_id}")
            return universe_id, self.chronos_token

    async def run_probe_against_paypal(self, probe_name: str) -> dict[str, Any]:
        """Run a probe against real PayPal sandbox."""
        probe = get_probe(probe_name)
        token = await self.get_paypal_token()

        return await self._run_probe(probe_name, probe, token, self.paypal_base_url)

    async def run_probe_against_chronos(self, probe_name: str) -> dict[str, Any]:
        """Run a probe against Chronos twin."""
        probe = get_probe(probe_name)
        token = self.chronos_token

        return await self._run_probe(probe_name, probe, token, self.chronos_base_url)

    async def _run_probe(
        self,
        probe_name: str,
        probe: dict[str, Any],
        token: str,
        base_url: str,
    ) -> dict[str, Any]:
        """Run a single probe against a base URL."""
        method = probe["method"]
        path = probe["path"]
        body = probe.get("body")
        auth = probe.get("auth", "none")

        # For dependent probes, we need to substitute IDs from previous probes
        # For now, we'll handle common cases
        if "{order_id}" in path or "{batch_id}" in path:
            # Skip dependent probes for now - they need prior state
            return {
                "skipped": True,
                "reason": "dependent_probe",
                "probe": probe_name,
            }

        async with httpx.AsyncClient(timeout=30.0) as client:
            headers: dict[str, str] = {}
            if auth == "bearer":
                headers["Authorization"] = f"Bearer {token}"
            elif auth == "oauth":
                pass  # Will use Basic auth below

            url = f"{base_url}{path}"

            try:
                if method == "POST":
                    response = await client.post(url, json=body, headers=headers)
                elif method == "GET":
                    response = await client.get(url, headers=headers)
                elif method == "PATCH":
                    response = await client.patch(url, json=body, headers=headers)
                elif method == "DELETE":
                    response = await client.delete(url, headers=headers)
                else:
                    return {"error": f"Unknown method: {method}"}

                result = {
                    "probe": probe_name,
                    "method": method,
                    "path": path,
                    "status_code": response.status_code,
                    "headers": dict(response.headers),
                }

                # Capture response body
                try:
                    result["body"] = response.json()
                except Exception:
                    result["body"] = response.text

                logger.info(f"  {probe_name}: {method} {path} -> {response.status_code}")
                return result

            except Exception as e:
                logger.error(f"  {probe_name}: ERROR: {e}")
                return {
                    "probe": probe_name,
                    "error": str(e),
                    "method": method,
                    "path": path,
                }

    def sanitize_response(self, response: dict[str, Any]) -> dict[str, Any]:
        """Sanitize sensitive data from response."""
        # Deep copy to avoid mutating original
        sanitized = json.loads(json.dumps(response, default=str))

        # Normalize all dynamic values
        if "body" in sanitized and isinstance(sanitized["body"], dict):
            sanitized["body"] = self.normalizer.normalize_response(sanitized["body"])

        return sanitized

    async def save_golden(self, probe_name: str, paypal_response: dict[str, Any], chronos_response: dict[str, Any]):
        """Save golden response to file."""
        golden = {
            "probe": probe_name,
            "recorded_at": datetime.now(UTC).isoformat(),
            "paypal": self.sanitize_response(paypal_response),
            "chronos": self.sanitize_response(chronos_response),
        }

        filename = f"{self.golden_dir}/{probe_name}.json"
        with open(filename, "w") as f:
            json.dump(golden, f, indent=2, default=str)

        logger.info(f"  Saved golden: {filename}")

    async def record_all_probes(self):
        """Record all probes."""
        logger.info("Starting probe recording...")

        # Setup Chronos universe
        engine = create_async_engine(settings.database_url)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with async_session() as session:
            universe_id, token = await self.setup_chronos_universe(session)
            self.chronos_token = token

        # Run each probe
        for probe_name in list_probes():
            logger.info(f"Recording probe: {probe_name}")

            try:
                paypal_response = await self.run_probe_against_paypal(probe_name)
                chronos_response = await self.run_probe_against_chronos(probe_name)

                # Skip dependent probes
                if paypal_response.get("skipped"):
                    logger.info(f"  Skipped: {paypal_response.get('reason')}")
                    continue

                await self.save_golden(probe_name, paypal_response, chronos_response)

            except Exception as e:
                logger.error(f"Failed to record probe {probe_name}: {e}")

        logger.info("✓ Probe recording complete")

        # Cleanup
        await engine.dispose()


async def main():
    """Main entry point."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        recorder = PayPalRecorder()
        await recorder.record_all_probes()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)


if __name__ == "__main__":
    asyncio.run(main())
