from __future__ import annotations
from typing import Optional
import networkx as nx

from .cell import Cell
from .indicator import Indicator
from .table import Table


class FinancialGraph:
    """Container for the three-layer financial knowledge graph.

    Layer 1: Cell graph (NetworkX DiGraph) — DEPENDS_ON edges from formulas
    Layer 2: Indicator dict — business-level line items
    Layer 3: Table dict — sheet-level logical tables
    """

    def __init__(self, source_file: str = ""):
        self.source_file = source_file
        # Layer 1: cell_id -> Cell
        self.cells: dict[str, Cell] = {}
        # Layer 1: directed dependency graph (cell_id -> cell_id)
        self.cell_graph: nx.DiGraph = nx.DiGraph()
        # Layer 2: indicator_id -> Indicator
        self.indicators: dict[str, Indicator] = {}
        # Layer 3: table_id -> Table
        self.tables: dict[str, Table] = {}

    # ── Cell layer ──────────────────────────────────────────────────────────

    def add_cell(self, cell: Cell) -> None:
        self.cells[cell.id] = cell
        self.cell_graph.add_node(cell.id)

    def add_dependency(self, from_id: str, to_id: str) -> None:
        """from_id DEPENDS_ON to_id (from_id's formula references to_id)."""
        self.cell_graph.add_edge(from_id, to_id)
        if from_id in self.cells and to_id not in self.cells[from_id].dependencies:
            self.cells[from_id].dependencies.append(to_id)
        if to_id in self.cells and from_id not in self.cells[to_id].dependents:
            self.cells[to_id].dependents.append(from_id)

    def get_cell(self, cell_id: str) -> Optional[Cell]:
        return self.cells.get(cell_id)

    # ── Indicator layer ──────────────────────────────────────────────────────

    def add_indicator(self, indicator: Indicator) -> None:
        self.indicators[indicator.id] = indicator

    # ── Table layer ──────────────────────────────────────────────────────────

    def add_table(self, table: Table) -> None:
        self.tables[table.id] = table

    # ── Orphan cells (no table association) ─────────────────────────────────

    def get_unlinked_cells(self) -> dict[str, list[str]]:
        """Return cells without table_id, grouped by sheet.

        Returns {sheet_name: [cell_id, ...]} sorted by count descending.
        """
        by_sheet: dict[str, list[str]] = {}
        for cid, cell in self.cells.items():
            if cell.table_id is None:
                by_sheet.setdefault(cell.sheet, []).append(cid)
        return dict(sorted(by_sheet.items(), key=lambda x: -len(x[1])))

    def unlinked_cell_count(self) -> int:
        return sum(1 for c in self.cells.values() if c.table_id is None)

    # ── Cycle detection ─────────────────────────────────────────────────────

    def has_cycles(self) -> tuple[bool, int, list[str]]:
        """Detect cycles in the cell dependency graph.

        Returns (has_cycles, cycle_count, list_of_cell_ids_in_cycles).
        Limited to first 100 cycles for performance.
        """
        try:
            cycles = list(nx.simple_cycles(self.cell_graph))
            if not cycles:
                return False, 0, []
            cycle_cells: set[str] = set()
            for cycle in cycles[:100]:
                cycle_cells.update(cycle)
            return True, len(cycles), list(cycle_cells)
        except nx.NetworkXError:
            return False, 0, []

    # ── Stats ────────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        formula_cells = sum(1 for c in self.cells.values() if c.formula_raw)
        unlinked = self.unlinked_cell_count()
        return {
            "total_cells": len(self.cells),
            "formula_cells": formula_cells,
            "dependency_edges": self.cell_graph.number_of_edges(),
            "total_indicators": len(self.indicators),
            "total_tables": len(self.tables),
            "unlinked_cells": unlinked,
            "sheets": list({c.sheet for c in self.cells.values()}),
        }
