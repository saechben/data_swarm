"""Per-table formula extraction step. Reads in-cell formula strings, translates
same-row in-table arithmetic into the engine grammar, and emits TableFormulas with
a downstream context hint. Untranslatable formulas are captured (empty operands +
reason) and recorded as provisional notes; only fully-translated targets get
role='computed'. Never raises."""
from __future__ import annotations

from openpyxl.utils import get_column_letter

from mcg_swarm.schemas import ColumnSpec, TableFormula
from mcg_swarm.formula_translate import translate_formula


def _gloss(target: str, operands, expression: str) -> str:
    names = [o.name for o in operands]
    if names:
        return (f"{target} is computed as {expression} "
                f"(same-row columns: {', '.join(names)}).")
    return f"{target} holds an Excel formula that could not be translated."


def extract_formulas(source, index, columns: list, scan_limit: int = 20) -> tuple:
    """Return (formulas, notes). Mutates role='computed' on translated targets."""
    try:
        col_phys = index.physical_columns()                 # name -> abs col
        if not col_phys:
            return [], []
        phys_to_name = {c: n for n, c in col_phys.items()}
        col_by_letter = {get_column_letter(c): n for n, c in col_phys.items()}
        data_rows = index.data_row_numbers()
        if not data_rows:
            return [], []
        min_col, max_col = min(col_phys.values()), max(col_phys.values())
        scan_rows = data_rows[:scan_limit]
        grid = source.read_formula_region(
            index.sheet, scan_rows[0], min_col, scan_rows[-1], max_col)

        by_col = {c.name: c for c in columns}
        seen: set = set()
        formulas: list = []
        notes: list = []

        for abs_row, row in zip(scan_rows, grid):
            for offset, val in enumerate(row):
                if not (isinstance(val, str) and val.startswith("=")):
                    continue
                phys_col = min_col + offset
                target = phys_to_name.get(phys_col)
                if target is None:
                    continue
                expression, operands, reason = translate_formula(
                    val, abs_row, col_by_letter)
                if expression is not None:
                    key = (target, expression)
                    if key in seen:
                        continue
                    seen.add(key)
                    formulas.append(TableFormula(
                        target=target, expression=expression, operands=operands,
                        context=_gloss(target, operands, expression)))
                    spec = by_col.get(target)
                    if isinstance(spec, ColumnSpec):
                        spec.role = "computed"
                else:
                    key = (target, val)
                    if key in seen:
                        continue
                    seen.add(key)
                    formulas.append(TableFormula(
                        target=target, expression=val, operands=[],
                        context=f"{target}: Excel formula not translated ({reason}); "
                                f"shown for reference."))
                    notes.append(f"untranslated formula in {target!r}: {reason}")
        return formulas, notes
    except Exception as exc:  # never raise from extraction
        return [], [f"formula extraction error: {exc}"]
