"""Run an adapter over the labeled workbooks and score the four capabilities."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from eval.adapters.base import EvalAdapter
from eval.schemas import WorkbookLabel
from eval.util import range_iou, values_match

HERE = Path(__file__).resolve().parents[1]
DEFAULT_LABELS = HERE / "data" / "labels"
DEFAULT_WORKBOOKS = HERE / "data" / "workbooks"

# sample.type -> capability bucket
CAP_OF_TYPE = {
    "boundary": "table_boundaries",
    "extraction": "value_extraction",
    "semantic": "semantic_extraction",
    "formula": "formula_compute",
}
CAPABILITIES = [
    "table_boundaries", "value_extraction", "semantic_extraction",
    "formula_compute", "measure_detection",
]


@dataclass
class SampleResult:
    workbook: str
    difficulty: str
    capability: str
    sample_id: str
    sample_type: str
    passed: bool
    expected: Any
    got: Any
    detail: str = ""


@dataclass
class MeasureResult:
    workbook: str
    difficulty: str
    tp: int
    fp: int
    fn: int

    @property
    def precision(self) -> float:
        d = self.tp + self.fp
        return self.tp / d if d else 1.0

    @property
    def recall(self) -> float:
        d = self.tp + self.fn
        return self.tp / d if d else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


@dataclass
class RunResult:
    adapter: str
    samples: list[SampleResult] = field(default_factory=list)
    measures: list[MeasureResult] = field(default_factory=list)


def load_labels(labels_dir: Path) -> list[WorkbookLabel]:
    out = []
    for p in sorted(labels_dir.glob("*.json")):
        out.append(WorkbookLabel.model_validate_json(p.read_text()))
    return out


def _score_measures(adapter: EvalAdapter, label: WorkbookLabel) -> MeasureResult:
    detected = adapter.detected_measures(label.workbook)
    labeled = {
        (m.table_id, m.row_label, m.col_label): m.value for m in label.measures
    }
    matched: set[tuple[str, str, str]] = set()
    tp = fp = 0
    for d in detected:
        key = (d.table_id, d.row_label, d.col_label)
        if key in labeled and values_match(labeled[key], d.value, 1e-9,
                                            "number" if isinstance(labeled[key], (int, float)) else "string"):
            if key not in matched:
                tp += 1
                matched.add(key)
            else:
                fp += 1  # duplicate detection
        else:
            fp += 1
    fn = len(labeled) - len(matched)
    return MeasureResult(label.workbook, label.difficulty, tp, fp, fn)


def run_benchmark(
    adapter: EvalAdapter,
    labels: list[WorkbookLabel],
    workbooks_dir: Path = DEFAULT_WORKBOOKS,
) -> RunResult:
    result = RunResult(adapter=adapter.name)
    for label in labels:
        path = str(workbooks_dir / label.workbook)
        adapter.prepare(path, label)
        wb, diff = label.workbook, label.difficulty

        for s in label.samples:
            cap = CAP_OF_TYPE[s.type]
            if s.type == "boundary":
                got = adapter.table_region(wb, s.table_id, s.table_name, s.sheet)
                iou = range_iou(s.expected_region, got) if got else 0.0
                passed = bool(got) and iou >= s.min_iou
                result.samples.append(SampleResult(
                    wb, diff, cap, s.id, s.type, passed, s.expected_region, got,
                    detail=f"IoU={iou:.3f}"))
            elif s.type == "extraction":
                got = adapter.extract(wb, s.table_id, s.table, s.sheet,
                                      s.row_label, s.col_label)
                passed = values_match(s.expected_value, got, s.tolerance, s.dtype)
                result.samples.append(SampleResult(
                    wb, diff, cap, s.id, s.type, passed, s.expected_value, got,
                    detail=f"{s.table}!{s.row_label}/{s.col_label}"))
            elif s.type == "semantic":
                res = adapter.answer_semantic(wb, s.query)
                passed = values_match(s.expected_value, res.value, s.tolerance, s.dtype)
                loc = (res.table_id == s.expected_table_id
                       and res.row_label == s.expected_row_label
                       and res.col_label == s.expected_col_label)
                result.samples.append(SampleResult(
                    wb, diff, cap, s.id, s.type, passed, s.expected_value, res.value,
                    detail=f"loc_ok={loc} q={s.query!r}"))
            elif s.type == "formula":
                got = adapter.compute_formula(wb, s.expression, s.operands,
                                              s.business_logic)
                passed = got is not None and values_match(
                    s.expected_value, got, s.tolerance, "number")
                result.samples.append(SampleResult(
                    wb, diff, cap, s.id, s.type, passed, s.expected_value, got,
                    detail=s.description))

        result.measures.append(_score_measures(adapter, label))
    return result


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
def aggregate(result: RunResult) -> dict:
    def rate(items):
        items = list(items)
        n = len(items)
        p = sum(1 for x in items if x.passed)
        return {"passed": p, "total": n, "rate": (p / n if n else 1.0)}

    by_cap = {}
    for cap in ["table_boundaries", "value_extraction", "semantic_extraction",
                "formula_compute"]:
        by_cap[cap] = rate(s for s in result.samples if s.capability == cap)

    # measure detection (micro precision/recall/f1 across workbooks)
    tp = sum(m.tp for m in result.measures)
    fp = sum(m.fp for m in result.measures)
    fn = sum(m.fn for m in result.measures)
    prec = tp / (tp + fp) if (tp + fp) else 1.0
    rec = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    by_cap["measure_detection"] = {
        "precision": prec, "recall": rec, "f1": f1, "tp": tp, "fp": fp, "fn": fn,
    }

    by_tier = {}
    for tier in ["easy", "medium", "hard"]:
        by_tier[tier] = rate(s for s in result.samples if s.difficulty == tier)

    by_workbook = {}
    for s in result.samples:
        by_workbook.setdefault(s.workbook, []).append(s)
    wb_rates = {
        wb: rate(items) | {"difficulty": items[0].difficulty}
        for wb, items in by_workbook.items()
    }

    overall = rate(result.samples)
    return {
        "adapter": result.adapter,
        "overall": overall,
        "by_capability": by_cap,
        "by_difficulty": by_tier,
        "by_workbook": wb_rates,
    }


def to_payload(result: RunResult) -> dict:
    return {
        "summary": aggregate(result),
        "samples": [asdict(s) for s in result.samples],
        "measure_detail": [
            {"workbook": m.workbook, "difficulty": m.difficulty, "tp": m.tp,
             "fp": m.fp, "fn": m.fn, "precision": m.precision, "recall": m.recall,
             "f1": m.f1}
            for m in result.measures
        ],
    }
