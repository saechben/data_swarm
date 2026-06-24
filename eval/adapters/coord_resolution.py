"""Phrase → (table_id, row, col) resolution helpers for the swarm adapter.

Stateless building blocks the adapter composes: catalog construction over the
swarm's ExtractionIndex objects, the LLM-resolver response schema, and the LLM
call itself. The adapter owns the caches and the live LLM client; these helpers
take their inputs explicitly so they stay easy to test and reason about.
"""
from __future__ import annotations

import json
import sys
from typing import Optional

from pydantic import BaseModel

# Max row_keys to include in the (prompt-facing) LLM catalog per table.
_CATALOG_MAX_ROWS = 60


class CoordResolution(BaseModel):
    """Schema the LLM coordinate-resolver must return (enforced at the client boundary)."""
    found: bool
    table_id: Optional[str] = None
    row_label: Optional[str] = None
    col_label: Optional[str] = None


def queryable_columns(idx) -> list[str]:
    """Columns a phrase can resolve to — the value/computed columns, NOT the key.

    The key column holds the row identifiers (already exposed as ``row_keys``); it
    is never the target of a "what is X for row Y" query.  Worse, its NAME (e.g.
    "Department", "Vendor", "Product") often appears in the query's table-title
    suffix ("...in Headcount by Department"), so leaving it in the candidate set
    makes it win the longest-verbatim-match and the resolver returns the row label
    itself instead of a value.  Drop it.
    """
    out = []
    for name in idx.column_names():
        spec = idx.columns.get(name)
        if spec is not None and spec.role == "key":
            continue
        out.append(name)
    # Defensive: never return an empty column list (would make resolution impossible).
    return out or idx.column_names()


def build_catalog(indices: dict) -> list[dict]:
    """Compact catalog (row_keys capped) for the LLM prompt.

    *indices* is the workbook's {table_id: ExtractionIndex} mapping.
    Each entry: {table_id, columns, row_keys[, row_keys_truncated]}.
    """
    catalog = []
    for table_id, idx in indices.items():
        all_rows = [str(k) for k in idx.row_keys()]
        entry = {
            "table_id": table_id,
            "columns": queryable_columns(idx),
            "row_keys": all_rows[:_CATALOG_MAX_ROWS],
        }
        # Annotate if truncated so the LLM knows the list is partial.
        if len(all_rows) > _CATALOG_MAX_ROWS:
            entry["row_keys_truncated"] = True
        catalog.append(entry)
    return catalog


def build_full_catalog(indices: dict) -> list[dict]:
    """Catalog with ALL row_keys (no cap) for the deterministic resolver.

    The deterministic resolver uses a token-inverted index, so including all
    100k+ rows doesn't cause O(rows*tokens) blowup.
    """
    catalog = []
    for table_id, idx in indices.items():
        catalog.append({
            "table_id": table_id,
            "columns": queryable_columns(idx),
            "row_keys": [str(k) for k in idx.row_keys()],
        })
    return catalog


def resolve_via_llm(llm, catalog: list[dict], phrase: str) -> Optional[tuple]:
    """Ask the LLM to resolve *phrase* against *catalog*.

    Returns (table_id, row, col) — validated to exist in the catalog — or None.
    """
    catalog_json = json.dumps(catalog, indent=2)
    system = (
        "You are a spreadsheet data resolver. Given a catalog of tables and a phrase, "
        "identify the EXACT table, row, and column that the phrase refers to. "
        "You MUST choose table_id, row_label, and col_label VERBATIM from the catalog lists. "
        "If nothing matches, return {\"found\": false}."
    )
    user = (
        f"CATALOG:\n{catalog_json}\n\n"
        f"PHRASE: {phrase}\n\n"
        "Return JSON with these fields:\n"
        "  found: bool — true if you found a match, false otherwise\n"
        "  table_id: str — must be verbatim from catalog table_id values\n"
        "  row_label: str — must be verbatim from that table's row_keys list\n"
        "  col_label: str — must be verbatim from that table's columns list\n\n"
        "Return ONLY JSON, no prose."
    )

    try:
        resp = llm.complete(system, user, schema=CoordResolution)
    except Exception as _exc:
        print(f"[swarm_adapter] LLM call failed ({type(_exc).__name__}): {_exc}", file=sys.stderr)
        return None

    if not resp.get("found", False):
        return None

    table_id = resp.get("table_id")
    row_label = resp.get("row_label")
    col_label = resp.get("col_label")

    # Validate returned coords exist in the catalog.
    catalog_by_id = {e["table_id"]: e for e in catalog}
    entry = catalog_by_id.get(table_id)
    if entry is None:
        return None
    if col_label not in entry["columns"]:
        return None
    if row_label not in entry["row_keys"]:
        return None

    return (table_id, row_label, col_label)
