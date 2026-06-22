import math
from dataclasses import dataclass
from mcg_swarm.size_estimate import size_estimate, plan_bands, ROWS_PER_AGENT, K_MAX
from mcg_swarm.splitter import TableHandle
from mcg_swarm.schemas import ColumnSpec

def _handle(rows, cols):
    from openpyxl.utils import get_column_letter
    region = f"A1:{get_column_letter(cols)}{rows + 1}"  # +1 header row
    return TableHandle("S", region, 1, [ColumnSpec(name=f"c{i}", dtype="number") for i in range(cols)])

def test_small_table_no_fanout():
    axis, k, bands = plan_bands(_handle(100, 5))
    assert k == 1 and len(bands) == 1

def test_large_table_fans_out_by_rows():
    axis, k, bands = plan_bands(_handle(12_000, 10))
    assert axis == "row" and k == math.ceil(12_000 / ROWS_PER_AGENT) == 3
    assert len(bands) == 3 and bands[0].row_start < bands[1].row_start

def test_enterprise_file_fans_to_at_least_two():
    axis, k, _ = plan_bands(_handle(100_000, 22))
    assert k >= 2 and k <= K_MAX

def test_size_estimate_counts():
    se = size_estimate(_handle(3, 4))
    assert se.rows == 3 and se.cols == 4 and se.cell_count == 12
