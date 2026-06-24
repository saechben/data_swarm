"""Deterministic token-matching resolver for the MCG swarm.

Maps a name / NL-query phrase to a (table_id, row_label, col_label) coordinate
WITHOUT an LLM, by tokenising both the phrase and every column / row-key name
and scoring matches.

Public API
----------
deterministic_resolve(phrase, catalog) -> tuple[str, str, str] | None
Resolver  -- stateful variant holding its own per-table index cache

Each catalog entry (dict):
    table_id : str
    columns  : list[str]      -- original-case column names
    row_keys : list[str]      -- original-case row-key labels (ALL rows, not capped)

Algorithm
---------
1. Tokenise: lowercase, split on whitespace AND non-alphanumeric chars, and split
   CamelCase runs ("CostPerUnit" → cost/per/unit).  (see ``tokens`` module)
2. Name-matching (column or row-key): a name matches if its verbatim lowercased
   form is a bounded substring of the phrase, OR all its tokens appear in the
   phrase token set, OR (weakest) a prefix/truncation matches.
3. Per table, find the best column match and best row-key match; both required.
4. Score = combined match tier, then combined name length.  Highest across tables
   wins; ties break on catalog order.
5. Efficiency: per-table inverted indices are built once and cached on the
   resolver (see ``table_index`` module), so resolution is roughly
   O(phrase_tokens + matched_candidates), not O(rows).
"""
from __future__ import annotations

from mcg_swarm.resolve.resolver import Resolver, deterministic_resolve

__all__ = ["deterministic_resolve", "Resolver"]
