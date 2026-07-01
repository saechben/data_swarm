"""Pluggable sheet-level structural analysis.

Each SheetAnalyzer is a *lens* over a sheet grid, emitting LayoutCandidate(s).
A registry selects the active lenses (SwarmConfig.analyzers); assess() picks the
winning candidate. See docs/superpowers/specs/2026-07-01-modular-static-analysis-design.md.
"""
from mcg_swarm.analyzers.base import LayoutCandidate, SheetAnalyzer

__all__ = ["LayoutCandidate", "SheetAnalyzer"]
