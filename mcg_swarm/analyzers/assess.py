from __future__ import annotations

from dataclasses import dataclass

from mcg_swarm.analyzers.base import LayoutCandidate
from mcg_swarm.schemas import Finding


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


@dataclass(frozen=True)
class Assessment:
    """assess_sheet_full result: the winner plus what Stages 3-4 need to guard it.

    baseline:  the deduped vertical-lens candidate when present (the floor and
               run_swarm's live A/B compare against it); None otherwise.
    contested: the top candidate failed to dominate the runner-up — genuine
               lens disagreement. run_swarm live-re-validates contested
               non-baseline winners before commitment (Stage 4).
    findings:  sheet-scope Finding records from the arbiter/floor decisions.
    """
    winner: LayoutCandidate
    baseline: LayoutCandidate | None
    contested: bool
    findings: tuple = ()


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


def assess_sheet_full(candidates: list[LayoutCandidate], *, source, grid,
                      sheet: str, arbiter=None) -> Assessment:
    """Spec §4.5 Stages 0-3. Stage 4 (live re-validation) happens in run_swarm.

    Stage 0: dedup. A single surviving interpretation returns by identity
             (the neutrality anchor) — no scoring, no agent.
    Stage 1: rich rank. When the top dominates the runner-up on all three
             axes, short-circuit deterministically (no agent).
    Stage 2: genuine disagreement — the arbiter (duck-typed: choose(topk,
             source=..., sheet=...) -> int), when injected, picks among the
             top-K (K<=3). Arbiter failures/out-of-range picks degrade to the
             deterministic top with a warning finding. Never raises for that.
    Stage 3: floor — a winner that is not the vertical baseline must be
             provably not-worse (>= baseline coverage, <= baseline errors) or
             the baseline stands. The ensemble can never score below today's
             splitter on any sheet.
    """
    if not candidates:
        raise ValueError("assess_sheet requires at least one candidate")
    deduped = _dedup(candidates)
    # Baseline = the vertical lens's interpretation. Look up by label first;
    # if _dedup handed the identical interpretation to a higher-confidence
    # lens (label steal), recover it by signature so the floor and Stage-4
    # never silently disable (B2b final-review #4).
    baseline = next((c for c in deduped if c.method == "vertical"), None)
    if baseline is None:
        v = next((c for c in candidates if c.method == "vertical"), None)
        if v is not None:
            vsig = _signature(v)
            baseline = next((c for c in deduped if _signature(c) == vsig), None)
    if len(deduped) == 1:
        return Assessment(deduped[0], baseline, contested=False)

    ranked = rank_candidates(deduped, source=source, grid=grid, sheet=sheet)
    (top, top_score), (_, runner_up_score) = ranked[0], ranked[1]
    if _dominates(top_score, runner_up_score):
        return Assessment(top, baseline, contested=False)

    findings: list[Finding] = []
    winner, w_score = top, top_score
    if arbiter is not None:
        topk = ranked[:3]
        idx = 0
        try:
            idx = int(arbiter.choose(topk, source=source, sheet=sheet))
        except Exception as e:  # agent trouble must not sink assessment
            findings.append(Finding(
                category="arbiter-error", severity="warning", scope="sheet",
                source="agent", ref=f"{sheet}!A1",
                message=f"arbiter failed ({e}); kept deterministic top"))
        if not 0 <= idx < len(topk):
            findings.append(Finding(
                category="arbiter-error", severity="warning", scope="sheet",
                source="agent", ref=f"{sheet}!A1",
                message=f"arbiter chose out-of-range candidate {idx}; "
                        "kept deterministic top"))
            idx = 0
        winner, w_score = topk[idx]
        if idx != 0:
            findings.append(Finding(
                category="arbiter-choice", severity="info", scope="sheet",
                source="agent", ref=f"{sheet}!A1",
                message=(f"arbiter chose {winner.method!r} over deterministic "
                         f"top {top.method!r}")))

    if baseline is not None and _signature(winner) != _signature(baseline):
        b_score = next(s for c, s in ranked if _signature(c) == _signature(baseline))
        if not (w_score[0] >= b_score[0] and w_score[1] <= b_score[1]):
            findings.append(Finding(
                category="assessor-floor", severity="info", scope="sheet",
                source="static", ref=f"{sheet}!A1",
                message=(f"winner {winner.method!r} scored below the vertical "
                         f"baseline (coverage {w_score[0]} vs {b_score[0]}, "
                         f"errors {w_score[1]} vs {b_score[1]}); kept baseline")))
            winner = baseline
    return Assessment(winner, baseline, contested=True, findings=tuple(findings))


def assess_sheet(candidates: list[LayoutCandidate], *, source, grid,
                 sheet: str, arbiter=None) -> LayoutCandidate:
    """Back-compat wrapper: the assessed winner only (see assess_sheet_full)."""
    return assess_sheet_full(candidates, source=source, grid=grid, sheet=sheet,
                             arbiter=arbiter).winner
