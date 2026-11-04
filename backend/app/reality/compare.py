"""
Comparison engine for real vs. twin responses.

Normalizes dynamic values, flattens responses to JSON paths with types,
and compares to generate a fidelity score per probe and overall.

No cherry-picking: all fields are included in the comparison.
Any ignore list must be explicit, small, and justified in code comments.
"""

import json
from pathlib import Path
from typing import Any

from app.reality.normalizer import Normalizer

# Fields that are explicitly ignored (justified)
IGNORE_FIELDS = {
    # PayPal returns debug_id on every error; we don't need exact match
    # as long as format is present. Both sides generate unique debug_ids.
    "*/debug_id",
    # Nonce is random per token request
    "*/nonce",
    # Transmission headers include timestamp which varies
    "*/PAYPAL-TRANSMISSION-TIME",
    "*/PAYPAL-TRANSMISSION-ID",
    "*/PAYPAL-TRANSMISSION-SIG",
    # Webhook cert URL points to PayPal's real certs; Chronos uses dev certs
    "*/PAYPAL-CERT-URL",
}


class ProbeComparison:
    """Result of comparing a single probe's real vs. twin response."""

    def __init__(self, probe_name: str):
        self.probe_name = probe_name
        self.status_code_match = False
        self.matched_paths: list[str] = []
        self.missing_in_twin: list[str] = []
        self.extra_in_twin: list[str] = []
        self.type_mismatches: list[dict[str, Any]] = []
        self.value_mismatches: list[dict[str, Any]] = []
        self.errors: list[str] = []

    def fidelity_score(self) -> float:
        """Calculate fidelity score for this probe (0.0 to 1.0)."""
        total_paths = (
            len(self.matched_paths)
            + len(self.missing_in_twin)
            + len(self.extra_in_twin)
            + len(self.type_mismatches)
            + len(self.value_mismatches)
        )

        if total_paths == 0:
            # No paths means error or skipped
            return 0.0

        matched = len(self.matched_paths)
        return matched / total_paths

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for JSON serialization."""
        return {
            "probe": self.probe_name,
            "status_code_match": self.status_code_match,
            "fidelity_score": self.fidelity_score(),
            "matched_paths": len(self.matched_paths),
            "missing_in_twin": len(self.missing_in_twin),
            "extra_in_twin": len(self.extra_in_twin),
            "type_mismatches": len(self.type_mismatches),
            "value_mismatches": len(self.value_mismatches),
            "errors": self.errors,
            # Detail for debugging
            "_missing_sample": self.missing_in_twin[:3],
            "_extra_sample": self.extra_in_twin[:3],
            "_type_mismatch_sample": self.type_mismatches[:2],
            "_value_mismatch_sample": self.value_mismatches[:2],
        }


class ComparisonEngine:
    """Compare real vs. twin responses."""

    def __init__(self, golden_dir: str = "backend/app/reality/golden"):
        self.golden_dir = Path(golden_dir)
        self.normalizer = Normalizer()

    def load_golden(self, probe_name: str) -> dict[str, Any]:
        """Load golden response for a probe."""
        filepath = self.golden_dir / f"{probe_name}.json"
        if not filepath.exists():
            raise FileNotFoundError(f"Golden file not found: {filepath}")

        with open(filepath) as f:
            return json.load(f)

    def compare_probe(self, probe_name: str) -> ProbeComparison:
        """Compare a single probe's real vs. twin response."""
        result = ProbeComparison(probe_name)

        try:
            golden = self.load_golden(probe_name)
        except FileNotFoundError as e:
            result.errors.append(str(e))
            return result

        paypal_response = golden.get("paypal", {})
        chronos_response = golden.get("chronos", {})

        # Check for errors in either response
        if "error" in paypal_response:
            result.errors.append(f"PayPal error: {paypal_response['error']}")
            return result
        if "error" in chronos_response:
            result.errors.append(f"Chronos error: {chronos_response['error']}")
            return result

        # Compare status codes
        paypal_status = paypal_response.get("status_code")
        chronos_status = chronos_response.get("status_code")

        if paypal_status == chronos_status:
            result.status_code_match = True
        else:
            result.errors.append(f"Status code mismatch: PayPal={paypal_status}, Chronos={chronos_status}")

        # Compare response bodies
        paypal_body = paypal_response.get("body", {})
        chronos_body = chronos_response.get("body", {})

        if not isinstance(paypal_body, dict):
            paypal_body = {}
        if not isinstance(chronos_body, dict):
            chronos_body = {}

        # Flatten and compare
        paypal_flat = self.normalizer.flatten(paypal_body)
        chronos_flat = self.normalizer.flatten(chronos_body)

        comparison = self.normalizer.compare_flattened(chronos_flat, paypal_flat)

        result.matched_paths = comparison["matched"]
        result.missing_in_twin = comparison["missing_in_twin"]
        result.extra_in_twin = comparison["extra_in_twin"]
        result.type_mismatches = comparison["type_mismatches"]
        result.value_mismatches = comparison["value_mismatches"]

        return result

    def compare_all_probes(self) -> tuple[list[ProbeComparison], float]:
        """Compare all probes and return results + overall fidelity."""
        results: list[ProbeComparison] = []

        for golden_file in sorted(self.golden_dir.glob("*.json")):
            probe_name = golden_file.stem
            result = self.compare_probe(probe_name)
            results.append(result)

        # Calculate overall fidelity (mean of all probes)
        if results:
            overall_fidelity = sum(r.fidelity_score() for r in results) / len(results)
        else:
            overall_fidelity = 0.0

        return results, overall_fidelity

    def generate_report(self) -> dict[str, Any]:
        """Generate a full comparison report."""
        results, overall_fidelity = self.compare_all_probes()

        # Group by fidelity level
        perfect = [r for r in results if r.fidelity_score() == 1.0]
        good = [r for r in results if 0.8 <= r.fidelity_score() < 1.0]
        fair = [r for r in results if 0.5 <= r.fidelity_score() < 0.8]
        poor = [r for r in results if r.fidelity_score() < 0.5]
        errored = [r for r in results if r.errors]

        # Generate recommendations for fixes
        recommendations: list[dict[str, Any]] = []
        for result in poor + fair:
            if result.type_mismatches:
                for mismatch in result.type_mismatches[:1]:
                    recommendations.append(
                        {
                            "probe": result.probe_name,
                            "type": "type_mismatch",
                            "path": mismatch["path"],
                            "twin_type": mismatch["twin_type"],
                            "paypal_type": mismatch["real_type"],
                            "priority": "high" if mismatch["real_type"] != "string" else "medium",
                        }
                    )

            if result.missing_in_twin:
                for path in result.missing_in_twin[:1]:
                    recommendations.append(
                        {
                            "probe": result.probe_name,
                            "type": "missing_field",
                            "path": path,
                            "priority": "medium" if "optional" in path else "high",
                        }
                    )

        return {
            "timestamp": str(Path(__file__).stat().st_mtime),
            "overall_fidelity": round(overall_fidelity, 4),
            "total_probes": len(results),
            "perfect_probes": len(perfect),
            "good_probes": len(good),
            "fair_probes": len(fair),
            "poor_probes": len(poor),
            "errored_probes": len(errored),
            "probe_results": [r.to_dict() for r in results],
            "top_recommendations": sorted(recommendations, key=lambda x: x.get("priority", "low") == "high", reverse=True)[
                :10
            ],
            "not_probed": [
                "CAPTURE after authorization (requires buyer approval in real sandbox)",
                "VOID authorization after capture (requires specific merchant config)",
                "Webhook deliveries (network-dependent, tested separately)",
                "INSTRUMENT_DECLINED mock failure (requires special header + valid instrument)",
                "Subscription operations (not in MVP)",
                "Dispute resolution (not in MVP)",
            ],
        }


if __name__ == "__main__":
    engine = ComparisonEngine()
    report = engine.generate_report()
    print(json.dumps(report, indent=2))
