from __future__ import annotations
import os
from mcg_swarm.schemas import WorkbookExtraction, Finding
from mcg_swarm.splitter import TableHandle
from mcg_swarm.analyzers.pipeline import analyze_workbook
from mcg_swarm.analyzers.registry import build_analyzers
from mcg_swarm.orchestrator import orchestrate_table
from mcg_swarm.subagent import build_subagent, build_table_validator, build_structural_reviewer
from mcg_swarm.config import SwarmConfig
from mcg_swarm.extraction import build_index
from mcg_swarm.source import as_source
from mcg_swarm.coverage import scan_handle
from mcg_swarm.views import TransposedView

GENERATOR_VERSION = "mcg-swarm-v2.0.0"


def _view_orientation(view, sheet: str):
    """Map a lens view to a persistable orientation.

    None → "vertical". Views declare theirs via an `orientation` attribute
    (TransposedView → "transposed"). An unknown view kind persists "vertical"
    plus a warning Finding: extraction still reads through the view, so the
    adapter rebuild may misread — surfacing beats silence.
    Returns (orientation, Finding | None).
    """
    if view is None:
        return "vertical", None
    orient = getattr(view, "orientation", None)
    if orient in ("vertical", "transposed"):
        return orient, None
    return "vertical", Finding(
        category="unknown-view", severity="warning", scope="sheet",
        source="static", ref=f"{sheet}!A1",
        message=(f"view {type(view).__name__} declares no known orientation; "
                 "persisted 'vertical' — adapter rebuilds may misread this sheet"))


def _interpretation(handles, view) -> tuple:
    """Layout identity for the Stage-4 winner-vs-baseline comparison — same
    notion as assess._signature: regions + header placement + view kind."""
    return (type(view).__name__ if view is not None else "",
            tuple(sorted((h.region, h.header_row, h.header_span)
                         for h in handles)))


def run_swarm(workbooks, *, llm=None, runner=None, config: SwarmConfig = SwarmConfig()) -> WorkbookExtraction:
    """Fan-out across all tabs and return a WorkbookExtraction.

    Accepts a path string, ``{"main": path}`` dict, or any ``WorkbookSource``.
    One bad tab never fails the file — its errors land on its CanonicalTable.
    resolve_messy_tab is handled internally by orchestrate_table (Task 11).
    """
    source = as_source(workbooks)            # dict/path/source all OK
    name = getattr(source, "path", "workbook")
    name = os.path.basename(name) if isinstance(name, str) else "workbook"
    # Fail fast on a misconfigured analyzer name: this is a config/programming
    # error, not a data error, so it must raise out of run_swarm rather than
    # being swallowed by the try/except below and misreported as an
    # "unreadable workbook" data error. analyze_workbook still builds its own
    # analyzers internally; this call is purely for validation.
    build_analyzers(config.analyzers)
    try:
        sheet_analyses = analyze_workbook(source, config=config, runner=runner)
    except Exception as e:
        return WorkbookExtraction(
            workbook=name,
            sheets=[],
            tables=[],
            generator_version=GENERATOR_VERSION,
            errors=[f"unreadable workbook: {e}"],
        )
    # The application injects the ReAct runner (built against its provider/transport).
    # runner is None → static-only band subagent and no table validator.
    subagent = build_subagent(llm=llm, runner=runner, config=config)
    table_validator = build_table_validator(runner=runner, config=config)
    reviewer = build_structural_reviewer(runner=runner, config=config)
    tables, sheets, wb_findings = [], [], []
    for i, sa in enumerate(sheet_analyses):
        sheets.append(sa.sheet)
        wb_findings.extend(sa.findings)
        sheet_src = sa.view or source
        orient, view_finding = _view_orientation(sa.view, sa.sheet)
        if view_finding is not None:
            wb_findings.append(view_finding)

        if not sa.handles:
            continue  # zero-handle winner (e.g. all-diagram sheet): findings already recorded

        if (sa.contested and sa.baseline_handles
                and _interpretation(sa.handles, sa.view)
                != _interpretation(sa.baseline_handles, sa.baseline_view)):
            # Stage 4 (spec §4.5): a contested non-baseline winner must prove
            # itself against the vertical baseline on the LIVE pipeline before
            # commitment — snapshot scores can miss live-only behavior (band
            # verifier, table validator). Mirrors the Layer-2 re-cut pattern.
            base_src = sa.baseline_view or source
            base_orient, base_vf = _view_orientation(sa.baseline_view, sa.sheet)
            if base_vf is not None:
                wb_findings.append(base_vf)

            def _run(src_, handles_, orient_):
                multi = len(handles_) > 1
                return [orchestrate_table(
                            src_, sh,
                            table_id=f"{sa.sheet}__{i}_{j}" if multi else f"{sa.sheet}__{i}",
                            llm=llm, subagent=subagent,
                            table_validator=table_validator,
                            detect_findings=[], orientation=orient_)
                        for j, sh in enumerate(handles_)]

            try:
                cand_tables = _run(sheet_src, sa.handles, orient)
                base_tables = _run(base_src, sa.baseline_handles, base_orient)
                cand_err = sum(len(t.errors) for t in cand_tables)
                base_err = sum(len(t.errors) for t in base_tables)
            except Exception:
                cand_tables, base_tables = None, None  # never break extraction

            if cand_tables is not None and cand_err <= base_err:
                tables.extend(cand_tables)
                wb_findings.append(Finding(
                    category="contested-layout", severity="info", scope="sheet",
                    source="static", ref=f"{sa.sheet}!A1",
                    message=(f"lens disagreement: committed {sa.method!r} "
                             f"(live errors {cand_err} vs baseline {base_err})")))
            else:
                if base_tables is None:  # the A/B itself failed → conservative
                    base_tables = _run(base_src, sa.baseline_handles, base_orient)
                tables.extend(base_tables)
                wb_findings.append(Finding(
                    category="contested-layout", severity="warning", scope="sheet",
                    source="static", ref=f"{sa.sheet}!A1",
                    message=(f"lens disagreement: {sa.method!r} raised live "
                             "errors; kept vertical baseline")))
            continue  # tables + findings committed for this sheet

        if len(sa.handles) > 1:
            # Multi-table interpretation from a lens: orchestrate each handle.
            # Layer-2 review presumes a single baseline handle, so it is skipped
            # here — multi-handle winners were already assessed at analyze time.
            for j, sh in enumerate(sa.handles):
                tables.append(orchestrate_table(
                    sheet_src, sh, table_id=f"{sa.sheet}__{i}_{j}", llm=llm,
                    subagent=subagent, table_validator=table_validator,
                    detect_findings=[], orientation=orient))
            continue

        h = sa.handles[0]
        try:
            grid = sheet_src.read_region(sa.sheet)
            scan = scan_handle(grid, h, sa.sheet)
        except Exception:
            grid, scan = None, []  # never let detection break extraction

        review = None
        if (reviewer is not None and grid is not None
                and any(f.category == "uncovered-data" for f in scan)):
            try:
                review = reviewer.review(sheet_src, h, grid, scan)
            except Exception:
                review = None  # never let alteration break extraction

        if review is not None and review.recut:
            # Live re-validation: the static gate proved the re-cut structurally
            # better, but the real per-table pipeline (band ReAct verifier, which
            # patches column role/dtype unconditionally, + table validator) can
            # behave differently on the smaller tables — a split can newly cross the
            # ReAct escalation threshold the monolithic baseline never hit. Never let
            # an accepted re-cut raise the live error count above the baseline.
            try:
                cand_tables = [orchestrate_table(
                        sheet_src, sh, table_id=f"{sa.sheet}__{i}_{j}", llm=llm,
                        subagent=subagent, table_validator=table_validator,
                        detect_findings=tf, orientation=orient)
                    for j, (sh, tf) in enumerate(
                        zip(review.handles, review.detect_findings))]
                base_table = orchestrate_table(
                    sheet_src, h, table_id=f"{sa.sheet}__{i}", llm=llm,
                    subagent=subagent, table_validator=table_validator,
                    detect_findings=[f for f in scan if f.scope != "sheet"],
                    orientation=orient)
                cand_err = sum(len(t.errors) for t in cand_tables)
                base_err = len(base_table.errors)
            except Exception:
                cand_tables, base_table = None, None  # never let it break extraction

            if cand_tables is not None and cand_err <= base_err:
                tables.extend(cand_tables)
                wb_findings.extend(review.sheet_findings)      # stays 'fixed'
            else:
                # live pipeline regressed (or failed) → keep deterministic baseline,
                # flip the detection annotation from fixed to rejected.
                tables.append(base_table if base_table is not None else orchestrate_table(
                    sheet_src, h, table_id=f"{sa.sheet}__{i}", llm=llm,
                    subagent=subagent, table_validator=table_validator,
                    detect_findings=[f for f in scan if f.scope != "sheet"],
                    orientation=orient))
                note = "re-cut raised live-pipeline errors; kept deterministic baseline"
                wb_findings.extend(
                    f.model_copy(update={"resolution": "rejected", "agent_action": note})
                    for f in scan if f.scope == "sheet")
            continue  # tables + findings already committed for this sheet

        if review is None:
            sheet_handles = [h]
            per_handle = [[f for f in scan if f.scope != "sheet"]]
            wb_findings.extend(f for f in scan if f.scope == "sheet")
        else:
            sheet_handles = review.handles          # baseline kept (reject/declined/open)
            per_handle = review.detect_findings
            wb_findings.extend(review.sheet_findings)

        multi = len(sheet_handles) > 1
        for j, (sh, tf) in enumerate(zip(sheet_handles, per_handle)):
            table_id = f"{sa.sheet}__{i}_{j}" if multi else f"{sa.sheet}__{i}"
            tables.append(orchestrate_table(
                sheet_src, sh, table_id=table_id, llm=llm,
                subagent=subagent, table_validator=table_validator,
                detect_findings=tf, orientation=orient))
    return WorkbookExtraction(
        workbook=name,
        sheets=sheets,
        tables=tables,
        generator_version=GENERATOR_VERSION,
        findings=wb_findings,
    )


def build_indices(path, extraction: WorkbookExtraction) -> dict:
    """Rebuild ExtractionIndex objects deterministically for the adapter.

    Skips tables that have errors (failed tables have no valid index).
    Transposed tables (extracted through a TransposedView) are rebuilt through
    the same view kind so their view-coordinate regions resolve correctly.
    """
    out = {}
    for t in extraction.tables:
        if t.errors:  # don't build an index for a failed table
            continue
        handle = TableHandle(
            sheet=t.sheet,
            region=t.region,
            header_row=t.header_row,
            columns=t.columns,
            header_span=getattr(t, "header_span", 1),
        )
        src = as_source(path)
        if t.orientation == "transposed":
            src = TransposedView(src)
        out[t.table_id] = build_index(src, handle, row_key=t.extraction.row_key)
    return out
