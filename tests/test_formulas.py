import pytest
from mcg_swarm.formulas import parse_ast, eval_expr, build_env, evaluate, FORMULA_FUNCS
from mcg_swarm.schemas import TableFormula, OperandBinding


def test_parse_ast_shape():
    assert parse_ast("Gross - Discount") == {
        "op": "-", "args": [{"var": "Gross"}, {"var": "Discount"}]}

def test_eval_arithmetic():
    assert eval_expr("Gross - Discount", {"Gross": 100, "Discount": 30}) == 70

def test_eval_functions_over_list():
    assert eval_expr("SUM(net)", {"net": [1, 2, 3]}) == 6
    assert eval_expr("AVG(net)", {"net": [2, 4]}) == 3
    assert eval_expr("ROUND(x, 1)", {"x": 1.234}) == 1.2

def test_eval_if_and_comparison():
    assert eval_expr("IF(a > b, a, b)", {"a": 5, "b": 9}) == 9

def test_disallowed_raises():
    with pytest.raises(ValueError):
        eval_expr("__import__('os')", {})
    with pytest.raises(ValueError):
        eval_expr("foo.bar", {"foo": 1})

def test_build_env_column_and_param_override():
    f = TableFormula(
        target="Net", expression="Gross - Discount",
        operands=[OperandBinding(name="Gross", source="column", ref="Gross"),
                  OperandBinding(name="Discount", source="param", ref="Discount")],
    )
    def fake_query(row, col):  # mimics query(row, column) -> object with .value
        return type("V", (), {"value": {"Gross": 100}[col]})()
    env = build_env(f, row_key=["r1"], query=fake_query, overrides={"Discount": 25})
    assert env == {"Gross": 100, "Discount": 25}
    assert evaluate(f, env) == 75
