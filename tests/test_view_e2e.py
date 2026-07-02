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


from mcg_swarm.analyzers.base import LayoutCandidate
from mcg_swarm.analyzers.registry import register
from mcg_swarm.config import SwarmConfig
from mcg_swarm.coverage import coverage_score, nonempty_cells
from mcg_swarm.runner import run_swarm


class _TransposeLens:
    """Test-only skeleton of Phase C's transpose lens: unconditionally presents
    the sheet through a TransposedView. Registered under a test-unique name."""

    name = "transpose_e2e"

    def analyze(self, grid, sheet, source=None):
        if source is None:
            return []
        view = TransposedView(source)
        vgrid = view.read_region(sheet)
        handle = detect_table(vgrid, sheet)
        total = len(nonempty_cells(vgrid))
        cov = coverage_score(vgrid, [handle.region]) / total if total else 0.0
        return [LayoutCandidate(method="transpose_e2e", handles=(handle,),
                                coverage=cov, view=view)]


register("transpose_e2e", _TransposeLens)


def test_run_swarm_extracts_transposed_sheet_through_view():
    """The full seam: lens builds view -> run_swarm orchestrates through it ->
    orientation persists -> adapter-path rebuild queries the right axis."""
    src = _GridSource(_HORIZONTAL)
    ex = run_swarm(src, config=SwarmConfig(analyzers=("transpose_e2e",)))

    assert len(ex.tables) == 1
    t = ex.tables[0]
    assert t.orientation == "transposed"
    assert not t.errors
    assert [c.name for c in t.columns] == ["Region", "Sales"]

    idx = build_indices(src, ex)[t.table_id]
    assert idx.query("North", "Sales").value == 10
    assert idx.query("South", "Sales").value == 20


def test_vertical_workbook_unaffected_by_transpose_lens_availability():
    """Default config never touches the registered e2e lens: byte-parity guard."""
    vertical = {"S": [("Region", "Sales"), ("North", 10), ("South", 20)]}
    ex = run_swarm(_GridSource(vertical))          # default SwarmConfig()
    t = ex.tables[0]
    assert t.orientation == "vertical"
    assert not t.errors
    idx = build_indices(_GridSource(vertical), ex)[t.table_id]
    assert idx.query("North", "Sales").value == 10


def test_error_stub_persists_orientation():
    """An ambiguous handle extracted through a view still records the view's
    orientation on its error stub (closes the B2a-review stub-test gap)."""
    from mcg_swarm.splitter import TableHandle
    src = _GridSource(_HORIZONTAL)
    view = TransposedView(src)
    bad = TableHandle("S", "A1:A1", 1, [], ambiguous=True, reason="forced stub")
    table = orchestrate_table(view, bad, table_id="S__stub",
                              orientation="transposed")
    assert table.errors                      # it IS a failure stub
    assert table.orientation == "transposed"


def test_unknown_view_kind_warns_and_persists_vertical():
    """A lens view with no `orientation` attribute must not silently misread:
    run_swarm persists 'vertical' AND emits an unknown-view warning finding."""
    from mcg_swarm.analyzers.base import LayoutCandidate
    from mcg_swarm.analyzers.registry import register
    from mcg_swarm.config import SwarmConfig
    from mcg_swarm.runner import run_swarm

    class _NamelessView:
        """Identity pass-through WorkbookSource wrapper with NO orientation attr."""
        def __init__(self, inner): self._inner = inner
        def sheet_names(self): return self._inner.sheet_names()
        def read_cell(self, sheet, row, col): return self._inner.read_cell(sheet, row, col)
        def read_region(self, sheet, min_row=None, max_row=None, min_col=None, max_col=None):
            return self._inner.read_region(sheet, min_row, max_row, min_col, max_col)
        def read_formula_region(self, sheet, min_row=None, max_row=None, min_col=None, max_col=None):
            return self._inner.read_formula_region(sheet, min_row, max_row, min_col, max_col)

    class _NamelessLens:
        name = "nameless_view"
        def analyze(self, grid, sheet, source=None):
            view = _NamelessView(source)
            handle = detect_table(view.read_region(sheet), sheet)
            return [LayoutCandidate(method="nameless_view", handles=(handle,),
                                    coverage=1.0, view=view)]

    register("nameless_view", _NamelessLens)
    vertical = {"S": [("Region", "Sales"), ("North", 10)]}
    ex = run_swarm(_GridSource(vertical),
                   config=SwarmConfig(analyzers=("nameless_view",)))
    assert ex.tables[0].orientation == "vertical"
    assert any(f.category == "unknown-view" and f.severity == "warning"
               for f in ex.findings)
