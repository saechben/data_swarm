"""Tokenisation and name/phrase matching primitives for the resolver.

These are pure functions (plus a regex-compilation memo) shared by the index
builder and the core resolver. See ``mcg_swarm.resolve`` for the algorithm.
"""
from __future__ import annotations

import re

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_NONALNUM_RE = re.compile(r"[^a-z0-9]+")


def tokenise_list(s: str) -> list[str]:
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


def tokenise(s: str) -> set[str]:
    """Return the set of lowercase tokens from a string (see ``tokenise_list``)."""
    return set(tokenise_list(s))


def name_tokens(name: str) -> frozenset[str]:
    """Stable token set for a column or row-key name (cache-friendly)."""
    return frozenset(tokenise(name))


# Pure memo of compiled regexes keyed by the lowercased name — no correctness
# concern, just avoids recompiling the same bounded-substring pattern.
_BOUNDED_WORD_CACHE: dict[str, re.Pattern] = {}


def bounded_pattern(name_lower: str) -> re.Pattern:
    """Return a compiled regex for a bounded substring match of *name_lower*."""
    if name_lower not in _BOUNDED_WORD_CACHE:
        # Escape the literal name (handles "2024-09", "T088977", etc.)
        escaped = re.escape(name_lower)
        # Require non-alphanumeric or string boundary on each side.
        pat = re.compile(r"(?<![a-z0-9])" + escaped + r"(?![a-z0-9])")
        _BOUNDED_WORD_CACHE[name_lower] = pat
    return _BOUNDED_WORD_CACHE[name_lower]


def squash(s: str) -> str:
    """Lowercase, strip ALL non-alphanumeric chars: 'SKU-100' -> 'sku100'."""
    return _NONALNUM_RE.sub("", s.lower())


def match_tier(name: str, name_tokens_set: frozenset[str],
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
    if bounded_pattern(name_lower).search(phrase_lower):
        return 3
    if name_tokens_set and name_tokens_set.issubset(phrase_tokens):
        return 2
    squashed = squash(name)
    if squashed and squashed in phrase_tokens:
        return 2
    # Truncation / prefix abbreviation (weakest tier).
    if squashed and len(squashed) >= 3:
        for tok in phrase_tokens:
            if len(tok) >= 3 and (squashed.startswith(tok) or tok.startswith(squashed)):
                return 1
    return 0


def name_matches(name: str, name_tokens_set: frozenset[str],
                 phrase_lower: str, phrase_tokens: set[str]) -> bool:
    """True if *name* matches *phrase* via verbatim OR all-tokens rule."""
    return match_tier(name, name_tokens_set, phrase_lower, phrase_tokens) > 0
