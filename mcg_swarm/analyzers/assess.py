from __future__ import annotations

from mcg_swarm.analyzers.base import LayoutCandidate


def _signature(candidate: LayoutCandidate) -> tuple:
    """Interpretation identity of a candidate. Two candidates are the same
    interpretation only if they claim the same regions WITH the same header
    placement/span AND read through the same kind of view."""
    view_tag = type(candidate.view).__name__ if candidate.view is not None else ""
    return (view_tag, tuple(sorted(
        (h.region, h.header_row, h.header_span) for h in candidate.handles)))


def assess(candidates: list[LayoutCandidate]) -> LayoutCandidate:
    """Pick the winning candidate for a sheet.

    Phase A: deterministic only.
      Stage 0 — dedup by region signature, keeping the highest-confidence per signature.
      Stage 1 — return the candidate maximizing (coverage, confidence).
    A single candidate is returned unchanged (the Phase-A neutrality anchor).
    """
    if not candidates:
        raise ValueError("assess requires at least one candidate")

    # Stage 0: dedup — collapse identical region signatures.
    best_by_sig: dict[tuple, LayoutCandidate] = {}
    for c in candidates:
        sig = _signature(c)
        cur = best_by_sig.get(sig)
        if cur is None or c.confidence > cur.confidence:
            best_by_sig[sig] = c

    # Stage 1: rank by coverage, then confidence.
    return max(best_by_sig.values(), key=lambda c: (c.coverage, c.confidence))
