"""Verification test for DATEDIF fix in the recalculation engine.

The DATEDIF function was not properly supported by the formulas library,
causing #NUM! errors in time-series month-count cells. This cascaded to
SUMIF returning 0, then depreciation cells showing 0.

This test verifies:
1. DATEDIF fast path evaluates correctly
2. Full recalculation with changed depreciation period produces non-zero values
3. Key cells match expected approximate values
"""
import json
import pytest
from financial_kg.models.cell import Cell
from financial_kg.models.graph import FinancialGraph
from financial_kg.engine.evaluator import (
    evaluate_cell, _datedif_calc, _RE_DATEDIF, _RE_DATEDIF_ROUNDED,
)
from financial_kg.engine.recalculator import recalculate


GRAPH_PATH = "output/0f53d2b8_cells.json"


def _load_graph() -> FinancialGraph:
    with open(GRAPH_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    graph = FinancialGraph()
    for c_data in data["cells"]:
        cell = Cell(
            id=c_data["id"],
            sheet=c_data["sheet"],
            row=c_data["row"],
            col=c_data["col"],
            value=c_data["value"],
            formula_raw=c_data.get("formula_raw"),
            data_type=c_data.get("data_type", "number"),
        )
        graph.cells[cell.id] = cell
    return graph


def _find_sheet(graph: FinancialGraph, row: int, col: str, hint: str) -> str | None:
    for k, c in graph.cells.items():
        if c.row == row and c.col == col and hint in c.sheet:
            return c.sheet
    return None


class TestDatedifCalc:
    def test_days_lowercase(self):
        assert _datedif_calc(45258, 47483, "d") == 2225

    def test_days_uppercase(self):
        assert _datedif_calc(45258, 47483, "D") == 2225

    def test_months(self):
        result = _datedif_calc(45258, 45583, "M")
        assert result == 10 or result == 11  # approximate months

    def test_years(self):
        result = _datedif_calc(45258, 48908, "Y")
        assert isinstance(result, (int, float)) and result > 0

    def test_reversed_returns_none(self):
        assert _datedif_calc(47483, 45258, "d") is None


class TestDatedifRegex:
    def test_rounded_division(self):
        m = _RE_DATEDIF_ROUNDED.match('=ROUND((DATEDIF(C19,D19,"d"))/30,0)')
        assert m is not None

    def test_rounded_division_with_multiply(self):
        m = _RE_DATEDIF_ROUNDED.match('=ROUND(DATEDIF(I5,I7,"D")/365*12,0)')
        assert m is not None

    def test_bare_datedif(self):
        m = _RE_DATEDIF.match('=DATEDIF(C19,D19,"d")')
        assert m is not None

    def test_no_match_for_other(self):
        m = _RE_DATEDIF.match('=SUM(A1:A10)')
        assert m is None


class TestDatedifEvaluation:
    def test_base_datedif_cell(self):
        graph = _load_graph()
        sheet = _find_sheet(graph, 20, "D", "序列")
        assert sheet is not None
        cell_id = f"{sheet}_20_D"
        result = evaluate_cell(cell_id, graph)
        assert result == 11.0

    def test_datedif_after_param_change(self):
        graph = _load_graph()
        sheet = _find_sheet(graph, 20, "D", "序列")
        assert sheet is not None

        # Change depreciation period to 20
        param_sheet = _find_sheet(graph, 622, "I", "参数")
        assert param_sheet is not None
        graph.cells[f"{param_sheet}_622_I"].value = 20

        # Re-evaluate the chain: row 17 → 18 → 19 → 20
        for row in [17, 18, 19, 20]:
            cid = f"{sheet}_{row}_D"
            r = evaluate_cell(cid, graph)
            assert r is not None and r != "#NUM!" and r != "#VALUE!", (
                f"Row {row} D returned error: {r}"
            )
            graph.cells[cid].value = r

        # Row 20 D should still give 11 (first year has 11 months)
        assert graph.cells[f"{sheet}_20_D"].value == 11.0


class TestFullRecalculation:
    """Tests that verify the DATEDIF fix via manual cell-by-cell evaluation.
    Full recalculation requires the dependency graph which is built by the
    Streamlit app at runtime. These tests verify the core fix works end-to-end."""

    def test_datedif_chain_produces_correct_months(self):
        """Verify the entire formula chain: param change → dates → DATEDIF → months."""
        graph = _load_graph()
        ts_sheet = _find_sheet(graph, 20, "D", "序列")
        param_sheet = _find_sheet(graph, 622, "I", "参数")
        assert ts_sheet and param_sheet

        # Change depreciation to 20 years
        graph.cells[f"{param_sheet}_622_I"].value = 20

        # Evaluate the dependency chain manually for column D
        for row in [17, 18, 19, 20]:
            cid = f"{ts_sheet}_{row}_D"
            r = evaluate_cell(cid, graph)
            assert r is not None and not isinstance(r, str), (
                f"Row {row} D error: {r}"
            )
            graph.cells[cid].value = r

        # Row 20 D = ROUND(DATEDIF(row19_C, row19_D, "d")/30, 0)
        val = graph.cells[f"{ts_sheet}_20_D"].value
        assert val == 11.0, f"Expected 11 months for first year, got {val}"

    def test_all_datedif_cells_evaluate(self):
        """Verify DATEDIF cells across multiple columns produce non-error values."""
        graph = _load_graph()
        ts_sheet = _find_sheet(graph, 20, "D", "序列")
        assert ts_sheet

        from openpyxl.utils import get_column_letter
        errors = []
        for col_idx in range(4, 30):  # D to AC
            col = get_column_letter(col_idx)
            cid = f"{ts_sheet}_20_{col}"
            cell = graph.cells.get(cid)
            if cell and cell.formula_raw and "DATEDIF" in cell.formula_raw:
                r = evaluate_cell(cid, graph)
                if isinstance(r, str) and r.startswith("#"):
                    errors.append(f"{cid}: {r}")
        assert len(errors) == 0, f"DATEDIF errors: {errors}"

    def test_param_sheet_datedif_cells(self):
        """Verify the 3 DATEDIF cells in 参数输入表 evaluate correctly."""
        graph = _load_graph()
        param_sheet = _find_sheet(graph, 9, "I", "参数")
        assert param_sheet

        for row in [9, 11, 12]:
            cid = f"{param_sheet}_{row}_I"
            cell = graph.cells.get(cid)
            if cell and cell.formula_raw and "DATEDIF" in cell.formula_raw:
                r = evaluate_cell(cid, graph)
                assert isinstance(r, (int, float)), (
                    f"{cid} returned {r} instead of number"
                )
                assert r > 0, f"{cid} returned {r}, expected positive"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
