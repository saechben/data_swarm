"""The adapter boundary between the eval harness and the system under test.

The harness never imports the swarm. It talks to an ``EvalAdapter``. To benchmark
your swarm, subclass ``EvalAdapter``, run your orchestrator in ``prepare()``, and
answer the five queries below from its outputs (the ``WorkbookExtraction`` of
independent ``CanonicalTable``s + their extraction scripts). See ``oracle.py`` for a
reference and ``swarm_adapter.py`` for a wiring stub.

Each method corresponds to one scored capability:
  - table_region        -> table-boundary detection
  - extract             -> measure & value extraction (the query(row, col) contract)
  - answer_semantic     -> natural-language retrieval + extraction
  - detected_measures   -> measure detection & mapping
  - compute_formula     -> intra-table formula computation
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from eval.schemas import WorkbookLabel


@dataclass
class SemanticResult:
    value: Any = None
    table_id: Optional[str] = None
    row_label: Optional[str] = None
    col_label: Optional[str] = None


@dataclass
class DetectedMeasure:
    table_id: str
    row_label: str
    col_label: str
    value: Any
    semantic_name: str = ""
    aliases: list[str] = field(default_factory=list)


class EvalAdapter(ABC):
    """Implement these against your system. ``wb`` is the workbook filename."""

    name: str = "abstract"

    def prepare(self, workbook_path: str, label: WorkbookLabel) -> None:
        """Run any per-workbook work once (e.g. run the swarm, cache the WorkbookExtraction).

        ``label`` is passed for convenience; a *real* adapter must NOT read answers
        from it — only metadata it would legitimately have (path, sheet names). The
        oracle adapter intentionally uses it as ground truth.
        """

    @abstractmethod
    def table_region(self, wb: str, table_id: str, table_name: str,
                     sheet: str) -> Optional[str]:
        """Return the detected A1 bounding box for the named table, or None."""

    @abstractmethod
    def extract(self, wb: str, table_id: str, table_name: str, sheet: str,
                row_label: str, col_label: str) -> Any:
        """Return the value at (row_label, col_label) in the table, or None."""

    @abstractmethod
    def answer_semantic(self, wb: str, query: str) -> SemanticResult:
        """Answer a natural-language query about a value in the workbook."""

    @abstractmethod
    def detected_measures(self, wb: str) -> list[DetectedMeasure]:
        """Return the canonical measures the system surfaced for this workbook."""

    @abstractmethod
    def compute_formula(self, wb: str, expression: str, operands: dict[str, str],
                        business_logic: str) -> Optional[float]:
        """Compute an intra-table formula for this workbook (no cross-table graph).

        ``business_logic`` is a v1 carry-over still present in the label schema; v2 is
        data-driven, so a real adapter should not depend on it.
        """
