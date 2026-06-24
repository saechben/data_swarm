"""Tests for the framework-agnostic Tool layer + BandView probes."""
import os

import openpyxl
import pytest

from mcg_swarm.size_estimate import Band
from mcg_swarm.subagent.tools import BandView, Tool, build_band_toolset


def _wb(tmp_path):
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Data"
    ws.append(["Quarterly Sales", None, None])  # row 1: title banner
    ws.append(["Region", "Revenue", "Units"])   # row 2: header
    ws.append(["EMEA", 100, 5])                  # row 3: data
    ws.append(["APAC", 200, 8])                  # row 4: data
    ws.append(["Total", 300, 13])                # row 5: totals row
    p = tmp_path / "t.xlsx"; wb.save(p); return str(p)


def _band():
    return Band(sheet="Data", header_row=2, region="A2:C5",
                col_start=1, col_end=3, row_start=3, row_end=5)


def test_geometry(tmp_path):
    v = BandView(_wb(tmp_path), _band())
    g = v.geometry()
    assert g["header_row"] == 2
    assert g["data_row_start"] == 3 and g["data_row_end"] == 5
    assert g["n_data_rows"] == 3 and g["n_cols"] == 3


def test_snapshot_is_single_open(tmp_path):
    """Deleting the file after construction must not break probes (proves snapshot)."""
    p = _wb(tmp_path)
    v = BandView(p, _band())
    os.remove(p)  # no probe may reopen the workbook
    assert v.peek_rows(0, 10)[0]["cells"][0] == "EMEA"


def test_header_candidates_include_banner_and_header(tmp_path):
    v = BandView(_wb(tmp_path), _band())
    rows = v.header_candidates(rows_above=2)
    by_row = {r["row"]: r["cells"] for r in rows}
    assert by_row[1][0] == "Quarterly Sales"   # title banner surfaced
    assert by_row[2][0] == "Region"            # the header row


def test_peek_rows_are_data_only(tmp_path):
    v = BandView(_wb(tmp_path), _band())
    rows = v.peek_rows(0, 2)
    assert [r["cells"][0] for r in rows] == ["EMEA", "APAC"]
    assert rows[0]["row"] == 3


def test_tail_rows_catches_totals(tmp_path):
    v = BandView(_wb(tmp_path), _band())
    tail = v.tail_rows(1)
    assert tail[-1]["cells"][0] == "Total"
    assert tail[-1]["row"] == 5


def test_column_values_includes_header_and_tail(tmp_path):
    v = BandView(_wb(tmp_path), _band())
    col = v.column_values(1)  # Revenue
    assert col["header"] == "Revenue"
    assert col["values"] == [100, 200, 300]


def test_peek_region_clamps_to_band(tmp_path):
    v = BandView(_wb(tmp_path), _band())
    # Over-wide request clamps to the band's columns/rows.
    rows = v.peek_region("A3:Z99")
    assert [r["row"] for r in rows] == [3, 4, 5]
    assert all(len(r["cells"]) == 3 for r in rows)
    sub = v.peek_region("A3:B4")
    assert [r["cells"] for r in sub] == [["EMEA", 100], ["APAC", 200]]


def test_build_band_toolset_shapes(tmp_path):
    v = BandView(_wb(tmp_path), _band())
    tools = build_band_toolset(v)
    assert all(isinstance(t, Tool) for t in tools)
    names = {t.name for t in tools}
    assert {"geometry", "peek_rows", "tail_rows", "column_values",
            "header_candidates", "peek_region"} <= names
    # Each tool is callable via its handler with a plain dict.
    geom = next(t for t in tools if t.name == "geometry")
    assert geom.handler({})["header_row"] == 2
    tail = next(t for t in tools if t.name == "tail_rows")
    assert tail.handler({"count": 1})["rows"][-1]["cells"][0] == "Total"
