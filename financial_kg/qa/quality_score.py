"""Data quality diagnostics and scoring for parsed financial models."""
from __future__ import annotations

from dataclasses import dataclass

from ..models.graph import FinancialGraph


@dataclass
class QualityDiagnostics:
    score: float  # 0-100 overall quality score
    sheet_count: int
    empty_sheets: list[str]  # sheets with zero non-empty cells
    header_confidence: float  # 0.0-1.0
    formula_ratio: float
    constant_ratio: float
    blank_ratio: float
    has_cycles: bool
    cycle_count: int
    cycle_cells: list[str]
    table_coverage: float  # cells with table_id / total_cells
    unlinked_ratio: float
    unlinked_hotspot: dict[str, int]  # {sheet_name: unlinked_count}


def compute_quality_diagnostics(graph: FinancialGraph) -> QualityDiagnostics:
    """Compute a comprehensive quality score and diagnostics."""
    total = len(graph.cells)
    if total == 0:
        return QualityDiagnostics(
            score=0, sheet_count=0, empty_sheets=[],
            header_confidence=0, formula_ratio=0, constant_ratio=0,
            blank_ratio=0, has_cycles=False, cycle_count=0,
            cycle_cells=[], table_coverage=0, unlinked_ratio=0,
            unlinked_hotspot={},
        )

    # Basic ratios
    formula_cells = sum(1 for c in graph.cells.values() if c.formula_raw)
    number_cells = sum(1 for c in graph.cells.values() if c.data_type == "number")
    empty_cells = sum(1 for c in graph.cells.values() if c.data_type in ("empty", ""))
    formula_ratio = formula_cells / total
    constant_ratio = number_cells / total
    blank_ratio = empty_cells / total

    # Table coverage
    linked = sum(1 for c in graph.cells.values() if c.table_id is not None)
    table_coverage = linked / total

    # Unlinked cells
    unlinked_count = graph.unlinked_cell_count()
    unlinked_ratio = unlinked_count / total
    unlinked_hotspot = {
        sheet: len(ids)
        for sheet, ids in graph.get_unlinked_cells().items()
    }

    # Empty sheets
    sheet_cell_counts: dict[str, int] = {}
    for c in graph.cells.values():
        sheet_cell_counts[c.sheet] = sheet_cell_counts.get(c.sheet, 0) + 1
    empty_sheets = [s for s, cnt in sheet_cell_counts.items() if cnt == 0]

    # Header confidence
    header_confidence = _compute_header_confidence(graph)

    # Cycle detection
    has_cycles, cycle_count, cycle_cells = graph.has_cycles()

    # Score computation
    link_score = (1 - unlinked_ratio) * 100

    if formula_ratio < 0.05:
        formula_score = 50
    elif formula_ratio <= 0.40:
        formula_score = 100
    else:
        formula_score = max(0, 100 - (formula_ratio - 0.40) * 250)

    table_score = table_coverage * 100

    if not has_cycles:
        cycle_score = 100
    else:
        cycle_score = max(0, 100 - cycle_count * 10)

    score = link_score * 0.30 + formula_score * 0.25 + table_score * 0.25 + cycle_score * 0.20

    return QualityDiagnostics(
        score=round(score, 1),
        sheet_count=len(sheet_cell_counts),
        empty_sheets=empty_sheets,
        header_confidence=round(header_confidence, 2),
        formula_ratio=round(formula_ratio, 4),
        constant_ratio=round(constant_ratio, 4),
        blank_ratio=round(blank_ratio, 4),
        has_cycles=has_cycles,
        cycle_count=cycle_count,
        cycle_cells=cycle_cells[:50],  # cap for display
        table_coverage=round(table_coverage, 4),
        unlinked_ratio=round(unlinked_ratio, 4),
        unlinked_hotspot=unlinked_hotspot,
    )


def _compute_header_confidence(graph: FinancialGraph) -> float:
    """Estimate how well headers were detected."""
    headers = [c for c in graph.cells.values() if c.is_header]
    if not headers:
        return 0.0
    qualified = sum(
        1 for h in headers
        if h.data_type == "string" and h.row <= 5 and len(h.dependents) > 0
    )
    return qualified / len(headers)
