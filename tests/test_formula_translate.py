from mcg_swarm.formula_translate import translate_formula

COL = {"A": "Units", "B": "Price", "C": "Revenue", "D": "Discount"}


def test_same_row_product():
    expr, ops, reason = translate_formula("=A2*B2", 2, COL)
    assert reason is None
    assert expr == "Units*Price"
    assert {(o.name, o.source, o.ref) for o in ops} == {
        ("Units", "column", "Units"), ("Price", "column", "Price")}


def test_same_row_subtraction_three_cols():
    expr, ops, reason = translate_formula("=A2-B2-D2", 2, COL)
    assert reason is None
    assert expr == "Units-Price-Discount"
    assert len(ops) == 3


def test_sum_range_expands_to_addition():
    expr, ops, reason = translate_formula("=SUM(A2:C2)", 2, COL)
    assert reason is None
    assert expr == "(Units+Price+Revenue)"
    assert {o.name for o in ops} == {"Units", "Price", "Revenue"}


def test_cross_sheet_bails():
    expr, ops, reason = translate_formula("=Inputs!B2*A2", 2, COL)
    assert expr is None and ops == []
    assert "cross-sheet" in reason.lower()


def test_named_range_bails():
    expr, ops, reason = translate_formula("=A2*TaxRate", 2, COL)
    assert expr is None and ops == []
    assert reason  # non-empty cause (unknown/named reference)


def test_transposed_different_row_bails():
    # =A2*A3 references two rows of the SAME column -> not same-row -> untranslatable
    expr, ops, reason = translate_formula("=A2*A3", 2, COL)
    assert expr is None and ops == []
    assert reason


def test_out_of_table_column_bails():
    # Z2 is not an in-table column
    expr, ops, reason = translate_formula("=A2*Z2", 2, COL)
    assert expr is None and ops == []
    assert reason


# --- Regression tests for final-review fixes ---

def test_caret_scalar_exponent_translates():
    """Excel ^ means exponent; =A2^2 must translate to Units**2."""
    expr, ops, reason = translate_formula("=A2^2", 2, COL)
    assert reason is None
    assert expr == "Units**2"
    assert {o.name for o in ops} == {"Units"}


def test_caret_column_exponent_translates():
    """=A2^B2 must translate to Units**Price."""
    expr, ops, reason = translate_formula("=A2^B2", 2, COL)
    assert reason is None
    assert expr == "Units**Price"
    assert {o.name for o in ops} == {"Units", "Price"}


def test_round_bails_with_function_reason_not_unknown_reference():
    """ROUND is a function call; bail reason must contain 'function', not 'unknown reference'."""
    expr, ops, reason = translate_formula("=ROUND(A2*B2,2)", 2, COL)
    assert expr is None and ops == []
    assert reason is not None
    assert "function" in reason
    assert "unknown reference" not in reason


def test_comparison_bails_cleanly_no_exception():
    """=A2>B2 is a comparison; must bail cleanly (no exception raised)."""
    expr, ops, reason = translate_formula("=A2>B2", 2, COL)
    assert expr is None and ops == []
    assert reason is not None


def test_excel_string_concat_bails_cleanly_no_exception():
    """=A2&B2 is Excel string-concat (becomes bitwise & in Python); must bail cleanly."""
    expr, ops, reason = translate_formula("=A2&B2", 2, COL)
    assert expr is None and ops == []
    assert reason is not None
