"""SwarmAdapter — wires the MCG swarm v2 to the eval harness.

``prepare`` runs the swarm once per workbook, builds ExtractionIndex objects,
then maps label table-ids to swarm CanonicalTables by sheet + region IoU so
all five capability methods can answer using label-facing ids.

Nothing here reads the ``label`` argument for answer *values* — only metadata
(sheet names, table regions) used for the label↔swarm mapping.
"""
from __future__ import annotations

from typing import Any, Optional

from eval.adapters.base import DetectedMeasure, EvalAdapter, SemanticResult
from eval.schemas import WorkbookLabel
from eval.util import range_iou
from mcg_swarm.runner import run_swarm, build_indices
from mcg_swarm.formulas import eval_expr


class SwarmAdapter(EvalAdapter):
    name = "swarm"

    def __init__(self) -> None:
        self._tables: dict[str, dict] = {}   # wb -> {label_table_id: CanonicalTable}
        self._indices: dict[str, dict] = {}  # wb -> {label_table_id: ExtractionIndex}
        self._paths: dict[str, str] = {}

    def prepare(self, workbook_path: str, label: WorkbookLabel) -> None:
        wb = label.workbook
        self._paths[wb] = workbook_path

        # Run the swarm and build extraction indices keyed by swarm table_id.
        ext = run_swarm({"main": workbook_path})
        idxs = build_indices(workbook_path, ext)  # {swarm_table_id: ExtractionIndex}

        # Map label table-ids -> swarm CanonicalTables by sheet match + max IoU.
        tables_map: dict[str, Any] = {}
        indices_map: dict[str, Any] = {}

        for lt in label.tables:
            best_iou = -1.0
            best_swarm_table = None
            for t in ext.tables:
                if t.sheet != lt.sheet:
                    continue
                iou = range_iou(lt.region, t.region)
                if iou > best_iou:
                    best_iou = iou
                    best_swarm_table = t

            if best_swarm_table is not None:
                tables_map[lt.table_id] = best_swarm_table
                # Only include index if the swarm successfully indexed this table.
                if best_swarm_table.table_id in idxs:
                    indices_map[lt.table_id] = idxs[best_swarm_table.table_id]

        self._tables[wb] = tables_map
        self._indices[wb] = indices_map

    def table_region(self, wb: str, table_id: str, table_name: str,
                     sheet: str) -> Optional[str]:
        t = self._tables.get(wb, {}).get(table_id)
        return t.region if t is not None else None

    def extract(self, wb: str, table_id: str, table_name: str, sheet: str,
                row_label: str, col_label: str) -> Any:
        idx = self._indices.get(wb, {}).get(table_id)
        if idx is None:
            return None
        try:
            return idx.query(row_label, col_label).value
        except KeyError:
            return None

    def answer_semantic(self, wb: str, query: str) -> SemanticResult:
        # Semantic NL path deferred — scores 0 for now, per plan.
        return SemanticResult()

    def detected_measures(self, wb: str) -> list[DetectedMeasure]:
        out = []
        for label_table_id, idx in self._indices.get(wb, {}).items():
            for col_name in idx.column_names():
                col_spec = idx.columns.get(col_name)
                if col_spec and col_spec.role == "key":
                    continue  # skip key columns — they are row labels, not values
                for row_key in idx._key_to_phys:
                    try:
                        val = idx.query(row_key, col_name).value
                    except KeyError:
                        continue
                    out.append(DetectedMeasure(
                        table_id=label_table_id,
                        row_label=str(row_key),
                        col_label=col_name,
                        value=val,
                        semantic_name=col_name,
                    ))
        return out

    def compute_formula(self, wb: str, expression: str, operands: dict[str, str],
                        business_logic: str) -> Optional[float]:
        # Build semantic_name -> value map from detected measures.
        measures: dict[str, Any] = {
            dm.semantic_name: dm.value for dm in self.detected_measures(wb)
        }
        env: dict[str, Any] = {}
        for symbol, semantic_name in operands.items():
            if semantic_name not in measures:
                return None
            env[symbol] = measures[semantic_name]
        try:
            return float(eval_expr(expression, env))
        except Exception:
            return None


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
