from __future__ import annotations
import openpyxl
from dataclasses import replace
from mcg_swarm.splitter import TableHandle
from mcg_swarm.schemas import ColumnSpec


def _preview(path: str, sheet: str, n: int = 30) -> list[list]:
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        ws = wb[sheet]
        return [list(r) for r in ws.iter_rows(min_row=1, max_row=n, values_only=True)]
    finally:
        wb.close()


def resolve_messy_tab(path: str, handle: TableHandle, llm) -> TableHandle:
    """
    Attempt to resolve an ambiguous TableHandle using an LLM preview.

    Never raises. Returns handle still-ambiguous on low confidence or error.
    """
    preview = _preview(path, handle.sheet)
    schema = {
        "confident": "bool",
        "header_row": "int",
        "region": "A1 range",
        "columns": [{"name": "str", "dtype": "number|string|boolean|date"}],
    }
    try:
        res = llm.complete(
            system=(
                "You analyze a messy spreadsheet tab and identify the single clean table: "
                "its header row, A1 region, and columns. Set confident=false if you cannot."
            ),
            user=f"Sheet {handle.sheet!r}, first rows (values only):\n{preview}",
            schema=schema,
        )
    except Exception as e:
        return replace(handle, ambiguous=True, reason=f"llm header fallback error: {e}")

    if not res.get("confident"):
        return replace(handle, ambiguous=True, reason="llm not confident about header structure")

    cols = [
        ColumnSpec(
            name=str(c["name"]),
            dtype=c.get("dtype", "string"),
            role="key" if i == 0 else "value",
        )
        for i, c in enumerate(res.get("columns", []))
    ]
    return TableHandle(
        sheet=handle.sheet,
        region=res["region"],
        header_row=int(res["header_row"]),
        columns=cols,
        ambiguous=False,
        reason="resolved by llm header fallback",
    )
