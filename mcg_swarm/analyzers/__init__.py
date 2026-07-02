"""Pluggable sheet-level structural analysis.

Each SheetAnalyzer is a *lens* over a sheet grid, emitting LayoutCandidate(s).
A registry selects the active lenses (SwarmConfig.analyzers); assess() picks the
winning candidate. See docs/superpowers/specs/2026-07-01-modular-static-analysis-design.md.
"""
from mcg_swarm.analyzers.assess import assess, assess_sheet
from mcg_swarm.analyzers.base import LayoutCandidate, SheetAnalysis, SheetAnalyzer
from mcg_swarm.analyzers.pipeline import analyze_sheet, analyze_workbook
from mcg_swarm.analyzers.registry import build_analyzers, register

__all__ = ["LayoutCandidate", "SheetAnalysis", "SheetAnalyzer", "analyze_sheet",
           "analyze_workbook", "assess", "assess_sheet", "build_analyzers", "register"]
