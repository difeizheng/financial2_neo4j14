"""Formula evaluator: re-evaluates a cell's formula using the formulas library.

The `formulas` library expects inputs keyed by raw Excel reference strings
(e.g. 'F5', '$I$250', 'F5:BE5', '参数输入表!I250').  Our cell IDs use the
format "{sheet}_{row}_{col}".  This module bridges the two representations.

Performance notes
-----------------
- Compiled formula objects are cached **persistently** across recalculation
  sessions (keyed by formula string).  Previously clear_formula_cache() wiped
  this on every recalc call, forcing re-compilation of every formula each run.
  Compilation is now ~10-50x more expensive than evaluation, so keeping the
  cache warm is critical.
- clear_formula_cache() is retained for tests / memory-pressure situations but
  should NOT be called at the start of every recalculate() call.
"""
from __future__ import annotations
import re
from datetime import datetime
from typing import Any, Optional

import numpy as np

try:
    from formulas import Parser as _FormulaParser
    _PARSER = _FormulaParser()
    _FORMULAS_AVAILABLE = True
except ImportError:
    _FORMULAS_AVAILABLE = False

from financial_kg.models.graph import FinancialGraph


# ── Sheet name mapping for Excel formula quirks ───────────────────────────────
_SHEET_NAME_ALIASES = {
    '表1-式样及构造信息表': '表1-资金筹措及还本付息表',
    '表1-资金筹措及还本付息信息表': '表1-资金筹措及还本付息表',
}


# ── Cached sheet-name lookup ─────────────────────────────────────────────────
# _SHEETS_CACHE stores (graph_id, set_of_sheet_names).  Rebuild only when the
# graph identity changes (i.e. a different FinancialGraph instance is passed).
_SHEETS_CACHE: list = [-1, set()]


def _get_actual_sheets(graph: FinancialGraph) -> set[str]:
    """Return set of sheet names from the graph, cached."""
    gid = id(graph)
    if _SHEETS_CACHE[0] == gid:
        return _SHEETS_CACHE[1]

    sheets: set[str] = set()
    for cid in graph.cells.keys():
        parts = cid.rsplit('_', 2)
        if len(parts) == 3:
            sheets.add(parts[0])
    _SHEETS_CACHE[0] = gid
    _SHEETS_CACHE[1] = sheets
    return sheets


def _normalize_sheet_name(sheet: str, graph: FinancialGraph) -> str:
    """Resolve sheet name aliases to actual storage names."""
    if sheet in _SHEET_NAME_ALIASES:
        return _SHEET_NAME_ALIASES[sheet]

    actual_sheets = _get_actual_sheets(graph)

    if sheet in actual_sheets:
        return sheet

    for actual in actual_sheets:
        if sheet.split('-')[0] == actual.split('-')[0] if '-' in sheet else False:
            return actual

    return sheet


# ── Reference key helpers ────────────────────────────────────────────────────

def _cell_id_to_ref(cell_id: str, formula_sheet: str) -> str:
    parts = cell_id.rsplit("_", 2)
    if len(parts) != 3:
        return cell_id
    sheet, row, col = parts
    ref = f"{col}{row}"
    if sheet != formula_sheet:
        ref = f"{sheet}!{ref}"
    return ref


def _build_input_map(
    func_inputs: dict,
    formula_sheet: str,
    graph: FinancialGraph,
) -> dict[str, np.ndarray]:
    kwargs: dict[str, np.ndarray] = {}
    for raw_key in func_inputs:
        kwargs[raw_key] = _resolve_input_key(raw_key, formula_sheet, graph)
    return kwargs


def _resolve_input_key(
    raw_key: str,
    formula_sheet: str,
    graph: FinancialGraph,
) -> np.ndarray:
    if "!" in raw_key:
        sheet_part, addr_part = raw_key.split("!", 1)
        sheet_part = sheet_part.strip("'")
        sheet_part = _normalize_sheet_name(sheet_part, graph)
    else:
        sheet_part = formula_sheet
        addr_part = raw_key

    addr_part = addr_part.replace("$", "")

    if ":" in addr_part:
        return _resolve_range(sheet_part, addr_part, graph)

    cell_id = _addr_to_cell_id(sheet_part, addr_part)
    cell = graph.cells.get(cell_id)
    val = cell.value if cell is not None else None
    return np.array([[_coerce(val)]])


def _resolve_range(sheet: str, addr: str, graph: FinancialGraph) -> np.ndarray:
    sheet = _normalize_sheet_name(sheet, graph)
    start, end = addr.split(":", 1)
    start_col, start_row = _split_col_row(start)
    end_col, end_row = _split_col_row(end)

    from openpyxl.utils import column_index_from_string, get_column_letter
    sc = column_index_from_string(start_col)
    ec = column_index_from_string(end_col)
    sr, er = int(start_row), int(end_row)

    rows = []
    for r in range(sr, er + 1):
        row_vals = []
        for c in range(sc, ec + 1):
            col_letter = get_column_letter(c)
            cell_id = f"{sheet}_{r}_{col_letter}"
            cell = graph.cells.get(cell_id)
            row_vals.append(_coerce(cell.value if cell else None))
        rows.append(row_vals)

    return np.array(rows)


def _split_col_row(addr: str):
    m = re.match(r"([A-Za-z]+)(\d+)", addr)
    if not m:
        raise ValueError(f"Cannot parse cell address: {addr!r}")
    return m.group(1).upper(), m.group(2)


def _addr_to_cell_id(sheet: str, addr: str) -> str:
    col, row = _split_col_row(addr)
    return f"{sheet}_{row}_{col}"


def _coerce(val: Any) -> Any:
    """Convert Python value to something numpy/formulas can handle."""
    if val is None:
        return 0.0

    if isinstance(val, str) and val in ('#NUM!', '#VALUE!', '#DIV/0!', '#REF!', '#N/A'):
        return val

    if isinstance(val, str) and 'T00:00:00' in val:
        try:
            dt = datetime.fromisoformat(val.replace('T00:00:00', ''))
            excel_epoch = datetime(1899, 12, 30)
            serial = (dt - excel_epoch).days
            return float(serial)
        except Exception:
            pass

    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        return val
    return str(val)


# ── Formula compilation cache (persistent across recalc sessions) ─────────────
#
# KEY CHANGE from original: this cache is intentionally NOT cleared between
# recalculate() calls.  Formulas don't change at runtime; there is no reason
# to recompile them.  clear_formula_cache() is kept for explicit invalidation
# (e.g. after loading a new workbook).

_compiled_cache: dict[str, Any] = {}


def clear_formula_cache() -> None:
    """Clear the compiled formula cache.

    Only call this when the workbook itself changes (new file loaded).
    Do NOT call this at the start of every recalculate() — that defeats the
    purpose of the cache and is the primary cause of the 11-hour runtime.
    """
    _compiled_cache.clear()


def _compile_formula(formula: str):
    """Parse and compile a formula, caching the result persistently."""
    if formula in _compiled_cache:
        return _compiled_cache[formula]

    if not formula.startswith("="):
        formula = "=" + formula

    try:
        ast_result = _PARSER.ast(formula)
        func = ast_result[1].compile()
        _compiled_cache[formula] = func
        return func
    except Exception:
        _compiled_cache[formula] = None  # cache failures too — don't retry
        return None


def evaluate_cell(cell_id: str, graph: FinancialGraph) -> Optional[Any]:
    """Re-evaluate a formula cell and return the new value.

    Returns None if the cell has no formula or evaluation fails.
    """
    if not _FORMULAS_AVAILABLE:
        return None

    cell = graph.cells.get(cell_id)
    if cell is None or not cell.formula_raw:
        return None

    func = _compile_formula(cell.formula_raw)
    if func is None:
        return None

    try:
        kwargs = _build_input_map(func.inputs, cell.sheet, graph)
        result = func(**kwargs)
    except Exception:
        return None

    return _extract_scalar(result)


def _extract_scalar(result: Any) -> Any:
    """Pull a Python scalar out of a numpy array result."""
    if isinstance(result, np.ndarray):
        flat = result.flatten()
        if flat.size == 0:
            return None
        val = flat[0]
        if isinstance(val, float) and np.isnan(val):
            return None
        if isinstance(val, (np.integer,)):
            return int(val)
        if isinstance(val, (np.floating,)):
            return float(val)
        if isinstance(val, (np.bool_,)):
            return bool(val)
        return val
    return result
