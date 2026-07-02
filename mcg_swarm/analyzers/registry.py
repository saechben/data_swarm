from __future__ import annotations

from typing import Callable

from mcg_swarm.analyzers.base import SheetAnalyzer
from mcg_swarm.analyzers.vertical import VerticalSplitAnalyzer

_REGISTRY: dict[str, Callable[[], SheetAnalyzer]] = {}


def register(name: str, factory: Callable[[], SheetAnalyzer]) -> None:
    """Register an analyzer factory under a stable string id."""
    _REGISTRY[name] = factory


def build_analyzers(names: tuple[str, ...]) -> list[SheetAnalyzer]:
    """Instantiate the named analyzers in order. Raises KeyError on unknown name."""
    built = []
    for name in names:
        if name not in _REGISTRY:
            raise KeyError(
                f"unknown analyzer {name!r} (registered: {sorted(_REGISTRY)})"
            )
        built.append(_REGISTRY[name]())
    return built


register("vertical", VerticalSplitAnalyzer)
