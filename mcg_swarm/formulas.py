"""Formula engine: extended allowlisted evaluator + FORMULA_FUNCS + build_env."""
from __future__ import annotations
import ast as _ast
import operator
from typing import Any, Callable


def _avg(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


FORMULA_FUNCS: dict[str, Callable] = {
    "SUM": lambda xs: sum(xs),
    "AVG": _avg,
    "MIN": min,
    "MAX": max,
    "COUNT": lambda xs: len(list(xs)),
    "ABS": abs,
    "ROUND": round,
}

_BIN = {
    _ast.Add: operator.add,
    _ast.Sub: operator.sub,
    _ast.Mult: operator.mul,
    _ast.Div: operator.truediv,
    _ast.Pow: operator.pow,
    _ast.FloorDiv: operator.floordiv,
}
_UNARY = {_ast.UAdd: operator.pos, _ast.USub: operator.neg}
_CMP = {
    _ast.Gt: operator.gt,
    _ast.Lt: operator.lt,
    _ast.GtE: operator.ge,
    _ast.LtE: operator.le,
    _ast.Eq: operator.eq,
    _ast.NotEq: operator.ne,
}


def eval_expr(expression: str, env: dict[str, Any]) -> Any:
    """Extended allowlisted evaluator: arithmetic + FORMULA_FUNCS + IF + comparisons. No eval/exec."""
    tree = _ast.parse(expression, mode="eval")

    def ev(node):
        if isinstance(node, _ast.Expression):
            return ev(node.body)
        if isinstance(node, _ast.BinOp) and type(node.op) in _BIN:
            return _BIN[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, _ast.UnaryOp) and type(node.op) in _UNARY:
            return _UNARY[type(node.op)](ev(node.operand))
        if isinstance(node, _ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in _CMP:
            return _CMP[type(node.ops[0])](ev(node.left), ev(node.comparators[0]))
        if isinstance(node, _ast.Name):
            if node.id in env:
                return env[node.id]
            raise ValueError(f"unknown name: {node.id}")
        if isinstance(node, _ast.Constant):
            return node.value
        if isinstance(node, _ast.Call) and isinstance(node.func, _ast.Name):
            fn = node.func.id
            if fn == "IF":
                cond, a, b = node.args
                return ev(a) if ev(cond) else ev(b)
            if fn in FORMULA_FUNCS:
                return FORMULA_FUNCS[fn](*[ev(a) for a in node.args])
            raise ValueError(f"disallowed function: {fn}")
        raise ValueError(f"unsupported expression element: {_ast.dump(node)}")

    return ev(tree)


def parse_ast(expression: str) -> dict:
    """Return a JSON-serialisable AST dict for the expression."""
    tree = _ast.parse(expression, mode="eval")
    sym = {
        _ast.Add: "+", _ast.Sub: "-", _ast.Mult: "*", _ast.Div: "/",
        _ast.Pow: "**", _ast.FloorDiv: "//",
    }

    def conv(node):
        if isinstance(node, _ast.Expression):
            return conv(node.body)
        if isinstance(node, _ast.BinOp):
            return {"op": sym[type(node.op)], "args": [conv(node.left), conv(node.right)]}
        if isinstance(node, _ast.UnaryOp):
            return {"op": "u-" if isinstance(node.op, _ast.USub) else "u+", "args": [conv(node.operand)]}
        if isinstance(node, _ast.Name):
            return {"var": node.id}
        if isinstance(node, _ast.Constant):
            return {"const": node.value}
        if isinstance(node, _ast.Call) and isinstance(node.func, _ast.Name):
            return {"call": node.func.id, "args": [conv(a) for a in node.args]}
        raise ValueError(f"cannot serialize node: {_ast.dump(node)}")

    return conv(tree)


def build_env(formula, row_key, query, query_cell=None, query_range=None, overrides=None) -> dict:
    """Build the variable environment for a TableFormula row evaluation."""
    env: dict[str, Any] = {}
    for op in formula.operands:
        if op.source == "column":
            env[op.name] = query(row_key, op.ref).value
        elif op.source == "cell":
            env[op.name] = query_cell(op.ref).value
        elif op.source == "range":
            env[op.name] = [c.value for c in query_range(op.ref)]
        elif op.source == "param":
            env[op.name] = (overrides or {})[op.name]
    if overrides:
        env.update(overrides)
    return env


def evaluate(formula, env: dict) -> Any:
    """Evaluate a TableFormula against a pre-built environment dict."""
    return eval_expr(formula.expression, env)
