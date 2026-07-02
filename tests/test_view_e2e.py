"""#3/#4 end-to-end: viewed tables survive orchestration, persistence, and rebuild."""
from mcg_swarm.orchestrator import orchestrate_table
from mcg_swarm.runner import build_indices
from mcg_swarm.schemas import WorkbookExtraction
from mcg_swarm.splitter import detect_table
from mcg_swarm.views import TransposedView
from tests.test_views import _GridSource

# Raw layout is horizontal (fields as rows); the view presents it vertical.
_HORIZONTAL = {"S": [("Region", "North", "South"), ("Sales", 10, 20)]}


def _viewed_table(table_id="S__0"):
    src = _GridSource(_HORIZONTAL)
    view = TransposedView(src)
    handle = detect_table(view.read_region("S"), "S")
    table = orchestrate_table(view, handle, table_id=table_id,
                              orientation="transposed")
    return src, table


def test_orchestrate_table_persists_orientation():
    _, table = _viewed_table()
    assert table.orientation == "transposed"
    assert not table.errors                       # extraction through the view is clean


def test_orientation_defaults_vertical():
    src = _GridSource({"S": [("Region", "Sales"), ("North", 10)]})
    handle = detect_table(src.read_region("S"), "S")
    table = orchestrate_table(src, handle, table_id="S__0")
    assert table.orientation == "vertical"


def test_build_indices_rebuilds_through_view():
    """#3: the adapter-path rebuild must wrap transposed tables in a TransposedView."""
    src, table = _viewed_table()
    ex = WorkbookExtraction(workbook="wb", sheets=["S"], tables=[table],
                            generator_version="test")
    idx = build_indices(src, ex)[table.table_id]  # build_indices as_sources its arg
    assert idx.query("North", "Sales").value == 10
    assert idx.query("South", "Sales").value == 20   # non-diagonal: axis genuinely correct
