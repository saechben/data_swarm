"""Wiring stub for benchmarking the real MCG swarm (v2 — independent canonical tables).

Fill in the TODOs to connect your orchestrator. The harness calls ``prepare`` once
per workbook, then asks the five capability questions. Keep all per-workbook state
(the produced ``WorkbookExtraction`` and its ``CanonicalTable``s) cached on ``self``
keyed by workbook filename.

Nothing here may read the ``label`` argument for answers — that's ground truth. Use
only what your system would legitimately have: the workbook path(s). v2 is data-driven
(the table itself drives extraction), so ``business_logic`` is not required.
"""
from __future__ import annotations

from typing import Any, Optional

from eval.adapters.base import DetectedMeasure, EvalAdapter, SemanticResult
from eval.schemas import WorkbookLabel


class SwarmAdapter(EvalAdapter):
    name = "swarm"

    def __init__(self) -> None:
        self._extractions: dict[str, Any] = {}   # wb -> produced WorkbookExtraction
        self._tables: dict[str, Any] = {}        # wb -> {table_id: CanonicalTable}
        self._paths: dict[str, str] = {}

    def prepare(self, workbook_path: str, label: WorkbookLabel) -> None:
        self._paths[label.workbook] = workbook_path
        # TODO: run the swarm here, e.g.
        #   result = run_swarm(workbooks={"main": workbook_path})   # WorkbookExtraction
        #   self._extractions[label.workbook] = result
        #   self._tables[label.workbook] = {t.table_id: t for t in result.tables}
        raise NotImplementedError(
            "Wire SwarmAdapter.prepare() to your orchestrator. "
            "Run `--adapter oracle` until then."
        )

    def table_region(self, wb, table_id, table_name, sheet) -> Optional[str]:
        # TODO: find the canonical table matching (table_name, sheet) and return its
        # A1 region (CanonicalTable.region from the deterministic splitter).
        raise NotImplementedError

    def extract(self, wb, table_id, table_name, sheet, row_label, col_label) -> Any:
        # TODO: locate the canonical table's extraction script and call
        # query(row_label, col_label); return the ExtractedValue.value.
        raise NotImplementedError

    def answer_semantic(self, wb, query) -> SemanticResult:
        # TODO: run your query-understanding path: map NL -> (table, column, row),
        # extract via the live query, and return value + where it resolved.
        raise NotImplementedError

    def detected_measures(self, wb) -> list[DetectedMeasure]:
        # TODO: emit one DetectedMeasure per canonical column/field: its
        # (table_id, row, col) directive, resolved value, name.
        raise NotImplementedError

    def compute_formula(self, wb, expression, operands, business_logic) -> Optional[float]:
        # TODO: evaluate the relevant *intra-table* formula end-to-end (build env from
        # the live operands, run the safe evaluator). No cross-table composition.
        # business_logic is a v1 carry-over; v2 is data-driven — ignore it.
        raise NotImplementedError


# Registry the CLI uses to resolve --adapter names.
def get_adapter(name: str) -> EvalAdapter:
    from eval.adapters.oracle import NoisyOracleAdapter, OracleAdapter

    if name == "oracle":
        return OracleAdapter()
    if name == "noisy":
        return NoisyOracleAdapter()
    if name == "swarm":
        return SwarmAdapter()
    raise ValueError(f"unknown adapter '{name}' (choose: oracle, noisy, swarm)")
