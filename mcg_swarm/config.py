"""SwarmConfig — value object for swarm behavior knobs.

Holds plain data the swarm acts on. It deliberately knows nothing about providers,
models, credentials, or runners: those are injected collaborators, not configuration.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SwarmConfig:
    """Behavior knobs for a swarm run.

    validate:          also run the agent on otherwise-clean tables (was MCG_REACT_VALIDATE,
                       default on).
    repair_max_passes: max table-level repair passes (was MCG_REPAIR_MAX_PASSES, default 3).
    """

    validate: bool = True
    repair_max_passes: int = 3
