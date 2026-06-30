"""Structured, categorized logging of repair passes — the data runway for deciding
which failure categories are worth automating with deterministic fixes later."""
from __future__ import annotations
import json
import logging
import os

_log = logging.getLogger("mcg_swarm.repair")

_PREFIXES = [
    ("coverage gap", "coverage_gap"),
    ("column-name", "column_name"),
    ("column-integrity", "column_integrity"),
    ("row-integrity", "row_integrity"),
    ("round-trip", "round_trip"),
    ("dtype-mismatch", "dtype_mismatch"),
    ("computed", "computed"),
]


def categorize_failures(failures) -> dict:
    cats = {"coverage_gap": 0, "column_name": 0, "column_integrity": 0,
            "row_integrity": 0, "round_trip": 0, "dtype_mismatch": 0,
            "computed": 0, "other": 0}
    known = {k for _, k in _PREFIXES}
    for f in failures:
        cat = getattr(f, "category", None)
        if cat is not None:                       # Finding: categorize by .category
            key = cat.replace("-", "_")
            cats[key if key in known else "other"] += 1
            continue
        s = str(f)                                # legacy string: categorize by prefix
        for prefix, key in _PREFIXES:
            if s.startswith(prefix):
                cats[key] += 1
                break
        else:
            cats["other"] += 1
    return cats


def log_repair_pass(workbook, table_id, pass_no, errors_before, errors_after,
                    accepted, patch_summary, latency_s) -> None:
    rec = {
        "workbook": workbook, "table_id": table_id, "pass": pass_no,
        "errors_before": list(errors_before), "errors_after": list(errors_after),
        "accepted": bool(accepted),
        "failure_categories": categorize_failures(errors_before),
        "patch_summary": patch_summary, "latency_s": round(float(latency_s), 3),
    }
    _log.info("repair pass %s table=%s before=%d after=%d accepted=%s",
              pass_no, table_id, len(rec["errors_before"]),
              len(rec["errors_after"]), rec["accepted"])
    path = os.environ.get("MCG_REPAIR_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
