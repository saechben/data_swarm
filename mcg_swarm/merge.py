# mcg_swarm/merge.py
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class MergeResult:
    columns: list
    formulas: list
    description: str
    conflicts: list = field(default_factory=list)


def merge_reports(reports, axis: str) -> MergeResult:
    conflicts: list[str] = []
    if axis == "col":
        columns = [c for r in reports for c in r.columns]
    else:
        base = {c.name: c for c in reports[0].columns}
        for r in reports[1:]:
            for c in r.columns:
                if c.name not in base:
                    conflicts.append(f"band {r.band} has extra column {c.name!r}")
                elif base[c.name].dtype != c.dtype:
                    conflicts.append(
                        f"dtype mismatch on {c.name!r}: {base[c.name].dtype} vs {c.dtype} (band {r.band})")
        columns = list(reports[0].columns)
    seen, formulas = set(), []
    for r in reports:
        for f in r.formulas:
            key = (f.target, f.expression)
            if key not in seen:
                seen.add(key); formulas.append(f)
    description = " ".join(r.description for r in reports if r.description)
    return MergeResult(columns=columns, formulas=formulas, description=description, conflicts=conflicts)
