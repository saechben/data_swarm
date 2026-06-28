from __future__ import annotations
from typing import Optional
from dataclasses import replace
from pydantic import BaseModel, model_validator
from mcg_swarm.source import as_source
from mcg_swarm.splitter import TableHandle
from mcg_swarm.schemas import ColumnSpec


# Output schema for the messy-tab resolution call (enforced at the client boundary).
class _HeaderCol(BaseModel):
    name: str
    dtype: Optional[str] = None


class MessyTabResolution(BaseModel):
    confident: bool
    header_row: Optional[int] = None
    region: Optional[str] = None
    columns: Optional[list[_HeaderCol]] = None

    @model_validator(mode="after")
    def _confident_requires_table(self):
        # A confident answer MUST carry the table location; otherwise it is malformed
        # and must be rejected (→ falls back to ambiguous), not half-applied.
        if self.confident and (self.header_row is None or not self.region or not self.columns):
            raise ValueError("confident=true requires header_row, region, and columns")
        return self


def _preview(source, sheet: str, n: int = 30) -> list[list]:
    src = as_source(source)
    return [list(r) for r in src.read_region(sheet, 1, None, n, None)]


def resolve_messy_tab(source, handle: TableHandle, llm) -> TableHandle:
    """
    Attempt to resolve an ambiguous TableHandle using an LLM preview.

    Never raises. Returns handle still-ambiguous on low confidence or error.
    """
    try:
        preview = _preview(source, handle.sheet)
        res = llm.complete(
            system=(
                "You analyze a messy spreadsheet tab and identify the single clean table: "
                "its header row, A1 region, and columns. Set confident=false if you cannot."
            ),
            user=f"Sheet {handle.sheet!r}, first rows (values only):\n{preview}",
            schema=MessyTabResolution,
        )
        if not res.get("confident"):
            return replace(handle, ambiguous=True, reason="llm not confident about header structure")
        cols = [
            ColumnSpec(
                name=str(c["name"]),
                dtype=c.get("dtype", "string"),
                role="key" if i == 0 else "value",
            )
            for i, c in enumerate(res["columns"])
        ]
        return TableHandle(
            sheet=handle.sheet,
            region=res["region"],
            header_row=int(res["header_row"]),
            columns=cols,
            ambiguous=False,
            reason="resolved by llm header fallback",
        )
    except Exception as e:
        return replace(handle, ambiguous=True, reason=f"llm header fallback error: {e}")
