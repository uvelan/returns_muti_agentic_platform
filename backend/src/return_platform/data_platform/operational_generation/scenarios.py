import random
from collections.abc import Mapping

from .models import ScenarioType


def distribute_scenarios(
    rng: random.Random, distribution: Mapping[ScenarioType, int]
) -> list[ScenarioType]:
    scenarios = []
    for s_type, count in distribution.items():
        scenarios.extend([s_type] * count)
    # deterministic shuffle based on RNG
    rng.shuffle(scenarios)
    return scenarios
