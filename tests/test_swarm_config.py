"""SwarmConfig: a frozen value object for swarm behavior knobs (no provider knowledge)."""
import pytest
from dataclasses import FrozenInstanceError

from mcg_swarm.config import SwarmConfig


def test_defaults():
    c = SwarmConfig()
    assert c.validate is True
    assert c.repair_max_passes == 3


def test_custom_values():
    c = SwarmConfig(validate=False, repair_max_passes=5)
    assert c.validate is False
    assert c.repair_max_passes == 5


def test_is_frozen():
    c = SwarmConfig()
    with pytest.raises(FrozenInstanceError):
        c.validate = False  # type: ignore[misc]
