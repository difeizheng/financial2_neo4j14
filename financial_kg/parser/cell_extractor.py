from __future__ import annotations
from typing import Optional

from ..models.cell import Cell, CellData
from ..models.graph import FinancialGraph
from .formula_parser import extract_dependencies


def build_cell_graph(
    sheet_cells: dict[str, list[CellData]],
    progress_callback=None,
) -> FinancialGraph:
    """Build the Cell layer of the knowledge graph.

    Steps:
    1. Create a Cell node for every CellData.
    2. Parse each formula and add DEPENDS_ON edges.
    3. Populate Cell.dependencies and Cell.dependents lists.

    progress_callback(sheet_name, done, total) is called after each sheet.
    """
    graph = FinancialGraph()

    # ── Pass 1: create all Cell nodes ────────────────────────────────────────
    for sheet_name, cells in sheet_cells.items():
        for cd in cells:
            cell = Cell(
                id=cd.id,
                sheet=cd.sheet,
                row=cd.row,
                col=cd.col,
                value=cd.value,
                formula_raw=cd.formula_raw,
                data_type=cd.data_type,
                is_merged=cd.is_merged,
                merge_parent_id=cd.merge_parent_id,
                number_format=cd.number_format,
            )
            graph.add_cell(cell)

    # ── Pass 2: build dependency edges ───────────────────────────────────────
    all_sheets = list(sheet_cells.keys())
    total = sum(len(v) for v in sheet_cells.values())
    done = 0

    for sheet_name, cells in sheet_cells.items():
        for cd in cells:
            if cd.formula_raw:
                deps = extract_dependencies(cd.formula_raw, sheet_name)
                for dep_id in deps:
                    # Only add edge if the dependency cell exists in our graph
                    # (external references to other workbooks are skipped)
                    if dep_id in graph.cells:
                        graph.add_dependency(cd.id, dep_id)
            done += 1

        if progress_callback:
            progress_callback(sheet_name, done, total)

    # ── Pass 3: detect header cells heuristically ────────────────────────────
    _mark_headers(graph, sheet_cells)

    return graph


def _mark_headers(graph: FinancialGraph, sheet_cells: dict[str, list[CellData]]) -> None:
    """Mark cells as headers using simple heuristics.

    A cell is considered a header if:
    - It has no formula
    - Its value is a non-empty string
    - It sits in the first non-empty row of a contiguous block
    """
    for sheet_name, cells in sheet_cells.items():
        # Group cells by row
        rows: dict[int, list[CellData]] = {}
        for cd in cells:
            rows.setdefault(cd.row, []).append(cd)

        if not rows:
            continue

        sorted_rows = sorted(rows.keys())
        # Find the first row that is all-text (no formulas, all string values)
        for row_num in sorted_rows[:5]:  # only check first 5 rows per sheet
            row_cells = rows[row_num]
            if all(
                cd.formula_raw is None and isinstance(cd.value, str) and cd.value.strip()
                for cd in row_cells
            ):
                for cd in row_cells:
                    if cd.id in graph.cells:
                        graph.cells[cd.id].is_header = True
                break
