"""Core deterministic resolver: phrase -> (table_id, row_label, col_label)."""
from __future__ import annotations

import re
from collections import Counter
from typing import Optional

from mcg_swarm.resolve.table_index import TableIndexCache
from mcg_swarm.resolve.tokens import (
    bounded_pattern, match_tier, name_tokens, squash, tokenise_list,
)

# Max distinct squashed row forms to scan for prefix/truncation matches. Above this
# (large data tables) we rely on exact token/squash matching only — keeps the 100k-row
# resolution fast (the perf test enforces < 1s).
_PREFIX_SCAN_ROW_CAP = 2000


class Resolver:
    """Deterministic token-matching resolver.

    Holds a per-table index cache as instance state, so repeated ``resolve``
    calls against the same catalog object reuse built indices without any
    module-global mutable state.
    """

    def __init__(self) -> None:
        self._index = TableIndexCache()

    def resolve(
        self,
        phrase: str,
        catalog: list[dict],
    ) -> Optional[tuple[str, str, str]]:
        """Map *phrase* to (table_id, row_label, col_label) or None.

        Scans all tables in *catalog*, finds the best (col, row) pair match per
        table, and returns the highest-scoring candidate.  Returns None if no
        table has both a column and a row match.

        *catalog* entries: {"table_id": str, "columns": [str,...], "row_keys": [str,...]}
        Returns strings in ORIGINAL CASE exactly as in the catalog.
        """
        if not phrase or not catalog:
            return None

        phrase_lower = phrase.lower()
        # Insert CamelCase boundaries before lowercasing for phrase tokens too.
        phrase_token_list = tokenise_list(phrase)
        phrase_tokens = set(phrase_token_list)

        best_score = (-1, -1)  # (combined_tier, combined_name_len)
        best: Optional[tuple[str, str, str]] = None

        for entry in catalog:
            candidate = self._resolve_in_table(
                entry, phrase_lower, phrase_token_list, phrase_tokens)
            if candidate is None:
                continue
            score, result = candidate
            if score > best_score:
                best_score = score
                best = result

        return best

    def _resolve_in_table(self, entry, phrase_lower, phrase_token_list, phrase_tokens):
        """Best (col, row) match within one table, or None.

        Returns ((combined_tier, combined_name_len), (table_id, row, col)) or None.
        """
        table_id = entry["table_id"]
        columns: list[str] = entry["columns"]

        idx = self._index.get(entry)
        col_tokens_map: dict[str, frozenset[str]] = idx["col_tokens"]
        tok_to_rows: dict[str, list[str]] = idx["tok_to_rows"]
        squashed_to_rows: dict[str, list[str]] = idx["squashed_to_rows"]
        lower_to_row: dict[str, str] = idx["lower_to_row"]

        # --- Find best column match ---
        # Primary sort: match tier (verbatim > all-tokens).
        # Secondary: longest name (more chars = more specific).
        # Tertiary: most tokens (AvgSalary=2 tokens > Headcount=1 token when same length).
        best_col: Optional[str] = None
        best_col_key = (-1, -1, -1)  # (tier, name_len, token_count)
        for col in columns:
            ctoks = col_tokens_map.get(col, frozenset())
            tier = match_tier(col, ctoks, phrase_lower, phrase_tokens)
            if tier > 0:
                key = (tier, len(col), len(ctoks))
                if key > best_col_key:
                    best_col_key = key
                    best_col = col

        if best_col is None:
            return None  # No column match → skip table.

        best_col_tier = best_col_key[0]
        best_col_len = best_col_key[1]

        # --- Find best row-key match (longest matching name) ---
        # The row must not re-use the tokens / verbatim span the column already
        # claimed: 'fleet_total' = column Total + row Fleet, NOT row Total (which
        # exists too).  Match rows against a RESIDUAL phrase with the column's
        # contribution removed.
        # Consume only ONE occurrence of each of the column's tokens (and its
        # squashed form): 'fleet_total' loses its single 'total' (→ row Fleet, not
        # row Total), but 'the Total of Total' keeps the second 'total' for the row.
        best_col_tokens = col_tokens_map.get(best_col, frozenset())
        residual_counts = Counter(phrase_token_list)
        for t in set(best_col_tokens) | {squash(best_col)}:
            if residual_counts.get(t, 0) > 0:
                residual_counts[t] -= 1
        row_phrase_tokens = {t for t, c in residual_counts.items() if c > 0}
        # Remove ONE verbatim occurrence of the column from the phrase string too.
        row_phrase_lower = bounded_pattern(best_col.lower()).sub(" ", phrase_lower, count=1)

        best_row: Optional[str] = None
        best_row_key = (-1, -1, -1)  # (tier, name_len, token_count)

        candidate_rows = self._candidate_rows(
            row_phrase_tokens, row_phrase_lower,
            tok_to_rows, squashed_to_rows, lower_to_row)

        for rk in candidate_rows:
            rk_tokens = name_tokens(rk)
            tier = match_tier(rk, frozenset(rk_tokens), row_phrase_lower, row_phrase_tokens)
            if tier > 0:
                rkey = (tier, len(rk), len(rk_tokens))
                if rkey > best_row_key:
                    best_row_key = rkey
                    best_row = rk

        if best_row is None:
            return None  # No row match → skip table.

        best_row_tier = best_row_key[0]
        best_row_len = best_row_key[1]
        # --- Table score: prefer combined match tier, then combined name length. ---
        # A table where both the column and row appear verbatim outranks one matched
        # only via loose tokens, regardless of name length.
        score = (best_col_tier + best_row_tier, best_col_len + best_row_len)
        return score, (table_id, best_row, best_col)

    @staticmethod
    def _candidate_rows(row_phrase_tokens, row_phrase_lower,
                        tok_to_rows, squashed_to_rows, lower_to_row) -> set[str]:
        """Collect rows worth scoring against the residual phrase.

        Pulls rows sharing a token or squashed form with the residual phrase, rows
        whose lowercased form is a residual token, special-char rows matched
        verbatim (dates/ids), and small-table prefix/abbreviation candidates.
        """
        # Candidate rows: those sharing at least one token with the residual phrase,
        # plus rows whose separator-stripped form appears as a residual token.
        candidate_rows: set[str] = set()
        for tok in row_phrase_tokens:
            for rk in tok_to_rows.get(tok, []):
                candidate_rows.add(rk)
            for rk in squashed_to_rows.get(tok, []):
                candidate_rows.add(rk)

        # Also check verbatim: any row whose lowercased form is a residual token.
        for tok in row_phrase_tokens:
            if tok in lower_to_row:
                candidate_rows.add(lower_to_row[tok])

        # For rows not found via tokens (e.g. "2024-09" splits to ["2024","09"]),
        # handle bounded substring matching on the phrase.  Scan only row keys that
        # contain non-alphanumeric chars (dates, ids), and only if the phrase has
        # non-letter content — these are typically few.
        if re.search(r"[^a-z ]", row_phrase_lower):
            for rk_lower, rk_orig in lower_to_row.items():
                if rk_orig in candidate_rows:
                    continue
                if re.search(r"[^a-z0-9]", rk_lower):
                    if bounded_pattern(rk_lower).search(row_phrase_lower):
                        candidate_rows.add(rk_orig)

        # Prefix/truncation candidates ('eng'→'Engineering'): exact-token discovery
        # above misses these.  Bounded scan over DISTINCT squashed row forms — only
        # for small tables, since abbreviation-style keys live on summary tables, not
        # 100k-row ledgers (which carry exact-matchable id/date keys).
        if len(squashed_to_rows) <= _PREFIX_SCAN_ROW_CAP:
            long_toks = [t for t in row_phrase_tokens if len(t) >= 3]
            if long_toks:
                for sq, rks in squashed_to_rows.items():
                    if len(sq) < 3:
                        continue
                    for t in long_toks:
                        if sq.startswith(t) or t.startswith(sq):
                            candidate_rows.update(rks)
                            break

        return candidate_rows


# Module-level default resolver: preserves cross-call index caching for the free
# function API while keeping the cache encapsulated (tests can build fresh
# Resolver() instances for isolation).
_DEFAULT = Resolver()


def deterministic_resolve(
    phrase: str,
    catalog: list[dict],
) -> Optional[tuple[str, str, str]]:
    """Map *phrase* to (table_id, row_label, col_label) or None.

    Thin wrapper over a shared default :class:`Resolver`.  See ``Resolver.resolve``.
    """
    return _DEFAULT.resolve(phrase, catalog)
