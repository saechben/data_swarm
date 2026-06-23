"""Deterministic token-matching resolver for the MCG swarm.

Maps a name / NL-query phrase to a (table_id, row_label, col_label) coordinate
WITHOUT an LLM, by tokenising both the phrase and every column / row-key name
and scoring matches.

Public API
----------
deterministic_resolve(phrase, catalog) -> tuple[str, str, str] | None

Each catalog entry (dict):
    table_id : str
    columns  : list[str]      -- original-case column names
    row_keys : list[str]      -- original-case row-key labels (ALL rows, not capped)

Algorithm
---------
1. Tokenise:
   - Lowercase the string.
   - Split on whitespace AND non-alphanumeric chars (handles snake_case, punctuation,
     hyphens in dates like "2024-09").
   - ALSO split CamelCase runs (e.g. "CostPerUnit" → ["cost","per","unit"],
     "NetRevenue" → ["net","revenue"], "UnitsSold" → ["units","sold"]).
   - Build a set of all tokens.

2. Name-matching (column or row-key):
   A name matches the phrase if EITHER:
   (a) its verbatim lowercased form appears as a BOUNDED substring of the lowercased
       phrase (bounded = at string boundaries or surrounded by non-alphanumeric chars).
       This handles "CostPerUnit", "T088977", "2024-09", "EMEA" appearing intact.
   (b) ALL of the name's own tokens (from step 1) appear in the phrase token set.
       This handles snake_case operands ("cost_per_unit_emea" → cost,per,unit,emea)
       and multi-word camelCase names ("GrossProfit" → gross,profit).

3. For each table, find the LONGEST-name column match and LONGEST-name row-key match.
   A candidate requires BOTH.

4. Score = len(col_name) + len(row_key_label). Pick the highest across all tables.
   Tie-break: first table in catalog order (stable).

5. Efficiency: per-table, build a token-index {token → [original_row_key, ...]} at
   first use; cache by catalog identity (id of catalog object).  Resolution is
   O(phrase_tokens + matched_candidate_count), not O(rows).
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Optional

# ---------------------------------------------------------------------------
# Tokenisation helpers
# ---------------------------------------------------------------------------

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_NONALNUM_RE = re.compile(r"[^a-z0-9]+")

# Max distinct squashed row forms to scan for prefix/truncation matches. Above this
# (large data tables) we rely on exact token/squash matching only — keeps the 100k-row
# resolution fast (the perf test enforces < 1s).
_PREFIX_SCAN_ROW_CAP = 2000


def _tokenise_list(s: str) -> list[str]:
    """Return the ORDERED list of lowercase tokens (with multiplicity) from a string.

    Steps:
      1. Insert spaces at CamelCase boundaries.
      2. Lowercase.
      3. Split on any non-alphanumeric run.
      4. Drop empty strings.
    """
    expanded = _CAMEL_RE.sub(" ", s)
    lowered = expanded.lower()
    parts = _NONALNUM_RE.split(lowered)
    return [p for p in parts if p]


def _tokenise(s: str) -> set[str]:
    """Return the set of lowercase tokens from a string (see ``_tokenise_list``)."""
    return set(_tokenise_list(s))


def _name_tokens(name: str) -> frozenset[str]:
    """Stable token set for a column or row-key name (cache-friendly)."""
    return frozenset(_tokenise(name))


_BOUNDED_WORD_CACHE: dict[str, re.Pattern] = {}


def _bounded_pattern(name_lower: str) -> re.Pattern:
    """Return a compiled regex for a bounded substring match of *name_lower*."""
    if name_lower not in _BOUNDED_WORD_CACHE:
        # Escape the literal name (handles "2024-09", "T088977", etc.)
        escaped = re.escape(name_lower)
        # Require non-alphanumeric or string boundary on each side.
        pat = re.compile(r"(?<![a-z0-9])" + escaped + r"(?![a-z0-9])")
        _BOUNDED_WORD_CACHE[name_lower] = pat
    return _BOUNDED_WORD_CACHE[name_lower]


def _squash(s: str) -> str:
    """Lowercase, strip ALL non-alphanumeric chars: 'SKU-100' -> 'sku100'."""
    return _NONALNUM_RE.sub("", s.lower())


def _match_tier(name: str, name_tokens_set: frozenset[str],
                phrase_lower: str, phrase_tokens: set[str]) -> int:
    """Match quality of *name* against *phrase*.

    Returns:
      3 — verbatim bounded substring (strongest: the name appears intact),
      2 — all of the name's tokens appear in the phrase token set, OR the name's
          separator-stripped form ('sku100' for 'SKU-100') is a phrase token,
      1 — a phrase token is a truncation/prefix of the name's squashed form, or
          vice-versa ('netrev'→'netrevenue', 'eng'→'engineering'); both ≥3 chars,
      0 — no match.

    Tiering matters when stray tokens from a table-title leak into the phrase:
    e.g. "Product Price List" contributes the token 'price', which would let
    "UnitPrice" match a query for "UnitCost" via the all-tokens rule (tier 2).
    "UnitCost" appears verbatim (tier 3) and must outrank it.

    The squashed form bridges names whose separators were dropped in the phrase:
    a formula operand 'onhand_sku100' references row 'SKU-100' (squashed 'sku100').

    The prefix tier is the WEAKEST: it only decides a match when no exact/squashed
    match exists, so it can never override a higher-confidence match.
    """
    name_lower = name.lower()
    if _bounded_pattern(name_lower).search(phrase_lower):
        return 3
    if name_tokens_set and name_tokens_set.issubset(phrase_tokens):
        return 2
    squashed = _squash(name)
    if squashed and squashed in phrase_tokens:
        return 2
    # Truncation / prefix abbreviation (weakest tier).
    if squashed and len(squashed) >= 3:
        for tok in phrase_tokens:
            if len(tok) >= 3 and (squashed.startswith(tok) or tok.startswith(squashed)):
                return 1
    return 0


def _name_matches(name: str, name_tokens_set: frozenset[str],
                  phrase_lower: str, phrase_tokens: set[str]) -> bool:
    """True if *name* matches *phrase* via verbatim OR all-tokens rule."""
    return _match_tier(name, name_tokens_set, phrase_lower, phrase_tokens) > 0


# ---------------------------------------------------------------------------
# Per-table lookup structures
# ---------------------------------------------------------------------------

# Cache keyed by id(catalog) so each distinct catalog object is indexed once.
# catalog list is rebuilt per-workbook-call, so caching by Python object id
# is stable enough for a session.  We also store len(row_keys) to detect
# if the catalog entry was re-populated.
_TABLE_INDEX_CACHE: dict[int, dict] = {}


def _get_or_build_table_index(entry: dict) -> dict:
    """Return (or build and cache) the token index for a catalog entry.

    Returns a dict:
        token → list[original_row_key]
        "__col_tokens__" → {col_name: frozenset[str]}
    """
    eid = id(entry)
    cached = _TABLE_INDEX_CACHE.get(eid)
    if cached is not None and cached["_nrows"] == len(entry["row_keys"]):
        return cached

    # Build token → [row_key, ...] inverted index.
    tok_to_rows: dict[str, list[str]] = {}
    col_tokens: dict[str, frozenset[str]] = {}

    squashed_to_rows: dict[str, list[str]] = {}
    for rk in entry["row_keys"]:
        rk_str = str(rk)
        tokens = _name_tokens(rk_str)
        for tok in tokens:
            tok_to_rows.setdefault(tok, []).append(rk_str)
        # Index the separator-stripped form so 'sku100' finds 'SKU-100'.
        sq = _squash(rk_str)
        if sq:
            squashed_to_rows.setdefault(sq, []).append(rk_str)

    for col in entry["columns"]:
        col_tokens[col] = _name_tokens(col)

    idx = {
        "tok_to_rows": tok_to_rows,
        "squashed_to_rows": squashed_to_rows,
        "col_tokens": col_tokens,
        "_nrows": len(entry["row_keys"]),
        # Also store lower→original for bounded verbatim matching
        "lower_to_row": {str(rk).lower(): str(rk) for rk in entry["row_keys"]},
    }
    _TABLE_INDEX_CACHE[eid] = idx
    return idx


# ---------------------------------------------------------------------------
# Core resolver
# ---------------------------------------------------------------------------

def deterministic_resolve(
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
    phrase_token_list = _tokenise_list(phrase)
    phrase_tokens = set(phrase_token_list)

    best_score = (-1, -1)  # (combined_tier, combined_name_len)
    best: Optional[tuple[str, str, str]] = None

    for entry in catalog:
        table_id = entry["table_id"]
        columns: list[str] = entry["columns"]
        row_keys: list[str] = [str(k) for k in entry["row_keys"]]

        idx = _get_or_build_table_index(entry)
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
            tier = _match_tier(col, ctoks, phrase_lower, phrase_tokens)
            if tier > 0:
                key = (tier, len(col), len(ctoks))
                if key > best_col_key:
                    best_col_key = key
                    best_col = col
        best_col_tier = best_col_key[0] if best_col is not None else 0
        best_col_len = best_col_key[1] if best_col is not None else -1

        if best_col is None:
            continue  # No column match → skip table.

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
        for t in set(best_col_tokens) | {_squash(best_col)}:
            if residual_counts.get(t, 0) > 0:
                residual_counts[t] -= 1
        row_phrase_tokens = {t for t, c in residual_counts.items() if c > 0}
        # Remove ONE verbatim occurrence of the column from the phrase string too.
        row_phrase_lower = _bounded_pattern(best_col.lower()).sub(" ", phrase_lower, count=1)

        best_row: Optional[str] = None
        best_row_key = (-1, -1, -1)  # (tier, name_len, token_count)

        # Candidate rows: those sharing at least one token with the residual phrase,
        # plus rows whose separator-stripped form appears as a residual token.
        candidate_rows: set[str] = set()
        for tok in row_phrase_tokens:
            for rk in tok_to_rows.get(tok, []):
                candidate_rows.add(rk)
            for rk in squashed_to_rows.get(tok, []):
                candidate_rows.add(rk)

        # Also check verbatim: any row whose lowercased form appears bounded in phrase.
        # Rather than scanning all rows, scan phrase for any word-boundary tokens
        # that look like row keys (for small tables this is fast; for large tables
        # the inverted index already covers them).
        # Additionally, check lower_to_row directly for tokens in the residual phrase.
        for tok in row_phrase_tokens:
            if tok in lower_to_row:
                candidate_rows.add(lower_to_row[tok])

        # For rows not found via tokens (e.g. "2024-09" splits to ["2024","09"]),
        # we need to handle bounded substring matching on the phrase.
        # Strategy: collect any row whose lowercased form is a bounded substring.
        # We do this efficiently by looking at "non-token" entries — row keys that
        # contain non-alphanumeric characters (dates, ids with special chars).
        # These are typically few; scan only if the phrase contains digits or hyphens.
        if re.search(r"[^a-z ]", row_phrase_lower):
            # Phrase has non-letter content — check rows that contain such chars.
            for rk_lower, rk_orig in lower_to_row.items():
                if rk_orig in candidate_rows:
                    continue
                if re.search(r"[^a-z0-9]", rk_lower):
                    # This row key has special chars (date, id) — test verbatim.
                    if _bounded_pattern(rk_lower).search(row_phrase_lower):
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

        for rk in candidate_rows:
            rk_tokens = _name_tokens(rk)
            tier = _match_tier(rk, frozenset(rk_tokens), row_phrase_lower, row_phrase_tokens)
            if tier > 0:
                rkey = (tier, len(rk), len(rk_tokens))
                if rkey > best_row_key:
                    best_row_key = rkey
                    best_row = rk

        if best_row is None:
            continue  # No row match → skip table.

        best_row_tier = best_row_key[0]
        best_row_len = best_row_key[1]
        # --- Table score: prefer combined match tier, then combined name length. ---
        # A table where both the column and row appear verbatim outranks one matched
        # only via loose tokens, regardless of name length.
        score = (best_col_tier + best_row_tier, best_col_len + best_row_len)
        if score > best_score:
            best_score = score
            best = (table_id, best_row, best_col)

    return best
