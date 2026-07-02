from __future__ import annotations

from typing import Callable

from mcg_swarm.analyzers.agentic import AgenticLayoutLens
from mcg_swarm.analyzers.base import SheetAnalyzer
from mcg_swarm.analyzers.vertical import VerticalSplitAnalyzer

_REGISTRY: dict[str, Callable[[], SheetAnalyzer]] = {}


def register(name: str, factory: Callable[[], SheetAnalyzer]) -> None:
    """Register an analyzer factory under a stable string id."""
    _REGISTRY[name] = factory


def build_analyzers(names: tuple[str, ...], runner=None) -> list[SheetAnalyzer]:
    """Instantiate the named analyzers in order. Raises KeyError on unknown name.

    Factories marked with a truthy ``needs_runner`` attribute are constructed
    with the given ``runner`` (or None); all others take no arguments.
    """
    factories = []
    for name in names:
        if name not in _REGISTRY:
            raise KeyError(
                f"unknown analyzer {name!r} (registered: {sorted(_REGISTRY)})"
            )
        factories.append(_REGISTRY[name])
    return [f(runner=runner) if getattr(f, "needs_runner", False) else f()
            for f in factories]


register("vertical", VerticalSplitAnalyzer)
register("agentic", AgenticLayoutLens)
