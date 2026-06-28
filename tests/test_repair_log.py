import json
from mcg_swarm.repair_log import categorize_failures, log_repair_pass

def test_categorize_by_prefix():
    fails = [
        "coverage gap: column 'X' not in _col_to_phys",
        "column-name: duplicate column name 'A'",
        "column-integrity: 'B' index col=C but live header says col=D",
        "row-integrity: key 'k' -> row 5",
        "round-trip: 'V'@'k' live=1 but query()=2",
        "dtype-mismatch: column 'Days' declared number but 18/38 sampled non-null cells are not number",
        "computed mismatch Total@k: live=3 calc=4",
        "something weird",
    ]
    cats = categorize_failures(fails)
    assert cats == {"coverage_gap": 1, "column_name": 1, "column_integrity": 1,
                    "row_integrity": 1, "round_trip": 1, "dtype_mismatch": 1,
                    "computed": 1, "other": 1}

def test_jsonl_written(tmp_path, monkeypatch):
    out = tmp_path / "repair.jsonl"
    monkeypatch.setenv("MCG_REPAIR_LOG", str(out))
    log_repair_pass("wb.xlsx", "T__0", 0, ["coverage gap: x"], [], True, "meta:1col", 1.2)
    rec = json.loads(out.read_text().strip())
    assert rec["table_id"] == "T__0" and rec["accepted"] is True
    assert rec["failure_categories"]["coverage_gap"] == 1
    assert rec["errors_after"] == []
