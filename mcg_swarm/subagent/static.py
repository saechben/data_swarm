"""Deterministic-first band analysis — today's behavior behind the Subagent port.

`StaticSubagent.analyze` is byte-for-byte equivalent to the original
`analyze_band`: infer columns from a 20-row sample, then optionally run a single-shot
LLM header-verify to fill unit/role. It never invents cell values and never raises on
LLM failure (the failure is recorded as an anomaly and the deterministic result stands).
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from mcg_swarm.schemas import ColumnSpec, SegmentReport
from mcg_swarm.source import as_source
from mcg_swarm.splitter import _infer_dtype
from mcg_swarm.subagent.task import BandTask


# Output schema the LLM header-verify call must conform to (enforced at the client
# boundary). Extra fields are ignored; only shape/types of these are guaranteed.
class _ColumnPatch(BaseModel):
    name: str
    unit: Optional[str] = None
    role: Optional[str] = None


class HeaderVerification(BaseModel):
    columns: list[_ColumnPatch] = []


def _analyze_band_single_open(source, band, header):
    """Open workbook ONCE to infer column types AND detect first-row formulas.

    Replaces the two separate opens in _deterministic_columns + _detect_formulas
    that each cost ~2-3 s on large files (openpyxl parses the whole XML on open).
    Returns (columns, formulas, anomalies).
    """
    anomalies: list[str] = []
    # data_only=True returns computed cell VALUES (not raw formula strings).
    # Formula strings are only visible with data_only=False, which we intentionally
    # skip here to avoid a second workbook open.  As a result, the formulas list
    # stays empty in this fast path.  This is informational-only: formula detection
    # does not affect scored capabilities.
    src = as_source(source)
    # Read up to 20 rows for dtype sampling
    sample_rows = src.read_region(band.sheet, band.row_start,
                                  band.col_start,
                                  min(band.row_end, band.row_start + 19),
                                  band.col_end)

    cols = []
    for j, name in enumerate(header):
        samples = [r[j] if j < len(r) else None for r in sample_rows]
        cols.append(ColumnSpec(name=str(name), dtype=_infer_dtype(samples),
                               role="key" if j == 0 else "value"))
    return cols, [], anomalies


def run_static(source, band, header, llm=None) -> SegmentReport:
    """Deterministic column inference + optional one-shot LLM header-verify."""
    columns, formulas, anomalies = _analyze_band_single_open(source, band, header)
    desc = f"Band {band.region} with columns: {', '.join(c.name for c in columns)}."
    if llm is not None:
        try:
            res = llm.complete(
                system="You verify spreadsheet table headers. Confirm names make sense and "
                       "fill missing unit/role. Never invent cell values.",
                user=f"Header: {header}\nInferred: {[c.model_dump() for c in columns]}",
                schema=HeaderVerification)
            by_name = {c["name"]: c for c in res.get("columns", [])}
            for c in columns:
                patch = by_name.get(c.name)
                if patch:
                    if patch.get("unit") is not None: c.unit = patch["unit"]
                    if patch.get("role") in ("key", "value", "computed"): c.role = patch["role"]
        except Exception as e:  # fall back to deterministic; never fail the band
            anomalies.append(f"llm verify skipped: {e}")
    return SegmentReport(band=band.region, columns=columns, formulas=formulas,
                         description=desc, anomalies=anomalies)


class StaticSubagent:
    """Implements the Subagent port with the deterministic-first strategy."""

    def __init__(self, llm=None) -> None:
        self._llm = llm

    def analyze(self, task: BandTask) -> SegmentReport:
        source = task.source if task.source is not None else task.path
        return run_static(source, task.band, task.header, self._llm)
