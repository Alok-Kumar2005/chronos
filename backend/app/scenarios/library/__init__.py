"""Scenario library loader - reads YAML scenarios and validates them."""

import logging
from pathlib import Path

import yaml

from app.scenarios.dsl import Scenario

logger = logging.getLogger(__name__)

LIBRARY_DIR = Path(__file__).parent


def load_scenario_from_yaml(yaml_path: Path) -> Scenario:
    """
    Load and validate a scenario from a YAML file.

    Args:
        yaml_path: Path to YAML file

    Returns:
        Validated Scenario model

    Raises:
        ValueError: If YAML is invalid or doesn't conform to DSL
        yaml.YAMLError: If YAML syntax is invalid
    """
    with open(yaml_path) as f:
        data = yaml.safe_load(f)

    if not data:
        raise ValueError(f"YAML file is empty: {yaml_path}")

    # Validate and convert to Scenario
    try:
        scenario = Scenario.model_validate(data)
        logger.info(f"Loaded scenario: {scenario.id} from {yaml_path.name}")
        return scenario
    except Exception as e:
        logger.error(f"Failed to load scenario from {yaml_path}: {e}")
        raise ValueError(f"Invalid scenario in {yaml_path.name}: {e}") from e


def load_all_scenarios() -> dict[str, Scenario]:
    """
    Load all scenarios from the library directory.

    Returns:
        Dict mapping scenario ID to Scenario model

    Raises:
        ValueError: If any scenario is invalid
    """
    scenarios = {}
    yaml_files = sorted(LIBRARY_DIR.glob("*.yaml"))

    if not yaml_files:
        logger.warning(f"No scenario YAML files found in {LIBRARY_DIR}")
        return scenarios

    for yaml_path in yaml_files:
        try:
            scenario = load_scenario_from_yaml(yaml_path)
            scenarios[scenario.id] = scenario
        except Exception as e:
            logger.error(f"Failed to load {yaml_path.name}: {e}")
            # Continue loading other scenarios
            raise

    logger.info(f"Loaded {len(scenarios)} scenarios from library")
    return scenarios


def get_scenario(scenario_id: str) -> Scenario | None:
    """
    Get a specific scenario by ID.

    Args:
        scenario_id: Scenario ID

    Returns:
        Scenario if found, None otherwise
    """
    yaml_path = LIBRARY_DIR / f"{scenario_id}.yaml"
    if not yaml_path.exists():
        return None

    try:
        return load_scenario_from_yaml(yaml_path)
    except Exception as e:
        logger.error(f"Failed to load scenario {scenario_id}: {e}")
        return None
