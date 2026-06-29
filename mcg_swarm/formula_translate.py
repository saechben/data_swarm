"""Pure translator: in-cell Excel formula string -> engine-grammar expression +
column-operand bindings. Phase 1: same-row, in-table arithmetic only. Any reference
that is cross-sheet, a named range, a different row (transposed), or an out-of-table
column makes the whole formula untranslatable (returns a reason; never raises)."""
from __future__ import annotations

import ast as _ast
import re
from typing import Optional

from mcg_swarm.schemas import OperandBinding
from mcg_swarm.formulas import parse_ast

_A1 = re.compile(r"\$?([A-Z]{1,3})\$?([0-9]+)")
_SUM_RANGE = re.compile(r"SUM\(\s*\$?([A-Z]{1,3})\$?([0-9]+)\s*:\s*\$?([A-Z]{1,3})\$?([0-9]+)\s*\)")


def _col_to_idx(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n


def translate_formula(excel: str, formula_row: int, col_by_letter: dict) -> tuple:
    """Return (expression, operands, reason). reason is None on success."""
    expr = excel.strip()
    if expr.startswith("="):
        expr = expr[1:]
    if "!" in expr:
        return None, [], "cross-sheet reference"
    # Excel ^ means exponent; Python ** is supported by both eval_expr and parse_ast.
    expr = expr.replace("^", "**")

    idx_to_letter = {_col_to_idx(L): L for L in col_by_letter}
    operands: dict[str, OperandBinding] = {}

    def _add(name):
        operands[name] = OperandBinding(name=name, source="column", ref=name)
        return name

    # 1) Expand same-row horizontal SUM(start:end) into (a+b+c). Vertical or
    #    out-of-table ranges bail. Each expanded column is registered as an operand.
    def _expand_sum(m):
        c1, r1, c2, r2 = m.group(1), int(m.group(2)), m.group(3), int(m.group(4))
        if r1 != formula_row or r2 != formula_row:
            raise _Bail("SUM range is not on the formula's row (multi-row/transposed)")
        i1, i2 = _col_to_idx(c1), _col_to_idx(c2)
        if i1 > i2:
            i1, i2 = i2, i1
        names = []
        for i in range(i1, i2 + 1):
            if i not in idx_to_letter:
                raise _Bail("SUM range spans an out-of-table column")
            names.append(_add(col_by_letter[idx_to_letter[i]]))
        return "(" + "+".join(names) + ")"

    def _sub_ref(m):
        letters, row = m.group(1), int(m.group(2))
        if row != formula_row:
            raise _Bail("reference is not on the same row (transposed/multi-row)")
        if letters not in col_by_letter:
            raise _Bail(f"reference {letters}{row} is not an in-table column")
        return _add(col_by_letter[letters])

    try:
        # 1) expand SUM ranges first, then 2) replace remaining single A1 refs.
        expr = _SUM_RANGE.sub(_expand_sum, expr)
        expr = _A1.sub(_sub_ref, expr)
    except _Bail as b:
        return None, [], str(b)

    # 3) Validate the rewritten expression: must parse, every Call bails (Phase 1
    #    supports arithmetic only; SUM is already expanded by the regex above),
    #    every Name must be a registered operand. Call is checked first because
    #    ast.walk yields a Call before its child Name nodes, preventing the
    #    misleading "unknown reference: ROUND" message.
    try:
        tree = _ast.parse(expr, mode="eval")
    except SyntaxError:
        return None, [], "unparseable expression"
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Call):
            name = node.func.id if isinstance(node.func, _ast.Name) else "expression"
            return None, [], f"unsupported function: {name} (Phase 1)"
        if isinstance(node, _ast.Name) and node.id not in operands:
            return None, [], f"unknown reference: {node.id}"
    if not operands:
        return None, [], "no in-table column references"

    # Belt-and-suspenders: guard parse_ast so no unsupported node type can escape.
    try:
        ast_result = parse_ast(expr)
    except Exception:
        return None, [], "unsupported expression"
    return expr, list(operands.values()), None


class _Bail(Exception):
    """Internal control-flow signal for an untranslatable reference."""
