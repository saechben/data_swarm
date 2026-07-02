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


def _dedup(candidates: list[LayoutCandidate]) -> list[LayoutCandidate]:
    """Stage 0: collapse identical interpretations, keeping highest confidence."""
    best_by_sig: dict[tuple, LayoutCandidate] = {}
    for c in candidates:
        sig = _signature(c)
        cur = best_by_sig.get(sig)
        if cur is None or c.confidence > cur.confidence:
            best_by_sig[sig] = c
    return list(best_by_sig.values())


def _dominates(score_a: tuple, score_b: tuple) -> bool:
    """True when score_a is not-worse than score_b on all three axes
    (coverage higher-or-equal, errors and gaps lower-or-equal)."""
    return (score_a[0] >= score_b[0]
            and score_a[1] <= score_b[1]
            and score_a[2] <= score_b[2])


def rank_candidates(candidates: list[LayoutCandidate], *, source, grid,
                    sheet: str) -> list:
    """Stage 1 (rich): score each deduped candidate with the Layer-2 three-way
    metric and rank best-first: coverage desc, errors asc, gaps asc, confidence
    desc. Returns [(candidate, (coverage, errors, gaps)), ...]."""
    if source is None:
        raise ValueError("rank_candidates requires a source (got None)")
    # Lazy: structural pulls in the orchestration stack; keep analyzers light.
    from mcg_swarm.subagent.structural import score_handles

    deduped = _dedup(candidates)
    scored = []
    for c in deduped:
        # A viewed candidate's handles live in view coordinates: score them
        # against the view's source and grid, not the raw sheet (spec §4.3).
        c_src = c.view if c.view is not None else source
        c_grid = c.view.read_region(sheet) if c.view is not None else grid
        scored.append((c, score_handles(c_src, c_grid, list(c.handles), sheet)))
    scored.sort(key=lambda cs: (-cs[1][0], cs[1][1], cs[1][2], -cs[0].confidence))
    return scored


def assess_sheet(candidates: list[LayoutCandidate], *, source, grid,
                 sheet: str) -> LayoutCandidate:
    """Full deterministic sheet assessment (spec §4.5 Stages 0-1).

    Single candidate is returned by identity (the neutrality anchor). Plan B2
    inserts the agentic arbiter where the top candidate does NOT dominate the
    runner-up (genuine disagreement)."""
    if not candidates:
        raise ValueError("assess_sheet requires at least one candidate")
    deduped = _dedup(candidates)
    if len(deduped) == 1:
        return deduped[0]
    ranked = rank_candidates(deduped, source=source, grid=grid, sheet=sheet)
    return ranked[0][0]
