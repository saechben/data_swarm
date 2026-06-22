"""Shared utilities: safe arithmetic eval, cell-range math, value comparison."""
from __future__ import annotations

import ast
import operator
from typing import Any

from openpyxl.utils import range_boundaries

_BIN = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def safe_eval(expr: str, names: dict[str, float]) -> float:
    """Evaluate a pure-arithmetic expression over the given variable names."""
    tree = ast.parse(expr, mode="eval")

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            return _BIN[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            return _UNARY[type(node.op)](ev(node.operand))
        if isinstance(node, ast.Name):
            return names[node.id]
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"unsupported expression element: {ast.dump(node)}")

    return ev(tree)


# --------------------------------------------------------------------------- #
# Cell-range geometry
# --------------------------------------------------------------------------- #
def range_box(a1: str) -> tuple[int, int, int, int]:
    """'B3:F14' -> (min_row, min_col, max_row, max_col)."""
    min_col, min_row, max_col, max_row = range_boundaries(a1)
    return min_row, min_col, max_row, max_col


def _area(box: tuple[int, int, int, int]) -> int:
    r1, c1, r2, c2 = box
    return (r2 - r1 + 1) * (c2 - c1 + 1)


def range_iou(a1_a: str, a1_b: str) -> float:
    """Intersection-over-union of two cell ranges (rectangle math, area-based)."""
    a = range_box(a1_a)
    b = range_box(a1_b)
    ir1, ic1 = max(a[0], b[0]), max(a[1], b[1])
    ir2, ic2 = min(a[2], b[2]), min(a[3], b[3])
    if ir1 > ir2 or ic1 > ic2:
        inter = 0
    else:
        inter = (ir2 - ir1 + 1) * (ic2 - ic1 + 1)
    union = _area(a) + _area(b) - inter
    return inter / union if union else 0.0


# --------------------------------------------------------------------------- #
# Value comparison
# --------------------------------------------------------------------------- #
def values_match(expected: Any, got: Any, tolerance: float, dtype: str = "number") -> bool:
    if got is None:
        return False
    if dtype == "number":
        try:
            e = float(expected)
            g = float(got)
        except (TypeError, ValueError):
            return False
        denom = max(abs(e), 1.0)
        return abs(e - g) <= tolerance * denom
    return str(expected).strip() == str(got).strip()
