"""SwarmAdapter — wires the MCG swarm v2 to the eval harness.

``prepare`` runs the swarm once per workbook, builds ExtractionIndex objects,
then maps label table-ids to swarm CanonicalTables by sheet + region IoU so
all five capability methods can answer using label-facing ids.

Nothing here reads the ``label`` argument for answer *values* — only metadata
(sheet names, table regions) used for the label↔swarm mapping.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from eval.adapters.base import DetectedMeasure, EvalAdapter, SemanticResult
from eval.adapters.coord_resolution import build_catalog, build_full_catalog, resolve_via_llm
from eval.schemas import WorkbookLabel
from eval.util import range_iou
from mcg_swarm.runner import run_swarm, build_indices
from mcg_swarm.formulas import eval_expr
from mcg_swarm.env import load_dotenv
from mcg_swarm.llm.client import AnthropicClient
from mcg_swarm.resolve import deterministic_resolve


MEASURE_ROW_CAP = 200
# Labeled measures only live on small summary/metric tables (≤12 rows in this corpus).
# Large data tables (transactions, ledger) carry NO labeled measures — emitting per-cell
# measures on them floods false positives (precision crashes to ~2%).
# Skip measure emission entirely for tables whose row-key count exceeds this threshold.
MEASURE_MAX_TABLE_ROWS = 40


class SwarmAdapter(EvalAdapter):
    name = "swarm"

    def __init__(self) -> None:
        self._tables: dict[str, dict] = {}          # wb -> {label_table_id: CanonicalTable}
        self._indices: dict[str, dict] = {}         # wb -> {label_table_id: ExtractionIndex}
        self._paths: dict[str, str] = {}
        self._measures_cache: dict[str, list] = {}  # wb -> list[DetectedMeasure]
        self._llm = None                             # set during prepare(); injectable for tests
        self._catalog_cache: dict[str, list] = {}        # wb -> LLM catalog (row_keys capped)
        self._full_catalog_cache: dict[str, list] = {}   # wb -> deterministic catalog (all rows)
        self._coord_cache: dict[tuple, Any] = {}    # (wb, phrase) -> (table_id, row, col) | None

    def prepare(self, workbook_path: str, label: WorkbookLabel) -> None:
        wb = label.workbook
        self._paths[wb] = workbook_path

        # Load .env (tolerates missing file, won't clobber existing env vars).
        load_dotenv()

        # Wire LLM only when a key is available; fall back to deterministic (llm=None).
        llm = AnthropicClient(model="claude-haiku-4-5-20251001") if os.environ.get("ANTHROPIC_API_KEY") else None
        self._llm = llm

        # Run the swarm and build extraction indices keyed by swarm table_id.
        ext = run_swarm({"main": workbook_path}, llm=llm)
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

    # ------------------------------------------------------------------
    # Catalog + semantic resolver
    # ------------------------------------------------------------------

    def _build_catalog(self, wb: str) -> list[dict]:
        """Compact (row_keys-capped) catalog for the LLM prompt; cached per workbook."""
        if wb not in self._catalog_cache:
            self._catalog_cache[wb] = build_catalog(self._indices.get(wb, {}))
        return self._catalog_cache[wb]

    def _build_full_catalog(self, wb: str) -> list[dict]:
        """Full (uncapped) catalog for the deterministic resolver; cached per workbook."""
        if wb not in self._full_catalog_cache:
            self._full_catalog_cache[wb] = build_full_catalog(self._indices.get(wb, {}))
        return self._full_catalog_cache[wb]

    def resolve_coord(self, wb: str, phrase: str) -> Optional[tuple]:
        """Map a phrase to (table_id, row_label, col_label) or None.

        Try LLM first (if available); fall back to deterministic token-matching
        resolver when LLM is absent or fails.  Result cached per (wb, phrase).
        """
        cache_key = (wb, phrase)
        if cache_key in self._coord_cache:
            return self._coord_cache[cache_key]

        result = None
        try:
            # --- LLM path (uses capped catalog to keep prompt size small) ---
            if self._llm is not None:
                catalog = self._build_catalog(wb)
                if catalog:
                    result = self._resolve_via_llm(catalog, phrase)

            # --- Deterministic fallback (uses FULL row_keys — fast via token index) ---
            if result is None:
                full_catalog = self._build_full_catalog(wb)
                if full_catalog:
                    result = deterministic_resolve(phrase, full_catalog)

        except Exception as _exc:
            import sys
            print(f"[swarm_adapter] resolve_coord error ({type(_exc).__name__}): {_exc}", file=sys.stderr)
            result = None

        self._coord_cache[cache_key] = result
        return result

    def _resolve_via_llm(self, catalog: list[dict], phrase: str) -> Optional[tuple]:
        """Resolve phrase against the catalog via the LLM. Returns (table_id, row, col) or None."""
        return resolve_via_llm(self._llm, catalog, phrase)

    # ------------------------------------------------------------------
    # Capability methods
    # ------------------------------------------------------------------

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
        try:
            coord = self.resolve_coord(wb, query)
            if coord is None:
                return SemanticResult()
            table_id, row_label, col_label = coord
            idx = self._indices.get(wb, {}).get(table_id)
            if idx is None:
                return SemanticResult()
            extracted = idx.query(row_label, col_label)
            return SemanticResult(
                value=extracted.value,
                table_id=table_id,
                row_label=row_label,
                col_label=col_label,
            )
        except Exception:
            return SemanticResult()

    def detected_measures(self, wb: str) -> list[DetectedMeasure]:
        # Memoize: compute once per workbook, return cached on subsequent calls.
        if wb in self._measures_cache:
            return self._measures_cache[wb]

        out: list[DetectedMeasure] = []
        for label_table_id, idx in self._indices.get(wb, {}).items():
            total = len(idx._key_to_phys)
            # Skip large data tables — they carry no labeled measures and flood false positives.
            if total > MEASURE_MAX_TABLE_ROWS:
                continue
            capped = MEASURE_ROW_CAP is not None and total > MEASURE_ROW_CAP
            if capped:
                print(
                    f"[swarm_adapter] measure emission capped at {MEASURE_ROW_CAP} rows "
                    f"for table {label_table_id} ({total} rows)"
                )

            # One workbook open for the whole table — O(1) opens per table.
            rows = idx.read_all(max_rows=MEASURE_ROW_CAP)

            for row_key, col_name, value, _cell_ref in rows:
                if value is None:
                    continue
                col_spec = idx.columns.get(col_name)
                # Emit only numeric value/computed columns; skip key columns and
                # non-numeric columns — they only add false positives.
                if col_spec is None:
                    continue
                if col_spec.role not in ("value", "computed"):
                    continue
                if col_spec.dtype != "number":
                    continue
                out.append(DetectedMeasure(
                    table_id=label_table_id,
                    row_label=str(row_key),
                    col_label=col_name,
                    value=value,
                    semantic_name=col_name,
                ))

        self._measures_cache[wb] = out
        return out

    def compute_formula(self, wb: str, expression: str, operands: dict[str, str],
                        business_logic: str) -> Optional[float]:
        try:
            env: dict[str, Any] = {}
            for symbol, semantic_name in operands.items():
                coord = self.resolve_coord(wb, semantic_name)
                if coord is None:
                    return None
                table_id, row_label, col_label = coord
                idx = self._indices.get(wb, {}).get(table_id)
                if idx is None:
                    return None
                extracted = idx.query(row_label, col_label)
                if extracted.value is None:
                    return None
                try:
                    env[symbol] = float(extracted.value)
                except (TypeError, ValueError):
                    return None
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
