"""Build-time anomaly records on ExtractionIndex — the raw material for the
gate's row-coverage checks (silent-shadowing hole, verified live 2026-07-02)."""
import openpyxl

from mcg_swarm.extraction import build_index
from mcg_swarm.splitter import split_workbook


def _wb(tmp_path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Data"
    for r in rows:
        ws.append(r)
    p = tmp_path / "t.xlsx"
    wb.save(p)
    return str(p)


def test_duplicate_row_keys_recorded_resolution_unchanged(tmp_path):
    p = _wb(tmp_path, [["Region", "Sales"], ["North", 10], ["South", 20],
                       ["North", 99]])
    h = split_workbook(p)[0]
    idx = build_index(p, h, row_key=["Region"])
    assert idx.duplicate_row_keys == [("North", 2, 4)]   # (key, shadowed, winner)
    assert idx.query("North", "Sales").value == 99       # last-wins UNCHANGED


def test_blank_key_rows_recorded(tmp_path):
    p = _wb(tmp_path, [["Region", "Sales"], ["North", 10], [None, 55],
                       ["South", 20]])
    h = split_workbook(p)[0]
    idx = build_index(p, h, row_key=["Region"])
    assert idx.blank_key_rows == [3]


def test_clean_table_records_empty(tmp_path):
    p = _wb(tmp_path, [["Region", "Sales"], ["North", 10], ["South", 20]])
    h = split_workbook(p)[0]
    idx = build_index(p, h, row_key=["Region"])
    assert idx.duplicate_row_keys == [] and idx.blank_key_rows == []
