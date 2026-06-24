"""Per-table token index used by the resolver to avoid O(rows) scans.

The index maps tokens (and separator-stripped forms) of a catalog entry's
row-keys back to the original row labels, plus precomputed column token sets.
``TableIndexCache`` memoises one index per catalog entry; it is held as instance
state on a ``Resolver`` (see ``mcg_swarm.resolve.resolver``) rather than as a
module global, so cache lifetime is scoped to the resolver that owns it.
"""
from __future__ import annotations

from mcg_swarm.resolve.tokens import name_tokens, squash


def build_table_index(entry: dict) -> dict:
    """Build the token index for a catalog entry.

    Returns a dict with:
        tok_to_rows      : token -> [original_row_key, ...]
        squashed_to_rows : separator-stripped row form -> [original_row_key, ...]
        col_tokens       : col_name -> frozenset[str]
        lower_to_row     : lowercased row form -> original_row_key
        _nrows           : len(row_keys) at build time (cache-validity guard)
    """
    tok_to_rows: dict[str, list[str]] = {}
    squashed_to_rows: dict[str, list[str]] = {}
    for rk in entry["row_keys"]:
        rk_str = str(rk)
        for tok in name_tokens(rk_str):
            tok_to_rows.setdefault(tok, []).append(rk_str)
        # Index the separator-stripped form so 'sku100' finds 'SKU-100'.
        sq = squash(rk_str)
        if sq:
            squashed_to_rows.setdefault(sq, []).append(rk_str)

    col_tokens = {col: name_tokens(col) for col in entry["columns"]}

    return {
        "tok_to_rows": tok_to_rows,
        "squashed_to_rows": squashed_to_rows,
        "col_tokens": col_tokens,
        "_nrows": len(entry["row_keys"]),
        "lower_to_row": {str(rk).lower(): str(rk) for rk in entry["row_keys"]},
    }


class TableIndexCache:
    """Memoises table indices keyed by ``id(entry)``.

    Catalog lists are rebuilt per workbook call, so caching by Python object id
    is stable enough for a session; ``_nrows`` guards against an id being reused
    for a re-populated entry.
    """

    def __init__(self) -> None:
        self._by_id: dict[int, dict] = {}

    def get(self, entry: dict) -> dict:
        eid = id(entry)
        cached = self._by_id.get(eid)
        if cached is not None and cached["_nrows"] == len(entry["row_keys"]):
            return cached
        idx = build_table_index(entry)
        self._by_id[eid] = idx
        return idx
