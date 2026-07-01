from __future__ import annotations

from mcg_swarm.analyzers.base import LayoutCandidate


def _signature(candidate: LayoutCandidate) -> tuple[str, ...]:
    """Region-set identity of a candidate — two candidates that claim the same
    regions are considered the same interpretation."""
    return tuple(sorted(h.region for h in candidate.handles))


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
    best_by_sig: dict[tuple[str, ...], LayoutCandidate] = {}
    for c in candidates:
        sig = _signature(c)
        cur = best_by_sig.get(sig)
        if cur is None or c.confidence > cur.confidence:
            best_by_sig[sig] = c

    # Stage 1: rank by coverage, then confidence.
    return max(best_by_sig.values(), key=lambda c: (c.coverage, c.confidence))
